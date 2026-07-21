"""v4.2.1 Audit Web — single-page audit visualizer.

This module starts a tiny stdlib HTTP server on port ``8765`` that:

- Serves the static assets in ``audit_web/`` (``index.html``,
  ``style.css``, ``app.js``).
- Exposes JSON endpoints:

  * ``GET  /api/scenarios``   -- list of preset scenarios the page can
                                 load into the editor.
  * ``POST /api/audit``       -- runs ``engine.audit_payload(...)``
                                 and returns the full AuditReport dict
                                 plus the decision and the visible
                                 patient-facing response.
  * ``GET  /api/health``      -- readiness probe.

Scenario source
---------------
All scenarios live in :mod:`audit_scenarios` which reads
``data/audit_scenarios.json``.  This module does NOT keep its own
copy of any scenario array.

Backward compat
---------------
``legacy_trace_scenarios``, ``legacy_dashboard_scenarios``, and
``legacy_full_clinical_scenarios`` are shim generators that pull the
historical groupings from the unified file. They exist so tests that
imported them by name keep passing; production code paths use
``/api/scenarios``.

Run with:

    python audit_web.py            # default port 8765
    python audit_web.py --port N   # choose a different port

Or simply launch ``audit_web.bat`` on Windows, which opens the
browser to http://127.0.0.1:8765/ automatically.
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

from audit_scenarios import (
    get_scenario_by_id,
    list_web_scenarios,
    SCENARIOS_FILE,
)


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
# Scenario grouping (legacy surface — kept for backward compat with tests)
# ---------------------------------------------------------------------------


def _scenarios_by_category(categories: List[str]) -> List[Dict[str, Any]]:
    out = []
    for s in list_web_scenarios():
        if s.get("category") in categories:
            out.append(s)
    return out


def _strip_presentation(scenario: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of the scenario containing only the fields the web
    UI is allowed to read. Strip every test-only / presentation-only
    field so the browser cannot branch on them.
    """
    return {
        "id": scenario.get("id"),
        "title": scenario.get("title"),
        "summary": scenario.get("summary"),
        "category": scenario.get("category"),
        "tags": scenario.get("tags", []),
        "enabled_for_console": bool(scenario.get("enabled_for_console", False)),
        "enabled_for_web": bool(scenario.get("enabled_for_web", False)),
        "case_profile": scenario.get("case_profile"),
        "retrieved_evidence": scenario.get("retrieved_evidence", []),
        "audit_input": dict(scenario.get("audit_input", {})),
        "simulate_error": bool(scenario.get("simulate_error", False)),
        # INTENTIONALLY OMITTED:
        #   - expected_assertions  (test-only; the engine must NEVER
        #     see this field.)
    }


