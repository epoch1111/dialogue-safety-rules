from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class DialogueDraft:
    reply_text: str
    medication_actions: List[Dict[str, Any]] = field(default_factory=list)
    food_advice: List[Dict[str, Any]] = field(default_factory=list)
    exercise_advice: List[Dict[str, Any]] = field(default_factory=list)
    # v4.2.0: care_actions must be present (even when empty) so the
    # strict dialogue_output schema validates correctly.
    care_actions: List[Dict[str, Any]] = field(default_factory=list)
    # v4.2.0: requires_review + uncertainty_reasons are optional but
    # always forwarded if present.
    requires_review: bool = False
    uncertainty_reasons: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DialogueDraft":
        return cls(
            reply_text=str(data.get("reply_text", "")),
            medication_actions=list(data.get("medication_actions", [])),
            food_advice=list(data.get("food_advice", [])),
            exercise_advice=list(data.get("exercise_advice", [])),
            care_actions=list(data.get("care_actions", []) or []),
            requires_review=bool(data.get("requires_review", False)),
            uncertainty_reasons=list(data.get("uncertainty_reasons", []) or []),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reply_text": self.reply_text,
            "medication_actions": self.medication_actions,
            "food_advice": self.food_advice,
            "exercise_advice": self.exercise_advice,
            "care_actions": self.care_actions,
            "requires_review": self.requires_review,
            "uncertainty_reasons": list(self.uncertainty_reasons),
        }


@dataclass
class RuleViolation:
    rule_id: str
    severity: str
    category: str
    message: str
    details: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class AuditResult:
    decision: str
    violations: List[RuleViolation]
    patient_visible_response: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "violations": [item.to_dict() for item in self.violations],
            "patient_visible_response": self.patient_visible_response,
        }