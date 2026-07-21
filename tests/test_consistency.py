"""Unit tests for SYS001 / SYS002 / SYS003 consistency violations."""

from __future__ import annotations

import unittest
from pathlib import Path

from safety import DialogueSafetyEngine
from safety.consistency_checker import ConsistencyChecker
from safety.models import NormalizedDraft
from safety.normalizer import normalize_draft


ROOT = Path(__file__).resolve().parents[1]


class ConsistencyCheckerUnitTests(unittest.TestCase):
    def setUp(self):
        self.checker = ConsistencyChecker()

    def test_sys002_missing_dose(self):
        draft = normalize_draft({
            "medication_actions": [
                {"drug": "amlodipine", "action": "increase"}
            ]
        })
        v = self.checker.check(draft, [], [], [], drug_canonicalizer=lambda x: x)
        codes = {c.code for c in v}
        self.assertIn("SYS002_MISSING_MEDICATION_PARAMETERS", codes)

    def test_sys003_all_conflict(self):
        draft = normalize_draft({
            "reply_text": "可以喝西柚汁。",
            "medication_actions": [],
            "food_advice": [
                {"food": "西柚汁", "action": "avoid"}
            ]
        })
        v = self.checker.check(draft, [], [], [], drug_canonicalizer=lambda x: x)
        codes = {c.code for c in v}
        self.assertIn("SYS003_TEXT_STRUCTURE_CONFLICT", codes)

    def test_sys003_partial_conflict_not_fired(self):
        # Reply says avoid, structured has one avoid + one recommend — not
        # all items conflict, so SYS003 must NOT fire.
        draft = normalize_draft({
            "reply_text": "请避免剧烈运动。",
            "medication_actions": [],
            "food_advice": [],
            "exercise_advice": [
                {"activity": "跑步", "intensity": "vigorous", "action": "avoid"},
                {"activity": "游泳", "intensity": "moderate", "action": "recommend"}
            ]
        })
        v = self.checker.check(draft, [], [], [], drug_canonicalizer=lambda x: x)
        codes = {c.code for c in v}
        self.assertNotIn("SYS003_TEXT_STRUCTURE_CONFLICT", codes)

    def test_sys001_text_mentions_drug_no_action(self):
        # reply_text mentions 氨氯地平 but medication_actions is empty
        draft = normalize_draft({
            "reply_text": "建议把氨氯地平停掉。",
            "medication_actions": [],
            "food_advice": [],
            "exercise_advice": []
        })
        v = self.checker.check(
            draft, [], ["amlodipine"], ["amlodipine"],
            drug_canonicalizer=lambda x: "amlodipine" if "氨氯地平" in (x or "") else (x or "").lower(),
        )
        codes = {c.code for c in v}
        self.assertIn("SYS001_TEXT_STRUCTURE_MISMATCH", codes)


class ConsistencyAuditIntegrationTests(unittest.TestCase):
    """Verify SYS codes surface in the engine AuditReport."""

    @classmethod
    def setUpClass(cls):
        cls.engine = DialogueSafetyEngine(ROOT / "rules")

    def test_sys002_in_audit(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "current_medications": []},
            dialogue_output={
                "reply_text": "氨氯地平加量。",
                "medication_actions": [
                    {"drug": "氨氯地平", "action": "increase"}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        codes = {c.code for c in report.consistency_violations}
        self.assertIn("SYS002_MISSING_MEDICATION_PARAMETERS", codes)


class Sys004Tests(unittest.TestCase):
    """SYS004: unknown enum values must be flagged, not silently defaulted."""

    def setUp(self):
        from safety import DialogueSafetyEngine
        from pathlib import Path
        self.engine = DialogueSafetyEngine(Path(__file__).resolve().parents[1] / "rules")

    def test_invalid_food_action(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [],
                "food_advice": [{"food": "x", "action": "aviod", "instruction": ""}],
                "exercise_advice": [],
            },
        )
        codes = {c.code for c in report.consistency_violations}
        self.assertIn("SYS004_INVALID_STRUCTURED_ENUM", codes)

    def test_invalid_exercise_action(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [],
                "food_advice": [],
                "exercise_advice": [
                    {"activity": "x", "intensity": "moderate", "action": "recomend", "instruction": ""}
                ],
            },
        )
        codes = {c.code for c in report.consistency_violations}
        self.assertIn("SYS004_INVALID_STRUCTURED_ENUM", codes)

    def test_invalid_intensity(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [],
                "food_advice": [],
                "exercise_advice": [
                    {"activity": "x", "intensity": "extreme", "action": "recommend", "instruction": ""}
                ],
            },
        )
        codes = {c.code for c in report.consistency_violations}
        self.assertIn("SYS004_INVALID_STRUCTURED_ENUM", codes)

    def test_invalid_medication_action(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "x", "action": "starrt", "dose_value": 5, "dose_unit": "mg", "frequency_per_day": 1}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        codes = {c.code for c in report.consistency_violations}
        self.assertIn("SYS004_INVALID_STRUCTURED_ENUM", codes)


class Sys005Tests(unittest.TestCase):
    """SYS005: replace without replace_drug must be flagged."""

    def setUp(self):
        from safety import DialogueSafetyEngine
        from pathlib import Path
        self.engine = DialogueSafetyEngine(Path(__file__).resolve().parents[1] / "rules")

    def test_replace_without_target(self):
        report = self.engine.audit(
            patient_state={"patient_id": "P", "current_medications": []},
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "阿托伐他汀", "action": "replace"}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        codes = {c.code for c in report.consistency_violations}
        self.assertIn("SYS005_MISSING_REPLACE_TARGET", codes)
        self.assertEqual(report.decision, "REVIEW")


class Sys006Tests(unittest.TestCase):
    """SYS006: unknown medication status must be flagged."""

    def setUp(self):
        from safety import DialogueSafetyEngine
        from pathlib import Path
        self.engine = DialogueSafetyEngine(Path(__file__).resolve().parents[1] / "rules")

    def test_unknown_status(self):
        report = self.engine.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [
                    {"name": "辛伐他汀", "status": "zombie"}
                ],
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        codes = {c.code for c in report.consistency_violations}
        self.assertIn("SYS006_UNKNOWN_MEDICATION_STATUS", codes)
        self.assertEqual(report.decision, "REVIEW")

    def test_canonical_status_does_not_fire(self):
        # "held" is a canonical inactive synonym, no SYS006.
        report = self.engine.audit(
            patient_state={
                "patient_id": "P",
                "current_medications": [
                    {"name": "辛伐他汀", "status": "held"}
                ],
            },
            dialogue_output={
                "reply_text": "",
                "medication_actions": [],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        codes = {c.code for c in report.consistency_violations}
        self.assertNotIn("SYS006_UNKNOWN_MEDICATION_STATUS", codes)


if __name__ == "__main__":
    unittest.main()