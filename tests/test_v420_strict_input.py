"""v4.2.0 strict-input tests.

Covers:

1. Schema tests
2. Invalid input type tests
3. Conditional required-field tests
4. Unknown drug tests
5. drug_id vs drug_name mismatch tests
6. Invalid enum tests
7. Unit conversion tests
8. Unknown unit tests
9. Negative / NaN / Infinity tests
10. Missing required context tests
11. Data freshness tests (placeholder)
12. Text-structure conflict tests
13. fail-closed exception tests
14. replace / avoid_start / held status tests
15. all/any/not DSL tests

Every test asserts that invalid input cannot produce PASS and that the
engine never raises an uncaught exception.
"""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

from safety import DialogueSafetyEngine, InputValidator


ROOT = Path(__file__).resolve().parents[1]


def _engine() -> DialogueSafetyEngine:
    return DialogueSafetyEngine(ROOT / "rules")


class StrictInputTests(unittest.TestCase):
    """Schema + JSON shape."""

    def test_empty_inputs_are_review_not_pass(self):
        eng = _engine()
        report = eng.audit(patient_state={}, dialogue_output={})
        self.assertEqual(report.decision, "REVIEW")
        self.assertFalse(report.original_llm_reply_was_sent)
        self.assertIn("INPUT_VALIDATION", report.decision_basis)

    def test_missing_schema_version_is_review(self):
        # Test the validator directly: a payload without schema_version
        # is rejected by the strict validator. The audit() entry point
        # always wraps the input with schema_version="1.0" for
        # backward compatibility, so we exercise the validator to
        # verify the contract.
        validator = InputValidator(_engine().repository)
        result = validator.validate({
            "patient_state": {"patient_id": "P", "current_medications": [],
                              "disease_codes": []},
            "dialogue_output": {"reply_text": "", "medication_actions": [],
                                "food_advice": [], "exercise_advice": [],
                                "care_actions": []},
        })
        self.assertFalse(result.is_valid)
        codes = {issue.code for issue in result.issues}
        self.assertIn("INPUT_SCHEMA_VERSION_MISSING", codes)

    def test_unknown_schema_version_is_review(self):
        validator = InputValidator(_engine().repository)
        result = validator.validate({
            "schema_version": "2.5",
            "patient_state": {"patient_id": "P", "current_medications": [],
                              "disease_codes": []},
            "dialogue_output": {"reply_text": "", "medication_actions": [],
                                "food_advice": [], "exercise_advice": [],
                                "care_actions": []},
        })
        self.assertFalse(result.is_valid)
        codes = {issue.code for issue in result.issues}
        self.assertIn("INPUT_SCHEMA_VERSION_UNSUPPORTED", codes)

    def test_non_object_input_is_review_not_crash(self):
        eng = _engine()
        # Pass a list as the top-level payload. Must not crash.
        try:
            report = eng.audit(patient_state=[], dialogue_output=[])
        except Exception as exc:
            self.fail(f"audit() raised: {exc}")
        else:
            self.assertEqual(report.decision, "REVIEW")
            self.assertFalse(report.original_llm_reply_was_sent)

    def test_validator_returns_issues_for_each_problem(self):
        validator = InputValidator(_engine().repository)
        result = validator.validate({
            "schema_version": "1.0",
            "patient_state": {"current_medications": ["metformin"]},
            "dialogue_output": {"reply_text": "x"},
        })
        self.assertFalse(result.is_valid)
        codes = {issue.code for issue in result.issues}
        self.assertIn("INPUT_PATIENT_ID_MISSING", codes)
        self.assertIn("INPUT_INVALID_MEDICATION_ITEM", codes)

    def test_validator_flags_missing_dialogue_lists(self):
        validator = InputValidator(_engine().repository)
        # ``dialogue_output`` missing its list fields
        result = validator.validate({
            "schema_version": "1.0",
            "patient_state": {"patient_id": "P", "current_medications": [],
                              "disease_codes": []},
            "dialogue_output": {"reply_text": "x"},
        })
        codes = {issue.code for issue in result.issues}
        self.assertIn("INPUT_DIALOGUE_FIELD_MISSING", codes)

    def test_validator_rejects_completely_missing_dialogue_output(self):
        validator = InputValidator(_engine().repository)
        result = validator.validate({
            "schema_version": "1.0",
            "patient_state": {"patient_id": "P", "current_medications": [],
                              "disease_codes": []},
        })
        codes = {issue.code for issue in result.issues}
        self.assertIn("INPUT_DIALOGUE_OUTPUT_MISSING", codes)


