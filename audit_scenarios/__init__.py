"""Unified audit scenario loader (v4.2.1 refactor).

All console Demo, Web Demo, and integration-test scenarios live in a
single source-of-truth file: ``data/audit_scenarios.json``. This package
exposes the public loader API and validators.

Public API:

- :func:`load_all_scenarios`     -- parses ``data/audit_scenarios.json``
                                    once and caches the result.
- :func:`get_scenario_by_id`      -- lookup a single scenario.
- :func:`list_console_scenarios`  -- ``enabled_for_console=true``.
- :func:`list_web_scenarios`      -- ``enabled_for_web=true``.
- :func:`validate_scenario`       -- structural validation of one scenario.
- :func:`validate_all_scenarios`  -- structural validation of the whole
                                    dataset; raises on the first error.

Important contract
-----------------
- ``audit_input`` is the only payload that ever reaches
  ``DialogueSafetyEngine.audit_payload``.
- ``case_profile``, ``retrieved_evidence``, ``tags``, ``category``, and
  ``expected_assertions`` are presentation / test metadata. They MUST
  NEVER leak into ``audit_payload``.
- The loader returns deep copies so callers cannot mutate the cached
  global state.
"""

from audit_scenarios.loader import (
    CATEGORIES,
    SCENARIOS_FILE,
    get_scenario_by_id,
    list_console_scenarios,
    list_web_scenarios,
    load_all_scenarios,
    validate_all_scenarios,
    validate_scenario,
)

__all__ = [
    "CATEGORIES",
    "SCENARIOS_FILE",
    "get_scenario_by_id",
    "list_console_scenarios",
    "list_web_scenarios",
    "load_all_scenarios",
    "validate_all_scenarios",
    "validate_scenario",
]
