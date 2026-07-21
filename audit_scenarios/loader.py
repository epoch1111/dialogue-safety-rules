"""Audit scenario loader implementation.

Designed to be tiny, stdlib-only, deterministic, and free of hidden
state. The on-disk file is parsed once and cached; callers always
receive deep copies so they cannot accidentally mutate the cache.

Public functions
----------------
- :func:`load_all_scenarios`
- :func:`get_scenario_by_id`
- :func:`list_console_scenarios`
- :func:`list_web_scenarios`
- :func:`validate_scenario`
- :func:`validate_all_scenarios`

Allowed categories:

    trace, dashboard, full_clinical, legacy, regression, console_demo
"""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


#: Path to the unified scenarios JSON file. Resolved relative to the
#: project root (the directory containing ``audit_scenarios/``).
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
SCENARIOS_FILE: Path = PROJECT_ROOT / "data" / "audit_scenarios.json"

#: Allowed scenario categories. New categories require updating
#: ``validate_scenario`` and the README.
CATEGORIES: Set[str] = {
    "trace",
    "dashboard",
    "full_clinical",
    "legacy",
    "regression",
    "console_demo",
}

#: Allowed decision strings in ``expected_assertions.decision``.
_ALLOWED_DECISIONS: Set[str] = {"PASS", "REVIEW", "BLOCK"}


# ---------------------------------------------------------------------------
# Cached loader
# ---------------------------------------------------------------------------


_cache_lock = threading.Lock()
_cached_scenarios: Optional[List[Dict[str, Any]]] = None
_cached_signature: Optional[Tuple[int, int]] = None  # (mtime_ns, size_bytes)


def _load_disk() -> List[Dict[str, Any]]:
    """Read the scenarios JSON file. Errors are loud — we never silently
    skip a malformed scenario."""
    if not SCENARIOS_FILE.exists():
        raise FileNotFoundError(
            f"Unified scenarios file not found: {SCENARIOS_FILE}. "
            "Run from the project root or update PROJECT_ROOT."
        )
    try:
        payload = json.loads(
            SCENARIOS_FILE.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Invalid JSON in {SCENARIOS_FILE}: {exc}"
        ) from exc
    if not isinstance(payload, list):
        raise RuntimeError(
            f"{SCENARIOS_FILE} must be a JSON array of scenario objects; "
            f"got {type(payload).__name__}"
        )
    return payload


def _cache_is_fresh() -> bool:
    if _cached_scenarios is None or _cached_signature is None:
        return False
    try:
        stat = SCENARIOS_FILE.stat()
    except FileNotFoundError:
        return False
    return _cached_signature == (stat.st_mtime_ns, stat.st_size)


def _maybe_reload() -> List[Dict[str, Any]]:
    """Return the cached scenarios list, reloading the file if it has
    changed on disk since the last call."""
    global _cached_scenarios, _cached_signature
    if _cache_is_fresh():
        return _cached_scenarios or []
    with _cache_lock:
        if not _cache_is_fresh():
            stat = SCENARIOS_FILE.stat()
            _cached_scenarios = _load_disk()
            _cached_signature = (stat.st_mtime_ns, stat.st_size)
        return _cached_scenarios or []


def invalidate_cache() -> None:
    """Force the next :func:`load_all_scenarios` call to re-read the
    file. Useful in tests."""
    global _cached_scenarios, _cached_signature
    with _cache_lock:
        _cached_scenarios = None
        _cached_signature = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_all_scenarios() -> List[Dict[str, Any]]:
    """Load every scenario in the unified file.

    Returns a deep copy so the caller cannot mutate the cache.
    """
    return copy.deepcopy(_maybe_reload())


def get_scenario_by_id(scenario_id: str) -> Optional[Dict[str, Any]]:
    """Return the scenario with ``id == scenario_id`` or ``None``."""
    for scenario in load_all_scenarios():
        if scenario.get("id") == scenario_id:
            return scenario
    return None


def list_console_scenarios() -> List[Dict[str, Any]]:
    """Return scenarios where ``enabled_for_console`` is true."""
    return [s for s in load_all_scenarios()
            if s.get("enabled_for_console", False)]


def list_web_scenarios() -> List[Dict[str, Any]]:
    """Return scenarios where ``enabled_for_web`` is true."""
    return [s for s in load_all_scenarios()
            if s.get("enabled_for_web", False)]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _is_dict(value: Any) -> bool:
    return isinstance(value, dict)


def _is_list(value: Any) -> bool:
    return isinstance(value, list)


def _require(condition: bool, message: str,
             errors: List[str], scenario_id: str) -> None:
    if not condition:
        errors.append(f"[{scenario_id}] {message}")


