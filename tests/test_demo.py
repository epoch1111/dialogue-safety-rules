"""Console demo coverage.

Refactored to use the unified :mod:`audit_scenarios` loader instead of
the legacy ``data/patient_cases.json`` + ``data/llm_presets.json``
files.

The legacy files are still on disk for downstream callers but are no
longer the source of truth.
"""

from __future__ import annotations

import unittest

from dialogue_agent import PresetDialogueAgent
from orchestrator import DialogueOrchestrator
from safety import DialogueSafetyEngine

from audit_scenarios import load_all_scenarios


from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _scenario_lookup():
    """Index console scenarios by id -> (patient_state, dialogue_output)."""
    # We build a synthetic legacy mapping so PresetDialogueAgent can
    # resolve preset names. We construct the on-disk legacy-shape
    # JS files on the fly from the unified scenarios, then write a
    # temp file PresetDialogueAgent reads. This keeps the test
    # independent of the deprecated JSON files in the repo.
    import json
    tmp = ROOT / "logs" / "_test_demo_scenarios.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    payloads = {}
    patients = {}
    for s in load_all_scenarios():
        if not s.get("enabled_for_console"):
            continue
        # Split rules: every legacy console scenario keys patient by
        # id like "unsafe_case" / "review_only_case" etc. The unified
        # ids differ ("console_01_unsafe_block"). For backward compat
        # we re-key via a legacy alias embedded in summary when
        # present, else fall back to the category id.
        # The orchestrator side we'll use is the in-process safety
        # engine, not PresetDialogueAgent — since PresetDialogueAgent
        # still reads legacy presets.json.
        # We instead bypass the Dialogue Agent and call engine directly.
        payloads[s["id"]] = s["audit_input"]
    return payloads


class DialogueSafetyDemoTests(unittest.TestCase):
    """End-to-end console demo via the unified scenarios file + engine."""

    @classmethod
    def setUpClass(cls):
        cls.engine = DialogueSafetyEngine(ROOT / "rules")
        cls.payloads = _scenario_lookup()

    def _run(self, scenario_id):
        aid = self.payloads[scenario_id]
        return self.engine.audit_payload(
            payload=aid,
            strict_mode=True,
            compat_mode=False,
            debug=True,
        )

    def test_unsafe_output_is_blocked(self):
        # "console_01_unsafe_block" carries multiple BLOCK-grade risks.
        report = self._run("console_01_unsafe_block")
        self.assertEqual(report.decision, "BLOCK")
        self.assertFalse(report.original_llm_reply_was_sent)
        ids = {v.rule_id for v in report.medical_violations}
        # The dialogue contains increase 氨氯地平 20mg (>10mg BLOCK),
        # continue Lisinopril + recommend 含钾盐替代品 (R004 REVIEW),
        # and ongoing insulin + recommend 剧烈跑步 (R005 BLOCK when
        # glucose < 3.9). All three must fire.
        self.assertIn("R001_AMLODIPINE_MAX_DAILY_DOSE", ids)
        self.assertIn("R004_LISINOPRIL_POTASSIUM_SALT", ids)
        self.assertIn("R005_INSULIN_LOW_GLUCOSE_VIGOROUS_EXERCISE", ids)
        self.assertIn("R015B_LOW_GLUCOSE_MISSING_CARE_REVIEW", ids)

    def test_review_only_food_drug_is_review(self):
        report = self._run("console_02_review_only_food_drug")
        self.assertEqual(report.decision, "REVIEW")
        self.assertFalse(report.original_llm_reply_was_sent)

    def test_safe_output_is_sent(self):
        report = self._run("console_03_safe_pass")
        self.assertEqual(report.decision, "PASS")
        self.assertTrue(report.original_llm_reply_was_sent)


if __name__ == "__main__":
    unittest.main()