class InvalidMedicationItemTests(unittest.TestCase):

    def test_string_in_current_medications_is_review(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": ["metformin"],
                "disease_codes": [],
            },
            dialogue_output={"reply_text": "", "medication_actions": [],
                             "food_advice": [], "exercise_advice": [],
                             "care_actions": []},
        )
        self.assertEqual(report.decision, "REVIEW")
        self.assertFalse(report.original_llm_reply_was_sent)
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_INVALID_MEDICATION_ITEM", codes)

    def test_null_in_current_medications_is_review(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [None],
                "disease_codes": [],
            },
            dialogue_output={"reply_text": "", "medication_actions": [],
                             "food_advice": [], "exercise_advice": [],
                             "care_actions": []},
        )
        self.assertEqual(report.decision, "REVIEW")
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_INVALID_MEDICATION_ITEM", codes)

    def test_missing_status_is_review(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [{
                    "drug_id": "metformin",
                    "drug_name": "二甲双胍",
                }],
                "disease_codes": [],
            },
            dialogue_output={"reply_text": "", "medication_actions": [],
                             "food_advice": [], "exercise_advice": [],
                             "care_actions": []},
        )
        self.assertEqual(report.decision, "REVIEW")
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_MEDICATION_MISSING_FIELDS", codes)

    def test_invalid_status_enum_is_review(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [{
                    "drug_id": "metformin",
                    "drug_name": "二甲双胍",
                    "status": "definitely-active",
                }],
                "disease_codes": [],
            },
            dialogue_output={"reply_text": "", "medication_actions": [],
                             "food_advice": [], "exercise_advice": [],
                             "care_actions": []},
        )
        self.assertEqual(report.decision, "REVIEW")
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_INVALID_MEDICATION_STATUS", codes)


class DrugIdentityTests(unittest.TestCase):

    def test_drug_id_name_mismatch_is_review(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [{
                    "drug_id": "simvastatin",
                    "drug_name": "阿托伐他汀",
                    "status": "active",
                }],
                "disease_codes": [],
            },
            dialogue_output={"reply_text": "", "medication_actions": [],
                             "food_advice": [], "exercise_advice": [],
                             "care_actions": []},
        )
        self.assertEqual(report.decision, "REVIEW")
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_DRUG_ID_NAME_MISMATCH", codes)

    def test_unknown_drug_name_is_review(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [{
                    "drug_id": "simvastatin",
                    "drug_name": "辛伐他烨",
                    "status": "active",
                }],
                "disease_codes": [],
            },
            dialogue_output={"reply_text": "", "medication_actions": [],
                             "food_advice": [], "exercise_advice": [],
                             "care_actions": []},
        )
        self.assertEqual(report.decision, "REVIEW")
        # May flag either DRUG_ID_NAME_MISMATCH or UNKNOWN_DRUG; either
        # way the patient receives REVIEW.
        codes = {i.code for i in report.input_validation_errors}
        self.assertTrue(codes & {
            "INPUT_UNKNOWN_DRUG",
            "INPUT_DRUG_ID_NAME_MISMATCH",
            "INPUT_UNKNOWN_DRUG_NAME",
        })


