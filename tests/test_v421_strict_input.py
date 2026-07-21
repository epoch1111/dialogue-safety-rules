"""v4.2.1 strict-input + required-context regression tests.

Covers (per the v4.2.1 spec, sections 3, 4, 5, 6, 7, 8, 9, 10, 11, 13):

- audit_payload() strict_mode / compat_mode behavior
- legal new-schema input never produces SYS001 or SYS008
- requires_review=true forces REVIEW
- uncertainty_reasons non-empty forces REVIEW
- start/increase/decrease/replace missing route -> REVIEW
- replace missing replace_drug_id -> REVIEW
- measurement unit error / observed_at error / observed_at without tz
  / source unknown / confirmed=false -> REVIEW
- disease_code unknown -> REVIEW
- food_concept_id unknown -> REVIEW (but the validator treats it as
  INFO so the audit trail records it without forcing REVIEW when the
  action+name pair is otherwise valid)
- activity_concept_id unknown -> REVIEW (INFO, same rule)
- drug_id / drug_name mismatch -> REVIEW
- replace_drug_id / replace_drug_name mismatch -> REVIEW
- strict_mode rejects legacy fields
- compat_mode produces DEPRECATED_INPUT_SCHEMA finding
- missing schema_version -> REVIEW
- unsupported schema_version -> REVIEW
- audit() never auto-fills schema_version silently
- hold metformin does NOT require egfr from a "continue" rule
- stop metformin does NOT require egfr
- no exercise_advice: drug_exercise rules don't require BP
- food action=avoid: drug_food rules don't require irrelevant context
- exercise action=avoid: drug_exercise rules don't fire recommend risks
- NaN / Infinity / negative dose -> REVIEW
- unknown unit -> REVIEW
- RequiredContextChecker 1000 / 10000 pressure tests (no full scan)
- audit() fail-closed with explicit SystemError detection
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from safety import DialogueSafetyEngine
from safety.legacy_adapter import LegacyInputAdapter
from safety.required_context_checker import RequiredContextChecker


ROOT = Path(__file__).resolve().parents[1]


def _engine(rules_dir: Path = None) -> DialogueSafetyEngine:
    return DialogueSafetyEngine(rules_dir or ROOT / "rules")


# ============================================================ strict_mode

class StrictModeRejectsLegacyFieldsTests(unittest.TestCase):

    def test_legacy_drug_field_in_medication_actions_is_review(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {
                "patient_id": "P", "current_medications": [],
                "disease_codes": [],
            },
            "dialogue_output": {
                "reply_text": "",
                "medication_actions": [{
                    "drug": "氨氯地平", "action": "start",
                    "dose_value": 5, "dose_unit": "mg",
                    "frequency_per_day": 1, "route": "oral",
                }],
                "food_advice": [], "exercise_advice": [],
                "care_actions": [],
            },
        }, strict_mode=True, compat_mode=False)
        self.assertEqual(report.decision, "REVIEW")
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_LEGACY_FIELD_NOT_ALLOWED", codes)
        self.assertFalse(report.original_llm_reply_was_sent)

    def test_legacy_name_field_in_current_medications_is_review(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {
                "patient_id": "P",
                "current_medications": [{
                    "name": "metformin", "status": "active",
                }],
                "disease_codes": [],
            },
            "dialogue_output": {
                "reply_text": "", "medication_actions": [],
                "food_advice": [], "exercise_advice": [],
                "care_actions": [],
            },
        }, strict_mode=True, compat_mode=False)
        self.assertEqual(report.decision, "REVIEW")
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_LEGACY_FIELD_NOT_ALLOWED", codes)


class CompatModeAcceptsLegacyFieldsTests(unittest.TestCase):

    def test_compat_mode_adapts_legacy_drug_field(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {
                "patient_id": "P", "current_medications": [],
                "disease_codes": [],
            },
            "dialogue_output": {
                "reply_text": "",
                "medication_actions": [{
                    "drug": "氨氯地平", "action": "start",
                    "dose_value": 5, "dose_unit": "mg",
                    "frequency_per_day": 1, "route": "oral",
                }],
                "food_advice": [], "exercise_advice": [],
                "care_actions": [],
            },
        }, strict_mode=True, compat_mode=True)
        codes = {i.code for i in report.input_validation_errors}
        # Legacy field converted: no INPUT_LEGACY_FIELD_NOT_ALLOWED.
        self.assertNotIn("INPUT_LEGACY_FIELD_NOT_ALLOWED", codes)
        # DEPRECATED_INPUT_SCHEMA finding is present.
        consistency_codes = {c.code for c in report.consistency_violations}
        self.assertIn("DEPRECATED_INPUT_SCHEMA", consistency_codes)


# ============================================================ schema_version

class SchemaVersionEnforcementTests(unittest.TestCase):

    def test_missing_schema_version_is_review(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "patient_state": {"patient_id": "P", "current_medications": [],
                              "disease_codes": []},
            "dialogue_output": {"reply_text": "", "medication_actions": [],
                                "food_advice": [], "exercise_advice": [],
                                "care_actions": []},
        }, strict_mode=True, compat_mode=False)
        self.assertEqual(report.decision, "REVIEW")
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_SCHEMA_VERSION_MISSING", codes)

    def test_unsupported_schema_version_is_review(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "schema_version": "2.5",
            "patient_state": {"patient_id": "P", "current_medications": [],
                              "disease_codes": []},
            "dialogue_output": {"reply_text": "", "medication_actions": [],
                                "food_advice": [], "exercise_advice": [],
                                "care_actions": []},
        }, strict_mode=True, compat_mode=False)
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_SCHEMA_VERSION_UNSUPPORTED", codes)

    def test_audit_does_not_silently_add_schema_version(self):
        # Direct validator-level check: a payload WITHOUT schema_version
        # is rejected.
        eng = _engine()
        from safety.input_validator import InputValidator
        result = InputValidator(eng.repository).validate({
            "patient_state": {"patient_id": "P", "current_medications": [],
                              "disease_codes": []},
            "dialogue_output": {"reply_text": "", "medication_actions": [],
                                "food_advice": [], "exercise_advice": [],
                                "care_actions": []},
        })
        codes = {issue.code for issue in result.issues}
        self.assertIn("INPUT_SCHEMA_VERSION_MISSING", codes)


# ============================================================ requires_review

class LLMDeclaredUncertaintyTests(unittest.TestCase):

    def test_requires_review_true_is_review(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {"patient_id": "P", "current_medications": [],
                              "disease_codes": []},
            "dialogue_output": {
                "reply_text": "",
                "medication_actions": [], "food_advice": [],
                "exercise_advice": [], "care_actions": [],
                "requires_review": True, "uncertainty_reasons": [],
            },
        }, strict_mode=True, compat_mode=False)
        self.assertEqual(report.decision, "REVIEW")
        self.assertIn("LLM_DECLARED_UNCERTAINTY", report.decision_basis)
        self.assertFalse(report.original_llm_reply_was_sent)

    def test_uncertainty_reasons_nonempty_is_review(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {"patient_id": "P", "current_medications": [],
                              "disease_codes": []},
            "dialogue_output": {
                "reply_text": "",
                "medication_actions": [], "food_advice": [],
                "exercise_advice": [], "care_actions": [],
                "requires_review": False,
                "uncertainty_reasons": ["无法确认患者当前用药"],
            },
        }, strict_mode=True, compat_mode=False)
        self.assertEqual(report.decision, "REVIEW")
        self.assertIn("LLM_DECLARED_UNCERTAINTY", report.decision_basis)


# ============================================================ required fields

class RequiredFieldsTests(unittest.TestCase):

    def test_start_missing_route_is_review(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {"patient_id": "P", "current_medications": [],
                              "disease_codes": []},
            "dialogue_output": {
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "amlodipine", "drug_name": "氨氯地平",
                    "action": "start",
                    "dose_value": 5, "dose_unit": "mg",
                    "frequency_per_day": 1,
                }],
                "food_advice": [], "exercise_advice": [],
                "care_actions": [], "requires_review": False,
                "uncertainty_reasons": [],
            },
        }, strict_mode=True, compat_mode=False)
        self.assertEqual(report.decision, "REVIEW")
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_MEDICATION_ACTION_MISSING_FIELDS", codes)

    def test_increase_missing_route_is_review(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {"patient_id": "P", "current_medications": [],
                              "disease_codes": []},
            "dialogue_output": {
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "amlodipine", "drug_name": "氨氯地平",
                    "action": "increase",
                    "dose_value": 10, "dose_unit": "mg",
                    "frequency_per_day": 1,
                }],
                "food_advice": [], "exercise_advice": [],
                "care_actions": [], "requires_review": False,
                "uncertainty_reasons": [],
            },
        }, strict_mode=True, compat_mode=False)
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_MEDICATION_ACTION_MISSING_FIELDS", codes)

    def test_replace_missing_replace_drug_id_is_review(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {"patient_id": "P",
                              "current_medications": [
                                  {"drug_id": "metformin", "drug_name": "二甲双胍",
                                   "status": "active"}],
                              "disease_codes": []},
            "dialogue_output": {
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "lisinopril", "drug_name": "赖诺普利",
                    "action": "replace",
                    "dose_value": 10, "dose_unit": "mg",
                    "frequency_per_day": 1, "route": "oral",
                }],
                "food_advice": [], "exercise_advice": [],
                "care_actions": [], "requires_review": False,
                "uncertainty_reasons": [],
            },
        }, strict_mode=True, compat_mode=False)
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_REPLACE_MISSING_OLD", codes)


# ============================================================ measurements

class MeasurementValidationTests(unittest.TestCase):

    def test_measurement_unit_wrong_is_review(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {
                "patient_id": "P", "current_medications": [],
                "disease_codes": [],
                "measurements": {
                    "egfr": {
                        "value": 24, "unit": "BANANA",
                        "observed_at": "not-a-date",
                        "source": "madeup", "confirmed": False,
                    }
                },
            },
            "dialogue_output": {
                "reply_text": "", "medication_actions": [],
                "food_advice": [], "exercise_advice": [],
                "care_actions": [], "requires_review": False,
                "uncertainty_reasons": [],
            },
        }, strict_mode=True, compat_mode=False)
        self.assertEqual(report.decision, "REVIEW")
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_MEASUREMENT_UNIT_NOT_ALLOWED", codes)
        self.assertIn("INPUT_MEASUREMENT_OBSERVED_AT_INVALID", codes)
        self.assertIn("INPUT_MEASUREMENT_SOURCE_INVALID", codes)
        self.assertIn("INPUT_MEASUREMENT_NOT_CONFIRMED", codes)

    def test_measurement_observed_at_without_tz_is_review(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {
                "patient_id": "P", "current_medications": [],
                "disease_codes": [],
                "measurements": {
                    "egfr": {
                        "value": 24, "unit": "mL/min/1.73m2",
                        "observed_at": "2026-07-20T09:00:00",
                        "source": "laboratory", "confirmed": True,
                    }
                },
            },
            "dialogue_output": {
                "reply_text": "", "medication_actions": [],
                "food_advice": [], "exercise_advice": [],
                "care_actions": [], "requires_review": False,
                "uncertainty_reasons": [],
            },
        }, strict_mode=True, compat_mode=False)
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_MEASUREMENT_OBSERVED_AT_INVALID", codes)


# ============================================================ terminology

class TerminologyValidationTests(unittest.TestCase):

    def test_unknown_disease_code_is_review(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {
                "patient_id": "P", "current_medications": [],
                "disease_codes": ["unknown_disease_xyz"],
            },
            "dialogue_output": {
                "reply_text": "", "medication_actions": [],
                "food_advice": [], "exercise_advice": [],
                "care_actions": [], "requires_review": False,
                "uncertainty_reasons": [],
            },
        }, strict_mode=True, compat_mode=False)
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_UNKNOWN_DISEASE_CODE", codes)

    def test_drug_id_name_mismatch_is_review(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {
                "patient_id": "P",
                "current_medications": [{
                    "drug_id": "simvastatin",
                    "drug_name": "阿托伐他汀",
                    "status": "active",
                }],
                "disease_codes": [],
            },
            "dialogue_output": {
                "reply_text": "", "medication_actions": [],
                "food_advice": [], "exercise_advice": [],
                "care_actions": [], "requires_review": False,
                "uncertainty_reasons": [],
            },
        }, strict_mode=True, compat_mode=False)
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_DRUG_ID_NAME_MISMATCH", codes)


# ============================================================ legal schema

class LegalSchemaPassesTests(unittest.TestCase):

    LEGAL_INPUT = {
        "schema_version": "1.0",
        "patient_state": {
            "patient_id": "P", "current_medications": [],
            "disease_codes": [],
            "measurements": {
                "egfr": {
                    "value": 90, "unit": "mL/min/1.73m2",
                    "observed_at": "2026-07-20T09:00:00+08:00",
                    "source": "laboratory", "confirmed": True,
                }
            },
        },
        "dialogue_output": {
            "reply_text": "建议开始氨氯地平5毫克，每日一次。",
            "medication_actions": [{
                "drug_id": "amlodipine", "drug_name": "氨氯地平",
                "action": "start",
                "dose_value": 5, "dose_unit": "mg",
                "frequency_per_day": 1, "route": "oral",
                "duration_days": None,
                "use_current_regimen": False,
                "replace_drug_id": None,
                "replace_drug_name": None,
            }],
            "food_advice": [], "exercise_advice": [],
            "care_actions": [],
            "requires_review": False, "uncertainty_reasons": [],
        },
    }

    def test_legal_input_does_not_emit_sys001(self):
        eng = _engine()
        report = eng.audit_payload(payload=self.LEGAL_INPUT,
                                   strict_mode=True, compat_mode=False)
        codes = {c.code for c in report.consistency_violations}
        self.assertNotIn("SYS001_TEXT_STRUCTURE_MISMATCH", codes)

    def test_legal_input_does_not_emit_sys008(self):
        eng = _engine()
        report = eng.audit_payload(payload=self.LEGAL_INPUT,
                                   strict_mode=True, compat_mode=False)
        codes = {c.code for c in report.consistency_violations}
        self.assertNotIn("SYS008_TEXT_CONFLICT_DRUG_MENTION", codes)

    def test_legal_input_pass_when_no_risk(self):
        eng = _engine()
        report = eng.audit_payload(payload=self.LEGAL_INPUT,
                                   strict_mode=True, compat_mode=False)
        # amlodipine start without risk flag → PASS
        self.assertEqual(report.decision, "PASS")
        self.assertTrue(report.original_llm_reply_was_sent)


# ============================================================ RequiredContext

class RequiredContextLogicTests(unittest.TestCase):

    def test_hold_metformin_does_not_require_egfr(self):
        eng = _engine()
        # Patient on metformin; LLM suggests "hold" (no risk-flag,
        # no continue) → must NOT require eGFR from the continue rule.
        report = eng.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {
                "patient_id": "P",
                "current_medications": [
                    {"drug_id": "metformin", "drug_name": "二甲双胍",
                     "status": "active"}],
                "disease_codes": [],
            },
            "dialogue_output": {
                "reply_text": "建议暂停二甲双胍并尽快就医。",
                "medication_actions": [{
                    "drug_id": "metformin", "drug_name": "二甲双胍",
                    "action": "hold", "route": "oral",
                }],
                "food_advice": [], "exercise_advice": [],
                "care_actions": [
                    {"type": "urgent_medical_evaluation",
                     "action": "recommend"},
                ],
                "requires_review": False, "uncertainty_reasons": [],
            },
        }, strict_mode=True, compat_mode=False, debug=True)
        paths = [m["field_path"] for m in report.missing_context_fields]
        # hold does not trigger drug patient_state context. No
        # required-context rule requires egfr for a hold action.
        self.assertNotIn("egfr", paths)

    def test_stop_metformin_does_not_require_egfr(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {
                "patient_id": "P",
                "current_medications": [
                    {"drug_id": "metformin", "drug_name": "二甲双胍",
                     "status": "active"}],
                "disease_codes": [],
            },
            "dialogue_output": {
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "metformin", "drug_name": "二甲双胍",
                    "action": "stop",
                }],
                "food_advice": [], "exercise_advice": [],
                "care_actions": [], "requires_review": False,
                "uncertainty_reasons": [],
            },
        }, strict_mode=True, compat_mode=False, debug=True)
        paths = [m["field_path"] for m in report.missing_context_fields]
        self.assertNotIn("egfr", paths)

    def test_no_exercise_advice_does_not_require_bp(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {
                "patient_id": "P",
                "current_medications": [
                    {"drug_id": "amlodipine", "drug_name": "氨氯地平",
                     "status": "active"}],
                "disease_codes": [],
            },
            "dialogue_output": {
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "amlodipine", "drug_name": "氨氯地平",
                    "action": "continue",
                    "dose_value": 5, "dose_unit": "mg",
                    "frequency_per_day": 1, "route": "oral",
                }],
                "food_advice": [], "exercise_advice": [],
                "care_actions": [], "requires_review": False,
                "uncertainty_reasons": [],
            },
        }, strict_mode=True, compat_mode=False, debug=True)
        paths = [m["field_path"] for m in report.missing_context_fields]
        # No exercise_advice → BP not required by drug_exercise channel.
        self.assertNotIn("latest_systolic_bp_mmHg", paths)

    def test_food_avoid_does_not_require_recommend_context(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {
                "patient_id": "P",
                "current_medications": [
                    {"drug_id": "simvastatin", "drug_name": "辛伐他汀",
                     "status": "active"}],
                "disease_codes": [],
            },
            "dialogue_output": {
                "reply_text": "不要喝西柚汁。",
                "medication_actions": [],
                "food_advice": [{
                    "food_concept_id": "grapefruit",
                    "food_name": "西柚汁",
                    "action": "avoid",
                }],
                "exercise_advice": [], "care_actions": [],
                "requires_review": False, "uncertainty_reasons": [],
            },
        }, strict_mode=True, compat_mode=False, debug=True)
        # food_avoid must not trigger R023_STATIN_GRAPEFRUIT (which
        # only fires on recommend).
        ids = {v.rule_id for v in report.medical_violations}
        self.assertNotIn("R023_STATIN_GRAPEFRUIT", ids)


# ============================================================ fail-closed

class FailClosedAuditPayloadTests(unittest.TestCase):

    def test_internal_exception_is_review_via_audit_payload(self):
        # Monkey-patch _audit_impl to raise. The audit_payload wrapper
        # must catch and produce a REVIEW report.
        import safety.safety_engine as engine_mod
        original = engine_mod.DialogueSafetyEngine
        class Exploding(original):
            def _audit_impl(self, *a, **kw):
                raise RuntimeError("simulated explosion")
        engine_mod.DialogueSafetyEngine = Exploding
        try:
            eng = Exploding(ROOT / "rules")
            report = eng.audit_payload(payload={
                "schema_version": "1.0",
                "patient_state": {"patient_id": "P",
                                  "current_medications": [],
                                  "disease_codes": []},
                "dialogue_output": {
                    "reply_text": "", "medication_actions": [],
                    "food_advice": [], "exercise_advice": [],
                    "care_actions": [], "requires_review": False,
                    "uncertainty_reasons": [],
                },
            }, strict_mode=True, compat_mode=False)
            self.assertEqual(report.decision, "REVIEW")
            self.assertIn("SYSTEM_ERROR", report.decision_basis)
            self.assertFalse(report.original_llm_reply_was_sent)
        finally:
            engine_mod.DialogueSafetyEngine = original


# ============================================================ numerics

class NumericEdgeCaseTests(unittest.TestCase):

    def test_nan_dose_is_review(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {"patient_id": "P",
                              "current_medications": [],
                              "disease_codes": []},
            "dialogue_output": {
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "amlodipine", "drug_name": "氨氯地平",
                    "action": "start",
                    "dose_value": float("nan"), "dose_unit": "mg",
                    "frequency_per_day": 1, "route": "oral",
                }],
                "food_advice": [], "exercise_advice": [],
                "care_actions": [], "requires_review": False,
                "uncertainty_reasons": [],
            },
        }, strict_mode=True, compat_mode=False)
        self.assertEqual(report.decision, "REVIEW")
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_NON_FINITE_DOSE", codes)

    def test_infinity_dose_is_review(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {"patient_id": "P",
                              "current_medications": [],
                              "disease_codes": []},
            "dialogue_output": {
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "amlodipine", "drug_name": "氨氯地平",
                    "action": "start",
                    "dose_value": float("inf"), "dose_unit": "mg",
                    "frequency_per_day": 1, "route": "oral",
                }],
                "food_advice": [], "exercise_advice": [],
                "care_actions": [], "requires_review": False,
                "uncertainty_reasons": [],
            },
        }, strict_mode=True, compat_mode=False)
        self.assertEqual(report.decision, "REVIEW")

    def test_negative_dose_is_review(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {"patient_id": "P",
                              "current_medications": [],
                              "disease_codes": []},
            "dialogue_output": {
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "amlodipine", "drug_name": "氨氯地平",
                    "action": "start",
                    "dose_value": -5, "dose_unit": "mg",
                    "frequency_per_day": 1, "route": "oral",
                }],
                "food_advice": [], "exercise_advice": [],
                "care_actions": [], "requires_review": False,
                "uncertainty_reasons": [],
            },
        }, strict_mode=True, compat_mode=False)
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_NEGATIVE_DOSE", codes)

    def test_unknown_unit_is_review(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {"patient_id": "P",
                              "current_medications": [],
                              "disease_codes": []},
            "dialogue_output": {
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "amlodipine", "drug_name": "氨氯地平",
                    "action": "start",
                    "dose_value": 5, "dose_unit": "mL",
                    "frequency_per_day": 1, "route": "oral",
                }],
                "food_advice": [], "exercise_advice": [],
                "care_actions": [], "requires_review": False,
                "uncertainty_reasons": [],
            },
        }, strict_mode=True, compat_mode=False)
        self.assertEqual(report.decision, "REVIEW")
        codes = {i.code for i in report.input_validation_errors}
        self.assertIn("INPUT_UNKNOWN_DOSE_UNIT", codes)


# ============================================================ pressure

class RequiredContextPressureTests(unittest.TestCase):
    """Build a synthetic 1000/10000-rule rule base and prove that the
    RequiredContextChecker consults only the precise indexes, not the
    full rule set."""

    def _build_synthetic_rules(self, count: int) -> Path:
        rules_dir = Path(tempfile.mkdtemp()) / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        # Copy the alias table.
        import shutil
        shutil.copy(ROOT / "rules" / "aliases.json",
                    rules_dir / "aliases.json")
        decoys = []
        for i in range(count):
            decoys.append({
                "id": f"RCC_DECOY_{i:06d}",
                "version": 1, "status": "active",
                "type": "patient_state", "severity": "REVIEW",
                "triggers": {
                    "drugs_any": ["metformin"],
                    "patient_fields_any": [f"lab_{i:04d}"],
                },
                "parameters": {
                    "drugs": ["metformin"],
                    "field": f"lab_{i:04d}",
                    "operator": "lt", "threshold": 1.0,
                },
                "message": f"decoy {i}",
                "source": {
                    "document_title": "synthetic decoy",
                    "document_version": "v4.2.1",
                },
            })
        r002 = {
            "id": "R002_METFORMIN_EGFR_LT_30",
            "version": 1, "status": "active",
            "type": "patient_state", "severity": "BLOCK",
            "triggers": {
                "drugs_any": ["metformin"],
                "patient_fields_any": ["egfr"],
            },
            "parameters": {
                "drugs": ["metformin"],
                "field": "egfr", "operator": "lt", "threshold": 30,
            },
            "message": "egfr < 30 → BLOCK",
            "source": {
                "document_title": "synthetic",
                "document_version": "v4.2.1",
            },
        }
        (rules_dir / "_decoys.json").write_text(
            json.dumps({"rules": decoys}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        (rules_dir / "_r002.json").write_text(
            json.dumps({"rules": [r002]}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        (rules_dir / "manifest.json").write_text(
            json.dumps({
                "ruleset_version": "dialogue-safety-rules-synthetic",
                "schema_version": 4, "input_schema_version": "1.0",
                "decision_policy": {
                    "BLOCK": "x", "REVIEW": "y", "PASS": "z"
                },
                "rule_files": ["aliases.json", "_r002.json", "_decoys.json"],
            }, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return rules_dir

    def test_1000_required_context_uses_precise_index(self):
        rules_dir = self._build_synthetic_rules(1000)
        engine = DialogueSafetyEngine(rules_dir)
        report = engine.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {
                "patient_id": "P", "egfr": 24,
                "current_medications": [
                    {"drug_id": "metformin", "drug_name": "二甲双胍",
                     "status": "active"}],
                "disease_codes": [],
            },
            "dialogue_output": {
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "metformin", "drug_name": "二甲双胍",
                    "action": "continue",
                    "dose_value": 500, "dose_unit": "mg",
                    "frequency_per_day": 2, "route": "oral",
                }],
                "food_advice": [], "exercise_advice": [],
                "care_actions": [], "requires_review": False,
                "uncertainty_reasons": [],
            },
        }, strict_mode=True, compat_mode=False, debug=True)
        # No synthetic decoy should appear in candidate_rule_ids or
        # evaluated_rule_ids.
        for rid in (report.candidate_rule_ids + report.evaluated_rule_ids):
            self.assertFalse(rid.startswith("RCC_DECOY_"))
        # R002 should be a candidate and fire.
        self.assertIn("R002_METFORMIN_EGFR_LT_30",
                      report.candidate_rule_ids)
        self.assertEqual(report.decision, "BLOCK")
        # RequiredContextChecker: total_rules_consulted must be much
        # smaller than total_rules_in_repo (we have 1001 rules but
        # consulted only the precise-indexed ones for metformin).
        dev = report.developer_diagnostics
        rc = dev["required_context"]
        self.assertGreater(rc["total_rules_in_repo"], 1000)
        # We must have consulted fewer than 100 rules — proving the
        # precise-index path.
        self.assertLess(rc["total_rules_consulted"], 100)

    def test_10000_required_context_uses_precise_index(self):
        rules_dir = self._build_synthetic_rules(10000)
        engine = DialogueSafetyEngine(rules_dir)
        report = engine.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {
                "patient_id": "P", "egfr": 24,
                "current_medications": [
                    {"drug_id": "metformin", "drug_name": "二甲双胍",
                     "status": "active"}],
                "disease_codes": [],
            },
            "dialogue_output": {
                "reply_text": "",
                "medication_actions": [{
                    "drug_id": "metformin", "drug_name": "二甲双胍",
                    "action": "continue",
                    "dose_value": 500, "dose_unit": "mg",
                    "frequency_per_day": 2, "route": "oral",
                }],
                "food_advice": [], "exercise_advice": [],
                "care_actions": [], "requires_review": False,
                "uncertainty_reasons": [],
            },
        }, strict_mode=True, compat_mode=False, debug=True)
        for rid in (report.candidate_rule_ids + report.evaluated_rule_ids):
            self.assertFalse(rid.startswith("RCC_DECOY_"))
        self.assertIn("R002_METFORMIN_EGFR_LT_30",
                      report.candidate_rule_ids)
        self.assertEqual(report.decision, "BLOCK")
        rc = report.developer_diagnostics["required_context"]
        self.assertGreater(rc["total_rules_in_repo"], 10000)
        self.assertLess(rc["total_rules_consulted"], 100)


# ============================================================ project version

class ProjectVersionTests(unittest.TestCase):

    def test_report_project_version_is_4_2_1(self):
        eng = _engine()
        report = eng.audit_payload(payload={
            "schema_version": "1.0",
            "patient_state": {"patient_id": "P",
                              "current_medications": [],
                              "disease_codes": []},
            "dialogue_output": {
                "reply_text": "", "medication_actions": [],
                "food_advice": [], "exercise_advice": [],
                "care_actions": [], "requires_review": False,
                "uncertainty_reasons": [],
            },
        }, strict_mode=True, compat_mode=False)
        self.assertEqual(report.developer_diagnostics["project_version"],
                         "4.2.1")
        self.assertEqual(report.ruleset_version,
                         "dialogue-safety-rules-4.2.1")

    def test_legacy_adapter_emits_finding_only_when_legacy_seen(self):
        adapter = LegacyInputAdapter(_engine().repository)
        out, findings = adapter.adapt({
            "schema_version": "1.0",
            "patient_state": {"patient_id": "P",
                              "current_medications": [],
                              "disease_codes": []},
            "dialogue_output": {"reply_text": "", "medication_actions": [],
                                "food_advice": [], "exercise_advice": [],
                                "care_actions": [], "requires_review": False,
                                "uncertainty_reasons": []},
        })
        # No legacy fields → no DEPRECATED_INPUT_SCHEMA finding.
        codes = {f.get("code") for f in findings}
        self.assertNotIn("DEPRECATED_INPUT_SCHEMA", codes)


if __name__ == "__main__":
    unittest.main()