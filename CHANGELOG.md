# Changelog

All notable changes to the Dialogue Agent Safety Rule Engine are
documented here. The engine is shipped with a `ruleset_version`
string in `rules/manifest.json`; this string bumps independently
from `project_version` (the engine itself) and `input_schema_version`
(the JSON contract).

## v4.2.1 — strict input contract + required-context retrieval (2026-07-21)

### Summary

v4.2.1 hardens the strict input contract introduced in v4.2.0 and
rebuilds the `RequiredContextChecker` to consult only precise
per-channel indexes instead of the full rule base. The change makes
the safety engine safer for production use: legacy fields are no
longer silently accepted, missing required fields can no longer be
swept under the rug, and the LLM's own uncertainty declaration now
forces `REVIEW`.

### Project / rule / schema versions

| Concept | Value |
|---|---|
| Project version (`engine.PROJECT_VERSION`) | `4.2.1` |
| Rule set version (`manifest.json`) | `dialogue-safety-rules-4.2.1` |
| Input schema version (`schemas/`) | `1.0` (unchanged — strict contract) |

These three are independent. Bumping the rule set does NOT require a
new schema version; bumping the schema version (a real breaking
change) requires a major bump.

### New formal entry point: `engine.audit_payload()`

```python
engine.audit_payload(
    payload={
        "schema_version": "1.0",
        "patient_state": {...},
        "dialogue_output": {...},
    },
    strict_mode=True,        # default; rejects legacy fields
    compat_mode=False,       # default; production must NOT enable
    debug=False,
)
```

The legacy `engine.audit(patient_state=..., dialogue_output=...)`
shim is preserved for v4.1 / v4.2.0 callers but defaults to
`compat_mode=True` so old fixture data still works.

### Strict input contract (`strict_mode=True`)

- `schema_version` must be supplied. Missing or unsupported values
  force `REVIEW` (codes `INPUT_SCHEMA_VERSION_MISSING` /
  `INPUT_SCHEMA_VERSION_UNSUPPORTED`).
- Legacy field names are rejected with `INPUT_LEGACY_FIELD_NOT_ALLOWED`:
  - `current_medications[].name`
  - `current_medications[].drug`
  - `medication_actions[].name`
  - `medication_actions[].drug`
  - `food_advice[].food`
  - `food_advice[].concept`
  - `exercise_advice[].activity`
  - `exercise_advice[].concept`
- `audit()` no longer auto-fills `schema_version` when called via the
  legacy kwarg path. `audit_payload(payload=...)` is the only way to
  build a strict envelope.

### Compat mode (`compat_mode=True`)

- Old fields are routed through `safety.legacy_adapter.LegacyInputAdapter`.
- A `DEPRECATED_INPUT_SCHEMA` finding is emitted with `severity=INFO`
  so the audit trail records the legacy path.
- Compat mode does NOT mask unknown drugs, illegal enums, type
  errors, drug-id/name mismatches, or unit conversion failures.

### New Schema fields wired through the engine

The pipeline now consumes the strict v4.2.0 fields end-to-end:

- `current_medications[].drug_id` and `current_medications[].drug_name`
- `medication_actions[].drug_id` and `medication_actions[].drug_name`
- `food_advice[].food_concept_id` and `food_advice[].food_name`
- `exercise_advice[].activity_concept_id` and `exercise_advice[].activity_name`
- `medication_actions[].replace_drug_id` and `medication_actions[].replace_drug_name`
- `dialogue_output.requires_review` and `dialogue_output.uncertainty_reasons`

The model layer never auto-derives `drug_id` from `drug_name` or
defaults `route`/`dose_unit` anymore. Any missing required field
forces `REVIEW` with a stable code.

### Removed silent defaults

| Default | Status in v4.2.1 |
|---|---|
| `route = "oral"` | removed |
| `dose_unit = "mg"` | removed |
| `care_action.action = "recommend"` | removed |
| `drug_id = drug_name.strip().lower()` | removed |
| `food_concept_id = food_name.strip().lower()` | removed |
| `activity_concept_id = activity_name.strip().lower()` | removed |
| Unknown status → silently `"active"` | removed |
| `schema_version = "1.0"` silently injected | removed from strict entry |

### Runtime Schema validation

`safety/input_validator.py` runs a strict validator on every payload:

- Top-level type checks
- `schema_version` enum
- Required vs optional fields
- `additionalProperties: false`
- Array element types
- Enum values
- Numeric ranges and finiteness (NaN / Infinity rejected)
- ISO 8601 timestamps with timezone (timezone required)
- Measurement contract: `value`, `unit`, `observed_at`, `source`,
  `confirmed`; each kind (`egfr`, `systolic_bp`, …) has a fixed set
  of allowed units. Bad units → `INPUT_MEASUREMENT_UNIT_NOT_ALLOWED`.
- Source enum: `laboratory`, `home_measurement`, `clinic_visit`,
  `wearable`, `patient_self_report`, `other`.
- `confirmed=false` for required safety fields → `INPUT_MEASUREMENT_NOT_CONFIRMED`.

### `requires_review` / `uncertainty_reasons` decision participation

Both LLM-declared flags now participate in the decision via a new
`decision_basis` token: `LLM_DECLARED_UNCERTAINTY`. Either of:

```json
{"requires_review": true}
{"uncertainty_reasons": ["..."]}
```

forces `REVIEW` and sets `original_llm_reply_was_sent = false`.

### Standard terminology validation