class EnumValidationTests(unittest.TestCase):

    def test_invalid_medication_action_is_review(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [],
                "disease_codes": [],
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "amlodipine",
                    "drug_name": "氨氯地平",
                    "action": "super-increase",
                    "dose_value": 5,
                    "dose_unit": "mg",
                    "frequency_per_day": 1,
                    "route": "oral",
                }],
                "food_advice": [],
                "exercise_advice": [],
                "care_actions": [],
            },
        )
        self.assertEqual(report.decision, "REVIEW")
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_INVALID_MEDICATION_ACTION", codes)

    def test_invalid_food_action_is_review(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [],
                "disease_codes": [],
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [],
                "food_advice": [{
                    "food_concept_id": "grapefruit",
                    "food_name": "西柚汁",
                    "action": "drink-it",
                }],
                "exercise_advice": [],
                "care_actions": [],
            },
        )
        self.assertEqual(report.decision, "REVIEW")
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_INVALID_FOOD_ACTION", codes)

    def test_invalid_intensity_is_review(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [],
                "disease_codes": [],
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [],
                "food_advice": [],
                "exercise_advice": [{
                    "activity_concept_id": "running",
                    "activity_name": "跑步",
                    "intensity": "extreme",
                    "action": "recommend",
                }],
                "care_actions": [],
            },
        )
        self.assertEqual(report.decision, "REVIEW")
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_INVALID_EXERCISE_INTENSITY", codes)


class UnitConversionTests(unittest.TestCase):

    def test_unit_conversion_basic(self):
        from safety.unit_converter import convert_mass_to_mg
        self.assertEqual(convert_mass_to_mg(1000, "mcg").value_mg, 1.0)
        self.assertEqual(convert_mass_to_mg(1, "g").value_mg, 1000.0)
        self.assertEqual(convert_mass_to_mg(500, "mg").value_mg, 500.0)

    def test_amlodipine_1g_is_overdose(self):
        """amlodipine 1 g → 1000 mg → BLOCK."""
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [],
                "disease_codes": [],
                "latest_systolic_bp_mmHg": 130,
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "amlodipine",
                    "drug_name": "氨氯地平",
                    "action": "start",
                    "dose_value": 1,
                    "dose_unit": "g",
                    "frequency_per_day": 1,
                    "route": "oral",
                }],
                "food_advice": [],
                "exercise_advice": [],
                "care_actions": [],
            },
        )
        self.assertEqual(report.decision, "BLOCK")
        ids = {v.rule_id for v in report.medical_violations}
        self.assertIn("R001_AMLODIPINE_MAX_DAILY_DOSE", ids)

    def test_unknown_unit_is_review(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [],
                "disease_codes": [],
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "amlodipine",
                    "drug_name": "氨氯地平",
                    "action": "start",
                    "dose_value": 5,
                    "dose_unit": "mL",
                    "frequency_per_day": 1,
                    "route": "oral",
                }],
                "food_advice": [],
                "exercise_advice": [],
                "care_actions": [],
            },
        )
        # No conversion → REVIEW, never BLOCK (per spec section 6:
        # unknown unit must REVIEW).
        self.assertEqual(report.decision, "REVIEW")

    def test_negative_dose_is_review(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [],
                "disease_codes": [],
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "amlodipine",
                    "drug_name": "氨氯地平",
                    "action": "start",
                    "dose_value": -5,
                    "dose_unit": "mg",
                    "frequency_per_day": 1,
                    "route": "oral",
                }],
                "food_advice": [],
                "exercise_advice": [],
                "care_actions": [],
            },
        )
        self.assertEqual(report.decision, "REVIEW")
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_NEGATIVE_DOSE", codes)

    def test_nan_dose_is_review(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [],
                "disease_codes": [],
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "amlodipine",
                    "drug_name": "氨氯地平",
                    "action": "start",
                    "dose_value": float("nan"),
                    "dose_unit": "mg",
                    "frequency_per_day": 1,
                    "route": "oral",
                }],
                "food_advice": [],
                "exercise_advice": [],
                "care_actions": [],
            },
        )
        self.assertEqual(report.decision, "REVIEW")
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_NON_FINITE_DOSE", codes)

    def test_infinity_dose_is_review(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [],
                "disease_codes": [],
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "amlodipine",
                    "drug_name": "氨氯地平",
                    "action": "start",
                    "dose_value": float("inf"),
                    "dose_unit": "mg",
                    "frequency_per_day": 1,
                    "route": "oral",
                }],
                "food_advice": [],
                "exercise_advice": [],
                "care_actions": [],
            },
        )
        self.assertEqual(report.decision, "REVIEW")

    def test_string_dose_unparseable_is_review(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [],
                "disease_codes": [],
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "amlodipine",
                    "drug_name": "氨氯地平",
                    "action": "start",
                    "dose_value": "twenty",
                    "dose_unit": "mg",
                    "frequency_per_day": 1,
                    "route": "oral",
                }],
                "food_advice": [],
                "exercise_advice": [],
                "care_actions": [],
            },
        )
        # Validator catches non-finite numeric; rule will fire REVIEW.
        self.assertEqual(report.decision, "REVIEW")


