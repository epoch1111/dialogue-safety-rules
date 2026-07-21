"""v4.2.1 Audit Web — single-page audit visualizer.

This module starts a tiny stdlib HTTP server on port ``8765`` that:

- Serves the static assets in ``audit_web/`` (``index.html``,
  ``style.css``, ``app.js``).
- Exposes JSON endpoints:

  * ``GET  /api/scenarios``  — list of preset scenarios the page can
                              load into the editor.
  * ``POST /api/audit``     — runs ``engine.audit_payload(...)``
                              and returns the full AuditReport dict
                              plus the decision and the visible
                              patient-facing response.
  * ``GET  /api/health``    — readiness probe.

The server is intentionally stdlib-only so it works inside the
bundled ``.venv`` and does not require any ``pip install`` step.

Run it with:

    python audit_web.py            # default port 8765
    python audit_web.py --port N   # choose a different port

Or simply launch ``audit_web.bat`` on Windows, which opens the
browser to http://127.0.0.1:8765/ automatically.

v4.2.1 scenarios
----------------
A: legal new-schema input, no risk → PASS
B: metformin + eGFR 24 → BLOCK
C: metformin + missing eGFR → REVIEW (MISSING_CONTEXT)
D: current_medications contains a string → REVIEW
E: unknown drug name "辛伐他烨" → REVIEW
F: drug_id ↔ drug_name mismatch → REVIEW
G: amlodipine 1 g (1000 mg/day > 10) → BLOCK
H: dose_value = -5 → REVIEW (NEGATIVE_DOSE)
I: replace with atorvastatin not in active regimen → REVIEW
J: text says "avoid grapefruit" but structured food says "recommend"
   → REVIEW (SYS003)
K: simulate engine exception via ``simulate_error=true`` → REVIEW,
   SYSTEM_ERROR
L: requires_review=true → REVIEW (LLM_DECLARED_UNCERTAINTY)
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import traceback
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "audit_web"
DEFAULT_PORT = 8765


# Lazy-load the safety engine so the module can be imported for tests
# without immediately loading rules.
_engine_lock = threading.Lock()
_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                from safety import DialogueSafetyEngine
                _engine = DialogueSafetyEngine(ROOT / "rules")
    return _engine


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

TRACE_SCENARIOS: List[Dict[str, Any]] = [
    {
        "id": "trace_a_metformin_egfr",
        "title": "场景A：二甲双胍 + eGFR 24",
        "summary": "患者有二甲双胍治疗 + eGFR=24，但 LLM 建议继续。应 BLOCK。",
        "patient_state": {
            "patient_id": "TRACE_METFORMIN_EGFR",
            "egfr": 24,
            "current_medications": [
                {"drug_id": "metformin", "drug_name": "二甲双胍", "status": "active"},
            ],
        },
        "reply_text": "建议继续使用二甲双胍500毫克，每日2次。",
        "dialogue_output": {
            "reply_text": "建议继续使用二甲双胍500毫克，每日2次。",
            "medication_actions": [
                {
                    "drug_id": "metformin",
                    "drug_name": "二甲双胍",
                    "action": "continue",
                    "dose_value": 500,
                    "dose_unit": "mg",
                    "frequency_per_day": 2,
                    "route": "oral",
                },
            ],
            "food_advice": [],
            "exercise_advice": [],
            "care_actions": [],
        },
    },
    {
        "id": "trace_b_metformin_hold",
        "title": "场景B：同一患者，但建议 hold",
        "summary": "同一患者，但 LLM 建议暂停二甲双胍 + 提供急诊评估。应 PASS。",
        "patient_state": {
            "patient_id": "TRACE_METFORMIN_SAFE",
            "egfr": 24,
            "latest_systolic_bp_mmHg": 130,
            "serum_potassium_mmol_l": 4.0,
            "current_medications": [
                {"drug_id": "metformin", "drug_name": "二甲双胍", "status": "active"},
            ],
        },
        "reply_text": (
            "您的肾功能指标需要医生重新评估,"
            "目前不要自行继续或调整二甲双胍,"
            "请尽快联系医生。"
        ),
        "dialogue_output": {
            "reply_text": (
                "您的肾功能指标需要医生重新评估,"
                "目前不要自行继续或调整二甲双胍,"
                "请尽快联系医生。"
            ),
            "medication_actions": [
                {
                    "drug_id": "metformin",
                    "drug_name": "二甲双胍",
                    "action": "hold",
                    "route": "oral",
                },
            ],
            "food_advice": [],
            "exercise_advice": [],
            "care_actions": [
                {
                    "type": "urgent_medical_evaluation",
                    "target": "renal_function",
                    "action": "recommend",
                },
            ],
        },
    },
]


# v4.2.0: 11 scenarios required by the spec section 15.
LEGACY_V42_SCENARIOS: List[Dict[str, Any]] = [
    {
        "id": "v42_A_legal_pass",
        "title": "A. 合法 PASS 输入",
        "summary": "完整 patient_state + 合规 dialogue_output → PASS。",
        "patient_state": {
            "patient_id": "P_A",
            "egfr": 90,
            "latest_systolic_bp_mmHg": 120,
            "latest_glucose_mmol_l": 6.0,
            "serum_potassium_mmol_l": 4.0,
            "current_medications": [],
            "disease_codes": [],
        },
        "reply_text": "请按医生建议定期复查。",
        "dialogue_output": {
            "reply_text": "请按医生建议定期复查。",
            "medication_actions": [],
            "food_advice": [],
            "exercise_advice": [],
            "care_actions": [],
            "requires_review": False,
            "uncertainty_reasons": [],
        },
    },
    {
        "id": "v42_B_explicit_block",
        "title": "B. 明确 BLOCK 输入",
        "summary": "二甲双胍 + eGFR=24 → BLOCK。",
        "patient_state": {
            "patient_id": "P_B",
            "egfr": 24,
            "current_medications": [
                {"drug_id": "metformin", "drug_name": "二甲双胍",
                 "status": "active", "dose_value": 500, "dose_unit": "mg",
                 "frequency_per_day": 2, "route": "oral"},
            ],
            "disease_codes": ["diabetes"],
        },
        "reply_text": "继续使用二甲双胍。",
        "dialogue_output": {
            "reply_text": "继续使用二甲双胍。",
            "medication_actions": [{
                "drug_id": "metformin", "drug_name": "二甲双胍",
                "action": "continue", "dose_value": 500, "dose_unit": "mg",
                "frequency_per_day": 2, "route": "oral",
            }],
            "food_advice": [], "exercise_advice": [], "care_actions": [],
            "requires_review": False, "uncertainty_reasons": [],
        },
    },
    {
        "id": "v42_C_missing_egfr",
        "title": "C. 缺少 eGFR → REVIEW",
        "summary": "Metformin 续用但缺少 eGFR → REVIEW。",
        "patient_state": {
            "patient_id": "P_C",
            "current_medications": [
                {"drug_id": "metformin", "drug_name": "二甲双胍", "status": "active"},
            ],
            "disease_codes": ["diabetes"],
        },
        "reply_text": "继续二甲双胍。",
        "dialogue_output": {
            "reply_text": "继续二甲双胍。",
            "medication_actions": [{
                "drug_id": "metformin", "drug_name": "二甲双胍",
                "action": "continue", "dose_value": 500, "dose_unit": "mg",
                "frequency_per_day": 2, "route": "oral",
            }],
            "food_advice": [], "exercise_advice": [], "care_actions": [],
            "requires_review": False, "uncertainty_reasons": [],
        },
    },
    {
        "id": "v42_D_string_med_item",
        "title": "D. current_medications 含字符串 → REVIEW",
        "summary": "current_medications 中包含字符串 → REVIEW，不崩溃。",
        "patient_state": {
            "patient_id": "P_D",
            "current_medications": ["metformin"],
            "disease_codes": [],
        },
        "reply_text": "建议",
        "dialogue_output": {
            "reply_text": "建议",
            "medication_actions": [], "food_advice": [], "exercise_advice": [],
            "care_actions": [], "requires_review": False, "uncertainty_reasons": [],
        },
    },
    {
        "id": "v42_E_unknown_drug",
        "title": "E. 未知药物「辛伐他烨」 → REVIEW",
        "summary": "drug_name 不在标准药物表 → REVIEW。",
        "patient_state": {
            "patient_id": "P_E",
            "egfr": 90,
            "latest_systolic_bp_mmHg": 120,
            "current_medications": [
                {"drug_id": "simvastatin", "drug_name": "辛伐他烨",
                 "status": "active"},
            ],
            "disease_codes": [],
        },
        "reply_text": "",
        "dialogue_output": {
            "reply_text": "",
            "medication_actions": [],
            "food_advice": [], "exercise_advice": [], "care_actions": [],
            "requires_review": False, "uncertainty_reasons": [],
        },
    },
    {
        "id": "v42_F_drug_id_mismatch",
        "title": "F. drug_id 与 drug_name 不匹配 → REVIEW",
        "summary": "drug_id=simvastatin, drug_name=阿托伐他汀 → REVIEW。",
        "patient_state": {
            "patient_id": "P_F",
            "egfr": 90,
            "latest_systolic_bp_mmHg": 120,
            "current_medications": [
                {"drug_id": "simvastatin", "drug_name": "阿托伐他汀",
                 "status": "active"},
            ],
            "disease_codes": [],
        },
        "reply_text": "",
        "dialogue_output": {
            "reply_text": "",
            "medication_actions": [], "food_advice": [], "exercise_advice": [],
            "care_actions": [], "requires_review": False, "uncertainty_reasons": [],
        },
    },
    {
        "id": "v42_G_amlodipine_1g",
        "title": "G. 氨氯地平 1 g → 单位换算后 BLOCK",
        "summary": "amlodipine 1 g = 1000 mg/day > 10 mg/day → BLOCK。",
        "patient_state": {
            "patient_id": "P_G",
            "egfr": 90,
            "latest_systolic_bp_mmHg": 130,
            "current_medications": [],
            "disease_codes": [],
        },
        "reply_text": "开始氨氯地平 1 克，每日 1 次。",
        "dialogue_output": {
            "reply_text": "开始氨氯地平 1 克，每日 1 次。",
            "medication_actions": [{
                "drug_id": "amlodipine", "drug_name": "氨氯地平",
                "action": "start", "dose_value": 1, "dose_unit": "g",
                "frequency_per_day": 1, "route": "oral",
            }],
            "food_advice": [], "exercise_advice": [], "care_actions": [],
            "requires_review": False, "uncertainty_reasons": [],
        },
    },
    {
        "id": "v42_H_negative_dose",
        "title": "H. dose_value 为负数 → REVIEW",
        "summary": "dose_value=-5 → REVIEW。",
        "patient_state": {
            "patient_id": "P_H",
            "egfr": 90,
            "latest_systolic_bp_mmHg": 120,
            "current_medications": [],
            "disease_codes": [],
        },
        "reply_text": "",
        "dialogue_output": {
            "reply_text": "",
            "medication_actions": [{
                "drug_id": "amlodipine", "drug_name": "氨氯地平",
                "action": "start", "dose_value": -5, "dose_unit": "mg",
                "frequency_per_day": 1, "route": "oral",
            }],
            "food_advice": [], "exercise_advice": [], "care_actions": [],
            "requires_review": False, "uncertainty_reasons": [],
        },
    },
    {
        "id": "v42_I_replace_no_old",
        "title": "I. replace 缺少旧药 → REVIEW",
        "summary": "replace 但旧药不在 current → REVIEW。",
        "patient_state": {
            "patient_id": "P_I",
            "egfr": 90,
            "latest_systolic_bp_mmHg": 120,
            "current_medications": [
                {"drug_id": "ramipril", "drug_name": "雷米普利",
                 "status": "active"},
            ],
            "disease_codes": [],
        },
        "reply_text": "",
        "dialogue_output": {
            "reply_text": "",
            "medication_actions": [{
                "drug_id": "lisinopril", "drug_name": "赖诺普利",
                "action": "replace",
                "replace_drug_id": "atorvastatin",
                "replace_drug_name": "阿托伐他汀",
                "dose_value": 10, "dose_unit": "mg",
                "frequency_per_day": 1, "route": "oral",
            }],
            "food_advice": [], "exercise_advice": [], "care_actions": [],
            "requires_review": False, "uncertainty_reasons": [],
        },
    },
    {
        "id": "v42_J_text_food_conflict",
        "title": "J. 正文与结构化 food action 冲突 → REVIEW",
        "summary": "正文 '不要喝西柚汁' 但 food_advice action=recommend → REVIEW。",
        "patient_state": {
            "patient_id": "P_J",
            "egfr": 90,
            "latest_systolic_bp_mmHg": 120,
            "current_medications": [
                {"drug_id": "simvastatin", "drug_name": "辛伐他汀",
                 "status": "active"},
            ],
            "disease_codes": [],
        },
        "reply_text": "不要喝西柚汁。",
        "dialogue_output": {
            "reply_text": "不要喝西柚汁。",
            "medication_actions": [],
            "food_advice": [{
                "food_concept_id": "grapefruit",
                "food_name": "西柚汁",
                "action": "recommend",
            }],
            "exercise_advice": [], "care_actions": [],
            "requires_review": False, "uncertainty_reasons": [],
        },
    },
    {
        "id": "v42_K_system_error",
        "title": "K. 模拟异常 → REVIEW, 原回复不发送",
        "summary": "通过 /api/audit 触发异常路径 → REVIEW, original_llm_reply_was_sent=False。",
        "patient_state": {"patient_id": "P_K", "current_medications": [],
                          "disease_codes": []},
        "reply_text": "",
        "dialogue_output": {
            "reply_text": "",
            "medication_actions": [], "food_advice": [], "exercise_advice": [],
            "care_actions": [], "requires_review": False, "uncertainty_reasons": [],
        },
    },
    {
        "id": "v42_L_requires_review",
        "title": "L. requires_review=true → REVIEW",
        "summary": "LLM 自报 requires_review=true → REVIEW (LLM_DECLARED_UNCERTAINTY)。",
        "patient_state": {"patient_id": "P_L", "current_medications": [],
                          "disease_codes": []},
        "reply_text": "",
        "dialogue_output": {
            "reply_text": "",
            "medication_actions": [], "food_advice": [], "exercise_advice": [],
            "care_actions": [],
            "requires_review": True, "uncertainty_reasons": [],
        },
    },
    {
        "id": "v42_M_uncertainty_reasons",
        "title": "M. uncertainty_reasons 非空 → REVIEW",
        "summary": "LLM 自报 uncertainty_reasons 非空 → REVIEW (LLM_DECLARED_UNCERTAINTY)。",
        "patient_state": {"patient_id": "P_M", "current_medications": [],
                          "disease_codes": []},
        "reply_text": "",
        "dialogue_output": {
            "reply_text": "",
            "medication_actions": [], "food_advice": [], "exercise_advice": [],
            "care_actions": [],
            "requires_review": False,
            "uncertainty_reasons": ["无法确认患者当前用药"],
        },
    },
    {
        "id": "v42_N_missing_route_start",
        "title": "N. start 缺少 route → REVIEW",
        "summary": "v4.2.1 禁止缺失 route → REVIEW (INPUT_MEDICATION_ACTION_MISSING_FIELDS)。",
        "patient_state": {"patient_id": "P_N", "current_medications": [],
                          "disease_codes": []},
        "reply_text": "",
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
    },
    {
        "id": "v42_O_measurement_unit_error",
        "title": "O. measurement unit 错误 → REVIEW",
        "summary": "egfr unit=BANANA + observed_at=not-a-date → REVIEW。",
        "patient_state": {
            "patient_id": "P_O", "current_medications": [],
            "disease_codes": [],
            "measurements": {
                "egfr": {
                    "value": 24, "unit": "BANANA",
                    "observed_at": "not-a-date",
                    "source": "madeup", "confirmed": False,
                }
            },
        },
        "reply_text": "",
        "dialogue_output": {
            "reply_text": "",
            "medication_actions": [], "food_advice": [], "exercise_advice": [],
            "care_actions": [], "requires_review": False,
            "uncertainty_reasons": [],
        },
    },
    {
        "id": "v42_P_hold_metformin_no_egfr",
        "title": "P. hold 二甲双胍且无 eGFR → REVIEW (但非因 eGFR)",
        "summary": "hold 动作不触发 continue/patient_state eGFR 规则；只有 unordered_message 才进 REVIEW。",
        "patient_state": {
            "patient_id": "P_P",
            "current_medications": [
                {"drug_id": "metformin", "drug_name": "二甲双胍",
                 "status": "active"}],
            "disease_codes": [],
        },
        "reply_text": "建议暂停二甲双胍并尽快就医。",
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
    },
]


def list_scenarios() -> List[Dict[str, Any]]:
    return [
        {"group": "踪迹演示", "items": TRACE_SCENARIOS},
        {"group": "v4.2.0 场景 A-K", "items": LEGACY_V42_SCENARIOS},
    ]


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------


def _read_body(handler: SimpleHTTPRequestHandler) -> bytes:
    length = int(handler.headers.get("Content-Length", "0"))
    return handler.rfile.read(length) if length else b""


class AuditWebHandler(SimpleHTTPRequestHandler):
    server_version = "AuditWeb/4.2.1"

    # ----- helpers

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, relative_path: str,
                     content_type: str) -> None:
        target = WEB_DIR / relative_path
        if not target.exists():
            self.send_error(HTTPStatus.NOT_FOUND, f"missing {relative_path}")
            return
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ----- GET

    def do_GET(self) -> None:  # noqa: N802 (stdlib API)
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send_static("index.html", "text/html")
            return
        if path == "/static/style.css":
            self._send_static("style.css", "text/css")
            return
        if path == "/static/app.js":
            self._send_static("app.js", "application/javascript")
            return
        if path == "/api/scenarios":
            self._send_json({"scenarios": list_scenarios()})
            return
        if path == "/api/health":
            self._send_json({
                "ok": True,
                "ruleset": _get_engine().repository.ruleset_version,
                "project_version": _get_engine().PROJECT_VERSION,
            })
            return
        # Fallback: 404
        self.send_error(HTTPStatus.NOT_FOUND, f"no route for {path}")

    # ----- POST

    def do_POST(self) -> None:  # noqa: N802 (stdlib API)
        path = self.path.split("?", 1)[0]
        if path != "/api/audit":
            self.send_error(HTTPStatus.NOT_FOUND, f"no route for {path}")
            return
        try:
            body = _read_body(self)
            data = json.loads(body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json({"error": f"bad json: {exc}"}, status=400)
            return

        # v4.2.1: build the canonical payload envelope. ``schema_version``
        # is filled only when not provided, to preserve the historical
        # demo behavior. Strict mode is the default.
        schema_version = str(data.get("schema_version", "") or "")
        if not schema_version:
            schema_version = "1.0"
        payload = {
            "schema_version": schema_version,
            "patient_state": data.get("patient_state") or {},
            "dialogue_output": data.get("dialogue_output") or {},
        }
        strict_mode = bool(data.get("strict_mode", True))
        compat_mode = bool(data.get("compat_mode", False))
        debug = bool(data.get("debug", True))

        # Special "K" scenario: client can opt into a simulated engine
        # exception by sending ``{"simulate_error": true}``.
        if data.get("simulate_error"):
            from safety.safety_engine import DialogueSafetyEngine as _DSE
            class _Boom(_DSE):
                def _audit_impl(self, *a, **kw):
                    raise RuntimeError("simulated explosion")
            eng = _Boom(ROOT / "rules")
        else:
            eng = _get_engine()
        try:
            report = eng.audit_payload(
                payload=payload,
                strict_mode=strict_mode,
                compat_mode=compat_mode,
                debug=debug,
            )
        except Exception as exc:  # pragma: no cover - server safety net
            self._send_json({
                "error": f"engine error: {exc}",
                "traceback": traceback.format_exc(),
            }, status=500)
            return
        self._send_json({
            "decision": report.decision,
            "patient_visible_response": report.patient_visible_response,
            "original_llm_reply_was_sent": report.original_llm_reply_was_sent,
            "audit_report": report.to_dict(),
        })


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the audit web UI.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="TCP port to bind (default: 8765).")
    parser.add_argument("--no-browser", action="store_true",
                        help="Skip auto-opening the browser.")
    args = parser.parse_args(argv)

    if not WEB_DIR.exists():
        print(f"[audit_web] missing directory: {WEB_DIR}", file=sys.stderr)
        return 1

    server = ThreadingHTTPServer(("127.0.0.1", args.port), AuditWebHandler)
    print(f"[audit_web] serving on http://127.0.0.1:{args.port}/")
    print(f"[audit_web] web assets: {WEB_DIR}")
    print(f"[audit_web] ruleset:    {_get_engine().repository.ruleset_version}")
    print("[audit_web] press Ctrl+C to stop.")

    if not args.no_browser:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(
            f"http://127.0.0.1:{args.port}/")).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[audit_web] shutting down.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())