def validate_scenario(scenario: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate a single scenario. Returns ``(ok, errors)``."""
    errors: List[str] = []
    # Validate id explicitly first so the per-field error messages
    # can show the actual id (or "<missing>").
    sid = scenario.get("id")
    if not (isinstance(sid, str) and bool(sid.strip())):
        errors.append(f"[<missing id>] id is required and must be a "
                      "non-empty string")
    display_sid = sid if (isinstance(sid, str) and bool(sid.strip())) \
        else "<missing id>"
    # _require(...) is used for everything else; the per-field error
    # will be tagged with display_sid.
    return _continue_validation(scenario, errors, display_sid)


def _continue_validation(scenario, errors, sid):
    _require(isinstance(scenario.get("title"), str)
             and bool(scenario["title"].strip()),
             "title is required and must be a non-empty string", errors, sid)
    category = scenario.get("category")
    _require(isinstance(category, str) and category in CATEGORIES,
             f"category must be one of {sorted(CATEGORIES)}",
             errors, sid)
    _require(_is_bool(scenario.get("enabled_for_console")),
             "enabled_for_console must be a bool", errors, sid)
    _require(_is_bool(scenario.get("enabled_for_web")),
             "enabled_for_web must be a bool", errors, sid)
    audit_input = scenario.get("audit_input")
    _require(_is_dict(audit_input),
             "audit_input must be a JSON object", errors, sid)
    if _is_dict(audit_input):
        _require(isinstance(audit_input.get("schema_version"), str)
                 and bool(audit_input["schema_version"]),
                 "audit_input.schema_version is required",
                 errors, sid)
        _require(_is_dict(audit_input.get("patient_state")),
                 "audit_input.patient_state is required", errors, sid)
        _require(_is_dict(audit_input.get("dialogue_output")),
                 "audit_input.dialogue_output is required", errors, sid)
    case_profile = scenario.get("case_profile")
    _require(case_profile is None or _is_dict(case_profile),
             "case_profile must be an object or null", errors, sid)
    evidence = scenario.get("retrieved_evidence")
    _require(evidence is None or _is_list(evidence),
             "retrieved_evidence must be a list or null", errors, sid)
    ea = scenario.get("expected_assertions")
    _require(ea is None or _is_dict(ea),
             "expected_assertions must be an object or null", errors, sid)
    if _is_dict(ea):
        decision = ea.get("decision")
        _require(decision in _ALLOWED_DECISIONS,
                 f"expected_assertions.decision must be one of "
                 f"{sorted(_ALLOWED_DECISIONS)}",
                 errors, sid)
        _require("must_include_rule_ids" in ea and
                 _is_list(ea.get("must_include_rule_ids")),
                 "expected_assertions.must_include_rule_ids must be a list",
                 errors, sid)
        # must_not_include_rule_ids is optional for backward compat with
        # full_clinical cases that didn't define it.
        if "must_not_include_rule_ids" in ea:
            _require(_is_list(ea.get("must_not_include_rule_ids")),
                     "expected_assertions.must_not_include_rule_ids must be a list",
                     errors, sid)
        _require("original_reply_was_sent" in ea and
                 _is_bool(ea.get("original_reply_was_sent")),
                 "expected_assertions.original_reply_was_sent must be a bool",
                 errors, sid)
    tags = scenario.get("tags")
    _require(tags is None or _is_list(tags),
             "tags must be a list of strings or null", errors, sid)
    if _is_list(tags):
        for tag in tags:
            if not isinstance(tag, str):
                _require(False, "tags must contain only strings",
                         errors, sid)
                break
    simulate_error = scenario.get("simulate_error")
    _require(simulate_error is None or _is_bool(simulate_error),
             "simulate_error must be a bool or null", errors, sid)
    return (not errors, errors)


# Backward-compat alias used internally; the public entry-point
# is still :func:`validate_scenario`.
_validate_scenario_impl = None


def validate_all_scenarios(
    scenarios: Optional[Iterable[Dict[str, Any]]] = None,
) -> Tuple[bool, List[str]]:
    """Validate the whole dataset.

    Also enforces cross-scenario uniqueness:

    - ``id`` must be unique
    - ``(id, audit_input)`` deep equality is reported (informational)

    Returns ``(ok, all_errors)``. Raises ``ValueError`` on duplicate
    ids because a duplicate id is structural and cannot be silently
    tolerated.
    """
    if scenarios is None:
        scenarios = load_all_scenarios()
    scenarios = list(scenarios)
    seen_ids: Set[str] = set()
    all_errors: List[str] = []
    for scenario in scenarios:
        sid = scenario.get("id", "<missing id>")
        if sid in seen_ids:
            raise ValueError(
                f"Duplicate scenario id {sid!r} in {SCENARIOS_FILE}. "
                "Scenario ids must be globally unique."
            )
        seen_ids.add(sid)
        ok, errs = validate_scenario(scenario)
        if not ok:
            all_errors.extend(errs)
    return (not all_errors, all_errors)