class MissingContextTests(unittest.TestCase):

    def test_missing_egfr_for_metformin_is_review(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [{
                    "drug_id": "metformin",
                    "drug_name": "二甲双胍",
                    "status": "active",
                }],
                "disease_codes": ["diabetes"],
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "metformin",
                    "drug_name": "二甲双胍",
                    "action": "continue",
                    "dose_value": 500,
                    "dose_unit": "mg",
                    "frequency_per_day": 2,
                    "route": "oral",
                }],
                "food_advice": [],
                "exercise_advice": [],
                "care_actions": [],
            },
        )
        # Even if R002 happens to pass because egfr is unknown, the
        # RequiredContextChecker must flag egfr as missing → REVIEW.
        self.assertEqual(report.decision, "REVIEW")
        self.assertFalse(report.original_llm_reply_was_sent)
        self.assertIn("MISSING_CONTEXT", report.decision_basis)
        paths = [m["field_path"] for m in report.missing_context_fields]
        self.assertIn("egfr", paths)

    def test_missing_serum_potassium_for_acei_is_review(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [{
                    "drug_id": "lisinopril",
                    "drug_name": "赖诺普利",
                    "status": "active",
                }],
                "disease_codes": [],
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "lisinopril",
                    "drug_name": "赖诺普利",
                    "action": "continue",
                    "dose_value": 10,
                    "dose_unit": "mg",
                    "frequency_per_day": 1,
                    "route": "oral",
                }],
                "food_advice": [],
                "exercise_advice": [],
                "care_actions": [],
            },
        )
        self.assertEqual(report.decision, "REVIEW")
        self.assertIn("MISSING_CONTEXT", report.decision_basis)

    def test_missing_glucose_for_insulin_vigorous_is_review(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [{
                    "drug_id": "insulin",
                    "drug_name": "甘精胰岛素",
                    "status": "active",
                }],
                "disease_codes": [],
                "latest_systolic_bp_mmHg": 120,
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [],
                "food_advice": [],
                "exercise_advice": [{
                    "activity_concept_id": "running",
                    "activity_name": "跑步",
                    "intensity": "vigorous",
                    "action": "recommend",
                }],
                "care_actions": [],
            },
        )
        self.assertEqual(report.decision, "REVIEW")
        self.assertIn("MISSING_CONTEXT", report.decision_basis)


class TextStructureConflictTests(unittest.TestCase):

    def test_text_says_avoid_structured_says_recommend(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [{
                    "drug_id": "simvastatin",
                    "drug_name": "辛伐他汀",
                    "status": "active",
                }],
                "egfr": 90,
                "latest_systolic_bp_mmHg": 120,
            },
            dialogue_output={
                "reply_text": "不要喝西柚汁。",
                "medication_actions": [],
                "food_advice": [{
                    "food_concept_id": "grapefruit",
                    "food_name": "西柚汁",
                    "action": "recommend",
                }],
                "exercise_advice": [],
                "care_actions": [],
            },
        )
        self.assertEqual(report.decision, "REVIEW")
        codes = {c.code for c in report.consistency_violations}
        self.assertIn("SYS003_TEXT_STRUCTURE_CONFLICT", codes)


