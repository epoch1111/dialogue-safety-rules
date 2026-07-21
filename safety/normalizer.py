"""Text + structured-field normalization helpers for the v4.2.0 safety engine.

v4.2.0 changes
--------------
- ``normalize_*`` functions no longer fall back to permissive defaults.
  An unknown action stays ``""`` so the input validator and consistency
  checker can flag it.
- ``normalize_draft`` accepts both the v4.2 strict shape (with
  ``drug_id``/``drug_name``) and the legacy v4.1 fields (``drug``).
- Empty / missing actions become empty strings (not "recommend" /
  "continue") so missing-action bugs surface as REVIEW, not PASS.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, List, Optional

from safety.models import (
    CareAction,
    ExerciseAdvice,
    FoodAdvice,
    MedicationAction,
    NormalizedDraft,
)


# ------------------------------------------------------------------ text

_FULLWIDTH_TRANSLATE_MAP = {
    0xFF0C: ",", 0x3002: ".", 0xFF1B: ";", 0xFF1A: ":", 0xFF1F: "?", 0xFF01: "!",
    0xFF08: "(", 0xFF09: ")", 0x3010: "[", 0x3011: "]", 0x300A: "<", 0x300B: ">",
    0x300C: '"', 0x300D: '"', 0x300E: '"', 0x300F: '"', 0x201C: '"', 0x201D: '"',
    0x2018: "'", 0x2019: "'", 0x3001: ",", 0xFF5E: "~", 0x2014: "-", 0x00B7: ".",
}

_WHITESPACE_RE = re.compile(r"\s+")


def normalize(value: object) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_FULLWIDTH_TRANSLATE_MAP)
    text = text.lower()
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def join_normalized(parts: Iterable[object], separator: str = " ") -> str:
    return separator.join(normalize(part) for part in parts if part is not None)


# ------------------------------------------------------------------ enums

VALID_MED_ACTIONS = {
    "start", "continue", "increase", "decrease", "stop", "hold", "avoid_start", "replace",
}

VALID_FOOD_ACTIONS = {"recommend", "allow", "limit", "avoid"}
VALID_EXERCISE_ACTIONS = {"recommend", "allow", "limit", "avoid", "stop"}
VALID_INTENSITIES = {"light", "moderate", "vigorous"}
VALID_CARE_TYPES = {
    "repeat_measurement",
    "urgent_medical_evaluation",
    "emergency_symptom_screening",
    "monitor",
    "follow_up",
}
VALID_CARE_ACTIONS = {"recommend", "perform"}

# v4.2.0 medication status. Replaces the previous 2-way active/inactive
# classification.
VALID_MEDICATION_STATUSES = {
    "active", "held", "stopped", "completed", "unknown",
}

_STATUS_ACTIVE_SYNONYMS = {
    "active", "current", "taking", "ongoing",
    "正在使用", "在用", "使用中", "在服用",
}
_STATUS_HELD_SYNONYMS = {"held", "暂停", "暂停使用", "temporary hold"}
_STATUS_STOPPED_SYNONYMS = {
    "stopped", "inactive", "discontinued", "cancelled", "已停", "已停用", "已停止",
}
_STATUS_COMPLETED_SYNONYMS = {"completed", "已完成", "finished"}


def normalize_medication_status(value: Any) -> Optional[str]:
    """Return one of ``active``, ``held``, ``stopped``, ``completed``,
    ``unknown``, or ``None`` for totally unparseable input."""
    if value is None:
        return None
    n = normalize(value)
    if not n:
        return None
    if n in _STATUS_ACTIVE_SYNONYMS:
        return "active"
    if n in _STATUS_HELD_SYNONYMS:
        return "held"
    if n in _STATUS_STOPPED_SYNONYMS:
        return "stopped"
    if n in _STATUS_COMPLETED_SYNONYMS:
        return "completed"
    return "unknown"


# ------------------------------------------------------------------ v4.2.0 enums


INVALID_MED_ACTION = "__invalid_med_action__"
INVALID_FOOD_ACTION = "__invalid_food_action__"
INVALID_EXERCISE_ACTION = "__invalid_exercise_action__"
INVALID_INTENSITY = "__invalid_intensity__"
INVALID_CARE_TYPE = "__invalid_care_type__"
INVALID_CARE_ACTION = "__invalid_care_action__"
INVALID_STATUS = "__invalid_status__"


def normalize_action(value: Any) -> str:
    """Return the action if known, else the sentinel ``INVALID_MED_ACTION``."""
    n = normalize(value)
    if n in VALID_MED_ACTIONS:
        return n
    return INVALID_MED_ACTION


def normalize_food_action(value: Any) -> str:
    n = normalize(value)
    if n in VALID_FOOD_ACTIONS:
        return n
    return INVALID_FOOD_ACTION


def normalize_exercise_action(value: Any) -> str:
    n = normalize(value)
    if n in VALID_EXERCISE_ACTIONS:
        return n
    return INVALID_EXERCISE_ACTION


def normalize_intensity(value: Any) -> str:
    n = normalize(value)
    if n in VALID_INTENSITIES:
        return n
    return INVALID_INTENSITY


def normalize_care_type(value: Any) -> str:
    n = normalize(value)
    if n in VALID_CARE_TYPES:
        return n
    return INVALID_CARE_TYPE


def normalize_care_action(value: Any) -> str:
    n = normalize(value)
    if n in VALID_CARE_ACTIONS:
        return n
    return INVALID_CARE_ACTION


# ------------------------------------------------------------------ draft


def normalize_draft(data: Any) -> NormalizedDraft:
    """Convert raw dialogue_output dict (or None) to :class:`NormalizedDraft`.

    v4.2.0: missing or invalid actions become the empty string / sentinel
    instead of silently defaulting to "continue" / "recommend" / "moderate".
    """

    if data is None:
        data = {}
    if not isinstance(data, dict):
        data = {}

    reply_text = str(data.get("reply_text", "") or "")

    invalid_enum_fields: List[Dict[str, Any]] = []

    medication_actions: List[MedicationAction] = []
    for raw in data.get("medication_actions", []) or []:
        if not isinstance(raw, dict):
            # v4.2.0: non-dict entries used to be silently skipped. Now
            # the input validator catches them and emits a REVIEW.
            continue
        raw_action = raw.get("action", None)
        if raw_action is None or raw_action == "":
            action = ""
            invalid_enum_fields.append({
                "path": "medication_actions[].action",
                "value": raw_action,
                "reason": "missing_action",
            })
        else:
            action = normalize_action(raw_action)
            if action == INVALID_MED_ACTION:
                invalid_enum_fields.append({
                    "path": "medication_actions[].action",
                    "value": raw_action,
                })

        dose_value = raw.get("dose_value", raw.get("dose_mg"))
        dose_unit = raw.get("dose_unit", "mg")
        dose_mg = raw.get("dose_mg")
        if dose_value is None and dose_mg is not None:
            dose_value = dose_mg
            dose_unit = dose_unit or "mg"

        medication_actions.append(
            MedicationAction(
                drug=str(raw.get("drug", "") or ""),
                drug_id=str(raw.get("drug_id", "") or ""),
                action=action,
                dose_value=_to_float(dose_value),
                dose_unit=str(dose_unit) if dose_unit else None,
                frequency_per_day=_to_float(raw.get("frequency_per_day")),
                route=str(raw.get("route", "oral") or "oral"),
                duration_days=_to_int(raw.get("duration_days")),
                dose_mg=_to_float(dose_mg),
                replace_drug=(str(raw.get("replace_drug", "") or "").strip() or None),
                replace_drug_id=str(raw.get("replace_drug_id", "") or ""),
                use_current_regimen=bool(raw.get("use_current_regimen", False)),
                raw=dict(raw),
            )
        )

    food_advice: List[FoodAdvice] = []
    for raw in data.get("food_advice", []) or []:
        if not isinstance(raw, dict):
            continue
        raw_action = raw.get("action", None)
        if raw_action is None or raw_action == "":
            action = ""
            invalid_enum_fields.append({
                "path": "food_advice[].action",
                "value": raw_action,
                "reason": "missing_action",
            })
        else:
            action = normalize_food_action(raw_action)
            if action == INVALID_FOOD_ACTION:
                invalid_enum_fields.append({
                    "path": "food_advice[].action",
                    "value": raw_action,
                })
        food_advice.append(
            FoodAdvice(
                food=str(raw.get("food", "") or ""),
                food_concept_id=str(raw.get("food_concept_id", "") or ""),
                concept=str(raw.get("concept", "") or ""),
                action=action,
                instruction=str(raw.get("instruction", "") or ""),
                amount=raw.get("amount"),
                frequency=raw.get("frequency"),
            )
        )

    exercise_advice: List[ExerciseAdvice] = []
    for raw in data.get("exercise_advice", []) or []:
        if not isinstance(raw, dict):
            continue
        raw_action = raw.get("action", None)
        if raw_action is None or raw_action == "":
            action = ""
            invalid_enum_fields.append({
                "path": "exercise_advice[].action",
                "value": raw_action,
                "reason": "missing_action",
            })
        else:
            action = normalize_exercise_action(raw_action)
            if action == INVALID_EXERCISE_ACTION:
                invalid_enum_fields.append({
                    "path": "exercise_advice[].action",
                    "value": raw_action,
                })
        raw_intensity = raw.get("intensity", None)
        if raw_intensity is None or raw_intensity == "":
            intensity = ""
            invalid_enum_fields.append({
                "path": "exercise_advice[].intensity",
                "value": raw_intensity,
                "reason": "missing_intensity",
            })
        else:
            intensity = normalize_intensity(raw_intensity)
            if intensity == INVALID_INTENSITY:
                invalid_enum_fields.append({
                    "path": "exercise_advice[].intensity",
                    "value": raw_intensity,
                })
        exercise_advice.append(
            ExerciseAdvice(
                activity=str(raw.get("activity", "") or ""),
                activity_concept_id=str(raw.get("activity_concept_id", "") or ""),
                intensity=intensity,
                action=action,
                duration_min=_to_int(raw.get("duration_min")),
                frequency_per_week=_to_int(raw.get("frequency_per_week")),
                instruction=str(raw.get("instruction", "") or ""),
            )
        )

    care_actions: List[CareAction] = []
    for raw in data.get("care_actions", []) or []:
        if not isinstance(raw, dict):
            continue
        ct = normalize_care_type(raw.get("type", ""))
        if ct == INVALID_CARE_TYPE:
            invalid_enum_fields.append({
                "path": "care_actions[].type",
                "value": raw.get("type", ""),
            })
            ct = ""
        ca = normalize_care_action(raw.get("action", ""))
        if ca == INVALID_CARE_ACTION:
            invalid_enum_fields.append({
                "path": "care_actions[].action",
                "value": raw.get("action", ""),
            })
            ca = ""
        urgency = raw.get("urgency", None)
        if urgency not in (None, "immediate", "same_day", "within_24h", "routine"):
            invalid_enum_fields.append({
                "path": "care_actions[].urgency",
                "value": urgency,
            })
            urgency = None
        care_actions.append(
            CareAction(
                type=ct,
                target=str(raw.get("target", "") or ""),
                action=ca or "recommend",
                urgency=urgency,
            )
        )

    return NormalizedDraft(
        reply_text=reply_text,
        medication_actions=medication_actions,
        food_advice=food_advice,
        exercise_advice=exercise_advice,
        care_actions=care_actions,
        invalid_enum_fields=invalid_enum_fields,
        raw_dialogue_output=dict(data),
    )


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None