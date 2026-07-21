"""v4.2.0 safety engine.

Pipeline (per ``audit()`` call):

  1. ``input_validation``     (InputValidator — strict JSON shape)
  2. ``normalization``        (normalize_draft — strict enums)
  3. ``matching``             (keyword scan, drug context build)
  4. ``text_parsing``         (text_dose_parser)
  5. ``risk_detection``       (patient_risk_field_index)
  6. ``required_context``     (RequiredContextChecker)
  7. ``consistency``          (SYS001..SYS008 with dedup)
  8. ``candidate_selection``  (per-channel recall — tightened)
  9. ``evaluation``           (candidates only)
 10. ``logging``              (audit_logger.write)

Decision rules:

- BLOCK  > REVIEW > PASS.
- Any input_validation_errors, missing_context_fields,
  consistency_violations or medical_violation with severity REVIEW
  forces REVIEW unless a BLOCK-severity finding is also present.
- ``original_llm_reply_was_sent`` is True iff ``decision == "PASS"``.
- Any unhandled exception during audit is converted to REVIEW with
  ``decision_basis=["SYSTEM_ERROR"]`` and the exception text lands in
  ``developer_diagnostics`` (NOT in ``patient_visible_response``).

v4.2.0 changes vs v4.1.1
------------------------
- Strict input validation phase; fail-closed wrapper around ``audit()``.
- RequiredContextChecker (new module) walks every recalled rule and
  emits missing-context findings when patient_state lacks the required
  fields.
- ``drug_only_rule_index`` replaces the legacy simple_index fallback
  for candidate recall.
- DSL now supports ``parameters.conditions`` (``all`` / ``any`` /
  ``not``) and ``parameters.range`` (``gte``/``lt``).
- Unit conversion via ``safety.unit_converter`` is mandatory for dose
  comparisons.
- New unified report: ``decision_basis``, ``medical_violations``,
  ``input_validation_errors``, ``missing_context_fields``,
  ``consistency_violations``, ``system_findings``, ``all_findings``,
  ``reviewer_message``, ``developer_diagnostics``. The legacy
  ``violations`` key is preserved as an alias of
  ``medical_violations``.
"""

from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from safety.audit_logger import AuditLogger
from safety.candidate_selector import (
    SelectionResult,
    select_candidate_rule_ids,
)
from safety.consistency_checker import ConsistencyChecker
from safety.input_models import (
    InputValidationIssue,
    StrictAuditInput,
    SUPPORTED_SCHEMA_VERSIONS,
    DialogueOutputInput,
    PatientStateInput,
)
from safety.input_validator import InputValidator
from safety.keyword_matcher import KeywordMatcher
from safety.models import (
    AuditReport,
    DrugContext,
    MatchedEntities,
    RetrievalChannel,
    RiskFlag,
    RuleConditionTrace,
    RuleEvaluationTrace,
    RuleViolation,
    SEVERITY_RANK,
    SystemViolation,
    TextExtraction,
    TimingBreakdown,
)
from safety.normalizer import (
    normalize,
    normalize_draft,
    normalize_medication_status,
)
from safety.required_context_checker import RequiredContextChecker
from safety.rule_evaluator import RuleEvaluator
from safety.rule_repository import Rule, RuleRepository
from safety.semantic_retriever import SemanticRetriever
from safety.text_dose_parser import TextDoseParser


_RESULTING_ACTIONS = {"start", "continue", "increase", "decrease", "replace"}
_REMOVING_ACTIONS = {"stop", "hold"}
_NEUTRAL_ACTIONS = {"avoid_start"}


@dataclass
class _MatcherOutcome:
    mentioned_drugs: Set[str] = field(default_factory=set)
    food_concepts: Set[str] = field(default_factory=set)
    exercise_intensities: Set[str] = field(default_factory=set)
    risk_codes: Set[str] = field(default_factory=set)
    care_types: Set[str] = field(default_factory=set)
    action_directions: Set[str] = field(default_factory=set)
    disease_codes: Set[str] = field(default_factory=set)


