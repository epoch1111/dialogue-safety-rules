"""Audit logger.

Writes each ``AuditReport`` to ``logs/audit/<timestamp>.json`` so that an
operator can review the recall / evaluation decisions after the fact.

Logging failures are never fatal: they print a warning and continue.
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from safety.models import AuditReport


class AuditLogger:
    def __init__(self, log_dir: str | Path = "logs/audit") -> None:
        self._log_dir = Path(log_dir)

    def write(self, report: AuditReport, tag: Optional[str] = None) -> Optional[Path]:
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            tag_part = f"_{tag}" if tag else ""
            path = self._log_dir / f"audit_{timestamp}{tag_part}.json"
            path.write_text(
                json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return path
        except OSError as exc:
            print(f"[audit_logger] warning: failed to write audit log: {exc}", file=sys.stderr)
            return None