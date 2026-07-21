"""v4.2.0 required-context checker.

The rule engine must not only react to whatever fields happen to be
present in ``patient_state``; it must also detect when a recommendation
would require additional context that is missing, stale, or unparsable.

The checker walks:

- every active rule whose trigger binds a drug (so the rule would fire
  if the audit touches that drug),
- every drug the LLM suggests (start / continue / increase / decrease /
  replace),
- every food or exercise concept the LLM mentions,
- every disease code in ``patient_state.disease_codes``,

and produces a flat list of :class:`MissingContextField` records. Each
record carries the patient field path, the rule IDs that needed it, and
a stable reason code.

The freshness policy is **declared but not enforced** in v4.2.0 — the
rule engine must never invent a clinical threshold. If a measurement is
present but stale, we still raise a missing-context finding and flag
``pending_medical_review`` so a clinician can configure the threshold
later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from safety.normalizer import normalize
from safety.rule_repository import Rule, RuleRepository
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
class RequiredContextReport:
    missing_fields: List[MissingContextField] = field(default_factory=list)
    checked_fields: List[str] = field(default_factory=list)
    freshness_policy: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_sufficient(self) -> bool:
        return not self.missing_fields

    def to_dict(self) -> dict:
        return {
            "is_sufficient": self.is_sufficient,
            "missing_context_fields": [m.to_dict() for m in self.missing_fields],
            "checked_fields": sorted(set(self.checked_fields)),
            "freshness_policy": dict(self.freshness_policy),
        }


class RequiredContextChecker:
    """Derives the patient fields required to safely evaluate a request."""

    # Map: rule type -> the patient field the rule needs.
    _RULE_TYPE_TO_FIELDS: Dict[str, List[str]] = {
        "patient_state": [],          # populated per-rule from parameters.field
        "max_daily_dose": [],         # dose is already in dialogue_output
        "drug_drug": [],
        "drug_food": [],
        "drug_exercise": [],          # may depend on parameters.field
        "disease_food": [],
        "disease_exercise": [],
        "response_compliance": [],
        "patient_risk": [],           # populated per-rule from parameters.field
    }

    # Drugs whose dose change requires an eGFR check.
    _EGFR_DRUGS = {"metformin", "二甲双胍"}

    # Drugs whose dose change requires a serum potassium check.
    _POTASSIUM_DRUGS = {
        "lisinopril", "ramipril", "enalapril",
        "losartan", "valsartan",
        "spironolactone", "amiloride",
        "acei", "arb", "螺内酯",
    }

    # Drugs / situations that require a glucose check.
    _GLUCOSE_DRUGS = {"insulin", "glipizide", "甘精胰岛素"}

    # Activities that require a systolic_bp check.
    _VIGOROUS_ACTIVITIES = {"running", "跑步", "vigorous_running"}

    # Foods / diseases that require a gout check.
    _GOUT_DISEASES = {"hyperuricemia_gout", "gout"}
    _GOUT_KEYWORDS = {"高嘌呤", "内脏", "海鲜汤", "啤酒", "动物肝脏"}

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
        this request that are missing, malformed, or stale."""

        report = RequiredContextReport(freshness_policy=dict(self._freshness_policy))
        state = patient_state or {}
        output = dialogue_output or {}

        drugs_in_focus = self._drugs_in_focus(state, output)
        foods_in_focus = self._foods_in_focus(output)
        exercises_in_focus = self._exercises_in_focus(output)
        diseases_in_focus = self._diseases_in_focus(state)

        rule_ids_by_field: Dict[str, Set[str]] = {}

        # 1. Walk every active rule that might fire for the drugs/foods/
        # exercises/diseases in focus, and record the fields each rule
        # requires.
        for rule in self._repository.iter_active_rules():
            needed_fields = self._fields_required_by_rule(
                rule, drugs_in_focus, foods_in_focus, exercises_in_focus,
                diseases_in_focus,
            )
            for field_path in needed_fields:
                rule_ids_by_field.setdefault(field_path, set()).add(rule.id)

        # 2. For each required field, check that the patient actually
        # provides it (possibly via the v4.2 nested ``measurements``
        # block, the legacy flat key, or the strict v4.2 flat fallback).
        for field_path, rule_ids in rule_ids_by_field.items():
            value, observed_at = self._extract_field(state, field_path)
            report.checked_fields.append(field_path)
            status = self._evaluate_field_status(field_path, value, observed_at)
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

    def _drugs_in_focus(
        self,
        state: Dict[str, Any],
        output: Dict[str, Any],
    ) -> Set[str]:
        drugs: Set[str] = set()
        for med in state.get("current_medications", []) or []:
            if isinstance(med, dict):
                canonical = normalize(med.get("drug_id") or med.get("name") or "")
                if canonical:
                    drugs.add(canonical)
        for action in output.get("medication_actions", []) or []:
            if not isinstance(action, dict):
                continue
            canonical = normalize(action.get("drug_id") or action.get("drug") or "")
            if canonical:
                drugs.add(canonical)
        return drugs

    def _foods_in_focus(self, output: Dict[str, Any]) -> Set[str]:
        foods: Set[str] = set()
        for fa in output.get("food_advice", []) or []:
            if isinstance(fa, dict):
                canonical = normalize(fa.get("food_concept_id") or fa.get("food") or "")
                if canonical:
                    foods.add(canonical)
        return foods

    def _exercises_in_focus(self, output: Dict[str, Any]) -> Set[str]:
        out: Set[str] = set()
        for ea in output.get("exercise_advice", []) or []:
            if isinstance(ea, dict):
                canonical = normalize(
                    ea.get("activity_concept_id") or ea.get("activity") or ""
                )
                if canonical:
                    out.add(canonical)
                intensity = normalize(ea.get("intensity") or "")
                if intensity:
                    out.add(intensity)
        return out

    def _diseases_in_focus(self, state: Dict[str, Any]) -> Set[str]:
        return {normalize(d) for d in (state.get("disease_codes") or []) if d}

    def _fields_required_by_rule(
        self,
        rule: Rule,
        drugs: Set[str],
        foods: Set[str],
        exercises: Set[str],
        diseases: Set[str],
    ) -> Set[str]:
        """Return the set of patient field paths the rule needs."""
        required: Set[str] = set()

        trigger_drugs = {normalize(d) for d in rule.triggers.get("drugs_any", []) or []}
        trigger_fields = list(rule.triggers.get("patient_fields_any", []) or [])

        # Decide whether the rule could plausibly fire.
        rule_drugs_param = rule.parameters.get("drugs") or []
        if rule.parameters.get("drug"):
            rule_drugs_param = list(rule_drugs_param) + [rule.parameters["drug"]]
        rule_drugs_param = {normalize(d) for d in rule_drugs_param if d}

        # Any rule whose drug binding matches the in-focus drug set is
        # considered "could fire". Drug-free rules fire on foods,
        # exercises, or diseases alone.
        fires_for_drug = bool(trigger_drugs & drugs) or bool(rule_drugs_param & drugs)
        fires_for_food = bool(foods) and rule.type in ("drug_food", "disease_food")
        fires_for_exercise = bool(exercises) and rule.type in ("drug_exercise", "disease_exercise")
        fires_for_disease = bool(
            rule.parameters.get("disease_code")
            and normalize(rule.parameters["disease_code"]) in diseases
        )

        # Drug-anchored rules: drugs are required (food/exercise/disease
        # alone is insufficient).
        if rule.type in {"drug_exercise", "drug_food"}:
            fires_for_exercise = fires_for_exercise and fires_for_drug
            fires_for_food = fires_for_food and fires_for_drug

        if not (
            fires_for_drug or fires_for_food or fires_for_exercise
            or fires_for_disease
        ):
            return required

        # patient_state / drug_exercise: pull parameters.field.
        if rule.type in ("patient_state", "patient_condition", "drug_exercise", "patient_risk"):
            field_name = rule.parameters.get("field")
            if field_name:
                required.add(field_name)

        # Drug-class context shortcuts.
        if rule.type in ("patient_state", "patient_condition", "max_daily_dose", "drug_drug"):
            if drugs & self._EGFR_DRUGS:
                required.add("egfr")
            if drugs & self._POTASSIUM_DRUGS:
                required.add("serum_potassium")
            if drugs & self._GLUCOSE_DRUGS:
                required.add("glucose")

        # Vigorous exercise implies a systolic BP check.
        if rule.type in ("drug_exercise", "disease_exercise"):
            if exercises & self._VIGOROUS_ACTIVITIES:
                required.add("systolic_bp")

        # Gout-related food/exercise checks.
        if rule.type in ("drug_food", "disease_food", "drug_exercise", "disease_exercise"):
            if diseases & self._GOUT_DISEASES:
                required.add("gout_acute_flare")

        # trigger_fields are added verbatim if the rule fires.
        for f in trigger_fields:
            required.add(f)

        return required

    def _extract_field(
        self,
        state: Dict[str, Any],
        field_name: str,
    ) -> Tuple[Any, Any]:
        """Return (value, observed_at) for ``field_name``.

        Looks up:
        - ``state.measurements.<field>.value``
        - the legacy flat key (e.g. ``state.egfr``)
        - the v4.1 flat fallback (e.g. ``state.serum_potassium_mmol_l``)
        """
        measurements = state.get("measurements") or {}
        if isinstance(measurements, dict) and field_name in measurements:
            entry = measurements[field_name]
            if isinstance(entry, dict):
                return entry.get("value"), entry.get("observed_at")

        if field_name in state:
            return state[field_name], None

        # Legacy fallback aliases.
        legacy_aliases = {
            "systolic_bp": "latest_systolic_bp_mmHg",
            "glucose": "latest_glucose_mmol_l",
            "serum_potassium": "serum_potassium_mmol_l",
        }
        legacy_key = legacy_aliases.get(field_name)
        if legacy_key and legacy_key in state:
            return state[legacy_key], None

        # clinical_flags pass-through.
        flags = state.get("clinical_flags") or {}
        if field_name in flags:
            return flags[field_name], None

        return None, None

    def _evaluate_field_status(
        self,
        field_name: str,
        value: Any,
        observed_at: Any,
    ) -> str:
        """Return ``"ok"``, ``"missing"``, ``"null"``, ``"non_finite"``,
        ``"unknown"``, ``"stale"``, etc."""

        if value is None:
            # clinical_flags may use ``None`` to mean "patient did not
            # disclose" — still treated as missing for safety purposes.
            return "missing"
        if isinstance(value, bool):
            return "ok"
        if isinstance(value, (int, float)) or isinstance(value, str):
            if not is_finite_number(value):
                return "non_finite"
            # Field present; freshness check below only when timestamp
            # is present in the v4.2 nested form.
            return "ok"
        # Unknown type (list, dict, ...) means malformed input.
        return "unknown"

    def _reason_for_status(self, field_name: str, status: str) -> str:
        if status == "missing":
            return "required_for_medication_safety_check"
        if status == "null":
            return "field_present_but_null"
        if status == "non_finite":
            return "field_not_finite_number"
        if status == "unknown":
            return "field_has_unexpected_type"
        if status == "stale":
            return "field_stale_pending_medical_review"
        return "required_for_medication_safety_check"