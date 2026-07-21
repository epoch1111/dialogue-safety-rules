from __future__ import annotations

import json
import unittest
from pathlib import Path

from dialogue_agent import PresetDialogueAgent
from orchestrator import DialogueOrchestrator
from safety import DialogueSafetyEngine


ROOT = Path(__file__).resolve().parents[1]


class DialogueSafetyDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patients = json.loads(
            (ROOT / "data" / "patient_cases.json").read_text(encoding="utf-8")
        )
        cls.orchestrator = DialogueOrchestrator(
            dialogue_agent=PresetDialogueAgent(
                ROOT / "data" / "llm_presets.json"
            ),
            safety_engine=DialogueSafetyEngine(
                ROOT / "rules"
            ),
        )

    def run_case(self, patient_key, preset_name):
        return self.orchestrator.handle_message(
            user_message="测试消息",
            patient_state=self.patients[patient_key],
            preset_name=preset_name,
        )

    def test_unsafe_output_is_blocked(self):
        result = self.run_case("unsafe_case", "unsafe_output")
        self.assertEqual(result["audit"]["decision"], "BLOCK")
        self.assertFalse(result["original_llm_reply_was_sent"])

        ids = {
            item["rule_id"]
            for item in result["audit"]["violations"]
        }
        self.assertIn("R001_AMLODIPINE_MAX_DAILY_DOSE", ids)
        self.assertIn("R002_METFORMIN_EGFR_LT_30", ids)
        self.assertIn("R003_SIMVASTATIN_CLARITHROMYCIN", ids)
        self.assertIn("R004_LISINOPRIL_POTASSIUM_SALT", ids)
        self.assertIn("R005_INSULIN_LOW_GLUCOSE_VIGOROUS_EXERCISE", ids)

    def test_review_output_is_not_sent(self):
        result = self.run_case("review_only_case", "review_only_output")
        self.assertEqual(result["audit"]["decision"], "REVIEW")
        self.assertFalse(result["original_llm_reply_was_sent"])

    def test_safe_output_is_sent(self):
        result = self.run_case("safe_case", "safe_output")
        self.assertEqual(result["audit"]["decision"], "PASS")
        self.assertTrue(result["original_llm_reply_was_sent"])
        self.assertEqual(
            result["sent_to_patient"],
            result["llm_draft"]["reply_text"],
        )


if __name__ == "__main__":
    unittest.main()
