"""Append the trace / dashboard / legacy / full_clinical scenarios to
data/audit_scenarios.json by reading the original Python module
sources.  This script is invoked once during the refactor; it is kept
under tools/ for reference.
"""
import json
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

def _convert_full_clinical_case(case):
    """full_clinical_cases → unified schema.

    Source uses top-level ``patient_state`` / ``dialogue_output``;
    the unified schema wraps them in ``audit_input``.
    """
    return {
        "id": case["id"],
        "title": case["title"],
        "summary": case["summary"],
        "category": "full_clinical",
        "tags": [],
        "enabled_for_console": True,
        "enabled_for_web": True,
        "case_profile": case.get("case_profile"),
        "retrieved_evidence": case.get("retrieved_evidence", []),
        "audit_input": {
            "schema_version": "1.0",
            "patient_state": case["patient_state"],
            "dialogue_output": case["dialogue_output"],
        },
        "expected_assertions": case.get("expected_assertions"),
        "simulate_error": False,
    }

def _convert_trace(c):
    return {
        "id": c["id"],
        "title": c["title"],
        "summary": c["summary"],
        "category": "trace",
        "tags": [],
        "enabled_for_console": False,
        "enabled_for_web": True,
        "case_profile": None,
        "retrieved_evidence": [],
        "audit_input": {
            "schema_version": "1.0",
            "patient_state": c["patient_state"],
            "dialogue_output": c["dialogue_output"],
        },
        "expected_assertions": None,
        "simulate_error": False,
    }

def _convert_legacy(c):
    return {
        "id": c["id"],
        "title": c["title"],
        "summary": c["summary"],
        "category": "legacy",
        "tags": [],
        "enabled_for_console": False,
        "enabled_for_web": True,
        "case_profile": None,
        "retrieved_evidence": [],
        "audit_input": {
            "schema_version": "1.0",
            "patient_state": c["patient_state"],
            "dialogue_output": c["dialogue_output"],
        },
        "expected_assertions": None,
        "simulate_error": False,
    }

def _convert_dashboard(c):
    return {
        "id": c["id"],
        "title": c["title"],
        "summary": c["summary"],
        "category": "dashboard",
        "tags": [],
        "enabled_for_console": False,
        "enabled_for_web": True,
        "case_profile": None,
        "retrieved_evidence": [],
        "audit_input": {
            "schema_version": "1.0",
            "patient_state": c["patient_state"],
            "dialogue_output": c["dialogue_output"],
        },
        "expected_assertions": None,
        "simulate_error": c.get("simulate_error", False),
    }

def main():
    out_file = ROOT / "data" / "audit_scenarios.json"
    existing = json.loads(out_file.read_text(encoding="utf-8"))
    seen = {s["id"] for s in existing}

    # Load audit_web.py for TRACE/DASHBOARD/LEGACY
    aw = _load_module(ROOT / "audit_web.py", "audit_web")
    # Load audit_web_cases/full_clinical_cases.py
    fcc = _load_module(ROOT / "audit_web_cases" / "full_clinical_cases.py",
                       "full_clinical_cases")

    additions = []

    for c in aw.TRACE_SCENARIOS:
        if c["id"] not in seen:
            additions.append(_convert_trace(c))
            seen.add(c["id"])

    for c in aw.DASHBOARD_SCENARIOS:
        if c["id"] not in seen:
            additions.append(_convert_dashboard(c))
            seen.add(c["id"])

    for c in aw.LEGACY_V42_SCENARIOS:
        if c["id"] not in seen:
            additions.append(_convert_legacy(c))
            seen.add(c["id"])

    for c in fcc.FULL_CLINICAL_SCENARIOS:
        if c["id"] not in seen:
            additions.append(_convert_full_clinical_case(c))
            seen.add(c["id"])

    print(f"Adding {len(additions)} more scenarios")
    final = existing + additions
    out_file.write_text(
        json.dumps(final, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Total {len(final)} scenarios written to {out_file}")


if __name__ == "__main__":
    main()
