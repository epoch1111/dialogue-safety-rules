"""Capture dashboard screenshots via headless Edge / Chrome.

Usage:
    python tests/capture_screenshots.py

Boots the audit_web server in-process on a free port, opens the
dashboard with ``?scenario=<id>`` so the page auto-runs the audit,
and snaps three PNGs into ``docs/screenshots/``:

- pass.png  — scenario 1 (legal PASS)
- review.png — scenario 6 (text-vs-structured conflict → REVIEW)
- block.png — scenario 2 (metformin + egfr=24 → BLOCK)
- full_case_pass.png / full_case_review.png / full_case_block.png —
  complete clinical cases rendered from their real audits.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHOT_DIR = ROOT / "docs" / "screenshots"
SHOT_DIR.mkdir(parents=True, exist_ok=True)

EDGE = (r"C:\Program Files (x86)\Microsoft\Edge\Application"
        r"\msedge.exe")
CHROME = (r"C:\Program Files\Google\Chrome\Application"
          r"\chrome.exe")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _pick_browser() -> str:
    for p in (CHROME, EDGE):
        if Path(p).exists():
            return p
    raise RuntimeError("Neither Chrome nor Edge found.")


def _wait_ready(url: str, deadline: float = 8.0) -> bool:
    """Poll the health endpoint for at most ``deadline`` seconds."""
    last_error: Exception | None = None
    last_status: int | None = None
    stop_at = time.time() + deadline
    while time.time() < stop_at:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                last_status = resp.status
                if resp.status == 200:
                    return True
        except Exception as exc:
            last_error = exc
            time.sleep(0.1)
    if last_error:
        print(f"[shot] health-check error: {last_error}", file=sys.stderr)
    elif last_status is not None:
        print(f"[shot] health-check status: {last_status}", file=sys.stderr)
    return False


def _capture(browser: str, url: str, shot_path: Path,
             window_size: str = "1600,2400",
             wait_ms: int = 4500) -> bool:
    cmd = [
        browser,
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--hide-scrollbars",
        f"--window-size={window_size}",
        f"--virtual-time-budget={wait_ms}",
        f"--screenshot={shot_path}",
        url,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if res.returncode != 0:
        print(f"[shot] browser exit code {res.returncode}",
              file=sys.stderr)
        print(res.stderr[:2000], file=sys.stderr)
        return False
    return shot_path.exists() and shot_path.stat().st_size > 0


def main() -> int:
    import sys
    sys.path.insert(0, str(ROOT))
    import audit_web  # noqa: E402
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), audit_web.AuditWebHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{port}"
        if not _wait_ready(base + "/api/health"):
            print("[shot] server failed to start", file=sys.stderr)
            return 1
        browser = _pick_browser()
        print(f"[shot] using browser: {browser}")
        cases = [
            ("v421_dash_01_legal_pass",         "pass.png"),
            ("v421_dash_06_text_struct_conflict", "review.png"),
            ("v421_dash_02_explicit_block",       "block.png"),
            ("full_case_01_stable_hypertension", "full_case_pass.png"),
            ("full_case_03_metformin_moderate_ckd", "full_case_review.png"),
            ("full_case_06_statin_macrolide_grapefruit", "full_case_block.png"),
        ]
        for sid, fname in cases:
            url = f"{base}/?scenario={sid}"
            shot = SHOT_DIR / fname
            print(f"[shot] {sid} → {shot.name} ...")
            ok = _capture(browser, url, shot)
            print(f"[shot]   {'OK' if ok else 'FAIL'} "
                  f"({shot.stat().st_size if shot.exists() else 0} bytes)")
        return 0
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
