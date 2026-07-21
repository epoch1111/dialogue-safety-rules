"""Integration coverage for the complete clinical dashboard cases."""

from __future__ import annotations

import json
import socket
import threading
import unittest
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

import audit_web
from audit_web_cases.full_clinical_cases import FULL_CLINICAL_SCENARIOS
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


class FullClinicalCaseTests(unittest.TestCase):
    def test_case_contract_and_real_decisions(self):
        self.assertGreaterEqual(len(FULL_CLINICAL_SCENARIOS), 8)
        engine = DialogueSafetyEngine(audit_web.ROOT / "rules")
        for case in FULL_CLINICAL_SCENARIOS:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["case_profile"])
                self.assertTrue(case["retrieved_evidence"])
                ps = case["patient_state"]
                self.assertTrue(ps["patient_id"])
                self.assertTrue(ps["current_medications"])
                self.assertIn("measurements", ps)
                self.assertIn("clinical_flags", ps)
                self.assertIn("allergies", ps)
                for med in ps["current_medications"]:
                    for field in ("drug_id", "drug_name", "status", "dose_value",
                                  "dose_unit", "frequency_per_day", "route"):
                        self.assertIn(field, med)
                output = case["dialogue_output"]
                for field in ("reply_text", "medication_actions", "food_advice",
                              "exercise_advice", "care_actions", "requires_review",
                              "uncertainty_reasons"):
                    self.assertIn(field, output)
                report = engine.audit_payload({"schema_version": "1.0", "patient_state": ps,
                                               "dialogue_output": output}, strict_mode=True,
                                              compat_mode=False, debug=True)
                self.assertEqual(report.decision, case["expected_assertions"]["decision"])
                self.assertEqual(report.original_llm_reply_was_sent,
                                 case["expected_assertions"]["original_reply_was_sent"])
                ids = {v.rule_id for v in report.medical_violations}
                self.assertTrue(set(case["expected_assertions"]["must_include_rule_ids"]).issubset(ids))
                self.assertFalse(report.input_validation_errors)

    def test_api_lists_cases_and_discards_presentation_metadata(self):
        with _server() as base:
            with urllib.request.urlopen(base + "/api/scenarios", timeout=5) as resp:
                groups = json.loads(resp.read().decode("utf-8"))["scenarios"]
            group = next(g for g in groups if g["group"] == "完整临床案例（Full Clinical Cases）")
            self.assertEqual(len(group["items"]), len(FULL_CLINICAL_SCENARIOS))
            case = group["items"][0]
            sent = {"schema_version": "1.0", "patient_state": case["patient_state"],
                    "dialogue_output": case["dialogue_output"], "strict_mode": True,
                    "compat_mode": False, "debug": True,
                    "expected_assertions": case["expected_assertions"],
                    "case_profile": case["case_profile"],
                    "retrieved_evidence": case["retrieved_evidence"]}
            req = urllib.request.Request(base + "/api/audit", data=json.dumps(sent).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(result["decision"], "PASS")
            audit_json = json.dumps(result["audit_report"], ensure_ascii=False)
            self.assertNotIn("expected_assertions", audit_json)
            self.assertNotIn("case_profile", audit_json)

    def test_web_assets_expose_clinical_case_panels_without_assertion_logic(self):
        html = (audit_web.WEB_DIR / "index.html").read_text(encoding="utf-8")
        js = (audit_web.WEB_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("case-profile-cards", html)
        self.assertIn("case-evidence-cards", html)
        self.assertIn("renderClinicalCase", js)
        self.assertNotIn("expected_assertions", js)

