"""Tests covering engine-wide invariants."""

from __future__ import annotations

import unittest
from pathlib import Path

from safety import DialogueSafetyEngine


ROOT = Path(__file__).resolve().parents[1]


class MatcherBuiltOnceTests(unittest.TestCase):
    def test_matcher_is_built_in_init_not_audit(self):
        engine = DialogueSafetyEngine(ROOT / "rules")
        # The matcher must be present after __init__.
        matcher_ref = engine._matcher
        # Run multiple audits; matcher object identity must NOT change.
        for _ in range(3):
            engine.audit(
                patient_state={"patient_id": "P", "current_medications": []},
                dialogue_output={"reply_text": "", "medication_actions": []},
            )
        self.assertIs(engine._matcher, matcher_ref)

    def test_matcher_drug_count_is_nonzero(self):
        engine = DialogueSafetyEngine(ROOT / "rules")
        self.assertGreater(engine._matcher.drug_count, 0)


class DrugContextTests(unittest.TestCase):
    def setUp(self):
        self.engine = DialogueSafetyEngine(ROOT / "rules")

    def test_current_drugs_canonicalized(self):
        report = self.engine.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [
                    {"name": "氨氯地平", "status": "active"},
                    {"name": "辛伐他汀", "status": "active"},
                ],
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        self.assertIn("amlodipine", report.current_drugs)
        self.assertIn("simvastatin", report.current_drugs)

    def test_stop_removes_from_resulting(self):
        report = self.engine.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [
                    {"name": "氨氯地平", "status": "active"}
                ],
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "氨氯地平", "action": "stop"}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        self.assertIn("amlodipine", report.current_drugs)
        self.assertNotIn("amlodipine", report.resulting_drugs)

    def test_continue_keeps_in_resulting(self):
        report = self.engine.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [
                    {"name": "氨氯地平", "status": "active"}
                ],
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "氨氯地平", "action": "continue"}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        self.assertIn("amlodipine", report.resulting_drugs)


class TextDoseDrugsDontPolluteResultingTests(unittest.TestCase):
    """v4.1: drugs found in reply_text must not enter resulting_drugs."""

    def setUp(self):
        self.engine = DialogueSafetyEngine(ROOT / "rules")

    def test_text_drug_not_in_resulting(self):
        # Patient is drug-naive; reply text alone says "take amlodipine".
        report = self.engine.audit(
            patient_state={"patient_id": "P", "current_medications": []},
            dialogue_output={
                "reply_text": "把氨氯地平加到20毫克每日一次。",
                "medication_actions": [],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        # Drug is in text_mentioned_drugs and text_dose_drugs.
        self.assertIn("amlodipine", report.text_mentioned_drugs)
        self.assertIn("amlodipine", report.text_dose_drugs)
        # But not in resulting_drugs.
        self.assertNotIn("amlodipine", report.resulting_drugs)


class AvoidStartKeepsCurrentTests(unittest.TestCase):
    """v4.1: avoid_start does NOT remove the current drug from resulting."""

    def setUp(self):
        self.engine = DialogueSafetyEngine(ROOT / "rules")

    def test_avoid_start_keeps_current_drug(self):
        # Patient on simvastatin. LLM adds avoid_start for the same drug.
        report = self.engine.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [
                    {"name": "辛伐他汀", "status": "active"},
                ],
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "辛伐他汀", "action": "avoid_start"}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        # Drug remains in resulting.
        self.assertIn("simvastatin", report.resulting_drugs)
        self.assertIn("simvastatin", report.current_drugs)


class ReplaceRemovesOldDrugTests(unittest.TestCase):
    """v4.1: replace action removes the replace_drug from resulting."""

    def setUp(self):
        self.engine = DialogueSafetyEngine(ROOT / "rules")

    def test_replace_with_target(self):
        report = self.engine.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [
                    {"name": "辛伐他汀", "status": "active"},
                ],
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "阿托伐他汀", "action": "replace", "replace_drug": "辛伐他汀"}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        # Old drug removed, new drug present.
        self.assertNotIn("simvastatin", report.resulting_drugs)
        self.assertIn("atorvastatin", report.resulting_drugs)


if __name__ == "__main__":
    unittest.main()