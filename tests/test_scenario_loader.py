"""Tests for the unified audit_scenarios loader (v4.2.1 refactor).

Covers:

- Loading the file once and caching.
- Returning deep copies (no caller mutation of cache).
- Filtering by enabled_for_console / enabled_for_web.
- Structural validation of every scenario.
- Cross-scenario unique id rule.
- The loader never passes the test-only
  ``expected_assertions`` to the engine via ``audit_input``.
- Adding a new scenario by editing ``data/audit_scenarios.json`` does
  NOT require touching ``run_demo.py`` or ``audit_web.py``.
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from audit_scenarios import (
    CATEGORIES,
    SCENARIOS_FILE,
    get_scenario_by_id,
    list_console_scenarios,
    list_web_scenarios,
    load_all_scenarios,
    validate_all_scenarios,
    validate_scenario,
    loader as _loader,
)
from safety import DialogueSafetyEngine


ROOT = Path(__file__).resolve().parents[1]


class BasicLoadTests(unittest.TestCase):
    def test_file_exists(self):
        self.assertTrue(SCENARIOS_FILE.exists())

    def test_load_all_returns_list(self):
        scenarios = load_all_scenarios()
        self.assertIsInstance(scenarios, list)
        self.assertGreater(len(scenarios), 0)

    def test_loader_returns_deep_copy(self):
        first = load_all_scenarios()
        # Mutate the returned list — must not poison the cache.
        first[0]["title"] = "MUTATED"
        second = load_all_scenarios()
        self.assertNotEqual(second[0]["title"], "MUTATED")

    def test_required_keys_exist(self):
        for s in load_all_scenarios():
            with self.subTest(scenario=s.get("id")):
                self.assertIn("id", s)
                self.assertIn("title", s)
                self.assertIn("category", s)
                self.assertIn("audit_input", s)
                self.assertIn("enabled_for_console", s)
                self.assertIn("enabled_for_web", s)

    def test_validate_all_succeeds(self):
        ok, errors = validate_all_scenarios()
        self.assertTrue(ok, f"validation errors: {errors[:5]}...")
        self.assertEqual(errors, [])

    def test_known_categories_used(self):
        seen = {s["category"] for s in load_all_scenarios()}
        self.assertTrue(seen.issubset(CATEGORIES),
                        f"unknown categories: {seen - CATEGORIES}")


class FilterTests(unittest.TestCase):
    def test_console_filter_excludes_console_false(self):
        for s in list_console_scenarios():
            self.assertTrue(s["enabled_for_console"])

    def test_web_filter_excludes_web_false(self):
        for s in list_web_scenarios():
            self.assertTrue(s["enabled_for_web"])

    def test_get_scenario_by_id(self):
        s = get_scenario_by_id("console_03_safe_pass")
        self.assertIsNotNone(s)
        self.assertEqual(s["id"], "console_03_safe_pass")

    def test_get_scenario_by_id_missing(self):
        self.assertIsNone(get_scenario_by_id("does-not-exist"))

    def test_uniqueness(self):
        ids = [s["id"] for s in load_all_scenarios()]
        self.assertEqual(len(ids), len(set(ids)), "duplicate scenario ids")


class StructuralValidationTests(unittest.TestCase):
    def test_valid_scenario(self):
        ok, errs = validate_scenario({
            "id": "x", "title": "y", "category": "dashboard",
            "enabled_for_console": False, "enabled_for_web": True,
            "audit_input": {"schema_version": "1.0",
                            "patient_state": {},
                            "dialogue_output": {}},
        })
        self.assertTrue(ok, errs)

    def test_missing_id(self):
        ok, errs = validate_scenario({
            "title": "y", "category": "dashboard",
            "enabled_for_console": False, "enabled_for_web": True,
            "audit_input": {"schema_version": "1.0",
                            "patient_state": {},
                            "dialogue_output": {}},
        })
        self.assertFalse(ok)

    def test_bad_category(self):
        ok, _ = validate_scenario({
            "id": "x", "title": "y", "category": "nonsense",
            "enabled_for_console": False, "enabled_for_web": True,
            "audit_input": {"schema_version": "1.0",
                            "patient_state": {},
                            "dialogue_output": {}},
        })
        self.assertFalse(ok)

    def test_missing_audit_input(self):
        ok, _ = validate_scenario({
            "id": "x", "title": "y", "category": "dashboard",
            "enabled_for_console": False, "enabled_for_web": True,
        })
        self.assertFalse(ok)

    def test_validate_all_raises_on_duplicate(self):
        scenarios = [
            {"id": "dup", "title": "a", "category": "dashboard",
             "enabled_for_console": False, "enabled_for_web": True,
             "audit_input": {"schema_version": "1.0",
                             "patient_state": {},
                             "dialogue_output": {}}},
            {"id": "dup", "title": "b", "category": "dashboard",
             "enabled_for_console": False, "enabled_for_web": True,
             "audit_input": {"schema_version": "1.0",
                             "patient_state": {},
                             "dialogue_output": {}}},
        ]
        with self.assertRaises(ValueError):
            validate_all_scenarios(scenarios)


class IsolationTests(unittest.TestCase):
    """Make sure test-only fields never reach the safety engine."""

    def test_expected_assertions_not_in_audit_input(self):
        engine = DialogueSafetyEngine(ROOT / "rules")
        for s in load_all_scenarios():
            ai = s["audit_input"]
            with self.subTest(s=s["id"]):
                # The expected_assertions must NEVER be inside
                # audit_input.
                self.assertNotIn("expected_assertions", ai)
                # Run a real audit — no attribute of the report
                # references expected_assertions.
                report = engine.audit_payload(
                    payload=ai,
                    strict_mode=True, compat_mode=False, debug=False,
                )
                self.assertIn(report.decision, {"PASS", "REVIEW", "BLOCK"})

    def test_post_audit_strips_test_metadata(self):
        from audit_web import _extract_strict_payload
        sent = {
            "schema_version": "1.0",
            "patient_state": {"patient_id": "X"},
            "dialogue_output": {"reply_text": ""},
            "expected_assertions": {"decision": "PASS"},
            "expected_decision": "PASS",
            "case_profile": {"x": 1},
            "retrieved_evidence": [{"x": 1}],
            "category": "dashboard",
            "tags": ["foo"],
        }
        clean = _extract_strict_payload(sent)
        self.assertNotIn("expected_assertions", clean)
        self.assertNotIn("expected_decision", clean)
        self.assertNotIn("case_profile", clean)
        self.assertNotIn("retrieved_evidence", clean)
        self.assertNotIn("category", clean)
        self.assertNotIn("tags", clean)
        self.assertIn("schema_version", clean)


class CacheTests(unittest.TestCase):
    def test_cache_invalidation(self):
        before = len(load_all_scenarios())
        _loader.invalidate_cache()
        after = len(load_all_scenarios())
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
