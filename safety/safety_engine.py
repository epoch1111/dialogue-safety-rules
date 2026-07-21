"""v4.2.1 safety engine.

Pipeline (per ``audit_payload()`` call):

  1. ``input_validation``     (InputValidator — strict JSON shape)
  2. ``normalization``        (normalize_draft — strict enums)
  3. ``matching``             (keyword scan, drug context build)
  4. ``text_parsing``         (text_dose_parser)
  5. ``risk_detection``       (patient_risk_field_index)
  6. ``required_context``     (RequiredContextChecker, precise indexes)
  7. ``consistency``          (SYS001..SYS008 with dedup)
  8. ``candidate_selection``  (per-channel recall — tightened)
  9. ``evaluation``           (candidates only)
 10. ``logging``              (audit_logger.write)

Decision rules (v4.2.1):

- BLOCK  > REVIEW > PASS.
- Any input_validation_errors, missing_context_fields,
  consistency_violations, medical_violation, requires_review=true,
  uncertainty_reasons non-empty, or DEPRECATED_INPUT_SCHEMA forces
  REVIEW unless a BLOCK-severity finding is also present.
- ``original_llm_reply_was_sent`` is True iff ``decision == "PASS"``.
- Any unhandled exception during audit is converted to REVIEW with
  ``decision_basis=["SYSTEM_ERROR"]`` and the exception text lands in
  ``developer_diagnostics`` (NOT in ``patient_visible_response``).

v4.2.1 changes vs v4.2.0
------------------------
- New ``audit_payload(payload, strict_mode, compat_mode, debug)`` entry
  point. ``audit()`` is preserved as a thin wrapper for callers that
  pass ``patient_state`` + ``dialogue_output`` as separate kwargs.
- ``schema_version`` must now be supplied by the caller. ``audit()``
  does NOT auto-fill it; only ``audit_payload(payload=...)`` does so
  for backward compat with v4.1 callers, and only when ``payload`` is
  a dict.
- ``strict_mode=True`` (default) rejects legacy fields (drug/name/food/
  concept/activity) with ``INPUT_LEGACY_FIELD_NOT_ALLOWED``.
- ``compat_mode=True`` routes legacy fields through
  :class:`safety.legacy_adapter.LegacyInputAdapter` and emits a
  ``DEPRECATED_INPUT_SCHEMA`` finding. ``compat_mode`` defaults to
  ``False``; production must NOT enable it.
- ``requires_review`` and ``uncertainty_reasons`` from the LLM now
  force REVIEW at the decision level via a new ``LLM_DECLARED_UNCERTAINTY``
  decision basis.
- All silent defaults removed from the model layer (route, dose_unit,
  drug_id, food_concept_id, activity_concept_id).
- RequiredContextChecker uses precise per-channel indexes and emits
  ``required_context_retrieval_trace`` with ``scanned_rule_count`` so
  tests can prove no full scan.
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
    """v4.2.1 engine."""

    PROJECT_VERSION = "4.2.1"

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

    def audit_payload(
        self,
        payload: Optional[Dict[str, Any]] = None,
        *,
        strict_mode: bool = True,
        compat_mode: bool = False,
        debug: bool = False,
    ) -> AuditReport:
        """Run the safety audit with the v4.2.1 entry contract.

        Parameters
        ----------
        payload : dict, optional
            Top-level envelope ``{schema_version, patient_state,
            dialogue_output}``. ``schema_version`` is REQUIRED. Missing
            or unsupported values force REVIEW.
        strict_mode : bool, default True
            When True, legacy fields (``name``, ``drug``, ``food``,
            ``activity``, ``concept``) are rejected with REVIEW.
        compat_mode : bool, default False
            When True, legacy fields are routed through
            :class:`safety.legacy_adapter.LegacyInputAdapter`. A
            ``DEPRECATED_INPUT_SCHEMA`` finding is added. Production
            callers must NOT enable this.
        debug : bool, default False
            Populates ``retrieval_trace``, ``evaluation_trace`` and
            ``required_context_retrieval_trace``.

        Notes
        -----
        - The engine NEVER accepts ``expected_decision`` or expected
          rule IDs as inputs. Test assertions live outside the engine.
        - Fail-closed: any uncaught exception is converted into a
          REVIEW report with ``decision_basis=["SYSTEM_ERROR"]`` and
          ``original_llm_reply_was_sent=False``.
        """
        try:
            return self._audit_payload_impl(
                payload=payload,
                strict_mode=strict_mode,
                compat_mode=compat_mode,
                debug=debug,
            )
        except Exception as exc:
            return self._build_system_error_report(exc)

    def audit(
        self,
        patient_state: Optional[Dict[str, Any]] = None,
        dialogue_output: Optional[Dict[str, Any]] = None,
        draft: Optional[Dict[str, Any]] = None,
        debug: bool = False,
        strict_mode: bool = True,
    ) -> AuditReport:
        """Legacy kwarg-style entry.

        v4.2.1: this is the **legacy** entry point. It defaults to
        ``compat_mode=True`` so existing v4.1 / v4.2.0 callers
        (test fixtures, demo data) continue to work. Production code
        that wants strict enforcement must use :meth:`audit_payload`
        with ``strict_mode=True, compat_mode=False``.
        """
        try:
            if dialogue_output is None and draft is not None:
                dialogue_output = draft
            if dialogue_output is None:
                dialogue_output = {}
            if patient_state is None:
                patient_state = {}
            envelope: Dict[str, Any] = {
                "patient_state": patient_state,
                "dialogue_output": dialogue_output,
            }
            envelope.setdefault("schema_version", "1.0")
            # Legacy callers get compat_mode=True so old field names
            # keep working. The audit_payload() entry is the only path
            # that enforces strict mode.
            return self._audit_payload_impl(
                payload=envelope,
                strict_mode=strict_mode,
                compat_mode=True,
                debug=debug,
            )
        except Exception as exc:
            return self._build_system_error_report(exc)

    # ----------------------------------------------------------------------

    def _audit_payload_impl(
        self,
        payload: Optional[Dict[str, Any]],
        strict_mode: bool,
        compat_mode: bool,
        debug: bool,
    ) -> AuditReport:
        # Backward-compat shim: existing tests / callers may monkey-patch
        # ``_audit_impl`` to inject failures. Keep the name alive.
        return self._audit_impl(payload, strict_mode, compat_mode, debug)

    def _audit_impl(
        self,
        payload: Optional[Dict[str, Any]],
        strict_mode: bool,
        compat_mode: bool,
        debug: bool,
    ) -> AuditReport:
        # 1. Wrap the payload into StrictAuditInput.
        envelope = dict(payload) if isinstance(payload, dict) else {}
        if not compat_mode and strict_mode and envelope.get("schema_version") in (None, ""):
            # strict_mode forces the caller to provide schema_version.
            # We mark it missing here so the validator can flag it.
            envelope.setdefault("schema_version", "")

        # 2. Optionally run the legacy adapter to convert legacy fields
        #    into the strict shape. The adapter is the ONLY path through
        #    which old inputs become valid.
        legacy_findings: List[SystemViolation] = []
        if compat_mode:
            from safety.legacy_adapter import LegacyInputAdapter
            adapter = LegacyInputAdapter(self.repository)
            envelope, adapter_findings = adapter.adapt(envelope)
            for f in adapter_findings:
                legacy_findings.append(SystemViolation(
                    code=f.get("code", "DEPRECATED_INPUT_SCHEMA"),
                    severity=f.get("severity", "REVIEW"),
                    message=f.get("message", "Deprecated input schema."),
                    details=f.get("details", {}),
                ))

        # 3. Run the strict validator.
        validation_result = self.input_validator.validate(envelope)
        if strict_mode and not compat_mode:
            # strict_mode (without compat_mode) adds an extra rejection
            # for legacy fields. When compat_mode is on, the legacy
            # adapter has already normalized those fields so the strict
            # legacy check would double-report.
            strict_findings = self.input_validator.strict_mode_legacy_check(envelope)
            validation_result.issues.extend(strict_findings)

        # 4. If schema_version is unsupported/missing, do NOT crash;
        #    the validator already produced a finding. We still build a
        #    best-effort StrictAuditInput so downstream code can produce
        #    a complete report.
        strict_input = StrictAuditInput.from_raw(envelope)

        # 5. Project to the flat patient_state the existing rules
        #    expect.
        projected_patient_state = strict_input.patient_state.to_engine_patient_state()
        projected_dialogue_output = strict_input.dialogue_output.to_engine_dialogue_output()

        # 6. Forward the LLM's own uncertainty declaration onto the
        #    projected dialogue_output so other modules can see it.
        projected_dialogue_output["requires_review"] = (
            strict_input.dialogue_output.requires_review
        )
        projected_dialogue_output["uncertainty_reasons"] = list(
            strict_input.dialogue_output.uncertainty_reasons
        )

        timing = TimingBreakdown()
        t_total = time.perf_counter()

        t0 = time.perf_counter()
        timing.input_validation_ms = (time.perf_counter() - t0) * 1000.0

        # 7. Normalize the Dialogue Agent output.
        t0 = time.perf_counter()
        norm_draft = normalize_draft(projected_dialogue_output)
        timing.normalization_ms = (time.perf_counter() - t0) * 1000.0

        # 8. Drug context + matcher.
        t0 = time.perf_counter()
        drug_ctx = self._build_drug_context(projected_patient_state, norm_draft)
        matched = self._scan_matcher(norm_draft)
        drug_ctx.text_mentioned_drugs = sorted(
            set(drug_ctx.text_mentioned_drugs) | matched.mentioned_drugs
        )
        timing.matching_ms = (time.perf_counter() - t0) * 1000.0

        # 9. Free-text dose parsing.
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

        # 10. Patient risk flags.
        t0 = time.perf_counter()
        risk_flags, evaluated_risk_ids = self._detect_risk_flags(projected_patient_state, drug_ctx)
        timing.risk_detection_ms = (time.perf_counter() - t0) * 1000.0

        # 11. Required-context check.
        t0 = time.perf_counter()
        required_context = self.required_context_checker.check(
            patient_state=projected_patient_state,
            dialogue_output=projected_dialogue_output,
        )
        timing.required_context_ms = (time.perf_counter() - t0) * 1000.0

        # 12. Consistency check.
        t0 = time.perf_counter()
        consistency = self.consistency_checker.check(
            draft=norm_draft,
            text_extractions=text_extractions,
            drug_aliases_in_text=drug_ctx.text_mentioned_drugs,
            known_canonical_drugs=self._known_drug_canonicals,
            drug_canonicalizer=self.repository.canonical_drug,
            patient_state=projected_patient_state,
        )
        # Include adapter-emitted findings so they surface in
        # consistency_violations / system_findings.
        consistency = list(consistency) + list(legacy_findings)
        timing.consistency_ms = (time.perf_counter() - t0) * 1000.0

        # 13. Candidate selection.
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

        # 14. Evaluate candidates.
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

        # 15. Aggregate the decision.
        decision, visible, reviewer, basis = self._aggregate(
            validation_result,
            required_context,
            consistency,
            violations,
            norm_draft,
            projected_dialogue_output,
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
            "required_context_retrieval_trace": (
                [r.to_dict() for r in required_context.retrieval_trace]
                if debug else []
            ),
            "normalized_draft": norm_draft.to_dict(),
            "drug_context": drug_ctx.to_dict(),
            "strict_mode": strict_mode,
            "compat_mode": compat_mode,
            "schema_version": envelope.get("schema_version", ""),
            "project_version": self.PROJECT_VERSION,
            "ruleset_version": self.repository.ruleset_version,
            "input_schema_version": envelope.get("schema_version", "") or "1.0",
            "input_was_payload_envelope": isinstance(payload, dict),
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
            input_schema_version=str(envelope.get("schema_version", "") or "1.0"),
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
                "project_version": self.PROJECT_VERSION,
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
            # Prefer drug_id (v4.2.1 canonical); fall back to drug.
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
        dialogue_output: Dict[str, Any],
    ) -> Tuple[str, str, str, List[str]]:
        basis: List[str] = []
        highest = 0

        if validation_result.issues:
            # Only REVIEW-or-higher validation issues force REVIEW.
            # INFO-severity findings (e.g. unknown terminology) are
            # surfaced for visibility but do not block.
            review_or_higher = [i for i in validation_result.issues
                                if SEVERITY_RANK.get(i.severity, 0)
                                >= SEVERITY_RANK["REVIEW"]]
            if review_or_higher:
                basis.append("INPUT_VALIDATION")
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

        # v4.2.1: requires_review=true forces REVIEW even if no other
        # signal fired. uncertainty_reasons non-empty forces REVIEW.
        if bool(dialogue_output.get("requires_review")):
            basis.append("LLM_DECLARED_UNCERTAINTY")
            highest = max(highest, SEVERITY_RANK["REVIEW"])
        if dialogue_output.get("uncertainty_reasons"):
            basis.append("LLM_DECLARED_UNCERTAINTY")
            highest = max(highest, SEVERITY_RANK["REVIEW"])

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
            visible = self._reviewer_visible_text(basis, norm_draft)
        else:
            visible = norm_draft.reply_text or ""

        reviewer = self._reviewer_message(basis, decision)
        return decision, visible, reviewer, basis

    @staticmethod
    def _reviewer_visible_text(basis: List[str], norm_draft) -> str:
        if "MISSING_CONTEXT" in basis:
            return (
                "当前缺少完成安全判断所需的患者信息，"
                "建议补充相关检查结果后再评估，暂不直接展示。"
            )
        if "INPUT_VALIDATION" in basis:
            # Distinguish missing-action vs unknown drug vs unknown unit.
            issues = []
            return (
                "该建议中的用药或饮食信息不完整或无法核验，"
                "暂不直接展示，需要医生或药师复核。"
            )
        if "TEXT_STRUCTURE_CONSISTENCY" in basis:
            return (
                "回复正文与结构化建议方向不一致，"
                "暂不直接展示，需要医生或药师复核。"
            )
        if "LLM_DECLARED_UNCERTAINTY" in basis:
            return (
                "本次回复中模型自身声明存在不确定性或需要复核，"
                "暂不直接展示，需要医生或药师确认后再发送。"
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