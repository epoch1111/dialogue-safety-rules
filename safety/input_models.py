"""v4.2.0 strict input dataclasses.

These mirror the JSON Schema files in ``schemas/`` and are used by
:class:`safety.input_validator.InputValidator`. The dataclasses are
intentionally permissive in their constructors — they accept the raw
external dict and remember the validation problems. Downstream code must
check ``InputValidationResult.issues`` before consuming the wrapped
payloads.

Design notes
------------
- The model classes do NOT raise on invalid input. They collect issues
  so the audit pipeline can decide whether the finding is REVIEW or
  BLOCK.
- :class:`StrictAuditInput` is the top-level wrapper. ``schema_version``
  is mandatory and must match one of the values in ``SUPPORTED_SCHEMA_VERSIONS``.
- :class:`PatientStateInput` accepts both the v4.2 nested shape
  (``measurements.egfr.value``) and the legacy flat fields the engine
  still indexes for backward compat. ``to_legacy_patient_state()`` emits
  the normalized flat dict the existing rules expect.
- :class:`DialogueOutputInput` mirrors the JSON Schema exactly. All
  ``*_actions`` lists MUST be present (even when empty).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


SUPPORTED_SCHEMA_VERSIONS = {"1.0"}


# ----------------------------------------------------------------- enums

VALID_MEDICATION_ACTIONS = {
    "start", "continue", "increase", "decrease", "stop", "hold", "avoid_start", "replace",
}
VALID_FOOD_ACTIONS = {"recommend", "allow", "limit", "avoid"}
VALID_EXERCISE_ACTIONS = {"recommend", "allow", "limit", "avoid", "stop"}
VALID_INTENSITIES = {"light", "moderate", "vigorous"}
VALID_MED_STATUSES = {"active", "held", "stopped", "completed", "unknown"}
VALID_CARE_TYPES = {
    "repeat_measurement",
    "urgent_medical_evaluation",
    "emergency_symptom_screening",
    "monitor",
    "follow_up",
}
VALID_CARE_ACTIONS = {"recommend", "perform"}
VALID_CARE_URGENCIES = {"immediate", "same_day", "within_24h", "routine", None}
VALID_MASS_UNITS = {"mcg", "mg", "g", "IU", "μg", "ug", "毫克", "克"}
VALID_ROUTES = {"oral", "iv", "im", "sc", "inhale", "topical", "other"}


# ----------------------------------------------------------------- issues


@dataclass
class InputValidationIssue:
    """Single finding produced by InputValidator.

    ``field_path`` is a dotted path, e.g. ``patient_state.measurements.egfr``.
    ``code`` is a stable token so callers can branch on it.
    ``severity`` is either ``"REVIEW"`` or ``"BLOCK"``.
    """

    code: str
    field_path: str
    message: str
    severity: str = "REVIEW"
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "field_path": self.field_path,
            "message": self.message,
            "severity": self.severity,
            "details": dict(self.details),
        }


@dataclass
class InputValidationResult:
    issues: List[InputValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.issues

    @property
    def has_block_issues(self) -> bool:
        return any(i.severity == "BLOCK" for i in self.issues)

    @property
    def has_review_issues(self) -> bool:
        return any(i.severity == "REVIEW" for i in self.issues)

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "issues": [i.to_dict() for i in self.issues],
        }


# ----------------------------------------------------------------- helpers


def _as_dict(value: Any) -> Optional[Dict[str, Any]]:
    return value if isinstance(value, dict) else None


def _as_list(value: Any) -> Optional[List[Any]]:
    return value if isinstance(value, list) else None


# ----------------------------------------------------------------- patient


@dataclass
class CurrentMedicationInput:
    drug_id: str = ""
    drug_name: str = ""
    status: str = ""
    dose_value: Optional[float] = None
    dose_unit: Optional[str] = None
    frequency_per_day: Optional[float] = None
    route: Optional[str] = None
    raw_was_object: bool = True  # v4.2.0: tracks if the source entry was an object

    @classmethod
    def from_raw(cls, raw: Any) -> "CurrentMedicationInput":
        d = _as_dict(raw)
        if d is None:
            # Non-object entries (string, number, null, ...) become a
            # sentinel CurrentMedicationInput so the validator can flag
            # them with INPUT_INVALID_MEDICATION_ITEM.
            return cls(drug_id="", drug_name="", status="",
                       raw_was_object=False)
        # v4.2.0 canonical fields. v4.1 used ``name`` instead of
        # ``drug_name``; we accept both for backward compatibility.
        drug_name = (
            d.get("drug_name")
            or d.get("name")
            or ""
        )
        return cls(
            drug_id=str(d.get("drug_id", "") or ""),
            drug_name=str(drug_name),
            status=str(d.get("status", "") or ""),
            dose_value=d.get("dose_value"),
            dose_unit=d.get("dose_unit"),
            frequency_per_day=d.get("frequency_per_day"),
            route=d.get("route"),
            raw_was_object=True,
        )

    def is_structurally_valid(self) -> bool:
        return bool(self.drug_id) and bool(self.drug_name) and bool(self.status)


@dataclass
class PatientStateInput:
    patient_id: str = ""
    current_medications: List[CurrentMedicationInput] = field(default_factory=list)
    disease_codes: List[str] = field(default_factory=list)
    measurements: Dict[str, Any] = field(default_factory=dict)
    clinical_flags: Dict[str, Any] = field(default_factory=dict)
    allergies: List[str] = field(default_factory=list)
    legacy_fields: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: Any) -> "PatientStateInput":
        d = _as_dict(raw) or {}
        meds: List[CurrentMedicationInput] = []
        for item in d.get("current_medications", []) or []:
            meds.append(CurrentMedicationInput.from_raw(item))
        measurements = _as_dict(d.get("measurements")) or {}
        clinical_flags = _as_dict(d.get("clinical_flags")) or {}
        legacy_fields: Dict[str, Any] = {}
        for k, v in d.items():
            if k in {
                "patient_id", "current_medications", "disease_codes",
                "measurements", "clinical_flags", "allergies",
            }:
                continue
            legacy_fields[k] = v
        return cls(
            patient_id=str(d.get("patient_id", "") or ""),
            current_medications=meds,
            disease_codes=[str(x) for x in (d.get("disease_codes") or []) if x],
            measurements=measurements,
            clinical_flags=clinical_flags,
            allergies=[str(x) for x in (d.get("allergies") or []) if x],
            legacy_fields=legacy_fields,
        )

    def to_legacy_patient_state(self) -> Dict[str, Any]:
        """Project into the flat shape the existing rule engine expects.

        The legacy fields are also forwarded verbatim so v4.1-style
        test data keeps working.
        """
        out: Dict[str, Any] = {
            "patient_id": self.patient_id,
            "current_medications": [
                {
                    "drug_id": m.drug_id,
                    "name": m.drug_name or m.drug_id,
                    "status": m.status,
                    "dose_value": m.dose_value,
                    "dose_unit": m.dose_unit,
                    "frequency_per_day": m.frequency_per_day,
                    "route": m.route,
                }
                for m in self.current_medications
            ],
            "disease_codes": list(self.disease_codes),
        }
        # Flatten measurements into legacy top-level keys.
        for name, entry in self.measurements.items():
            if not isinstance(entry, dict):
                continue
            v = entry.get("value")
            if name == "egfr":
                out["egfr"] = v
            elif name == "systolic_bp":
                out["latest_systolic_bp_mmHg"] = v
            elif name == "diastolic_bp":
                out["latest_diastolic_bp_mmHg"] = v
            elif name == "glucose":
                out["latest_glucose_mmol_l"] = v
            elif name == "serum_potassium":
                out["serum_potassium_mmol_l"] = v
            elif name == "uric_acid":
                out["latest_uric_acid_umol_l"] = v
        # Forward clinical_flags as boolean top-level keys too.
        for name, v in self.clinical_flags.items():
            out[name] = v
        # Forward legacy fields last so they don't shadow canonical
        # extractions.
        for k, v in self.legacy_fields.items():
            out.setdefault(k, v)
        return out


# ----------------------------------------------------------------- dialogue


@dataclass
class MedicationActionInput:
    drug_id: str = ""
    drug_name: str = ""
    action: str = ""
    dose_value: Optional[float] = None
    dose_unit: Optional[str] = None
    frequency_per_day: Optional[float] = None
    route: Optional[str] = None
    duration_days: Optional[int] = None
    use_current_regimen: Optional[bool] = None
    replace_drug_id: Optional[str] = None
    replace_drug_name: Optional[str] = None

    @classmethod
    def from_raw(cls, raw: Any) -> "MedicationActionInput":
        d = _as_dict(raw) or {}
        # v4.2.0 canonical: ``drug_id`` + ``drug_name``.
        # v4.1 used ``drug`` to denote the display name. We accept
        # either, preferring ``drug_name`` when present.
        drug_id = str(d.get("drug_id", "") or "")
        drug_name = str(d.get("drug_name", "") or d.get("drug", "") or "")
        if not drug_id and drug_name:
            # Map drug_name back to drug_id at the input model layer.
            drug_id = drug_name.strip().lower()
        # ``route`` was implicit in v4.1 (defaulted to "oral"). v4.2.0
        # makes it explicit; silently default for backward compat.
        route = d.get("route")
        if not route:
            route = "oral"
        return cls(
            drug_id=drug_id,
            drug_name=drug_name,
            action=str(d.get("action", "") or ""),
            dose_value=d.get("dose_value"),
            dose_unit=d.get("dose_unit"),
            frequency_per_day=d.get("frequency_per_day"),
            route=route,
            duration_days=d.get("duration_days"),
            use_current_regimen=d.get("use_current_regimen"),
            replace_drug_id=d.get("replace_drug_id"),
            replace_drug_name=d.get("replace_drug_name"),
        )


@dataclass
class FoodAdviceInput:
    food_concept_id: str = ""
    food_name: str = ""
    action: str = ""
    amount: Optional[float] = None
    frequency: Optional[str] = None
    instruction: str = ""

    @classmethod
    def from_raw(cls, raw: Any) -> "FoodAdviceInput":
        d = _as_dict(raw) or {}
        food_concept_id = str(d.get("food_concept_id", "") or d.get("concept", "") or "")
        food_name = str(d.get("food_name", "") or d.get("food", "") or "")
        if not food_concept_id and food_name:
            food_concept_id = food_name.strip().lower()
        return cls(
            food_concept_id=food_concept_id,
            food_name=food_name,
            action=str(d.get("action", "") or ""),
            amount=d.get("amount"),
            frequency=d.get("frequency"),
            instruction=str(d.get("instruction", "") or ""),
        )


@dataclass
class ExerciseAdviceInput:
    activity_concept_id: str = ""
    activity_name: str = ""
    intensity: str = ""
    action: str = ""
    duration_min: Optional[int] = None
    frequency_per_week: Optional[int] = None
    instruction: str = ""

    @classmethod
    def from_raw(cls, raw: Any) -> "ExerciseAdviceInput":
        d = _as_dict(raw) or {}
        activity_concept_id = str(d.get("activity_concept_id", "") or d.get("concept", "") or "")
        activity_name = str(d.get("activity_name", "") or d.get("activity", "") or "")
        if not activity_concept_id and activity_name:
            activity_concept_id = activity_name.strip().lower()
        return cls(
            activity_concept_id=activity_concept_id,
            activity_name=activity_name,
            intensity=str(d.get("intensity", "") or ""),
            action=str(d.get("action", "") or ""),
            duration_min=d.get("duration_min"),
            frequency_per_week=d.get("frequency_per_week"),
            instruction=str(d.get("instruction", "") or ""),
        )


@dataclass
class CareActionInput:
    type: str = ""
    target: str = ""
    action: str = ""
    urgency: Optional[str] = None

    @classmethod
    def from_raw(cls, raw: Any) -> "CareActionInput":
        d = _as_dict(raw) or {}
        return cls(
            type=str(d.get("type", "") or ""),
            target=str(d.get("target", "") or ""),
            action=str(d.get("action", "") or ""),
            urgency=d.get("urgency"),
        )


@dataclass
class DialogueOutputInput:
    reply_text: str = ""
    medication_actions: List[MedicationActionInput] = field(default_factory=list)
    food_advice: List[FoodAdviceInput] = field(default_factory=list)
    exercise_advice: List[ExerciseAdviceInput] = field(default_factory=list)
    care_actions: List[CareActionInput] = field(default_factory=list)
    requires_review: bool = False
    uncertainty_reasons: List[str] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: Any) -> "DialogueOutputInput":
        d = _as_dict(raw) or {}
        return cls(
            reply_text=str(d.get("reply_text", "") or ""),
            medication_actions=[
                MedicationActionInput.from_raw(x)
                for x in (d.get("medication_actions") or [])
            ],
            food_advice=[
                FoodAdviceInput.from_raw(x)
                for x in (d.get("food_advice") or [])
            ],
            exercise_advice=[
                ExerciseAdviceInput.from_raw(x)
                for x in (d.get("exercise_advice") or [])
            ],
            care_actions=[
                CareActionInput.from_raw(x)
                for x in (d.get("care_actions") or [])
            ],
            requires_review=bool(d.get("requires_review", False)),
            uncertainty_reasons=[
                str(x) for x in (d.get("uncertainty_reasons") or []) if x
            ],
        )

    def to_engine_dialogue_output(self) -> Dict[str, Any]:
        """Project into the v4.1-shaped dict the rule engine already
        consumes (``medication_actions`` with ``drug``/``dose_mg`` keys,
        ``food_advice`` with ``food``/``concept``, etc.)."""
        med_actions = []
        for ma in self.medication_actions:
            med_actions.append({
                "drug": ma.drug_name or ma.drug_id,
                "drug_id": ma.drug_id,
                "action": ma.action,
                "dose_value": ma.dose_value,
                "dose_unit": ma.dose_unit,
                "frequency_per_day": ma.frequency_per_day,
                "route": ma.route,
                "duration_days": ma.duration_days,
                "use_current_regimen": ma.use_current_regimen,
                "replace_drug": ma.replace_drug_name or ma.replace_drug_id,
                "replace_drug_id": ma.replace_drug_id,
            })
        food = []
        for fa in self.food_advice:
            food.append({
                "food": fa.food_name or fa.food_concept_id,
                "concept": fa.food_concept_id,
                "action": fa.action,
                "instruction": fa.instruction,
                "amount": fa.amount,
                "frequency": fa.frequency,
            })
        exercise = []
        for ea in self.exercise_advice:
            exercise.append({
                "activity": ea.activity_name or ea.activity_concept_id,
                "concept": ea.activity_concept_id,
                "intensity": ea.intensity,
                "action": ea.action,
                "duration_min": ea.duration_min,
                "frequency_per_week": ea.frequency_per_week,
                "instruction": ea.instruction,
            })
        care = []
        for ca in self.care_actions:
            care.append({
                "type": ca.type,
                "target": ca.target,
                "action": ca.action,
                "urgency": ca.urgency,
            })
        return {
            "reply_text": self.reply_text,
            "medication_actions": med_actions,
            "food_advice": food,
            "exercise_advice": exercise,
            "care_actions": care,
            "requires_review": self.requires_review,
            "uncertainty_reasons": list(self.uncertainty_reasons),
        }


# ----------------------------------------------------------------- top-level


@dataclass
class StrictAuditInput:
    schema_version: str = ""
    patient_state: PatientStateInput = field(default_factory=PatientStateInput)
    dialogue_output: DialogueOutputInput = field(default_factory=DialogueOutputInput)

    @classmethod
    def from_raw(cls, raw: Any) -> "StrictAuditInput":
        d = _as_dict(raw) or {}
        return cls(
            schema_version=str(d.get("schema_version", "") or ""),
            patient_state=PatientStateInput.from_raw(d.get("patient_state")),
            dialogue_output=DialogueOutputInput.from_raw(d.get("dialogue_output")),
        )

    def is_top_level_dict(self) -> bool:
        return self.schema_version != "" or bool(self.patient_state.patient_id)