def _scenarios_to_groups(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        c = it.get("category", "legacy")
        by_cat.setdefault(c, []).append(it)
    titles = {
        "trace": "踪迹演示 (Trace)",
        "dashboard": "v4.2.1 仪表板场景 1-15",
        "full_clinical": "完整临床案例（Full Clinical Cases）",
        "legacy": "v4.2.0 / v4.2.1 场景 A-P",
        "regression": "回归场景",
        "console_demo": "控制台演示场景",
    }
    order = ["trace", "dashboard", "full_clinical", "legacy", "regression", "console_demo"]
    out = []
    for cat in order:
        if cat in by_cat:
            out.append({
                "group": titles.get(cat, cat),
                "items": [_strip_presentation(x) for x in by_cat[cat]],
            })
    if not out:
        # Fallback: a single group containing everything.
        out.append({
            "group": "All Scenarios",
            "items": [_strip_presentation(x) for x in items],
        })
    return out


def list_scenarios() -> List[Dict[str, Any]]:
    """Return grouped scenarios. Only presentation data is exposed —
    ``expected_assertions`` is never sent to the front-end."""
    return _scenarios_to_groups(list_web_scenarios())


# ---------------------------------------------------------------------------
# Legacy test shims — these lists now just read the unified file.
# ---------------------------------------------------------------------------


def legacy_trace_scenarios() -> List[Dict[str, Any]]:
    """Test-only backward-compat accessor for TRACE scenarios."""
    return _scenarios_by_category(["trace"])


def legacy_dashboard_scenarios() -> List[Dict[str, Any]]:
    """Test-only backward-compat accessor for dashboard scenarios."""
    return _scenarios_by_category(["dashboard"])


def legacy_full_clinical_scenarios() -> List[Dict[str, Any]]:
    """Test-only backward-compat accessor for full clinical cases."""
    return _scenarios_by_category(["full_clinical"])


# ---------------------------------------------------------------------------
# Rule-type catalog (bottom panel)
# ---------------------------------------------------------------------------


_RULE_TYPE_CATALOG = [
    {"key": "patient_risk", "name_zh": "患者风险规则",
     "desc": "基于患者字段（如 eGFR、血压）触发风险标志，驱动 response_compliance。"},
    {"key": "response_compliance", "name_zh": "回复合规规则",
     "desc": "命中风险后必须 / 禁止的回复动作。"},
    {"key": "max_daily_dose", "name_zh": "剂量上限规则",
     "desc": "单药每日最大 mg 剂量（mcg/mg/g 自动换算）。"},
    {"key": "patient_state", "name_zh": "患者状态规则",
     "desc": "药物 + 患者指标组合的禁忌 / 慎用条件。"},
    {"key": "drug_drug", "name_zh": "药物相互作用规则",
     "desc": "同一 resulting_drugs 中两个药物联用的风险。"},
    {"key": "drug_food", "name_zh": "药物与食物规则",
     "desc": "特定药物 + 食物概念的风险。"},
    {"key": "drug_exercise", "name_zh": "药物与运动规则",
     "desc": "特定药物 + 运动强度的风险。"},
    {"key": "disease_food", "name_zh": "疾病与食物规则",
     "desc": "疾病背景 + 推荐食物方向。"},
    {"key": "disease_exercise", "name_zh": "疾病与运动规则",
     "desc": "疾病背景 + 推荐运动强度。"},
]


def _rule_type_catalog() -> List[Dict[str, Any]]:
    eng = _get_engine()
    counts = {rt["key"]: {"active": 0, "total": 0} for rt in _RULE_TYPE_CATALOG}
    for rule in eng.repository.iter_all_rules():
        if rule.type not in counts:
            continue
        counts[rule.type]["total"] += 1
        if rule.status == "active":
            counts[rule.type]["active"] += 1
    return [{**rt, "active_count": counts.get(rt["key"], {"active": 0, "total": 0})["active"],
             "total_count": counts.get(rt["key"], {"active": 0, "total": 0})["total"]}
            for rt in _RULE_TYPE_CATALOG]


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------


def _read_body(handler: SimpleHTTPRequestHandler) -> bytes:
    length = int(handler.headers.get("Content-Length", "0"))
    return handler.rfile.read(length) if length else b""


# Fields that the /api/audit endpoint MUST accept (audit input only).
# Anything else from the request body is silently ignored to keep the
# front-end from accidentally leaking test metadata into the engine.
_ALLOWED_AUDIT_FIELDS = {"schema_version", "patient_state",
                         "dialogue_output", "strict_mode",
                         "compat_mode", "debug", "simulate_error"}


def _extract_strict_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """Pick only the audit-input fields from the request body."""
    return {k: v for k, v in data.items() if k in _ALLOWED_AUDIT_FIELDS}


class AuditWebHandler(SimpleHTTPRequestHandler):
    server_version = "AuditWeb/4.2.1"

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
            eng = _get_engine()
            self._send_json({
                "ok": True,
                "ruleset": eng.repository.ruleset_version,
                "project_version": eng.PROJECT_VERSION,
                "input_schema_version": "1.0",
                "scenarios_file": str(SCENARIOS_FILE),
            })
            return
        if path == "/api/rule-types":
            self._send_json({"rule_types": _rule_type_catalog()})
            return
        self.send_error(HTTPStatus.NOT_FOUND, f"no route for {path}")

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

        # Hard isolation: any test-only key (expected_assertions,
        # expected_decision, case_profile, retrieved_evidence,
        # category, tags) MUST NOT pass through to the engine.
        strict = _extract_strict_payload(data)
        schema_version = str(strict.get("schema_version", "") or "")
        if not schema_version:
            schema_version = "1.0"
        payload = {
            "schema_version": schema_version,
            "patient_state": strict.get("patient_state") or {},
            "dialogue_output": strict.get("dialogue_output") or {},
        }
        strict_mode = bool(strict.get("strict_mode", True))
        compat_mode = bool(strict.get("compat_mode", False))
        debug = bool(strict.get("debug", True))

        if strict.get("simulate_error"):
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
            "ui_trace": (report.developer_diagnostics or {}).get("ui_trace"),
            "project_version": (report.developer_diagnostics or {}).get("project_version"),
            "ruleset_version": report.ruleset_version,
            "input_schema_version": report.input_schema_version,
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
    print(f"[audit_web] scenarios:  {SCENARIOS_FILE}")
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
