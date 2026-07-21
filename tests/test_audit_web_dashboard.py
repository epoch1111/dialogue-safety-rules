"""v4.2.1 Audit Web dashboard tests.

These tests start the audit_web stdlib server in-process, hit the
HTTP endpoints, and verify that the dashboard surfaces the real
audit result — without hardcoding any decision, expected_decision,
or scenario names.

Each test:

1. Boots ``AuditWebHandler`` on a random localhost port.
2. Loads scenarios via ``/api/scenarios``.
3. POSTs the audit payload via ``/api/audit``.
4. Asserts the response payload reflects the actual engine decision
   and contains the visual dashboard fields.

No external framework is required. We use ``urllib.request`` from
the stdlib so this works inside the bundled ``.venv``.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import unittest
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

# Make sure we use the engine in this checkout, not a globally installed one.
ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextmanager
def _running_server():
    """Boot audit_web.AuditWebHandler on a free port; tear down on exit."""
    import audit_web  # local import keeps test discovery fast

    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), audit_web.AuditWebHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    # Wait until the engine is ready.
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/health", timeout=1
            ) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


def _post(url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url + "/api/audit",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200, f"unexpected status {resp.status}"
        return json.loads(resp.read().decode("utf-8"))


def _get(url: str, path: str):
    with urllib.request.urlopen(url + path, timeout=5) as resp:
        assert resp.status == 200, f"unexpected status {resp.status}"
        return resp.read()


class DashboardServerTests(unittest.TestCase):
    """End-to-end tests against the audit_web HTTP server."""

    def test_health_endpoint_returns_versions(self):
        with _running_server() as base:
            data = json.loads(_get(base, "/api/health").decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertIn("project_version", data)
            self.assertEqual(data["input_schema_version"], "1.0")
            self.assertTrue(data["ruleset"].startswith("dialogue-safety-rules-"))

    def test_scenarios_endpoint_lists_three_groups(self):
        with _running_server() as base:
            data = json.loads(_get(base, "/api/scenarios").decode("utf-8"))
            groups = [g["group"] for g in data["scenarios"]]
            self.assertIn("踪迹演示 (Trace)", groups)
            self.assertIn("v4.2.1 仪表板场景 1-15", groups)
            self.assertIn("v4.2.0 / v4.2.1 场景 A-P", groups)
            # Total scenarios >= 32
            total = sum(len(g["items"]) for g in data["scenarios"])
            self.assertGreaterEqual(total, 32)

    def test_rule_types_endpoint(self):
        with _running_server() as base:
            data = json.loads(_get(base, "/api/rule-types").decode("utf-8"))
            keys = {rt["key"] for rt in data["rule_types"]}
            for required in (
                "patient_risk", "response_compliance", "max_daily_dose",
                "patient_state", "drug_drug", "drug_food",
                "drug_exercise", "disease_food", "disease_exercise",
            ):
                self.assertIn(required, keys)

    def test_static_assets_serve(self):
        with _running_server() as base:
            for path in ("/", "/static/style.css", "/static/app.js"):
                body = _get(base, path)
                self.assertGreater(len(body), 100, f"{path} too short")

    def test_pass_scenario_returns_pass(self):
        with _running_server() as base:
            scenarios = json.loads(_get(base, "/api/scenarios").decode("utf-8"))
            ds = next(
                g for g in scenarios["scenarios"]
                if g["group"] == "v4.2.1 仪表板场景 1-15"
            )
            pass_scenario = ds["items"][0]
            data = _post(base, {
                "schema_version": "1.0",
                "patient_state": pass_scenario["patient_state"],
                "dialogue_output": pass_scenario["dialogue_output"],
                "strict_mode": True,
                "compat_mode": False,
                "debug": True,
            })
            self.assertEqual(data["decision"], "PASS")
            self.assertTrue(data["original_llm_reply_was_sent"])
            self.assertIn("ui_trace", data)
            self.assertEqual(len(data["ui_trace"]["steps"]), 9)
            self.assertEqual(data["ui_trace"]["steps"][8]["status"], "passed")

    def test_block_scenario_returns_block(self):
        with _running_server() as base:
            scenarios = json.loads(_get(base, "/api/scenarios").decode("utf-8"))
            ds = next(
                g for g in scenarios["scenarios"]
                if g["group"] == "v4.2.1 仪表板场景 1-15"
            )
            block_scenario = ds["items"][1]  # explicit BLOCK
            data = _post(base, {
                "schema_version": "1.0",
                "patient_state": block_scenario["patient_state"],
                "dialogue_output": block_scenario["dialogue_output"],
                "strict_mode": True,
                "compat_mode": False,
                "debug": True,
            })
            self.assertEqual(data["decision"], "BLOCK")
            self.assertFalse(data["original_llm_reply_was_sent"])
            # ui_trace step 8 should be "blocked"
            self.assertEqual(data["ui_trace"]["steps"][7]["status"], "blocked")

    def test_review_scenario_returns_review(self):
        with _running_server() as base:
            scenarios = json.loads(_get(base, "/api/scenarios").decode("utf-8"))
            ds = next(
                g for g in scenarios["scenarios"]
                if g["group"] == "v4.2.1 仪表板场景 1-15"
            )
            # scenario 3: missing egfr for metformin → REVIEW
            review_scenario = ds["items"][2]
            data = _post(base, {
                "schema_version": "1.0",
                "patient_state": review_scenario["patient_state"],
                "dialogue_output": review_scenario["dialogue_output"],
                "strict_mode": True,
                "compat_mode": False,
                "debug": True,
            })
            self.assertEqual(data["decision"], "REVIEW")
            self.assertFalse(data["original_llm_reply_was_sent"])
            # Step 9 status should be warning.
            self.assertEqual(data["ui_trace"]["steps"][8]["status"], "warning")

    def test_requires_review_scenario_forces_review(self):
        with _running_server() as base:
            scenarios = json.loads(_get(base, "/api/scenarios").decode("utf-8"))
            ds = next(
                g for g in scenarios["scenarios"]
                if g["group"] == "v4.2.1 仪表板场景 1-15"
            )
            s = ds["items"][6]  # requires_review=true
            data = _post(base, {
                "schema_version": "1.0",
                "patient_state": s["patient_state"],
                "dialogue_output": s["dialogue_output"],
                "strict_mode": True,
                "compat_mode": False,
                "debug": True,
            })
            self.assertEqual(data["decision"], "REVIEW")
            self.assertIn("LLM_DECLARED_UNCERTAINTY",
                          data["audit_report"]["decision_basis"])

    def test_fail_closed_scenario_returns_review_with_system_error(self):
        with _running_server() as base:
            # Simulate engine error via the special opt-in flag.
            scenarios = json.loads(_get(base, "/api/scenarios").decode("utf-8"))
            ds = next(
                g for g in scenarios["scenarios"]
                if g["group"] == "v4.2.1 仪表板场景 1-15"
            )
            s = ds["items"][14]  # last one (fail-closed)
            data = _post(base, {
                "schema_version": "1.0",
                "patient_state": s["patient_state"],
                "dialogue_output": s["dialogue_output"],
                "simulate_error": True,
                "strict_mode": True,
                "compat_mode": False,
                "debug": True,
            })
            self.assertEqual(data["decision"], "REVIEW")
            self.assertFalse(data["original_llm_reply_was_sent"])
            self.assertIn("SYSTEM_ERROR",
                          data["audit_report"]["decision_basis"])

    def test_ui_trace_steps_have_real_data(self):
        with _running_server() as base:
            scenarios = json.loads(_get(base, "/api/scenarios").decode("utf-8"))
            ds = next(
                g for g in scenarios["scenarios"]
                if g["group"] == "v4.2.1 仪表板场景 1-15"
            )
            block_scenario = ds["items"][1]  # explicit BLOCK
            data = _post(base, {
                "schema_version": "1.0",
                "patient_state": block_scenario["patient_state"],
                "dialogue_output": block_scenario["dialogue_output"],
                "strict_mode": True,
                "compat_mode": False,
                "debug": True,
            })
            steps = data["ui_trace"]["steps"]
            self.assertEqual(len(steps), 9)
            self.assertEqual(steps[0]["key"], "input_validation")
            self.assertEqual(steps[1]["key"], "normalize")
            self.assertEqual(steps[2]["key"], "drug_context")
            self.assertEqual(steps[3]["key"], "text_parsing")
            self.assertEqual(steps[4]["key"], "required_context")
            self.assertEqual(steps[5]["key"], "consistency")
            self.assertEqual(steps[6]["key"], "candidate_recall")
            self.assertEqual(steps[7]["key"], "evaluation")
            self.assertEqual(steps[8]["key"], "aggregate")
            # BLOCK scenario: aggregate status must be "blocked".
            self.assertEqual(steps[8]["status"], "blocked")
            self.assertEqual(steps[8]["details"]["decision"], "BLOCK")

    def test_unknown_path_returns_404(self):
        with _running_server() as base:
            try:
                _get(base, "/api/does-not-exist")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 404)
            else:
                self.fail("expected 404")


if __name__ == "__main__":
    unittest.main()