class DialogueSafetyEngine:
    """v4.2.0 engine."""

    def __init__(
        self,
        rules_path: str | Path,
        audit_logger: Optional[AuditLogger] = None,
        semantic_retriever: Optional[SemanticRetriever] = None,
        freshness_policy: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.repository = RuleRepository(rules_path)
        self.evaluator = RuleEvaluator()
        self.consistency_checker = ConsistencyChecker()
        self.text_parser = TextDoseParser()
        self.semantic_retriever = semantic_retriever or SemanticRetriever(enabled=False)
        self.audit_logger = audit_logger or AuditLogger()
        self.input_validator = InputValidator(self.repository)
        self.required_context_checker = RequiredContextChecker(
            self.repository,
            freshness_policy=freshness_policy,
        )

        # Build the keyword matcher ONCE.
        self._matcher = self._build_matcher()
        self._known_drug_canonicals: List[str] = list(
            self.repository.aliases.canonical_to_aliases.keys()
        )

    # ------------------------------------------------------------ matcher

    def _build_matcher(self) -> KeywordMatcher:
        matcher = KeywordMatcher()
        for canonical, aliases in self.repository.aliases.canonical_to_aliases.items():
            for alias in aliases:
                matcher.add_drug_alias(alias, canonical)
        for rule in self.repository.iter_active_rules():
            params = rule.parameters
            if rule.type in ("drug_food", "disease_food"):
                for kw in params.get("keywords", []) or []:
                    matcher.add_concept(kw, "food_concept", payload=kw, rule_id=rule.id)
            elif rule.type in ("drug_exercise", "disease_exercise"):
                intensity = params.get("exercise_intensity")
                if intensity:
                    matcher.add_concept(intensity, "exercise_intensity", payload=intensity, rule_id=rule.id)
            elif rule.type == "patient_risk":
                rc = params.get("risk_code")
                if rc:
                    matcher.add_concept(rc, "risk_code", payload=rc, rule_id=rule.id)
            elif rule.type == "response_compliance":
                for ct in params.get("required_care_types", []) or []:
                    matcher.add_concept(ct, "care_type", payload=ct, rule_id=rule.id)
                for kw in params.get("forbidden_actions", []) or []:
                    matcher.add_concept(kw, "action_direction", payload=kw, rule_id=rule.id)
        for code, names in self.repository.aliases.disease_to_codes.items():
            for n in names:
                matcher.add_concept(n, "disease_alias", payload=code, rule_id=None)
        return matcher

    # ----------------------------------------------------------------- audit

    def audit(
        self,
        patient_state: Optional[Dict[str, Any]] = None,
        dialogue_output: Optional[Dict[str, Any]] = None,
        draft: Optional[Dict[str, Any]] = None,
        debug: bool = False,
        strict_mode: bool = True,
    ) -> AuditReport:
        """Run the safety audit.

        Parameters
        ----------
        patient_state : dict, optional
            The patient's medical record.
        dialogue_output : dict, optional
            The Dialogue Agent's structured output (and ``reply_text``).
        draft : dict, optional
            Backward-compatible alias for ``dialogue_output``.
        debug : bool, default False
            Populates ``retrieval_trace`` and ``evaluation_trace``.
        strict_mode : bool, default True
            If True, top-level inputs must conform to the v4.2.0 strict
            schema (``schema_version`` present, ``current_medications``
            items are objects, etc.). When False a legacy adapter is
            used and the result is annotated with a DEPRECATED_INPUT
            finding.

        Notes
        -----
        The engine NEVER accepts expected_decision or expected rule IDs
        as inputs. Test assertions are evaluated outside the engine.

        Fail-closed
        -----------
        Any exception raised during audit is caught and converted into a
        REVIEW report with ``decision_basis`` containing
        ``"SYSTEM_ERROR"``. ``original_llm_reply_was_sent`` is forced
        to ``False``. The exception text lands in
        ``developer_diagnostics.exception`` (and is logged), NOT in
        ``patient_visible_response``.
        """
        try:
            return self._audit_impl(
                patient_state=patient_state,
                dialogue_output=dialogue_output,
                draft=draft,
                debug=debug,
                strict_mode=strict_mode,
            )
        except Exception as exc:
            return self._build_system_error_report(exc)

    # ----------------------------------------------------------------------

    def _audit_impl(
        self,
        patient_state: Optional[Dict[str, Any]],
        dialogue_output: Optional[Dict[str, Any]],
        draft: Optional[Dict[str, Any]],
        debug: bool,
        strict_mode: bool,
    ) -> AuditReport:
        if dialogue_output is None and draft is not None:
            dialogue_output = draft
        if dialogue_output is None:
            dialogue_output = {}
        if patient_state is None:
            patient_state = {}

        # Wrap as a v4.2.0 envelope.
        envelope: Dict[str, Any] = {
            "schema_version": "1.0",
            "patient_state": patient_state,
            "dialogue_output": dialogue_output,
        }

        timing = TimingBreakdown()
        t_total = time.perf_counter()

        # 1. Strict input validation.
        t0 = time.perf_counter()
        validation_result = self.input_validator.validate(envelope)
        timing.input_validation_ms = (time.perf_counter() - t0) * 1000.0

        # Project to the legacy flat patient_state the existing rules
        # expect. We do this before normalize_draft because
        # normalize_draft is fed dialogue_output and works on the
        # existing v4.1 keys.
        projected_patient_state = _project_patient_state(patient_state)

        # 2. Normalize the Dialogue Agent output.
        t0 = time.perf_counter()
        norm_draft = normalize_draft(dialogue_output)
        timing.normalization_ms = (time.perf_counter() - t0) * 1000.0

        # 3. Drug context + matcher.
        t0 = time.perf_counter()
        drug_ctx = self._build_drug_context(projected_patient_state, norm_draft)
        matched = self._scan_matcher(norm_draft)
        drug_ctx.text_mentioned_drugs = sorted(
            set(drug_ctx.text_mentioned_drugs) | matched.mentioned_drugs
        )
        timing.matching_ms = (time.perf_counter() - t0) * 1000.0

        # 4. Free-text dose parsing.
        t0 = time.perf_counter()
        raw_extractions = self.text_parser.parse(norm_draft.reply_text)
        text_extractions = self.text_parser.dedup_overlaps(raw_extractions)
        text_dose_set: Set[str] = set()
        for ext in text_extractions:
            if not ext.drug or ext.confidence not in ("high", "medium"):
                continue
            canonical = self.repository.canonical_drug(ext.drug)
            if canonical:
                text_dose_set.add(canonical)
        drug_ctx.text_dose_drugs = sorted(text_dose_set)
        timing.text_parsing_ms = (time.perf_counter() - t0) * 1000.0

        # 5. Patient risk flags.
        t0 = time.perf_counter()
        risk_flags, evaluated_risk_ids = self._detect_risk_flags(projected_patient_state, drug_ctx)
        timing.risk_detection_ms = (time.perf_counter() - t0) * 1000.0

        # 6. Required-context check (NEW in v4.2.0).
        t0 = time.perf_counter()
        required_context = self.required_context_checker.check(
            patient_state=projected_patient_state,
            dialogue_output=dialogue_output,
        )
        timing.required_context_ms = (time.perf_counter() - t0) * 1000.0

        # 7. Consistency check.
        t0 = time.perf_counter()
        consistency = self.consistency_checker.check(
            draft=norm_draft,
            text_extractions=text_extractions,
            drug_aliases_in_text=drug_ctx.text_mentioned_drugs,
            known_canonical_drugs=self._known_drug_canonicals,
            drug_canonicalizer=self.repository.canonical_drug,
            patient_state=projected_patient_state,
        )
        timing.consistency_ms = (time.perf_counter() - t0) * 1000.0

        # 8. Candidate selection.
        t0 = time.perf_counter()
        disease_codes = {rf.code for rf in risk_flags}
        patient_fields = list((projected_patient_state or {}).keys())
        matched_keywords = list(matched.food_concepts) + list(matched.exercise_intensities)
        selection = select_candidate_rule_ids(
            self.repository,
            drug_ctx,
            risk_flags,
            matched_keywords,
            patient_fields,
            text_dose_drugs=drug_ctx.text_dose_drugs,
            debug=debug,
        )
        candidate_ids = set(selection.candidate_rule_ids)
        timing.candidate_selection_ms = (time.perf_counter() - t0) * 1000.0

        # 9. Evaluate candidates.
        t0 = time.perf_counter()
        violations: List[RuleViolation] = []
        evaluated_ids: List[str] = []
        evaluation_trace: List[RuleEvaluationTrace] = []
        for rule_id in candidate_ids:
            rule = self.repository.get(rule_id)
            if debug:
                v, conds = self.evaluator.evaluate_with_trace(
                    rule, projected_patient_state, norm_draft, drug_ctx, risk_flags,
                    text_extractions, canonical_drug=self.repository.canonical_drug,
                )
                evaluation_trace.append(RuleEvaluationTrace(
                    rule_id=rule_id, type=rule.type, conditions=conds,
                    matched=v is not None,
                    severity=v.severity if v else "",
                    rule_rejected=rule.status != "active",
                ))
            else:
                v = self.evaluator.evaluate(
                    rule, projected_patient_state, norm_draft, drug_ctx, risk_flags,
                    text_extractions, canonical_drug=self.repository.canonical_drug,
                )
            evaluated_ids.append(rule_id)
            if v is not None:
                violations.append(v)
        timing.evaluation_ms = (time.perf_counter() - t0) * 1000.0

        # 10. Aggregate the decision.
        decision, visible, reviewer, basis = self._aggregate(
            validation_result,
            required_context,
            consistency,
            violations,
            norm_draft,
        )

        timing.total_ms = (time.perf_counter() - t_total) * 1000.0

        matched_entities = MatchedEntities(
            drugs=sorted(set(drug_ctx.resulting_drugs) | set(drug_ctx.current_drugs)),
            keywords=matched_keywords,
            patient_fields=patient_fields,
            disease_codes=sorted(disease_codes),
        )

        if debug:
            retrieval_trace: List[RetrievalChannel] = [
                RetrievalChannel(channel=ct.channel, key=list(ct.key), rule_ids=list(ct.rule_ids))
                for ct in selection.channel_trace
            ]
        else:
            retrieval_trace = []

        # Build the developer diagnostics bag.
        developer_diagnostics = {
            "input_validation": validation_result.to_dict(),
            "required_context": required_context.to_dict(),
            "normalized_draft": norm_draft.to_dict(),
            "drug_context": drug_ctx.to_dict(),
            "strict_mode": strict_mode,
            "schema_version": envelope.get("schema_version", ""),
        }

        report = AuditReport(
            decision=decision,
            ruleset_version=self.repository.ruleset_version,
            decision_basis=basis,
            input_validation_errors=[
                SystemViolation(
                    code=issue.code,
                    severity=issue.severity,
                    message=issue.message,
                    details=issue.to_dict(),
                )
                for issue in validation_result.issues
            ],
            missing_context_fields=[
                mc.to_dict() for mc in required_context.missing_fields
            ],
            consistency_violations=consistency,
            medical_violations=violations,
            system_findings=[],
            all_findings=[],
            patient_visible_response=visible,
            reviewer_message=reviewer,
            developer_diagnostics=developer_diagnostics,
            original_llm_reply_was_sent=(decision == "PASS"),
            input_schema_version=envelope.get("schema_version", "1.0"),
            matched_entities=matched_entities,
            candidate_rule_ids=sorted(candidate_ids),
            evaluated_rule_ids=sorted(evaluated_ids),
            risk_flags=risk_flags,
            current_drugs=drug_ctx.current_drugs,
            recommended_drugs=drug_ctx.recommended_drugs,
            resulting_drugs=drug_ctx.resulting_drugs,
            text_extractions=text_extractions,
            retrieval_channels=list(selection.channels),
            evaluated_risk_rule_ids=sorted(evaluated_risk_ids),
            text_mentioned_drugs=drug_ctx.text_mentioned_drugs,
            text_dose_drugs=drug_ctx.text_dose_drugs,
            timing=timing,
            retrieval_trace=retrieval_trace,
            evaluation_trace=evaluation_trace if debug else [],
            normalized_text_drugs=drug_ctx.text_mentioned_drugs,
            violations=violations,
        )

        # Populate system_findings + all_findings after we have the
        # final report.
        report.system_findings = self._collect_system_findings(report)
        report.all_findings = self._collect_all_findings(report)

        t_log = time.perf_counter()
        self.audit_logger.write(report)
        timing.logging_ms = (time.perf_counter() - t_log) * 1000.0
        report.timing = timing
        timing.total_ms = (time.perf_counter() - t_total) * 1000.0
        report.timing = timing

        return report

    # --------------------------------------------------------- system-error

    def _build_system_error_report(self, exc: Exception) -> AuditReport:
        timing = TimingBreakdown()
        violation = SystemViolation(
            code="SYSTEM_ERROR",
            severity="REVIEW",
            message="Audit pipeline raised an unexpected exception.",
            details={"exception_type": exc.__class__.__name__},
        )
        report = AuditReport(
            decision="REVIEW",
            ruleset_version=self.repository.ruleset_version,
            decision_basis=["SYSTEM_ERROR"],
            input_validation_errors=[violation],
            consistency_violations=[],
            medical_violations=[],
            system_findings=[violation],
            all_findings=[violation.to_dict()],
            patient_visible_response=self._safe_visible_response_for_error(),
            reviewer_message="审计引擎发生异常，已阻止发送原始回复，需要由工程师排查。",
            developer_diagnostics={
                "exception_type": exc.__class__.__name__,
                "exception_message": str(exc),
                "stack_trace": traceback.format_exc(),
            },
            original_llm_reply_was_sent=False,
            input_schema_version="1.0",
            timing=timing,
            violations=[],
        )
        try:
            self.audit_logger.write(report, tag="system_error")
        except Exception:  # pragma: no cover
            pass
        return report

    @staticmethod
    def _safe_visible_response_for_error() -> str:
        return (
            "系统当前无法安全地核验该建议，已暂时阻止发送。"
            "请稍后再试或联系医生或药师复核。"
        )

    # -------------------------------------------------------------- helpers

    def _build_drug_context(
        self,
        patient_state: Dict[str, Any],
        norm_draft,
    ) -> DrugContext:
        ctx = DrugContext()

        for med in patient_state.get("current_medications", []) or []:
            if not isinstance(med, dict):
                continue
            raw_status = med.get("status", "active")
            canonical_status = normalize_medication_status(raw_status) or "unknown"
            drug_label = (
                med.get("drug_id")
                or med.get("name")
                or med.get("drug_name")
                or ""
            )
            canonical = self.repository.canonical_drug(drug_label)
            if not canonical:
                continue
            if canonical_status == "active":
                ctx.current_drugs.append(canonical)
                ctx.active_drug_status[canonical] = "active"
            elif canonical_status == "held":
                ctx.held_drugs.append(canonical)
                ctx.active_drug_status[canonical] = "held"
            elif canonical_status == "stopped":
                ctx.stopped_drugs.append(canonical)
                ctx.active_drug_status[canonical] = "stopped"
            elif canonical_status == "completed":
                ctx.completed_drugs.append(canonical)
                ctx.active_drug_status[canonical] = "completed"
            elif canonical_status == "unknown":
                ctx.unknown_status_drugs.append(canonical)
                ctx.active_drug_status[canonical] = "unknown"

        resulting: Set[str] = set(ctx.current_drugs)
        recommended: Set[str] = set()

        for action in norm_draft.medication_actions:
            # Prefer drug_id (v4.2.0 canonical); fall back to drug.
            drug_label = action.drug_id or action.drug
            canonical = self.repository.canonical_drug(drug_label) if drug_label else ""
            if not canonical:
                continue
            if action.action in _RESULTING_ACTIONS:
                resulting.add(canonical)
                recommended.add(canonical)
                if action.action == "replace":
                    if action.replace_drug or action.replace_drug_id:
                        old_label = action.replace_drug_id or action.replace_drug
                        old = self.repository.canonical_drug(old_label)
                        if old in resulting:
                            resulting.discard(old)
            elif action.action in _REMOVING_ACTIONS:
                resulting.discard(canonical)
            elif action.action in _NEUTRAL_ACTIONS:
                # avoid_start: do NOT add or remove.
                pass

        mentioned: Set[str] = set()
        if norm_draft.reply_text:
            mentioned |= self._matcher.scan_drugs(norm_draft.reply_text)

        ctx.recommended_drugs = sorted(recommended)
        ctx.resulting_drugs = sorted(resulting)
        ctx.mentioned_drugs = sorted(mentioned)
        ctx.text_mentioned_drugs = sorted(mentioned)
        ctx.text_dose_drugs = []
        return ctx

    def _scan_matcher(self, norm_draft) -> _MatcherOutcome:
        outcome = _MatcherOutcome()
        text = norm_draft.reply_text or ""
        if not text:
            return outcome
        outcome.mentioned_drugs |= self._matcher.scan_drugs(text)
        for kind, payload, _raw in self._matcher.scan_concepts(text):
            if kind == "food_concept":
                outcome.food_concepts.add(payload)
            elif kind == "exercise_intensity":
                outcome.exercise_intensities.add(payload)
            elif kind == "risk_code":
                outcome.risk_codes.add(payload)
            elif kind == "care_type":
                outcome.care_types.add(payload)
            elif kind == "action_direction":
                outcome.action_directions.add(payload)
            elif kind == "disease_alias":
                outcome.disease_codes.add(payload)
        for fa in norm_draft.food_advice:
            for kind, payload, _raw in self._matcher.scan_concepts(
                f"{fa.food} {fa.concept} {fa.instruction}"
            ):
                if kind == "food_concept":
                    outcome.food_concepts.add(payload)
            outcome.mentioned_drugs |= self._matcher.scan_drugs(
                f"{fa.food} {fa.concept} {fa.instruction}"
            )
        for ea in norm_draft.exercise_advice:
            if ea.intensity:
                outcome.exercise_intensities.add(normalize(ea.intensity))
        return outcome

    def _detect_risk_flags(
        self,
        patient_state: Dict[str, Any],
        drug_ctx: DrugContext,
    ) -> Tuple[List[RiskFlag], List[str]]:
        patient_fields = list((patient_state or {}).keys())
        candidate_ids = self.repository.patient_risk_rule_ids_for_fields(patient_fields)
        flags: List[RiskFlag] = []
        evaluated_ids: List[str] = []
        for rule_id in candidate_ids:
            rule = self.repository.get_active(rule_id)
            if rule is None:
                continue
            evaluated_ids.append(rule_id)
            rf = self.evaluator.detect_risk_flag(rule, patient_state, drug_ctx)
            if rf is not None:
                flags.append(rf)
        flags.sort(key=lambda r: -SEVERITY_RANK.get(r.severity, 0))
        return flags, evaluated_ids

    def _aggregate(
        self,
        validation_result,
        required_context,
        consistency: List[SystemViolation],
        violations: List[RuleViolation],
        norm_draft,
    ) -> Tuple[str, str, str, List[str]]:
        basis: List[str] = []
        highest = 0

        if validation_result.issues:
            basis.append("INPUT_VALIDATION")
            # Validation issues never raise the severity to BLOCK by
            # themselves — they always produce REVIEW at minimum.
            highest = max(highest, SEVERITY_RANK["REVIEW"])

        if required_context.missing_fields:
            basis.append("MISSING_CONTEXT")
            highest = max(highest, SEVERITY_RANK["REVIEW"])

        if consistency:
            basis.append("TEXT_STRUCTURE_CONSISTENCY")
            for sv in consistency:
                highest = max(highest, SEVERITY_RANK.get(sv.severity, 0))

        if violations:
            basis.append("MEDICAL_RULE")
            for v in violations:
                highest = max(highest, SEVERITY_RANK.get(v.severity, 0))

        if highest >= SEVERITY_RANK["BLOCK"]:
            decision = "BLOCK"
        elif highest >= SEVERITY_RANK["REVIEW"]:
            decision = "REVIEW"
        else:
            decision = "PASS"

        # Patient-visible text. Must NEVER leak internal rule IDs or
        # stack traces.
        if decision == "BLOCK":
            visible = (
                "系统检测到该回复可能包含不安全的药物、饮食或运动建议，"
                "已阻止发送，请由医生或药师复核。"
            )
        elif decision == "REVIEW":
            visible = self._reviewer_visible_text(basis)
        else:
            visible = norm_draft.reply_text or ""

        reviewer = self._reviewer_message(basis, decision)
        return decision, visible, reviewer, basis

    @staticmethod
    def _reviewer_visible_text(basis: List[str]) -> str:
        if "MISSING_CONTEXT" in basis:
            return (
                "当前缺少完成安全判断所需的患者信息，"
                "建议补充相关检查结果后再评估，暂不直接展示。"
            )
        if "INPUT_VALIDATION" in basis:
            return (
                "该建议中的用药或饮食信息不完整，"
                "暂不直接展示，需要医生或药师复核。"
            )
        if "TEXT_STRUCTURE_CONSISTENCY" in basis:
            return (
                "回复正文与结构化建议方向不一致，"
                "暂不直接展示，需要医生或药师复核。"
            )
        return (
            "该建议涉及可能的药物与饮食相互作用或风险，"
            "暂不直接展示，需由医生或药师复核。"
        )

    @staticmethod
    def _reviewer_message(basis: List[str], decision: str) -> str:
        parts = [f"decision={decision}", f"basis={','.join(basis) or 'PASS'}"]
        return "; ".join(parts)

    @staticmethod
    def _collect_system_findings(report: AuditReport) -> List[SystemViolation]:
        return list(report.input_validation_errors)

    @staticmethod
    def _collect_all_findings(report: AuditReport) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for v in report.input_validation_errors:
            d = v.to_dict()
            d["category"] = "input_validation"
            out.append(d)
        for c in report.consistency_violations:
            d = c.to_dict()
            d["category"] = "consistency"
            out.append(d)
        for v in report.medical_violations:
            d = v.to_dict()
            d["category"] = "medical_rule"
            out.append(d)
        for m in report.missing_context_fields:
            d = dict(m)
            d["category"] = "missing_context"
            out.append(d)
        return out


def _project_patient_state(raw: Any) -> Dict[str, Any]:
    """Build the flat patient_state dict the rule engine still consumes.

    The strict v4.2.0 ``measurements`` block is unwrapped into the
    legacy ``egfr`` / ``latest_systolic_bp_mmHg`` /
    ``latest_glucose_mmol_l`` / ``serum_potassium_mmol_l`` keys the
    existing rules already understand. ``clinical_flags`` are surfaced
    as top-level booleans, and ``disease_codes`` /
    ``current_medications`` are forwarded verbatim.
    """
    if not isinstance(raw, dict):
        return {}

    out: Dict[str, Any] = {}
    for k, v in raw.items():
        out[k] = v

    measurements = raw.get("measurements")
    if isinstance(measurements, dict):
        for name, entry in measurements.items():
            if not isinstance(entry, dict):
                continue
            v = entry.get("value")
            if name == "egfr":
                out.setdefault("egfr", v)
            elif name == "systolic_bp":
                out.setdefault("latest_systolic_bp_mmHg", v)
            elif name == "diastolic_bp":
                out.setdefault("latest_diastolic_bp_mmHg", v)
            elif name == "glucose":
                out.setdefault("latest_glucose_mmol_l", v)
            elif name == "serum_potassium":
                out.setdefault("serum_potassium_mmol_l", v)
            elif name == "uric_acid":
                out.setdefault("latest_uric_acid_umol_l", v)

    flags = raw.get("clinical_flags")
    if isinstance(flags, dict):
        for name, v in flags.items():
            out.setdefault(name, v)

    if "current_medications" not in out:
        out["current_medications"] = []
    if "disease_codes" not in out:
        out["disease_codes"] = []

    return out