class FailClosedTests(unittest.TestCase):

    def test_internal_exception_is_review_not_pass(self):
        """Force the engine to fail mid-audit and verify the engine
        returns REVIEW instead of letting the original reply through."""
        import safety.safety_engine as engine_mod

        original_cls = engine_mod.DialogueSafetyEngine

        class ExplodingEngine(original_cls):
            def _audit_impl(self, *args, **kwargs):
                raise RuntimeError("simulated explosion")

        # Replace the symbol in the module so ``DialogueSafetyEngine``
        # resolves to the exploding subclass.
        engine_mod.DialogueSafetyEngine = ExplodingEngine
        try:
            eng = ExplodingEngine(ROOT / "rules")
            try:
                report = eng.audit(
                    patient_state={"patient_id": "P", "current_medications": [],
                                   "disease_codes": []},
                    dialogue_output={"reply_text": "hi", "medication_actions": [],
                                     "food_advice": [], "exercise_advice": [],
                                     "care_actions": []},
                )
            except Exception as exc:
                self.fail(f"audit() should not raise: {exc}")
            self.assertEqual(report.decision, "REVIEW")
            self.assertFalse(report.original_llm_reply_was_sent)
            self.assertIn("SYSTEM_ERROR", report.decision_basis)
            codes = [i.code for i in report.input_validation_errors]
            self.assertIn("SYSTEM_ERROR", codes)
        finally:
            engine_mod.DialogueSafetyEngine = original_cls


class ReplaceHeldStatusTests(unittest.TestCase):

    def test_replace_source_not_active_is_review(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [{
                    "drug_id": "lisinopril",
                    "drug_name": "赖诺普利",
                    "status": "held",
                }],
                "disease_codes": [],
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "ramipril",
                    "drug_name": "雷米普利",
                    "action": "replace",
                    "replace_drug_id": "lisinopril",
                    "replace_drug_name": "赖诺普利",
                    "dose_value": 5,
                    "dose_unit": "mg",
                    "frequency_per_day": 1,
                    "route": "oral",
                }],
                "food_advice": [],
                "exercise_advice": [],
                "care_actions": [],
            },
        )
        self.assertEqual(report.decision, "REVIEW")
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_REPLACE_SOURCE_NOT_ACTIVE", codes)

    def test_avoid_start_does_not_remove_current_drug(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [{
                    "drug_id": "simvastatin",
                    "drug_name": "辛伐他汀",
                    "status": "active",
                }],
                "egfr": 90,
                "latest_systolic_bp_mmHg": 120,
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "simvastatin",
                    "drug_name": "辛伐他汀",
                    "action": "avoid_start",
                }],
                "food_advice": [],
                "exercise_advice": [],
                "care_actions": [],
            },
        )
        # No active DDI; simvastatin stays in current_drugs →
        # nothing to block on. Resulting_drugs still contains it.
        self.assertIn("simvastatin", report.resulting_drugs)
        self.assertEqual(report.decision, "PASS")

    def test_held_status_excludes_drug_from_current(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [
                    {"drug_id": "simvastatin", "drug_name": "辛伐他汀",
                     "status": "held"},
                    {"drug_id": "clarithromycin", "drug_name": "克拉霉素",
                     "status": "active"},
                ],
                "egfr": 90,
                "latest_systolic_bp_mmHg": 120,
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "clarithromycin",
                    "drug_name": "克拉霉素",
                    "action": "continue",
                    "dose_value": 500,
                    "dose_unit": "mg",
                    "frequency_per_day": 2,
                    "route": "oral",
                }],
                "food_advice": [],
                "exercise_advice": [],
                "care_actions": [],
            },
        )
        # simvastatin is held → not in resulting_drugs → R003 does NOT
        # fire.
        ids = {v.rule_id for v in report.medical_violations}
        self.assertNotIn("R003_SIMVASTATIN_CLARITHROMYCIN", ids)


