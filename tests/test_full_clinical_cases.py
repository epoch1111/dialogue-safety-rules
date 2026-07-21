"""Integration coverage for the complete clinical dashboard cases.

Refactored to read scenarios from the unified
``data/audit_scenarios.json`` via :mod:`audit_scenarios` rather than
the legacy ``full_clinical_cases.py`` module.
"""

from __future__ import annotations

import json
import socket
import threading
import unittest
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

import audit_web
from audit_scenarios import list_web_scenarios, load_all_scenarios
from safety import DialogueSafetyEngine


@contextmanager
def _server():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), audit_web.AuditWebHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _full_clinical():
    return [s for s in load_all_scenarios()
            if s.get("category") == "full_clinical"]


class FullClinicalCaseTests(unittest.TestCase):

    def test_case_contract_and_real_decisions(self):
        cases = _full_clinical()
        self.assertGreaterEqual(len(cases), 8)
        engine = DialogueSafetyEngine(audit_web.ROOT / "rules")
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["case_profile"])
                self.assertTrue(case["retrieved_evidence"])
                ps = case["audit_input"]["patient_state"]
                self.assertTrue(ps["patient_id"])
                self.assertTrue(ps["current_medications"])
                self.assertIn("measurements", ps)
                self.assertIn("clinical_flags", ps)
                self.assertIn("allergies", ps)
                for med in ps["current_medications"]:
                    for field in ("drug_id", "drug_name", "status",
                                  "dose_value", "dose_unit",
                                  "frequency_per_day", "route"):
                        self.assertIn(field, med)
                output = case["audit_input"]["dialogue_output"]
                for field in ("reply_text", "medication_actions",
                              "food_advice", "exercise_advice",
                              "care_actions", "requires_review",
                              "uncertainty_reasons"):
                    self.assertIn(field, output)
                report = engine.audit_payload({
                    "schema_version": "1.0",
                    "patient_state": ps,
                    "dialogue_output": output,
                }, strict_mode=True, compat_mode=False, debug=True)
                self.assertEqual(
                    report.decision,
                    case["expected_assertions"]["decision"],
                )
                self.assertEqual(
                    report.original_llm_reply_was_sent,
                    case["expected_assertions"]["original_reply_was_sent"],
                )
                ids = {v.rule_id for v in report.medical_violations}
                self.assertTrue(
                    set(case["expected_assertions"]["must_include_rule_ids"])
                    .issubset(ids)
                )
                self.assertFalse(report.input_validation_errors)

    def test_api_lists_cases_and_discards_presentation_metadata(self):
        with _server() as base:
            with urllib.request.urlopen(base + "/api/scenarios",
                                       timeout=5) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            self.assertIn("scenarios", payload)
            items = []
            for g in payload["scenarios"]:
                items.extend(g["items"])
            fc_ids = {s["id"] for s in _full_clinical()}
            api_fc_ids = {it["id"] for it in items if it["category"] == "full_clinical"}
            self.assertEqual(fc_ids, api_fc_ids)

            case = next(it for it in items if it["id"] in fc_ids)
            # The presentation metadata that the front-end uses.
            self.assertIn("case_profile", case)
            self.assertIn("retrieved_evidence", case)
            self.assertIn("audit_input", case)

            # expected_assertions MUST NEVER reach the wire.
            self.assertNotIn("expected_assertions", case)

            # POST /api/audit must not accept expected_decision /
            # expected_assertions either.
            sent = {"schema_version": "1.0",
                    "patient_state": case["audit_input"]["patient_state"],
                    "dialogue_output": case["audit_input"]["dialogue_output"],
                    "expected_assertions": case.get("expected_assertions"),
                    "expected_decision": "PASS",
                    "case_profile": case["case_profile"],
                    "retrieved_evidence": case["retrieved_evidence"]}
            req = urllib.request.Request(base + "/api/audit",
                                         data=json.dumps(sent).encode(),
                                         headers={"Content-Type":
                                                  "application/json"},
                                         method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(result["decision"], "PASS")
            audit_json = json.dumps(result["audit_report"], ensure_ascii=False)
            self.assertNotIn("expected_assertions", audit_json)
            self.assertNotIn("case_profile", audit_json)
            self.assertNotIn("expected_decision", audit_json)

    def test_web_assets_expose_clinical_case_panels_without_assertion_logic(self):
        html = (audit_web.WEB_DIR / "index.html").read_text(encoding="utf-8")
        js = (audit_web.WEB_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("case-profile-cards", html)
        self.assertIn("case-evidence-cards", html)
        self.assertIn("renderClinicalCase", js)
        self.assertNotIn("expected_assertions", js)
