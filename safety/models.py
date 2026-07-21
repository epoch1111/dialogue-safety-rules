"""v4.2.0 dataclasses for the safety engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


SEVERITY_RANK = {
    "INFO": 0,
    "WARN": 1,
    "REVIEW": 2,
    "BLOCK": 3,
    "EMERGENCY": 4,
}


# --------------------------------------------------------------------------
# Normalized structured input (drugs / food / exercise / care)
# --------------------------------------------------------------------------


@dataclass
class MedicationAction:
    """Normalized medication action.

    v4.2.1: ``route`` and ``dose_unit`` are now ``Optional[str]`` and
    default to ``None`` instead of being silently coerced to
    ``"oral"`` / ``"mg"``. The validator surfaces a missing route for
    ``start`` / ``increase`` / ``decrease`` / ``replace`` actions as
    ``INPUT_MEDICATION_ACTION_MISSING_FIELDS``.
    """

    drug: str
    action: str  # start|continue|increase|decrease|stop|hold|avoid_start|replace
    dose_value: Optional[float] = None
    dose_unit: Optional[str] = None
    frequency_per_day: Optional[float] = None
    route: Optional[str] = None
    duration_days: Optional[int] = None
    dose_mg: Optional[float] = None  # legacy
    raw: Dict[str, Any] = field(default_factory=dict)
    replace_drug: Optional[str] = None
    raw_status: Optional[str] = None  # for current_medications normalization
    # v4.2.0
    drug_id: str = ""
    use_current_regimen: bool = False
    replace_drug_id: str = ""

    @property
    def effective_dose_value(self) -> Optional[float]:
        if self.dose_value is not None:
            return float(self.dose_value)
        if self.dose_mg is not None:
            return float(self.dose_mg)
        return None


@dataclass
class FoodAdvice:
    food: str
    concept: str = ""
    action: str = ""  # recommend|allow|limit|avoid (no default)
    instruction: str = ""
    # v4.2.0
    food_concept_id: str = ""
    amount: Optional[float] = None
    frequency: Optional[str] = None


@dataclass
class ExerciseAdvice:
    activity: str = ""
    intensity: str = ""  # light|moderate|vigorous (no default)
    action: str = ""  # recommend|allow|limit|avoid|stop (no default)
    duration_min: Optional[int] = None
    instruction: str = ""
    # v4.2.0
    activity_concept_id: str = ""
    frequency_per_week: Optional[int] = None


@dataclass
class CareAction:
    type: str = ""  # repeat_measurement|urgent_medical_evaluation|emergency_symptom_screening|monitor|follow_up
    target: str = ""
    action: str = ""  # v4.2.1: not defaulted; validator decides.
    urgency: Optional[str] = None


@dataclass
class NormalizedDraft:
    """Engine-internal representation of the Dialogue Agent output."""

    reply_text: str = ""
    medication_actions: List[MedicationAction] = field(default_factory=list)
    food_advice: List[FoodAdvice] = field(default_factory=list)
    exercise_advice: List[ExerciseAdvice] = field(default_factory=list)
    care_actions: List[CareAction] = field(default_factory=list)
    invalid_enum_fields: List[Dict[str, Any]] = field(default_factory=list)
    # v4.2.0
    raw_dialogue_output: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reply_text": self.reply_text,
            "medication_actions": [_med_to_dict(m) for m in self.medication_actions],
            "food_advice": [vars(f) for f in self.food_advice],
            "exercise_advice": [vars(e) for e in self.exercise_advice],
            "care_actions": [vars(c) for c in self.care_actions],
        }


def _med_to_dict(m: MedicationAction) -> Dict[str, Any]:
    return {
        "drug": m.drug,
        "action": m.action,
        "dose_value": m.dose_value,
        "dose_unit": m.dose_unit,
        "frequency_per_day": m.frequency_per_day,
        "route": m.route,
        "duration_days": m.duration_days,
        "dose_mg": m.dose_mg,
        "replace_drug": m.replace_drug,
        "drug_id": m.drug_id,
        "use_current_regimen": m.use_current_regimen,
        "replace_drug_id": m.replace_drug_id,
    }


# --------------------------------------------------------------------------
# Drug context
# --------------------------------------------------------------------------


@dataclass
class DrugContext:
    current_drugs: List[str] = field(default_factory=list)
    mentioned_drugs: List[str] = field(default_factory=list)
    recommended_drugs: List[str] = field(default_factory=list)
    resulting_drugs: List[str] = field(default_factory=list)
    text_mentioned_drugs: List[str] = field(default_factory=list)
    text_dose_drugs: List[str] = field(default_factory=list)
    # v4.2.0
    active_drug_status: Dict[str, str] = field(default_factory=dict)
    held_drugs: List[str] = field(default_factory=list)
    stopped_drugs: List[str] = field(default_factory=list)
    completed_drugs: List[str] = field(default_factory=list)
    unknown_status_drugs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current_drugs": sorted(set(self.current_drugs)),
            "mentioned_drugs": sorted(set(self.mentioned_drugs)),
            "recommended_drugs": sorted(set(self.recommended_drugs)),
            "resulting_drugs": sorted(set(self.resulting_drugs)),
            "text_mentioned_drugs": sorted(set(self.text_mentioned_drugs)),
            "text_dose_drugs": sorted(set(self.text_dose_drugs)),
            "active_drug_status": dict(self.active_drug_status),
            "held_drugs": sorted(set(self.held_drugs)),
            "stopped_drugs": sorted(set(self.stopped_drugs)),
            "completed_drugs": sorted(set(self.completed_drugs)),
            "unknown_status_drugs": sorted(set(self.unknown_status_drugs)),
        }


# --------------------------------------------------------------------------
# Matched entities
# --------------------------------------------------------------------------


@dataclass
class MatchedEntities:
    drugs: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    patient_fields: List[str] = field(default_factory=list)
    disease_codes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "drugs": sorted(set(self.drugs)),
            "keywords": sorted(set(self.keywords)),
            "patient_fields": sorted(set(self.patient_fields)),
            "disease_codes": sorted(set(self.disease_codes)),
        }


# --------------------------------------------------------------------------
# Risk flags, system violations, text extractions
# --------------------------------------------------------------------------


@dataclass
class RiskFlag:
    code: str
    severity: str
    source_rule_id: str
    patient_value: Any = None
    threshold: Any = None
    operator: str = ""
    related_drugs: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "source_rule_id": self.source_rule_id,
            "patient_value": self.patient_value,
            "threshold": self.threshold,
            "operator": self.operator,
            "related_drugs": sorted(set(self.related_drugs)),
        }


@dataclass
class SystemViolation:
    code: str
    severity: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "message": self.message, "details": self.details}


@dataclass
class TextExtraction:
    drug: Optional[str] = None
    dose_value: Optional[float] = None
    dose_unit: Optional[str] = None
    frequency_per_day: Optional[float] = None
    confidence: str = "none"
    raw_match: str = ""

    def to_dict(self) -> dict:
        return {
            "drug": self.drug,
            "dose_value": self.dose_value,
            "dose_unit": self.dose_unit,
            "frequency_per_day": self.frequency_per_day,
            "confidence": self.confidence,
            "raw_match": self.raw_match,
        }


# --------------------------------------------------------------------------
# Rule violations
# --------------------------------------------------------------------------


@dataclass
class RuleViolation:
    rule_id: str
    severity: str
    category: str
    message: str
    details: Dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class RuleConditionTrace:
    description: str
    actual: Any = None
    operator: str = ""
    expected: Any = None
    passed: bool = False

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "actual": self.actual,
            "operator": self.operator,
            "expected": self.expected,
            "passed": self.passed,
        }


@dataclass
class RuleEvaluationTrace:
    rule_id: str
    type: str
    conditions: List[RuleConditionTrace]
    matched: bool
    severity: str = ""
    rule_rejected: bool = False

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "type": self.type,
            "conditions": [c.to_dict() for c in self.conditions],
            "matched": self.matched,
            "severity": self.severity,
            "rule_rejected": self.rule_rejected,
        }


@dataclass
class RetrievalChannel:
    channel: str
    key: List[str]
    rule_ids: List[str]

    def to_dict(self) -> dict:
        return {
            "channel": self.channel,
            "key": list(self.key),
            "rule_ids": sorted(set(self.rule_ids)),
        }


@dataclass
class TimingBreakdown:
    """v4.2.0: new phases added (input_validation, required_context)."""

    input_validation_ms: float = 0.0
    normalization_ms: float = 0.0
    matching_ms: float = 0.0
    text_parsing_ms: float = 0.0
    risk_detection_ms: float = 0.0
    required_context_ms: float = 0.0
    consistency_ms: float = 0.0
    candidate_selection_ms: float = 0.0
    evaluation_ms: float = 0.0
    logging_ms: float = 0.0
    total_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "input_validation": round(self.input_validation_ms, 3),
            "normalization": round(self.normalization_ms, 3),
            "matching": round(self.matching_ms, 3),
            "text_parsing": round(self.text_parsing_ms, 3),
            "risk_detection": round(self.risk_detection_ms, 3),
            "required_context": round(self.required_context_ms, 3),
            "consistency": round(self.consistency_ms, 3),
            "candidate_selection": round(self.candidate_selection_ms, 3),
            "evaluation": round(self.evaluation_ms, 3),
            "logging": round(self.logging_ms, 3),
            "total": round(self.total_ms, 3),
            # legacy aliases (kept for v4.1 callers)
            "matching_ms": round(self.matching_ms, 3),
            "candidate_selection_ms": round(self.candidate_selection_ms, 3),
            "evaluation_ms": round(self.evaluation_ms, 3),
            "total_ms": round(self.total_ms, 3),
        }


# --------------------------------------------------------------------------
# Final report
# --------------------------------------------------------------------------


@dataclass
class AuditReport:
    decision: str
    ruleset_version: str

    # v4.2.0 unified bucket.
    decision_basis: List[str] = field(default_factory=list)
    input_validation_errors: List[SystemViolation] = field(default_factory=list)
    missing_context_fields: List[Dict[str, Any]] = field(default_factory=list)
    consistency_violations: List[SystemViolation] = field(default_factory=list)
    medical_violations: List[RuleViolation] = field(default_factory=list)
    system_findings: List[SystemViolation] = field(default_factory=list)
    all_findings: List[Dict[str, Any]] = field(default_factory=list)

    patient_visible_response: str = ""
    reviewer_message: str = ""
    developer_diagnostics: Dict[str, Any] = field(default_factory=dict)

    original_llm_reply_was_sent: bool = False
    input_schema_version: str = "1.0"

    # v4.1 fields preserved
    matched_entities: MatchedEntities = field(default_factory=MatchedEntities)
    candidate_rule_ids: List[str] = field(default_factory=list)
    evaluated_rule_ids: List[str] = field(default_factory=list)
    regeneration_constraints: List[Dict[str, Any]] = field(default_factory=list)
    timing: TimingBreakdown = field(default_factory=TimingBreakdown)

    risk_flags: List[RiskFlag] = field(default_factory=list)
    current_drugs: List[str] = field(default_factory=list)
    recommended_drugs: List[str] = field(default_factory=list)
    resulting_drugs: List[str] = field(default_factory=list)
    text_extractions: List[TextExtraction] = field(default_factory=list)
    retrieval_channels: List[str] = field(default_factory=list)

    evaluated_risk_rule_ids: List[str] = field(default_factory=list)
    text_mentioned_drugs: List[str] = field(default_factory=list)
    text_dose_drugs: List[str] = field(default_factory=list)

    retrieval_trace: List[RetrievalChannel] = field(default_factory=list)
    evaluation_trace: List[RuleEvaluationTrace] = field(default_factory=list)
    normalized_text_drugs: List[str] = field(default_factory=list)

    # Backward-compatible alias.
    violations: List[RuleViolation] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        # Keep ``violations`` in sync with ``medical_violations`` so v4.1
        # callers do not break.
        if not self.violations and self.medical_violations:
            self.violations = list(self.medical_violations)
        return {
            # v4.2.0 unified report
            "decision": self.decision,
            "decision_basis": list(self.decision_basis),
            "ruleset_version": self.ruleset_version,
            "input_schema_version": self.input_schema_version,
            "medical_violations": [v.to_dict() for v in self.medical_violations],
            "input_validation_errors": [v.to_dict() for v in self.input_validation_errors],
            "consistency_violations": [v.to_dict() for v in self.consistency_violations],
            "missing_context_fields": [dict(x) for x in self.missing_context_fields],
            "system_findings": [v.to_dict() for v in self.system_findings],
            "all_findings": [dict(x) for x in self.all_findings],
            "patient_visible_response": self.patient_visible_response,
            "reviewer_message": self.reviewer_message,
            "developer_diagnostics": dict(self.developer_diagnostics),
            "original_llm_reply_was_sent": self.original_llm_reply_was_sent,
            "candidate_rule_ids": sorted(set(self.candidate_rule_ids)),
            "evaluated_rule_ids": sorted(set(self.evaluated_rule_ids)),
            "retrieval_channels": list(self.retrieval_channels),
            "retrieval_trace": [r.to_dict() for r in self.retrieval_trace],
            "evaluation_trace": [e.to_dict() for e in self.evaluation_trace],
            "timing_ms": self.timing.to_dict(),
            # legacy v4.1 keys
            "violations": [v.to_dict() for v in self.medical_violations],
            "matched_entities": self.matched_entities.to_dict(),
            "regeneration_constraints": list(self.regeneration_constraints),
            "risk_flags": [r.to_dict() for r in self.risk_flags],
            "current_drugs": sorted(set(self.current_drugs)),
            "recommended_drugs": sorted(set(self.recommended_drugs)),
            "resulting_drugs": sorted(set(self.resulting_drugs)),
            "text_extractions": [t.to_dict() for t in self.text_extractions],
            "evaluated_risk_rule_ids": sorted(set(self.evaluated_risk_rule_ids)),
            "text_mentioned_drugs": sorted(set(self.text_mentioned_drugs)),
            "text_dose_drugs": sorted(set(self.text_dose_drugs)),
            "normalized_text_drugs": sorted(set(self.normalized_text_drugs)),
        }