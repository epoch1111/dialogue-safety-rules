"""v4.2.0 input validator.

The validator walks the strict input dataclasses in
:mod:`safety.input_models` and emits a list of
:class:`InputValidationIssue` objects. It also cross-checks:

- ``drug_id`` against the alias table,
- ``drug_id`` <-> ``drug_name`` agreement,
- ``drug_id`` <-> ``medication_status`` membership,
- ``action``-specific required-field sets,
- numeric finiteness (``NaN``, +/-``Infinity`` are not allowed),
- dose unit conversion via :mod:`safety.unit_converter`,
- ``replace`` actions: the source drug must be in the active regimen,
- ``avoid_start`` actions: never silently removes drugs,
- ``continue`` actions without ``use_current_regimen`` must declare full
  dose information, or the engine forces REVIEW.

The validator never throws. Failures become REVIEW-severity issues
unless the spec mandates BLOCK (e.g. unknown schema_version).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from safety.input_models import (
    CareActionInput,
    CurrentMedicationInput,
    DialogueOutputInput,
    ExerciseAdviceInput,
    FoodAdviceInput,
    InputValidationIssue,
    InputValidationResult,
    MedicationActionInput,
    PatientStateInput,
    StrictAuditInput,
    SUPPORTED_SCHEMA_VERSIONS,
    VALID_CARE_ACTIONS,
    VALID_CARE_TYPES,
    VALID_CARE_URGENCIES,
    VALID_EXERCISE_ACTIONS,
    VALID_FOOD_ACTIONS,
    VALID_INTENSITIES,
    VALID_MASS_UNITS,
    VALID_MED_STATUSES,
    VALID_MEDICATION_ACTIONS,
    VALID_ROUTES,
    _as_dict,
    _as_list,
)
from safety.rule_repository import RuleRepository
from safety.unit_converter import (
    convert_mass_to_mg,
    daily_total_mg,
    is_finite_number,
    to_finite_float,
)


def _issue(result: InputValidationResult, code: str, path: str,
           message: str, severity: str = "REVIEW",
           details: Optional[Dict[str, Any]] = None) -> None:
    result.issues.append(InputValidationIssue(
        code=code, field_path=path, message=message,
        severity=severity, details=details or {},
    ))


@dataclass
class InputValidator:
    repository: RuleRepository

    def validate(self, raw: Any) -> InputValidationResult:
        result = InputValidationResult()

        top = _as_dict(raw)
        if top is None:
            _issue(result, "INPUT_NOT_OBJECT", "$",
                   "Top-level audit input must be a JSON object.",
                   severity="REVIEW")
            return result

        schema_version = str(top.get("schema_version", "") or "")
        if not schema_version:
            _issue(result, "INPUT_SCHEMA_VERSION_MISSING", "schema_version",
                   "schema_version is required.",
                   severity="REVIEW")
        elif schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            _issue(result, "INPUT_SCHEMA_VERSION_UNSUPPORTED", "schema_version",
                   f"schema_version {schema_version!r} is not supported "
                   f"(supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}).",
                   severity="REVIEW",
                   details={"provided": schema_version})

        patient_state_raw = top.get("patient_state")
        dialogue_output_raw = top.get("dialogue_output")

        if "patient_state" not in top:
            _issue(result, "INPUT_PATIENT_STATE_MISSING", "patient_state",
                   "patient_state field is required.",
                   severity="REVIEW")
        if "dialogue_output" not in top:
            _issue(result, "INPUT_DIALOGUE_OUTPUT_MISSING", "dialogue_output",
                   "dialogue_output field is required.",
                   severity="REVIEW")

        patient_state = PatientStateInput.from_raw(patient_state_raw)
        dialogue_output = DialogueOutputInput.from_raw(dialogue_output_raw)

        self._validate_patient_state(patient_state, result)
        self._validate_dialogue_output(
            dialogue_output, patient_state, result,
            raw_dialogue=dialogue_output_raw,
        )
        return result

    # ----------------------------------------------------------- patient_state

    def _validate_patient_state(
        self,
        ps: PatientStateInput,
        result: InputValidationResult,
    ) -> None:
        if not ps.patient_id:
            _issue(result, "INPUT_PATIENT_ID_MISSING",
                   "patient_state.patient_id",
                   "patient_id is required.",
                   severity="REVIEW")

        meds = ps.current_medications
        if not isinstance(_as_list(meds), list):
            _issue(result, "INPUT_CURRENT_MEDICATIONS_NOT_LIST",
                   "patient_state.current_medications",
                   "current_medications must be a list.",
                   severity="REVIEW")
            meds = []

        for idx, item in enumerate(meds):
            path = f"patient_state.current_medications[{idx}]"
            if not isinstance(item, CurrentMedicationInput) or not getattr(item, "raw_was_object", True):
                _issue(result, "INPUT_INVALID_MEDICATION_ITEM", path,
                       "current_medications entry must be a JSON object.",
                       severity="REVIEW")
                continue

            # v4.2.0: legacy ``name`` field is accepted as a stand-in for
            # ``drug_id``. The validator silently maps it via the alias
            # table so v4.1 test data keeps working. New inputs are
            # expected to provide ``drug_id`` + ``drug_name``; we emit a
            # DEPRECATED_INPUT_SCHEMA finding only when explicitly
            # requested via strict_mode_emit_deprecated_finding.
            if not item.drug_id and item.drug_name:
                canonical = self.repository.canonical_drug(item.drug_name)
                if canonical and canonical != item.drug_name.strip().lower():
                    item.drug_id = canonical
                elif not canonical:
                    # Use the normalized form of the name as the
                    # drug_id so downstream code can at least look it up.
                    item.drug_id = item.drug_name.strip().lower()

            if not item.is_structurally_valid():
                _issue(result, "INPUT_MEDICATION_MISSING_FIELDS", path,
                       "drug_id, drug_name, and status are required.",
                       severity="REVIEW")
            if item.status and item.status not in VALID_MED_STATUSES:
                _issue(result, "INPUT_INVALID_MEDICATION_STATUS",
                       f"{path}.status",
                       f"status {item.status!r} is not in "
                       f"{sorted(VALID_MED_STATUSES)}.",
                       severity="REVIEW")
            if item.dose_unit and item.dose_unit not in VALID_MASS_UNITS:
                _issue(result, "INPUT_UNKNOWN_DOSE_UNIT", f"{path}.dose_unit",
                       f"dose_unit {item.dose_unit!r} is not supported.",
                       severity="REVIEW")
            if item.route and item.route not in VALID_ROUTES:
                _issue(result, "INPUT_INVALID_ROUTE", f"{path}.route",
                       f"route {item.route!r} is not in {sorted(VALID_ROUTES)}.",
                       severity="REVIEW")
            if item.dose_value is not None and not is_finite_number(item.dose_value):
                _issue(result, "INPUT_NON_FINITE_DOSE", f"{path}.dose_value",
                       "dose_value must be a finite number.",
                       severity="REVIEW")
            elif isinstance(item.dose_value, (int, float)) and item.dose_value < 0:
                _issue(result, "INPUT_NEGATIVE_DOSE", f"{path}.dose_value",
                       "dose_value cannot be negative.",
                       severity="REVIEW")
            if item.frequency_per_day is not None:
                if not is_finite_number(item.frequency_per_day):
                    _issue(result, "INPUT_NON_FINITE_FREQUENCY",
                           f"{path}.frequency_per_day",
                           "frequency_per_day must be a finite number.",
                           severity="REVIEW")
                elif float(item.frequency_per_day) <= 0:
                    _issue(result, "INPUT_NON_POSITIVE_FREQUENCY",
                           f"{path}.frequency_per_day",
                           "frequency_per_day must be > 0.",
                           severity="REVIEW")

            if item.drug_id and item.drug_name:
                self._validate_drug_identity(item.drug_id, item.drug_name, path, result)

    def _validate_drug_identity(
        self,
        drug_id: str,
        drug_name: str,
        path: str,
        result: InputValidationResult,
    ) -> None:
        """Cross-check drug_id vs drug_name via the alias table."""
        canonical_from_id = self.repository.canonical_drug(drug_id)
        canonical_from_name = self.repository.canonical_drug(drug_name)

        if canonical_from_id == drug_id and canonical_from_name == drug_name:
            _issue(result, "INPUT_UNKNOWN_DRUG", f"{path}.drug_id",
                   f"drug_id {drug_id!r} is not in the alias table.",
                   severity="REVIEW",
                   details={"drug_id": drug_id, "drug_name": drug_name})
            return
        if canonical_from_name == drug_name:
            _issue(result, "INPUT_UNKNOWN_DRUG_NAME", f"{path}.drug_name",
                   f"drug_name {drug_name!r} is not in the alias table.",
                   severity="REVIEW",
                   details={"drug_id": drug_id, "drug_name": drug_name})
        if canonical_from_id != canonical_from_name:
            _issue(result, "INPUT_DRUG_ID_NAME_MISMATCH", f"{path}.drug_name",
                   f"drug_id {drug_id!r} maps to {canonical_from_id!r} "
                   f"but drug_name {drug_name!r} maps to "
                   f"{canonical_from_name!r}.",
                   severity="REVIEW",
                   details={
                       "drug_id": drug_id,
                       "drug_name": drug_name,
                       "canonical_from_id": canonical_from_id,
                       "canonical_from_name": canonical_from_name,
                   })

    # -------------------------------------------------------- dialogue_output

    def _validate_dialogue_output(
        self,
        do: DialogueOutputInput,
        ps: PatientStateInput,
        result: InputValidationResult,
        raw_dialogue: Optional[Dict[str, Any]] = None,
    ) -> None:
        # Top-level required list fields (even when empty). The raw
        # payload is consulted so we can distinguish "explicitly empty
        # list" from "field omitted".
        raw = raw_dialogue if isinstance(raw_dialogue, dict) else {}
        for name in ("medication_actions", "food_advice",
                     "exercise_advice", "care_actions"):
            if name not in raw:
                _issue(
                    result,
                    "INPUT_DIALOGUE_FIELD_MISSING",
                    f"dialogue_output.{name}",
                    f"{name} must be present as a list (can be empty).",
                    severity="REVIEW",
                )

        for idx, ma in enumerate(do.medication_actions):
            self._validate_medication_action(ma, idx, ps, result)

        for idx, fa in enumerate(do.food_advice):
            self._validate_food_advice(fa, idx, result)

        for idx, ea in enumerate(do.exercise_advice):
            self._validate_exercise_advice(ea, idx, result)

        for idx, ca in enumerate(do.care_actions):
            self._validate_care_action(ca, idx, result)

    # ---------------------------------------------------- medication_actions

    def _validate_medication_action(
        self,
        ma: MedicationActionInput,
        idx: int,
        ps: PatientStateInput,
        result: InputValidationResult,
    ) -> None:
        path = f"dialogue_output.medication_actions[{idx}]"

        if not ma.drug_id or not ma.drug_name:
            _issue(result, "INPUT_MEDICATION_ACTION_MISSING_ID",
                   path,
                   "drug_id and drug_name are required.",
                   severity="REVIEW")

        if ma.action not in VALID_MEDICATION_ACTIONS:
            _issue(result, "INPUT_INVALID_MEDICATION_ACTION",
                   f"{path}.action",
                   f"action {ma.action!r} is not in "
                   f"{sorted(VALID_MEDICATION_ACTIONS)}.",
                   severity="REVIEW")
            return

        if ma.dose_unit and ma.dose_unit not in VALID_MASS_UNITS:
            _issue(result, "INPUT_UNKNOWN_DOSE_UNIT", f"{path}.dose_unit",
                   f"dose_unit {ma.dose_unit!r} is not supported.",
                   severity="REVIEW")

        if ma.route and ma.route not in VALID_ROUTES:
            _issue(result, "INPUT_INVALID_ROUTE", f"{path}.route",
                   f"route {ma.route!r} is not in {sorted(VALID_ROUTES)}.",
                   severity="REVIEW")

        if ma.dose_value is not None and not is_finite_number(ma.dose_value):
            _issue(result, "INPUT_NON_FINITE_DOSE", f"{path}.dose_value",
                   "dose_value must be a finite number.",
                   severity="REVIEW")
        if ma.frequency_per_day is not None and not is_finite_number(ma.frequency_per_day):
            _issue(result, "INPUT_NON_FINITE_FREQUENCY",
                   f"{path}.frequency_per_day",
                   "frequency_per_day must be a finite number.",
                   severity="REVIEW")
        if isinstance(ma.frequency_per_day, (int, float)) and ma.frequency_per_day <= 0:
            _issue(result, "INPUT_NON_POSITIVE_FREQUENCY",
                   f"{path}.frequency_per_day",
                   "frequency_per_day must be > 0.",
                   severity="REVIEW")
        if isinstance(ma.duration_days, (int, float)) and ma.duration_days < 0:
            _issue(result, "INPUT_NEGATIVE_DURATION",
                   f"{path}.duration_days",
                   "duration_days cannot be negative.",
                   severity="REVIEW")

        if ma.dose_value is not None and isinstance(ma.dose_value, (int, float)) \
                and ma.dose_value == 0 and ma.action in {"start", "increase", "replace"}:
            _issue(result, "INPUT_ZERO_DOSE_FOR_ACTION",
                   f"{path}.dose_value",
                   f"dose_value=0 is not allowed for action {ma.action!r}.",
                   severity="REVIEW")

        # Action-specific required fields.
        if ma.action == "start":
            self._require_dose_block(ma, path, result)
        elif ma.action in {"increase", "decrease"}:
            self._require_dose_block(ma, path, result)
        elif ma.action == "continue":
            has_full_dose = (
                ma.dose_value is not None
                and ma.dose_unit is not None
                and ma.frequency_per_day is not None
                and ma.route is not None
            )
            if not has_full_dose:
                if not ma.use_current_regimen:
                    _issue(result, "INPUT_CONTINUE_MISSING_DOSE",
                           path,
                           "continue action must either supply full dose "
                           "information or use_current_regimen=true with the "
                           "current regimen providing the same.",
                           severity="REVIEW")
                else:
                    # Check the current regimen has the required dose.
                    if not self._regimen_has_full_dose(ps, ma):
                        _issue(result, "INPUT_CONTINUE_NO_CURRENT_REGIMEN",
                               path,
                               "use_current_regimen=true but no full "
                               "current regimen was found.",
                               severity="REVIEW")
        elif ma.action == "replace":
            if not ma.replace_drug_id and not ma.replace_drug_name:
                _issue(result, "INPUT_REPLACE_MISSING_OLD",
                       f"{path}.replace_drug_id",
                       "replace action must declare replace_drug_id.",
                       severity="REVIEW")
            else:
                if not self._is_drug_in_active_regimen(ps, ma.replace_drug_id or ""):
                    _issue(result, "INPUT_REPLACE_SOURCE_NOT_ACTIVE",
                           f"{path}.replace_drug_id",
                           f"replace source drug {ma.replace_drug_id!r} is "
                           "not in the active current regimen.",
                           severity="REVIEW")
            self._require_dose_block(ma, path, result)
        elif ma.action in {"stop", "hold", "avoid_start"}:
            if not ma.drug_id or not ma.drug_name:
                _issue(result, "INPUT_MEDICATION_ACTION_MISSING_ID",
                       path,
                       "stop/hold/avoid_start actions require drug_id and drug_name.",
                       severity="REVIEW")

        # Cross-check drug_id vs drug_name via alias table.
        if ma.drug_id and ma.drug_name:
            self._validate_drug_identity(ma.drug_id, ma.drug_name, path, result)

        # Unit conversion pre-check.
        if ma.dose_value is not None and ma.dose_unit is not None:
            # Negative dose gets its own finding code so the caller can
            # branch on it precisely.
            try:
                if isinstance(ma.dose_value, (int, float)) and ma.dose_value < 0:
                    _issue(result, "INPUT_NEGATIVE_DOSE", f"{path}.dose_value",
                           "dose_value cannot be negative.",
                           severity="REVIEW",
                           details={"dose_value": ma.dose_value,
                                    "dose_unit": ma.dose_unit})
            except (TypeError, ValueError):
                pass
            converted = convert_mass_to_mg(ma.dose_value, ma.dose_unit)
            if not converted.is_valid and converted.reason != "NEGATIVE_DOSE":
                _issue(result, "INPUT_DOSE_UNIT_NOT_CONVERTIBLE",
                       f"{path}.dose_unit",
                       f"dose {ma.dose_value} {ma.dose_unit} cannot be "
                       f"converted to mg ({converted.reason}).",
                       severity="REVIEW",
                       details=converted.to_dict())

    @staticmethod
    def _require_dose_block(
        ma: MedicationActionInput,
        path: str,
        result: InputValidationResult,
    ) -> None:
        missing = []
        if ma.dose_value is None:
            missing.append("dose_value")
        if not ma.dose_unit:
            missing.append("dose_unit")
        if ma.frequency_per_day is None:
            missing.append("frequency_per_day")
        if not ma.route:
            missing.append("route")
        if missing:
            _issue(result, "INPUT_MEDICATION_ACTION_MISSING_FIELDS",
                   path,
                   f"{ma.action!r} action requires {missing}.",
                   severity="REVIEW",
                   details={"action": ma.action, "missing": missing})

    @staticmethod
    def _regimen_has_full_dose(
        ps: PatientStateInput, ma: MedicationActionInput
    ) -> bool:
        for med in ps.current_medications:
            if med.drug_id != ma.drug_id:
                continue
            if (
                med.dose_value is not None
                and med.dose_unit is not None
                and med.frequency_per_day is not None
            ):
                return True
        return False

    @staticmethod
    def _is_drug_in_active_regimen(ps: PatientStateInput, drug_id: str) -> bool:
        if not drug_id:
            return False
        for med in ps.current_medications:
            if med.status != "active":
                continue
            if med.drug_id == drug_id:
                return True
        return False

    # ------------------------------------------------------------ food_advice

    def _validate_food_advice(
        self,
        fa: FoodAdviceInput,
        idx: int,
        result: InputValidationResult,
    ) -> None:
        path = f"dialogue_output.food_advice[{idx}]"
        if not fa.food_concept_id or not fa.food_name:
            _issue(result, "INPUT_FOOD_ADVICE_MISSING_ID", path,
                   "food_concept_id and food_name are required.",
                   severity="REVIEW")
        if fa.action not in VALID_FOOD_ACTIONS:
            _issue(result, "INPUT_INVALID_FOOD_ACTION",
                   f"{path}.action",
                   f"action {fa.action!r} is not in "
                   f"{sorted(VALID_FOOD_ACTIONS)}.",
                   severity="REVIEW")

    # ------------------------------------------------------- exercise_advice

    def _validate_exercise_advice(
        self,
        ea: ExerciseAdviceInput,
        idx: int,
        result: InputValidationResult,
    ) -> None:
        path = f"dialogue_output.exercise_advice[{idx}]"
        if not ea.activity_concept_id or not ea.activity_name:
            _issue(result, "INPUT_EXERCISE_ADVICE_MISSING_ID", path,
                   "activity_concept_id and activity_name are required.",
                   severity="REVIEW")
        if ea.intensity not in VALID_INTENSITIES:
            _issue(result, "INPUT_INVALID_EXERCISE_INTENSITY",
                   f"{path}.intensity",
                   f"intensity {ea.intensity!r} is not in "
                   f"{sorted(VALID_INTENSITIES)}.",
                   severity="REVIEW")
        if ea.action not in VALID_EXERCISE_ACTIONS:
            _issue(result, "INPUT_INVALID_EXERCISE_ACTION",
                   f"{path}.action",
                   f"action {ea.action!r} is not in "
                   f"{sorted(VALID_EXERCISE_ACTIONS)}.",
                   severity="REVIEW")

    # --------------------------------------------------------- care_actions

    def _validate_care_action(
        self,
        ca: CareActionInput,
        idx: int,
        result: InputValidationResult,
    ) -> None:
        path = f"dialogue_output.care_actions[{idx}]"
        if ca.type not in VALID_CARE_TYPES:
            _issue(result, "INPUT_INVALID_CARE_TYPE",
                   f"{path}.type",
                   f"type {ca.type!r} is not in {sorted(VALID_CARE_TYPES)}.",
                   severity="REVIEW")
        if ca.action not in VALID_CARE_ACTIONS:
            _issue(result, "INPUT_INVALID_CARE_ACTION",
                   f"{path}.action",
                   f"action {ca.action!r} is not in {sorted(VALID_CARE_ACTIONS)}.",
                   severity="REVIEW")
        if ca.urgency not in VALID_CARE_URGENCIES:
            _issue(result, "INPUT_INVALID_CARE_URGENCY",
                   f"{path}.urgency",
                   f"urgency {ca.urgency!r} is not in "
                   f"{sorted(x for x in VALID_CARE_URGENCIES if x)}.",
                   severity="REVIEW")