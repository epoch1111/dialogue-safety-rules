# Input Schema Reference (v4.2.1, schema_version 1.0)

This document is the authoritative specification of the audit input
contract for `dialogue-safety-rules-4.2.1`. The on-disk JSON Schemas
in `schemas/` are the machine-readable form of this document.

Schema version `1.0` is unchanged from v4.2.0. v4.2.1 strengthens the
runtime enforcement and removes every silent default that previously
let incomplete inputs reach `PASS`.

## Three versions

| Concept | Value | Notes |
|---|---|---|
| Project version | `4.2.1` | `safety.safety_engine.DialogueSafetyEngine.PROJECT_VERSION` |
| Rule set version | `dialogue-safety-rules-4.2.1` | `rules/manifest.json` |
| Input schema version | `1.0` | `safety/input_models.SUPPORTED_SCHEMA_VERSIONS` |

These three are independent. Bumping the rule set does NOT require
a new schema version. Bumping the schema version (a real breaking
change) requires a major bump.

## Production entry point

```python
from safety import DialogueSafetyEngine

engine = DialogueSafetyEngine("rules")
report = engine.audit_payload(
    payload={
        "schema_version": "1.0",
        "patient_state": {...},
        "dialogue_output": {...},
    },
    strict_mode=True,        # production default
    compat_mode=False,       # production default; routes legacy fields via adapter only if True
    debug=False,             # True populates retrieval_trace and evaluation_trace
)
```

The legacy `audit(patient_state=..., dialogue_output=...)` shim
defaults to `compat_mode=True` for backward compatibility with v4.1
/ v4.2.0 test fixtures.

## Top-level envelope

```json
{
  "schema_version": "1.0",
  "patient_state": {...},
  "dialogue_output": {...}
}
```

- `schema_version` is **required** when `strict_mode=True`.
  Missing → `INPUT_SCHEMA_VERSION_MISSING` (REVIEW).
  Unsupported (anything other than `"1.0"`) → `INPUT_SCHEMA_VERSION_UNSUPPORTED` (REVIEW).
- `additionalProperties: false`.

## `patient_state`

| Field | Type | Required | Notes |
|---|---|---|---|
| `patient_id` | string | yes | Synthetic ID; no PII. |
| `current_medications` | array of objects | yes | Can be empty. |
| `disease_codes` | array of string | yes | Can be empty. |
| `measurements` | object | no | Map of kind → measurement object. |
| `clinical_flags` | object | no | Boolean-or-null flags. |
| `allergies` | array of string | no | |

Legacy flat fields (`egfr`, `latest_systolic_bp_mmHg`, ...) are
accepted as a convenience by `compat_mode=True`. They are not part
of the strict v4.2.1 contract.

### `current_medications[].required`

| Field | Type | Required | Notes |
|---|---|---|---|
| `drug_id` | string | yes | Canonical; validated against the alias table. |
| `drug_name` | string | yes | Display name; must map to the same `drug_id`. |
| `status` | enum | yes | `active` / `held` / `stopped` / `completed` / `unknown`. |

### `current_medications[].optional`

| Field | Type | Notes |
|---|---|---|
| `dose_value` | number | Finite; ≥ 0. |
| `dose_unit` | enum | `mcg` / `mg` / `g` / `IU`. |
| `frequency_per_day` | number | > 0. |
| `route` | enum | `oral` / `iv` / `im` / `sc` / `inhale` / `topical` / `other`. |

Legacy fields accepted by `LegacyInputAdapter`:
- `name` → becomes `drug_name` (compat_mode only)

### `measurements.<kind>`

Valid kinds: `egfr`, `systolic_bp`, `diastolic_bp`, `glucose`,
`serum_potassium`, `uric_acid`.

| Field | Type | Required | Notes |
|---|---|---|---|
| `value` | number | yes | Finite (no NaN / Infinity). |
| `unit` | string | yes | Enum per kind — see table below. |
| `observed_at` | string | yes | ISO 8601 timestamp **with timezone** (`+08:00` or `Z`). |
| `source` | enum | yes | `laboratory` / `home_measurement` / `clinic_visit` / `wearable` / `patient_self_report` / `other`. |
| `confirmed` | boolean | yes | `false` → measurement NOT trusted for safety decisions. |

Allowed units per kind:

| Kind | Allowed units |
|---|---|
| `egfr` | `mL/min/1.73m2` |
| `systolic_bp`, `diastolic_bp` | `mmHg` |
| `glucose` | `mmol/L`, `mg/dL` |
| `serum_potassium` | `mmol/L`, `mEq/L` |
| `uric_acid` | `umol/L`, `μmol/L`, `mg/dL` |

Bad unit → `INPUT_MEASUREMENT_UNIT_NOT_ALLOWED` (REVIEW).
Bad date → `INPUT_MEASUREMENT_OBSERVED_AT_INVALID` (REVIEW).
Bad source → `INPUT_MEASUREMENT_SOURCE_INVALID` (REVIEW).
`confirmed=false` for a required safety field →
`INPUT_MEASUREMENT_NOT_CONFIRMED` (REVIEW).

## `dialogue_output`

| Field | Type | Required | Notes |
|---|---|---|---|
| `reply_text` | string | yes | Free text; used for consistency checking and the keyword scanner. Required even when empty. |
| `medication_actions` | array | yes | Can be empty. |
| `food_advice` | array | yes | Can be empty. |
| `exercise_advice` | array | yes | Can be empty. |
| `care_actions` | array | yes | Can be empty. |
| `requires_review` | boolean | yes | LLM self-declared uncertainty flag. |
| `uncertainty_reasons` | array of string | yes | Can be empty. |

