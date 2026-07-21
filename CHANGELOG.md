# Changelog

All notable changes to the Dialogue Agent Safety Rule Engine are
documented here. Versions follow SemVer-ish rules. The engine is
shipped with a `ruleset_version` string in `rules/manifest.json`; this
file mirrors that string.

## v4.2.0 — 2026-07-21

### Strict input contract
- New JSON Schemas in `schemas/`:
  - `audit_input.schema.json`
  - `patient_state.schema.json`
  - `dialogue_output.schema.json`
- New module `safety/input_models.py` with `StrictAuditInput`,
  `PatientStateInput`, `DialogueOutputInput`, etc.
- New module `safety/input_validator.py` (`InputValidator`) that
  enforces the contract.
- New module `safety/unit_converter.py` (`convert_mass_to_mg`,
  `daily_total_mg`, `is_finite_number`) — `mcg`, `mg`, `g` only;
  `IU` / `mL` are REVIEW, never silently passed.
- New module `safety/required_context_checker.py`
  (`RequiredContextChecker`) walks every recalled rule and emits
  `missing_context_fields` entries.

### Engine changes
- `DialogueSafetyEngine.audit()` is wrapped in a fail-closed try/except
  that converts any uncaught exception into a REVIEW report with
  `decision_basis = ["SYSTEM_ERROR"]`.
- New pipeline phase `input_validation_ms` and `required_context_ms`
  recorded in `TimingBreakdown`.
- New `decision_basis` list on `AuditReport`. Priority remains
  `BLOCK > REVIEW > PASS`.
- New unified finding buckets:
  `input_validation_errors`, `missing_context_fields`,
  `consistency_violations`, `medical_violations`, `system_findings`,
  `all_findings`. The legacy `violations` field is preserved as an
  alias of `medical_violations`.

### DSL
- `parameters.conditions` supports `all` / `any` / `not` predicates
  with explicit `field` / `operator` / `value` fields.
- `parameters.range` supports `gte` / `gt` / `lte` / `lt` blocks.
- `parameters.match_mode = "all" | "any"` makes list semantics explicit.
- The `field`/`operator`/`threshold` triplet is still accepted for
  backward compatibility.

### Recall discipline
- New `drug_only_rule_index` is the tightest recall path (only
  `max_daily_dose` in production).
- The legacy `simple_index` union fallback is **removed** from the
  candidate selector.
- New recall-pressure test
  (`tests/test_v420_recall_pressure.py`) proves that 1000 / 10000
  synthetic `patient_state` rules sharing `drug=metformin` but each
  with a unique field DO NOT pollute the candidate set when only one
  real rule (R002) is loaded.

### Medication status
- Status enum expanded from 2-way `active`/`inactive` to 5-way
  `active` / `held` / `stopped` / `completed` / `unknown`.
- `DrugContext` tracks `held_drugs`, `stopped_drugs`,
  `completed_drugs`, `unknown_status_drugs` separately.
- `held` status excludes the drug from `resulting_drugs`; R019-style
  drug-drug rules do not fire on held drugs.
- `avoid_start` does NOT remove drugs from `resulting_drugs`.
- `replace` requires `replace_drug_id`; the source drug must be in
  the active regimen, otherwise REVIEW
  (`INPUT_REPLACE_SOURCE_NOT_ACTIVE`).

### Consistency checker
- New `SYS007_UNIT_NOT_CONVERTIBLE` (v4.2.0).
- New `SYS008_TEXT_CONFLICT_DRUG_MENTION` (v4.2.0).

### Rule files
- `rules/manifest.json` version bumped to
  `dialogue-safety-rules-4.2.0`.
- `R010_METFORMIN_EGFR_30_TO_45` rewritten to use the `range` DSL so
  it does NOT fire when `eGFR < 30` (avoids overlap with R002).
- `R019_NSAID_ACEI` split into three explicit rules
  (`R019_IBUPROFEN_LISINOPRIL`, `R019_CELECOXIB_LISINOPRIL`,
  `R019_ETORICOXIB_LISINOPRIL`) so each NSAID + ACEI pair fires
  independently.

### Visibility & diagnostics
- `patient_visible_response` never contains internal rule IDs or
  exception traces.
- `reviewer_message` carries structured `decision=<X>; basis=<Y>`.
- `developer_diagnostics` includes the wrapped input validation
  issues, the normalized draft, and the drug context.

### Tests
- 41 new tests in `tests/test_v420_strict_input.py` and
  `tests/test_v420_recall_pressure.py`.
- All 126 existing tests still pass.
- **Total: 167 unit tests.**

### Web UI
- New 11-scenario gallery (A–K) accessible via the scenario dropdown.
- Decision bar now shows `decision_basis` badges.
- Findings split into four cards: input validation, missing context,
  consistency, medical.
- Display panels for normalized patient state, drug context, candidate
  rule ids, evaluated rule ids, retrieval channels, timing breakdown.
- `K` scenario: client can send `simulate_error=true` to force the
  engine through the fail-closed path; the original LLM reply is
  NOT sent.

## v4.1.1 — 2026-07-20

- Composite `drug_field_index` is the sole recall path for
  `patient_state` rules; `field_only_rule_index` is reserved for
  forward-compatible field-only rules.
- Per-channel retrieval trace and per-condition evaluation trace
  populated only when `audit(debug=True)`.
- 21 Bad Case scenarios (BC01..BC21) verified by
  `tests/test_bad_cases.py`.

## v4.1.0

- `replace` and `avoid_start` actions honored.
- `DrugContext.text_mentioned_drugs` and
  `DrugContext.text_dose_drugs` tracked separately.
- `evaluated_risk_rule_ids` exposed in `AuditReport`.
- 8-phase `TimingBreakdown`.

## v4.0.0

- Initial release.
- 126 unit tests + 4 perf sub-tests.