`InputValidator` cross-checks:

- `disease_codes` against the curated disease terminology table
  (`aliases.json:disease_aliases`).
- `food_concept_id` against the curated food keywords drawn from
  active `drug_food` / `disease_food` rules.
- `activity_concept_id` against the curated exercise intensities plus
  an allowlist of common activity concepts.
- `drug_id` ↔ `drug_name` agreement via the alias table.
- `replace_drug_id` ↔ `replace_drug_name` agreement via the alias table.

Unknown or mismatched terms emit stable findings:
`INPUT_UNKNOWN_DISEASE_CODE`, `INPUT_UNKNOWN_FOOD_CONCEPT`,
`INPUT_UNKNOWN_ACTIVITY_CONCEPT`, `INPUT_DRUG_ID_NAME_MISMATCH`,
`INPUT_REPLACE_DRUG_ID_NAME_MISMATCH`.

### Required-context precise indexes

`safety/rule_repository.py` now builds eight precise per-channel
indexes:

- `_drug_required_field_index[drug]`
- `_drug_action_required_field_index[(drug, action)]`
- `_drug_food_required_field_index[drug]`
- `_drug_exercise_required_field_index[drug]`
- `_disease_food_required_field_index[code]`
- `_disease_exercise_required_field_index[code]`
- `_risk_required_field_index[risk_code]`
- `_care_required_field_index[care_type]`

`safety/required_context_checker.py` consults ONLY these indexes.
It NEVER iterates `iter_active_rules()`.

The report's `developer_diagnostics.required_context_retrieval_trace`
exposes the per-channel `scanned_rule_count` so tests can prove
no full scan.

### Required-context logic fixes

- `stop` / `hold` / `avoid_start` no longer trigger drug-safety
  required-context rules (no eGFR check for "stop metformin").
- `drug_exercise` channel only fires when the dialogue recommends
  / allows a matching-intensity exercise.
- `drug_food` channel only fires when the dialogue recommends / allows
  the food. `action=avoid` does not require recommend-side context.
- `disease_food` / `disease_exercise` channels require BOTH the disease
  to be present AND the recommendation direction to match.
- `RequiredContextChecker` looks at both current medications and
  resulting-action drugs, so an existing insulin user who is told to
  exercise triggers the drug-exercise channel even when the response
  doesn't issue a new insulin prescription.

### Performance

- `RequiredContextChecker` runs in O(matched keys) time, not
  O(|active rules|).
- The 1000 and 10000 unique-field stress tests confirm:
  `total_rules_consulted` stays under 100 even with 10 000 rules in
  the repo, while the candidate set is still exactly
  `{R002_METFORMIN_EGFR_LT_30}`.

### Patient-visible vs developer diagnostics

`patient_visible_response` never contains rule IDs, stack traces, or
internal exception messages. New contextual messages:

- `MISSING_CONTEXT` → "当前缺少完成安全判断所需的患者信息…"
- `LLM_DECLARED_UNCERTAINTY` → "本次回复中模型自身声明存在不确定性…"
- `SYSTEM_ERROR` → "系统当前无法安全地核验该建议…"

`developer_diagnostics` carries the full structured detail for
operators.

### Test count

- Baseline (v4.2.0): **167** tests passing.
- v4.2.1: **198** tests passing (167 original + 31 new regression
  tests in `tests/test_v421_strict_input.py`).

### Files added / changed in v4.2.1

- `safety/input_models.py` — strict v4.2.1 dataclasses (no silent
  defaults; `has_legacy_field` tracking).
- `safety/input_validator.py` — strict validation +
  `strict_mode_legacy_check` + measurement contract + terminology
  validation.
- `safety/safety_engine.py` — new `audit_payload()` entry, fail-closed
  wrapper, `requires_review` / `uncertainty_reasons` participation.
- `safety/normalizer.py` — removed silent `route="oral"` /
  `dose_unit="mg"` defaults.
- `safety/rule_repository.py` — precise required-context indexes
  + terminology accessors.
- `safety/required_context_checker.py` — fully rebuilt to use the
  precise indexes; emits retrieval trace.
- `safety/legacy_adapter.py` — NEW. `LegacyInputAdapter` for compat_mode.
- `safety/models.py` — `MedicationAction.route`/`dose_unit` no longer
  default; `CareAction.action` no longer defaults to `recommend`.
- `audit_web.py` — 12 v4.2.1 scenarios, calls `audit_payload()`.
- `run_trace_demo.py` — v4.2.1 input + 20-step trace output.
- `tests/test_v421_strict_input.py` — NEW. 31 regression tests.
- `rules/manifest.json` — `ruleset_version: dialogue-safety-rules-4.2.1`.
- `README.md`, `INPUT_SCHEMA.md`, `RULE_AUTHORING.md`,
  `CHANGELOG.md` — updated to v4.2.1.

## v4.2.0 — strict input + required context (2026-07-20)

- Strict JSON Schema input contract.
- New `InputValidator` with JSON-shape validation.
- `RequiredContextChecker` walks every recalled rule and emits
  missing-context findings.
- DSL extensions: `conditions.all/any/not` and `range.gte/gt/lte/lt`.
- `drug_only_rule_index` replaces the legacy `simple_index`.
- Unified report structure.
- Status enums expanded from 2 to 5.
- 1000/10000 unique-field recall-pressure tests.

## v4.1.1 — baseline

Initial demo with implicit input contract, simple `simple_index`
recall, and 2-way medication status.