`requires_review=true` or non-empty `uncertainty_reasons` →
`decision_basis = ["LLM_DECLARED_UNCERTAINTY"]`, decision `REVIEW`,
`original_llm_reply_was_sent = false`.

### `medication_actions[]`

| Field | Type | Required | Notes |
|---|---|---|---|
| `drug_id` | string | yes | Canonical. |
| `drug_name` | string | yes | Display name. |
| `action` | enum | yes | `start` / `continue` / `increase` / `decrease` / `stop` / `hold` / `avoid_start` / `replace`. |

Action-specific required fields:

| Action | Required additional fields |
|---|---|
| `start`, `increase`, `decrease`, `replace` | `dose_value`, `dose_unit`, `frequency_per_day`, `route` |
| `continue` | either (a) full dose block + `route`, or (b) `use_current_regimen=true` AND the matching `current_medications` entry has a full dose block |
| `replace` | also requires `replace_drug_id` (and `replace_drug_name`) — the source drug must be `active` in the regimen |
| `stop`, `hold`, `avoid_start` | `route` may be `null` |

Optional fields: `dose_value`, `dose_unit`, `frequency_per_day`,
`route`, `duration_days`, `use_current_regimen`, `replace_drug_id`,
`replace_drug_name`.

Legacy fields accepted by `LegacyInputAdapter`:
- `drug` → becomes `drug_name` (compat_mode only)
- `replace_drug` → becomes `replace_drug_name` (compat_mode only)

### `food_advice[]`

| Field | Type | Required | Notes |
|---|---|---|---|
| `food_concept_id` | string | yes | Standard food concept; validated against the curated food terminology. |
| `food_name` | string | yes | Display name. |
| `action` | enum | yes | `recommend` / `allow` / `limit` / `avoid`. |

Legacy: `food`, `concept` → become `food_name` / `food_concept_id`.

### `exercise_advice[]`

| Field | Type | Required | Notes |
|---|---|---|---|
| `activity_concept_id` | string | yes | Standard activity concept; validated. |
| `activity_name` | string | yes | Display name. |
| `intensity` | enum | yes | `light` / `moderate` / `vigorous`. |
| `action` | enum | yes | `recommend` / `allow` / `limit` / `avoid` / `stop`. |

Legacy: `activity`, `concept` → become `activity_name` /
`activity_concept_id`.

### `care_actions[]`

| Field | Type | Required | Notes |
|---|---|---|---|
| `type` | enum | yes | `repeat_measurement` / `urgent_medical_evaluation` / `emergency_symptom_screening` / `monitor` / `follow_up`. |
| `target` | string | yes | Free text describing what to act on. |
| `action` | enum | yes | `recommend` / `perform`. |
| `urgency` | enum \| null | no | `immediate` / `same_day` / `within_24h` / `routine` / `null`. |

## `strict_mode` and `compat_mode`

| Mode | Behavior |
|---|---|
| `strict_mode=True, compat_mode=False` (default) | Strict v4.2.1 contract. Legacy fields rejected. `schema_version` required. |
| `strict_mode=True, compat_mode=True` | Legacy fields accepted via `LegacyInputAdapter`. `DEPRECATED_INPUT_SCHEMA` finding emitted. |
| `strict_mode=False` | Reserved for debugging. |

`audit()` defaults to `compat_mode=True` for v4.1 / v4.2.0 caller
backward compatibility.

## `decision_basis` tokens

| Token | Meaning |
|---|---|
| `INPUT_VALIDATION` | One or more REVIEW+ validation issues. |
| `MISSING_CONTEXT` | Required patient context missing or stale. |
| `TEXT_STRUCTURE_CONSISTENCY` | SYS001..SYS008 system finding. |
| `MEDICAL_RULE` | One or more rule violations (BLOCK or REVIEW severity). |
| `LLM_DECLARED_UNCERTAINTY` | `requires_review=true` or non-empty `uncertainty_reasons`. |
| `SYSTEM_ERROR` | Unhandled exception in `audit()`. |

## Migration from v4.1 / v4.2.0

| v4.1 / v4.2.0 field | v4.2.1 replacement |
|---|---|
| `current_medications[].name` | `current_medications[].drug_name` |
| `medication_actions[].drug` | `medication_actions[].drug_name` (+ `drug_id` if you can) |
| `food_advice[].food` | `food_advice[].food_name` (+ `food_concept_id` if you can) |
| `food_advice[].concept` | `food_advice[].food_concept_id` |
| `exercise_advice[].activity` | `exercise_advice[].activity_name` (+ `activity_concept_id` if you can) |
| `exercise_advice[].concept` | `exercise_advice[].activity_concept_id` |
| `medication_actions[].replace_drug` | `medication_actions[].replace_drug_name` (+ `replace_drug_id`) |
| (silent route="oral" / dose_unit="mg") | explicit `route` and `dose_unit` |
| (silent drug_id = drug_name.lower()) | explicit `drug_id` |

For callers that cannot migrate immediately, set
`compat_mode=True` on `audit_payload(...)`. The audit trail will
emit `DEPRECATED_INPUT_SCHEMA` so the legacy dependency is
discoverable.