class DslTests(unittest.TestCase):

    def test_range_block_fires_when_in_range(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "egfr": 42,
                "current_medications": [],
                "disease_codes": [],
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "metformin",
                    "drug_name": "二甲双胍",
                    "action": "continue",
                    "dose_value": 500,
                    "dose_unit": "mg",
                    "frequency_per_day": 2,
                    "route": "oral",
                }],
                "food_advice": [],
                "exercise_advice": [],
                "care_actions": [],
            },
        )
        ids = {v.rule_id for v in report.medical_violations}
        self.assertIn("R010_METFORMIN_EGFR_30_TO_45", ids)
        self.assertEqual(report.decision, "REVIEW")

    def test_range_block_does_not_fire_outside_range(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "egfr": 24,
                "current_medications": [],
                "disease_codes": [],
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "metformin",
                    "drug_name": "二甲双胍",
                    "action": "continue",
                    "dose_value": 500,
                    "dose_unit": "mg",
                    "frequency_per_day": 2,
                    "route": "oral",
                }],
                "food_advice": [],
                "exercise_advice": [],
                "care_actions": [],
            },
        )
        ids = {v.rule_id for v in report.medical_violations}
        # R010 must NOT fire when eGFR=24; R002 fires instead.
        self.assertNotIn("R010_METFORMIN_EGFR_30_TO_45", ids)
        self.assertIn("R002_METFORMIN_EGFR_LT_30", ids)

    def test_decision_basis_lists_each_phase(self):
        eng = _engine()
        # Empty patient_state + empty dialogue_output → at least one
        # phase must show up in decision_basis.
        report = eng.audit(
            patient_state={"current_medications": [],
                           "disease_codes": []},  # no patient_id
            dialogue_output={"reply_text": "x", "medication_actions": [],
                             "food_advice": [], "exercise_advice": [],
                             "care_actions": []},
        )
        self.assertIn("INPUT_VALIDATION", report.decision_basis)
        self.assertEqual(report.decision, "REVIEW")

    def test_decision_basis_pass_is_empty(self):
        eng = _engine()
        # Minimal valid input → PASS, decision_basis empty.
        report = eng.audit(
            patient_state={"patient_id": "P", "current_medications": [],
                           "disease_codes": [], "egfr": 90,
                           "latest_systolic_bp_mmHg": 120,
                           "latest_glucose_mmol_l": 6.0},
            dialogue_output={"reply_text": "x", "medication_actions": [],
                             "food_advice": [], "exercise_advice": [],
                             "care_actions": []},
        )
        self.assertEqual(report.decision, "PASS")
        self.assertEqual(report.decision_basis, [])


class VisibilityTests(unittest.TestCase):
    """patient_visible_response must NEVER leak internal rule IDs."""

    def test_visible_response_does_not_contain_rule_ids(self):
        eng = _engine()
        report = eng.audit(
            patient_state={
                "patient_id": "P",
                "egfr": 24,
                "current_medications": [{
                    "drug_id": "metformin",
                    "drug_name": "二甲双胍",
                    "status": "active",
                }],
                "disease_codes": [],
            },
            dialogue_output={
                "reply_text": "继续",
                "medication_actions": [{
                    "drug_id": "metformin",
                    "drug_name": "二甲双胍",
                    "action": "continue",
                    "dose_value": 500,
                    "dose_unit": "mg",
                    "frequency_per_day": 2,
                    "route": "oral",
                }],
                "food_advice": [],
                "exercise_advice": [],
                "care_actions": [],
            },
        )
        for token in ("R00", "R01", "R02", "traceback", "Exception",
                     "stack_trace"):
            self.assertNotIn(token, report.patient_visible_response)


class ReportStructureTests(unittest.TestCase):

    def test_report_includes_new_fields(self):
        eng = _engine()
        report = eng.audit(
            patient_state={"patient_id": "P", "current_medications": [],
                           "disease_codes": []},
            dialogue_output={"reply_text": "x", "medication_actions": [],
                             "food_advice": [], "exercise_advice": [],
                             "care_actions": []},
        )
        d = report.to_dict()
        self.assertIn("decision_basis", d)
        self.assertIn("medical_violations", d)
        self.assertIn("input_validation_errors", d)
        self.assertIn("missing_context_fields", d)
        self.assertIn("consistency_violations", d)
        self.assertIn("system_findings", d)
        self.assertIn("all_findings", d)
        self.assertIn("patient_visible_response", d)
        self.assertIn("reviewer_message", d)
        self.assertIn("developer_diagnostics", d)
        self.assertIn("original_llm_reply_was_sent", d)
        self.assertIn("input_schema_version", d)
        self.assertEqual(d["input_schema_version"], "1.0")
        # Legacy alias
        self.assertIn("violations", d)

    def test_input_schema_version_is_propagated(self):
        eng = _engine()
        report = eng.audit(
            patient_state={"patient_id": "P", "current_medications": [],
                           "disease_codes": []},
            dialogue_output={"reply_text": "x", "medication_actions": [],
                             "food_advice": [], "exercise_advice": [],
                             "care_actions": []},
        )
        self.assertEqual(report.input_schema_version, "1.0")


if __name__ == "__main__":
    unittest.main()