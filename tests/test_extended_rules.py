"""Tests migrated from v3 -> v4 semantics.

Original tests for R002/R003/R004/R005/etc are preserved as
class+method names. The assertions are updated to v4 expectations.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from safety import DialogueSafetyEngine


ROOT = Path(__file__).resolve().parents[1]


class ExtendedDoseRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = DialogueSafetyEngine(ROOT / "rules")

    def test_colchicine_over_max_blocks(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "秋水仙碱", "action": "start",
                     "dose_value": 1, "dose_unit": "mg", "frequency_per_day": 3}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R006_COLCHICINE_MAX_DAILY_DOSE", ids)
        self.assertEqual(report.decision, "BLOCK")

    def test_allopurinol_over_max_blocks(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "别嘌醇", "action": "start",
                     "dose_value": 300, "dose_unit": "mg", "frequency_per_day": 3}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R007_ALLOPURINOL_MAX_DAILY_DOSE", ids)

    def test_metformin_over_max_blocks(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "egfr": 80, "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "二甲双胍", "action": "start",
                     "dose_value": 1000, "dose_unit": "mg", "frequency_per_day": 3}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R008_METFORMIN_MAX_DAILY_DOSE", ids)

    def test_ibuprofen_over_max_reviews(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "布洛芬", "action": "start",
                     "dose_value": 600, "dose_unit": "mg", "frequency_per_day": 3}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R009_IBUPROFEN_MAX_DAILY_DOSE", ids)
        self.assertEqual(report.decision, "REVIEW")


class ExtendedPatientConditionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = DialogueSafetyEngine(ROOT / "rules")

    def test_metformin_egfr_30_to_45_reviews(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "egfr": 42, "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "二甲双胍", "action": "continue",
                     "dose_value": 500, "dose_unit": "mg", "frequency_per_day": 3}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R010_METFORMIN_EGFR_30_TO_45", ids)
        self.assertEqual(report.decision, "REVIEW")

    def test_colchicine_egfr_low_blocks(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "egfr": 22, "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "秋水仙碱", "action": "continue",
                     "dose_value": 0.5, "dose_unit": "mg", "frequency_per_day": 2}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R011_COLCHICINE_EGFR_LT_30", ids)

    def test_febuxostat_with_chd_blocks(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "has_chd": 1, "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "非布司他", "action": "start",
                     "dose_value": 40, "dose_unit": "mg", "frequency_per_day": 1}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R012_FEBUXOSTAT_CORONARY_HEART_DISEASE", ids)

    def test_benzbromarone_with_kidney_stone_blocks(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "kidney_stone_history": 1, "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "苯溴马隆", "action": "start",
                     "dose_value": 50, "dose_unit": "mg", "frequency_per_day": 1}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R013_BENZBROMARONE_KIDNEY_STONE_HISTORY", ids)

    def test_lisinopril_high_potassium_blocks(self):
        # Direct patient_state rule on lisinopril + K+>5.5 -> BLOCK
        report = self.engine.audit(
            patient_state={"patient_id": "P", "serum_potassium_mmol_l": 5.7,
                            "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "赖诺普利", "action": "continue",
                     "dose_value": 10, "dose_unit": "mg", "frequency_per_day": 1}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R014A_HYPERKALEMIA_CONTINUE_ACEI_BLOCK", ids)

    def test_spironolactone_high_potassium_blocks(self):
        # v4: hyperkalemia + continue spironolactone -> R020A
        report = self.engine.audit(
            patient_state={"patient_id": "P", "serum_potassium_mmol_l": 5.3,
                            "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "螺内酯", "action": "continue",
                     "dose_value": 20, "dose_unit": "mg", "frequency_per_day": 1}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R020A_HYPERKALEMIA_CONTINUE_SPIRONOLACTONE_BLOCK", ids)

    def test_hypertensive_emergency_blocks(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "latest_systolic_bp_mmHg": 195,
                            "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "氨氯地平", "action": "increase",
                     "dose_value": 5, "dose_unit": "mg", "frequency_per_day": 1}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R016A_HIGH_BP_SELF_INCREASE_BLOCK", ids)


class ExtendedDrugDrugTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = DialogueSafetyEngine(ROOT / "rules")

    def test_atorvastatin_clarithromycin_reviews(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "阿托伐他汀", "action": "continue",
                     "dose_value": 20, "dose_unit": "mg", "frequency_per_day": 1},
                    {"drug": "克拉霉素", "action": "start",
                     "dose_value": 500, "dose_unit": "mg", "frequency_per_day": 2}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R017_ATORVASTATIN_CLARITHROMYCIN", ids)

    def test_colchicine_clarithromycin_blocks(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "秋水仙碱", "action": "continue",
                     "dose_value": 0.5, "dose_unit": "mg", "frequency_per_day": 2},
                    {"drug": "克拉霉素", "action": "start",
                     "dose_value": 500, "dose_unit": "mg", "frequency_per_day": 2}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R018_COLCHICINE_CLARITHROMYCIN", ids)
        self.assertEqual(report.decision, "BLOCK")

    def test_nsaid_acei_reviews(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "布洛芬", "action": "start",
                     "dose_value": 200, "dose_unit": "mg", "frequency_per_day": 3},
                    {"drug": "赖诺普利", "action": "continue",
                     "dose_value": 10, "dose_unit": "mg", "frequency_per_day": 1}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        # v4.2.0: R019 was split into explicit per-NSAID rules.
        self.assertIn("R019_IBUPROFEN_LISINOPRIL", ids)


class ExtendedDrugFoodTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = DialogueSafetyEngine(ROOT / "rules")

    def test_allopurinol_high_purine_food_reviews(self):
        # v4: depends on disease risk, not drug. Use a patient with
        # disease_codes: hyperuricemia_gout.
        report = self.engine.audit(
            patient_state={"patient_id": "P", "disease_codes": ["hyperuricemia_gout"],
                            "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "别嘌醇", "action": "continue",
                     "dose_value": 100, "dose_unit": "mg", "frequency_per_day": 1}
                ],
                "food_advice": [
                    {"food": "猪肝", "action": "recommend", "instruction": "每周一次"},
                    {"food": "老火鸡汤", "action": "recommend", "instruction": "每天一碗"}
                ],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R022_HYPERURICEMIA_FOOD_AVOID", ids)

    def test_statin_grapefruit_reviews(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "辛伐他汀", "action": "continue",
                     "dose_value": 20, "dose_unit": "mg", "frequency_per_day": 1}
                ],
                "food_advice": [
                    {"food": "西柚汁", "action": "recommend", "instruction": "每天一杯"}
                ],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R023_STATIN_GRAPEFRUIT", ids)

    def test_high_lipid_saturated_fat_reviews(self):
        # v4: depends on dyslipidemia risk
        report = self.engine.audit(
            patient_state={"patient_id": "P", "disease_codes": ["dyslipidemia"],
                            "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "阿托伐他汀", "action": "continue",
                     "dose_value": 20, "dose_unit": "mg", "frequency_per_day": 1}
                ],
                "food_advice": [
                    {"food": "五花肉", "action": "recommend", "instruction": "每天吃"},
                    {"food": "动物油", "action": "recommend", "instruction": "炒菜用"}
                ],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R024_DYSLIPIDEMIA_FOOD_AVOID", ids)


class ExtendedDrugExerciseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = DialogueSafetyEngine(ROOT / "rules")

    def test_insulin_high_glucose_vigorous_reviews(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "latest_glucose_mmol_l": 15.5,
                            "current_medications": [
                                {"name": "甘精胰岛素", "status": "active"}
                            ]},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [],
                "food_advice": [],
                "exercise_advice": [
                    {"activity": "跑步", "intensity": "vigorous", "action": "recommend"}
                ],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R025_INSULIN_GLUCOSE_HIGH_VIGOROUS", ids)

    def test_hypertension_vigorous_blocks(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "latest_systolic_bp_mmHg": 170,
                            "current_medications": [
                                {"name": "氨氯地平", "status": "active"}
                            ]},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [],
                "food_advice": [],
                "exercise_advice": [
                    {"activity": "跑步", "intensity": "vigorous", "action": "recommend"}
                ],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R026_HYPERTENSION_VIGOROUS_EXERCISE", ids)

    def test_gout_acute_flare_vigorous_blocks(self):
        # v4: depends on risk_flag acute_gout_flare, NOT on drug.
        report = self.engine.audit(
            patient_state={"patient_id": "P", "gout_acute_flare": 1,
                            "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [],
                "food_advice": [],
                "exercise_advice": [
                    {"activity": "马拉松", "intensity": "vigorous", "action": "recommend"}
                ],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R021_GOUT_ACUTE_VIGOROUS_BLOCK", ids)


class NegativeRecallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = DialogueSafetyEngine(ROOT / "rules")

    def test_safe_gout_swimming_passes(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "别嘌醇", "action": "continue",
                     "dose_value": 100, "dose_unit": "mg", "frequency_per_day": 1}
                ],
                "food_advice": [
                    {"food": "低脂牛奶", "action": "recommend", "instruction": "每天一杯"}
                ],
                "exercise_advice": [
                    {"activity": "游泳", "intensity": "moderate", "action": "recommend"}
                ],
                "care_actions": [],
            },
        )
        self.assertEqual(report.decision, "PASS")

    def test_unrelated_drug_recalls_no_new_rules(self):
        from safety.candidate_selector import select_candidate_rule_ids
        from safety.models import DrugContext
        repo = DialogueSafetyEngine(ROOT / "rules").repository
        drug_ctx = DrugContext(
            current_drugs=[], mentioned_drugs=[], recommended_drugs=[],
            resulting_drugs=["aspirin"],
        )
        result = select_candidate_rule_ids(
            repo, drug_ctx, [], [], []
        )
        gout_rules = {rid for rid in result.candidate_rule_ids if "GOUT" in rid or "STATIN" in rid or "COLCHICINE" in rid}
        self.assertEqual(gout_rules, set())

    def test_safe_aspirin_low_dose_passes(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "阿司匹林", "action": "continue",
                     "dose_value": 100, "dose_unit": "mg", "frequency_per_day": 1}
                ],
                "food_advice": [],
                "exercise_advice": [
                    {"activity": "快走", "intensity": "moderate", "action": "recommend"}
                ],
                "care_actions": [],
            },
        )
        self.assertEqual(report.decision, "PASS")


class ScaledRuleLoadTests(unittest.TestCase):
    """Confirm the loader handles many rules correctly and indexes scale."""

    def test_repository_reports_count(self):
        engine = DialogueSafetyEngine(ROOT / "rules")
        info = engine.repository.describe_indexes()
        self.assertGreaterEqual(info["rules"], 30)
        self.assertGreater(info["drug_keys"], 10)
        self.assertGreater(info["keyword_keys"], 20)
        # v4.1.1: ``field_keys`` now counts only TRUE field-only rules
        # (no drug binding). The demo rules are all ``patient_state``
        # rules, which live under ``drug_field_keys`` instead of
        # ``field_keys``. We assert BOTH indexes are healthy.
        # 1. Every demo rule that uses a patient field MUST live in the
        #    composite drug_field_index, NOT in field_keys.
        self.assertGreater(info["drug_field_keys"], 5)
        # 2. ``field_keys`` is allowed to be zero when no rule is pure
        #    field-only (true in the demo rule base). If non-zero, every
        #    entry MUST be a rule that does not bind a drug.
        self.assertEqual(
            info["field_keys"], 0,
            "demo rules are all patient_state, which MUST NOT enter "
            "field_only_rule_index",
        )


if __name__ == "__main__":
    unittest.main()