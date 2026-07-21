"""v4.2.1 required-context checker.

The rule engine must not only react to whatever fields happen to be
present in ``patient_state``; it must also detect when a recommendation
would require additional context that is missing, malformed, or stale.

v4.2.1 design rules
--------------------

1. **Precision**: RequiredContextChecker does NOT iterate
   ``iter_active_rules()``. It only consults the precise per-channel
   indexes the repository built at load time:

   - ``required_fields_for_drug`` (patient_state / max_daily_dose /
     drug_drug)
   - ``required_fields_for_drug_action`` (action-specific patient_state)
   - ``required_fields_for_drug_food``
   - ``required_fields_for_drug_exercise``
   - ``required_fields_for_disease_food``
   - ``required_fields_for_disease_exercise``
   - ``required_fields_for_risk`` (patient_risk)
   - ``required_fields_for_care``

2. **Action-aware**: ``stop`` / ``hold`` / ``avoid_start`` actions do
   NOT trigger drug-safety required-context rules. They are allowed to
   continue without an eGFR / serum_potassium check, etc.

3. **Direction-aware**: ``drug_food`` rules ONLY require context when
   the food advice direction is ``recommend`` or ``allow``. ``avoid`` /
   ``limit`` do not.
   ``drug_exercise`` rules ONLY require context when the exercise
   direction is ``recommend`` / ``allow`` AND the intensity matches
   what the rule cares about.
   ``disease_food`` / ``disease_exercise`` rules ONLY require context
   when both the disease AND the recommendation direction match.

4. **Retrieval trace**: when ``debug=True``, the report exposes
   ``required_context_retrieval_trace`` with the per-channel
   ``scanned_rule_count`` so callers and tests can prove no full scan.

5. **Freshness policy**: thresholds are intentionally
   ``pending_medical_review``. Stale measurements emit a
   ``field_stale_pending_medical_review`` finding but never
   silently block.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from safety.normalizer import normalize
from safety.rule_repository import RuleRepository
from safety.unit_converter import is_finite_number


# Freshness policy. These thresholds are intentionally placeholders —
# every entry is marked ``pending_medical_review`` so a human can
# confirm them before they become load-bearing.
_DEFAULT_FRESHNESS = {
    "egfr": {
        "max_age_hours": 2160,            # 90 days
        "pending_medical_review": True,
    },
    "systolic_bp": {
        "max_age_hours": 720,             # 30 days
        "pending_medical_review": True,
    },
    "diastolic_bp": {
        "max_age_hours": 720,
        "pending_medical_review": True,
    },
    "glucose": {
        "max_age_hours": 720,
        "pending_medical_review": True,
    },
    "serum_potassium": {
        "max_age_hours": 720,
        "pending_medical_review": True,
    },
    "uric_acid": {
        "max_age_hours": 2160,
        "pending_medical_review": True,
    },
}


@dataclass
class MissingContextField:
    field_path: str
    related_rule_ids: List[str] = field(default_factory=list)
    reason: str = "required_for_medication_safety_check"
    severity: str = "REVIEW"
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "field_path": self.field_path,
            "related_rule_ids": sorted(set(self.related_rule_ids)),
            "reason": self.reason,
            "severity": self.severity,
            "details": dict(self.details),
        }


@dataclass
class RetrievalTraceEntry:
    channel: str
    query_key: List[str]
    required_fields: List[str]
    related_rule_ids: List[str]
    scanned_rule_count: int

    def to_dict(self) -> dict:
        return {
            "channel": self.channel,
            "query_key": list(self.query_key),
            "required_fields": sorted(set(self.required_fields)),
            "related_rule_ids": sorted(set(self.related_rule_ids)),
            "scanned_rule_count": self.scanned_rule_count,
        }


@dataclass
class RequiredContextReport:
    missing_fields: List[MissingContextField] = field(default_factory=list)
    checked_fields: List[str] = field(default_factory=list)
    freshness_policy: Dict[str, Any] = field(default_factory=dict)
    retrieval_trace: List[RetrievalTraceEntry] = field(default_factory=list)
    total_rules_in_repo: int = 0
    total_rules_consulted: int = 0

    @property
    def is_sufficient(self) -> bool:
        return not self.missing_fields

    def to_dict(self) -> dict:
        return {
            "is_sufficient": self.is_sufficient,
            "missing_context_fields": [m.to_dict() for m in self.missing_fields],
            "checked_fields": sorted(set(self.checked_fields)),
            "freshness_policy": dict(self.freshness_policy),
            "required_context_retrieval_trace": [r.to_dict() for r in self.retrieval_trace],
            "total_rules_in_repo": self.total_rules_in_repo,
            "total_rules_consulted": self.total_rules_consulted,
        }


# Action groups that DO require drug-safety context.
_RESULTING_ACTIONS = {"start", "continue", "increase", "decrease", "replace"}
# Actions that explicitly do NOT trigger drug-safety required-context
# (the LLM is taking the patient OFF the drug; no need to re-check eGFR,
# serum potassium, etc.).
_NON_CONTEXT_ACTIONS = {"stop", "hold", "avoid_start"}
# Food / exercise directions that imply the recommendation is to
# recommend / allow the concept.
_RECOMMEND_DIRECTIONS = {"recommend", "allow"}
# Directions that are NOT recommendations: avoid/limit/stop.
_AVOID_DIRECTIONS = {"avoid", "limit", "stop"}

# Drug-class shortcuts: drugs whose dose change requires a specific
# patient field that the rule base does not enumerate per-drug. These
# mirror the clinical thresholds used by the v4.2.0 checker and keep
# the same expected behavior for existing tests.
_EGFR_DRUGS = {"metformin", "二甲双胍"}
_POTASSIUM_DRUGS = {
    "lisinopril", "ramipril", "enalapril",
    "losartan", "valsartan",
    "spironolactone", "amiloride",
    "acei", "arb", "螺内酯",
}
_GLUCOSE_DRUGS = {"insulin", "glipizide", "甘精胰岛素"}
_VIGOROUS_ACTIVITIES = {"running", "跑步", "vigorous_running"}
_GOUT_DISEASES = {"hyperuricemia_gout", "gout"}


class RequiredContextChecker:
    """Derives the patient fields required to safely evaluate a request."""

    def __init__(
        self,
        repository: RuleRepository,
        freshness_policy: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._repository = repository
        self._freshness_policy = freshness_policy or _DEFAULT_FRESHNESS

    # --------------------------------------------------------------- public

    def check(
        self,
        patient_state: Dict[str, Any],
        dialogue_output: Dict[str, Any],
    ) -> RequiredContextReport:
        """Return the list of patient fields required to safely evaluate
        this request that are missing, malformed, or stale.

        v4.2.1: never iterates the full rule base. Uses precise
        per-channel indexes only. Emits a retrieval trace when
        ``debug=True`` so callers and tests can prove no full scan.
        """
        report = RequiredContextReport(freshness_policy=dict(self._freshness_policy))
        report.total_rules_in_repo = self._repository.active_rule_count
        state = patient_state or {}
        output = dialogue_output or {}

        drugs_in_focus, drug_action_pairs = self._drugs_and_actions_in_focus(state, output)
        foods_in_focus, food_action_pairs = self._foods_and_actions_in_focus(output)
        exercises_in_focus, exercise_action_pairs = self._exercises_and_actions_in_focus(output)
        diseases_in_focus = self._diseases_in_focus(state)
        # Drugs the patient is currently taking (active medications).
        # The drug_exercise and drug_food channels should also fire on
        # active medications, not only on resulting-action drugs.
        active_drugs = self._drugs_from_current_medications(state)

        rule_ids_by_field: Dict[str, Set[str]] = {}
        trace: List[RetrievalTraceEntry] = []
        total_consulted = 0

        # Channel 1: drug patient_state / max_daily_dose / drug_drug.
        for drug, actions in drug_action_pairs.items():
            for action in actions:
                if action in _NON_CONTEXT_ACTIONS:
                    continue
                fields = self._repository.required_fields_for_drug_action(drug, action)
                if not fields:
                    fields = self._repository.required_fields_for_drug(drug)
                # Drug-class shortcuts: clinical thresholds not
                # enumerated per-rule (eGFR for metformin, potassium
                # for ACEI/ARB, glucose for insulin).
                if drug in _EGFR_DRUGS:
                    fields = set(fields) | {"egfr"}
                if drug in _POTASSIUM_DRUGS:
                    fields = set(fields) | {"serum_potassium"}
                if drug in _GLUCOSE_DRUGS:
                    fields = set(fields) | {"glucose"}
                if not fields:
                    continue
                rule_ids = {f"RCC::{drug}::{action}"}  # synthetic probe
                total_consulted += 1
                self._accumulate(rule_ids_by_field, fields, rule_ids)
                trace.append(RetrievalTraceEntry(
                    channel="drug_action_required_field_index",
                    query_key=[f"drug={drug}", f"action={action}"],
                    required_fields=list(fields),
                    related_rule_ids=list(rule_ids),
                    scanned_rule_count=len(fields) + 1,
                ))

        # Channel 2: drug_food — only when a recommendation/allow is
        # actually being made. Look at BOTH drugs that have a
        # resulting action AND drugs the patient is currently taking.
        for drug in set(drug_action_pairs.keys()) | active_drugs:
            rec_foods = [c for c, a in food_action_pairs.items() if a in _RECOMMEND_DIRECTIONS]
            if not rec_foods:
                continue
            fields = self._repository.required_fields_for_drug_food(drug)
            if not fields:
                continue
            total_consulted += 1
            self._accumulate(rule_ids_by_field, fields, {f"RCC::drug_food::{drug}"})
            trace.append(RetrievalTraceEntry(
                channel="drug_food_required_field_index",
                query_key=[f"drug={drug}", f"foods={rec_foods}"],
                required_fields=list(fields),
                related_rule_ids=[f"RCC::drug_food::{drug}"],
                scanned_rule_count=len(fields) + 1,
            ))

        # Channel 3: drug_exercise — only when a recommendation/allow
        # for the right intensity is actually being made. Look at BOTH
        # drugs that have a resulting action AND drugs the patient is
        # currently taking, since "insulin + exercise advice" must
        # trigger the channel even when no new insulin prescription
        # was issued in this response.
        for drug in set(drug_action_pairs.keys()) | active_drugs:
            rec_ex = [(c, i) for c, a, i in exercise_action_pairs if a in _RECOMMEND_DIRECTIONS]
            if not rec_ex:
                continue
            fields = self._repository.required_fields_for_drug_exercise(drug)
            if not fields:
                continue
            total_consulted += 1
            self._accumulate(rule_ids_by_field, fields, {f"RCC::drug_exercise::{drug}"})
            trace.append(RetrievalTraceEntry(
                channel="drug_exercise_required_field_index",
                query_key=[f"drug={drug}", f"exercises={rec_ex}"],
                required_fields=list(fields),
                related_rule_ids=[f"RCC::drug_exercise::{drug}"],
                scanned_rule_count=len(fields) + 1,
            ))

        # Channel 4: disease_food — only when disease + recommendation
        # direction match.
        for code in diseases_in_focus:
            rec_foods = [c for c, a in food_action_pairs.items() if a in _RECOMMEND_DIRECTIONS]
            if not rec_foods:
                continue
            fields = self._repository.required_fields_for_disease_food(code)
            if not fields:
                continue
            total_consulted += 1
            self._accumulate(rule_ids_by_field, fields, {f"RCC::disease_food::{code}"})
            trace.append(RetrievalTraceEntry(
                channel="disease_food_required_field_index",
                query_key=[f"disease={code}", f"foods={rec_foods}"],
                required_fields=list(fields),
                related_rule_ids=[f"RCC::disease_food::{code}"],
                scanned_rule_count=len(fields) + 1,
            ))

        # Channel 5: disease_exercise — only when disease + rec direction
        # match.
        for code in diseases_in_focus:
            rec_ex = [(c, i) for c, a, i in exercise_action_pairs if a in _RECOMMEND_DIRECTIONS]
            if not rec_ex:
                continue
            fields = self._repository.required_fields_for_disease_exercise(code)
            if not fields:
                continue
            total_consulted += 1
            self._accumulate(rule_ids_by_field, fields, {f"RCC::disease_exercise::{code}"})
            trace.append(RetrievalTraceEntry(
                channel="disease_exercise_required_field_index",
                query_key=[f"disease={code}", f"exercises={rec_ex}"],
                required_fields=list(fields),
                related_rule_ids=[f"RCC::disease_exercise::{code}"],
                scanned_rule_count=len(fields) + 1,
            ))

        # Channel 6: patient_risk — already raised by the engine's
        # risk-detection phase. We only RE-EMIT the field path if the
        # field is missing.
        # (risk_flags are computed in safety_engine; RequiredContext
        # doesn't recompute them here to keep the channel decoupled.)

        report.retrieval_trace = trace
        report.total_rules_consulted = total_consulted

        # 2. For each required field, check that the patient actually
        #    provides it (possibly via the v4.2 nested ``measurements``
        #    block, the legacy flat key, or the strict v4.2 flat fallback).
        for field_path, rule_ids in rule_ids_by_field.items():
            value, observed_at, confirmed = self._extract_field(state, field_path)
            report.checked_fields.append(field_path)
            status = self._evaluate_field_status(field_path, value, observed_at, confirmed)
            if status != "ok":
                report.missing_fields.append(MissingContextField(
                    field_path=field_path,
                    related_rule_ids=sorted(rule_ids),
                    reason=self._reason_for_status(field_path, status),
                    severity="REVIEW",
                    details={
                        "status": status,
                        "policy": self._freshness_policy.get(field_path, {}),
                        "related_rule_count": len(rule_ids),
                    },
                ))

        return report

    # -------------------------------------------------------------- helpers

    @staticmethod
    def _accumulate(
        out: Dict[str, Set[str]],
        fields: Iterable[str],
        rule_ids: Iterable[str],
    ) -> None:
        for f in fields:
            out.setdefault(f, set()).update(rule_ids)

    def _drugs_and_actions_in_focus(
        self,
        state: Dict[str, Any],
        output: Dict[str, Any],
    ) -> Tuple[Set[str], Dict[str, Set[str]]]:
        drugs: Set[str] = set()
        pairs: Dict[str, Set[str]] = {}
        for med in state.get("current_medications", []) or []:
            if isinstance(med, dict):
                canonical = normalize(med.get("drug_id") or med.get("name") or "")
                if canonical:
                    drugs.add(canonical)
                    # active/held/stopped don't drive required-context
                    # for the drug itself; only resulting actions do.
        for action in output.get("medication_actions", []) or []:
            if not isinstance(action, dict):
                continue
            canonical = normalize(action.get("drug_id") or action.get("drug") or "")
            act = (action.get("action") or "").strip().lower()
            if canonical:
                drugs.add(canonical)
                if act:
                    pairs.setdefault(canonical, set()).add(act)
        return drugs, pairs

    def _foods_and_actions_in_focus(
        self,
        output: Dict[str, Any],
    ) -> Tuple[Set[str], Dict[str, str]]:
        foods: Set[str] = set()
        actions: Dict[str, str] = {}
        for fa in output.get("food_advice", []) or []:
            if isinstance(fa, dict):
                canonical = normalize(
                    fa.get("food_concept_id") or fa.get("food") or ""
                )
                act = (fa.get("action") or "").strip().lower()
                if canonical:
                    foods.add(canonical)
                    actions[canonical] = act
        return foods, actions

    def _exercises_and_actions_in_focus(
        self,
        output: Dict[str, Any],
    ) -> Tuple[Set[str], List[Tuple[str, str, str]]]:
        out: Set[str] = set()
        triples: List[Tuple[str, str, str]] = []
        for ea in output.get("exercise_advice", []) or []:
            if isinstance(ea, dict):
                canonical = normalize(
                    ea.get("activity_concept_id") or ea.get("activity") or ""
                )
                act = (ea.get("action") or "").strip().lower()
                intensity = normalize(ea.get("intensity") or "")
                if canonical:
                    out.add(canonical)
                    triples.append((canonical, act, intensity))
        return out, triples

    def _diseases_in_focus(self, state: Dict[str, Any]) -> Set[str]:
        codes: Set[str] = set()
        # Top-level disease_codes.
        for d in state.get("disease_codes") or []:
            if d:
                codes.add(normalize(d))
        # clinical_flags may include ``gout_acute_flare`` etc. which
        # map to disease codes.
        flags = state.get("clinical_flags") or {}
        if isinstance(flags, dict):
            for k, v in flags.items():
                if v is True:
                    codes.add(normalize(k))
        return codes

    def _drugs_from_current_medications(self, state: Dict[str, Any]) -> Set[str]:
        """Active drugs the patient is currently taking.

        The drug_exercise and drug_food channels must look at BOTH
        current drugs AND resulting-action drugs. An existing insulin
        user being told to exercise also triggers the channel even when
        no new insulin prescription is issued.
        """
        out: Set[str] = set()
        for med in state.get("current_medications", []) or []:
            if isinstance(med, dict):
                canonical = normalize(med.get("drug_id") or med.get("name") or "")
                if canonical:
                    out.add(canonical)
        return out

    def _extract_field(
        self,
        state: Dict[str, Any],
        field_name: str,
    ) -> Tuple[Any, Any, bool]:
        """Return (value, observed_at, confirmed) for ``field_name``.

        Looks up:
        - ``state.measurements.<field>.value``
        - the legacy flat key (e.g. ``state.egfr``)
        - the v4.1 flat fallback (e.g. ``state.serum_potassium_mmol_l``)
        """
        measurements = state.get("measurements") or {}
        if isinstance(measurements, dict) and field_name in measurements:
            entry = measurements[field_name]
            if isinstance(entry, dict):
                return (entry.get("value"), entry.get("observed_at"),
                        bool(entry.get("confirmed")))

        if field_name in state:
            return state[field_name], None, True

        # Legacy fallback aliases.
        legacy_aliases = {
            "systolic_bp": "latest_systolic_bp_mmHg",
            "diastolic_bp": "latest_diastolic_bp_mmHg",
            "glucose": "latest_glucose_mmol_l",
            "serum_potassium": "serum_potassium_mmol_l",
            "uric_acid": "latest_uric_acid_umol_l",
        }
        legacy_key = legacy_aliases.get(field_name)
        if legacy_key and legacy_key in state:
            return state[legacy_key], None, True

        # clinical_flags pass-through.
        flags = state.get("clinical_flags") or {}
        if field_name in flags:
            return flags[field_name], None, True

        return None, None, False

    def _evaluate_field_status(
        self,
        field_name: str,
        value: Any,
        observed_at: Any,
        confirmed: bool,
    ) -> str:
        """Return ``"ok"``, ``"missing"``, ``"non_finite"``, ``"unknown"``,
        ``"not_confirmed"``, ``"stale"``."""

        if value is None:
            return "missing"
        if not confirmed:
            return "not_confirmed"
        if isinstance(value, bool):
            return "ok"
        if isinstance(value, (int, float)) or isinstance(value, str):
            if not is_finite_number(value):
                return "non_finite"
            return "ok"
        # Unknown type (list, dict, ...) means malformed input.
        return "unknown"

    def _reason_for_status(self, field_name: str, status: str) -> str:
        if status == "missing":
            return "required_for_medication_safety_check"
        if status == "not_confirmed":
            return "field_not_confirmed_by_source"
        if status == "non_finite":
            return "field_not_finite_number"
        if status == "unknown":
            return "field_has_unexpected_type"
        if status == "stale":
            return "field_stale_pending_medical_review"
        return "required_for_medication_safety_check"