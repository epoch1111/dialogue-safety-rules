# Input Schema Reference (v4.2.0)

This document is the authoritative specification of the audit input
contract for `dialogue-safety-rules-4.2.0`. The on-disk JSON Schemas in
`schemas/` are the machine-readable form of this document.

## Top-level envelope

```json
{
  "schema_version": "1.0",
  "patient_state": { ... },
  "dialogue_output": { ... }
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `schema_version` | string | yes | Must be `"1.0"`. Other values force REVIEW. |
| `patient_state` | object | yes | See below. |
| `dialogue_output` | object | yes | See below. |

The engine always wraps raw payloads with
`{"schema_version": "1.0", ...}` so legacy callers do not need to
know about the new top-level field. The validator enforces the strict
contract inside the wrapped payload.

## `patient_state`

| Field | Type | Required | Notes |
|---|---|---|---|
| `patient_id` | string | yes | Synthetic identifier only. |
| `current_medications` | array of objects | yes | Every entry MUST be an object. Strings, numbers, null, etc. are rejected. |
| `disease_codes` | array of strings | yes | Empty list allowed. Unknown codes are silently mapped via the alias table. |
| `measurements` | object | no | Nested v4.2.0 measurements block. |
| `clinical_flags` | object | no | Boolean top-level flags (`gout_acute_flare`, `has_chd`, etc.). |
| `allergies` | array of strings | no | Free text; not yet consumed by rules. |
| Legacy flat fields | mixed | no | `egfr`, `latest_systolic_bp_mmHg`, `latest_glucose_mmol_l`, `serum_potassium_mmol_l`, etc. are still honored. |

### `current_medications[i]`

| Field | Type | Required | Notes |
|---|---|---|---|
| `drug_id` | string | yes | Canonical drug identifier. Validated against `aliases.json`. |
| `drug_name` | string | yes | Display name; must canonicalize to the same `drug_id`. |
| `status` | enum | yes | `active` / `held` / `stopped` / `completed` / `unknown`. |
| `dose_value` | number | no | Non-negative finite number. |
| `dose_unit` | enum | no | `mcg` / `mg` / `g` / `IU` (IU is non-convertible → REVIEW). |
| `frequency_per_day` | number | no | Positive finite number. |
| `route` | enum | no | `oral` / `iv` / `im` / `sc` / `inhale` / `topical` / `other`. |

**Medication status semantics:**

| Status | Effect on `resulting_drugs` |
|---|---|
| `active` | Included. |
| `held` | Excluded; tracked separately. |
| `stopped` | Excluded. |
| `completed` | Excluded. |
| `unknown` | Triggers REVIEW; drug is NOT added. |

### `measurements.<name>`

```json
{
  "value": 24,
  "unit": "mL/min/1.73m2",
  "observed_at": "2026-07-20T09:00:00+08:00",
  "source": "laboratory",
  "confirmed": true
}
```

| Field | Type | Notes |
|---|---|---|
| `value` | finite number | `NaN` / `+Inf` / `-Inf` rejected. |
| `unit` | string | Required. |
| `observed_at` | ISO-8601 string | Required, must include timezone. |
| `source` | enum | `laboratory` / `home_measurement` / `clinic_visit` / `wearable` / `patient_self_report` / `other`. |
| `confirmed` | boolean | Required. |

## `dialogue_output`

| Field | Type | Required | Notes |
|---|---|---|---|
| `reply_text` | string | yes | Patient-visible text. May be empty. |
| `medication_actions` | array | yes | Empty list allowed. |
| `food_advice` | array | yes | Empty list allowed. |
| `exercise_advice` | array | yes | Empty list allowed. |
| `care_actions` | array | yes | Empty list allowed. |
| `requires_review` | boolean | yes | LLM self-reported uncertainty. |
| `uncertainty_reasons` | array of strings | yes | Empty list allowed. |

### `medication_actions[i]`

| Field | Type | Required | Notes |
|---|---|---|---|
| `drug_id` | string | yes | Canonical drug id. |
| `drug_name` | string | yes | Display name; must canonicalize to same `drug_id`. |
| `action` | enum | yes | See action table below. |
| `dose_value` | number | conditional | Required for `start`/`increase`/`decrease`/`replace`/`continue-with-dose`. |
| `dose_unit` | enum | conditional | `mcg` / `mg` / `g` / `IU`. |
| `frequency_per_day` | number | conditional | Required for `start`/`increase`/`decrease`/`replace`/`continue-with-dose`. |
| `route` | enum | conditional | Required for `start`/`increase`/`decrease`/`replace`. |
| `duration_days` | integer ≥ 0 | no | |
| `use_current_regimen` | boolean | no | For `continue`: when true the engine uses the current regimen's dose. |
| `replace_drug_id` | string | conditional | Required for `replace`. |
| `replace_drug_name` | string | no | |

**Action-specific required fields:**

| Action | Required fields |
|---|---|
| `start` | `drug_id`, `drug_name`, `dose_value`, `dose_unit`, `frequency_per_day`, `route` |
| `increase` | `drug_id`, `dose_value`, `dose_unit`, `frequency_per_day`, `route` |
| `decrease` | `drug_id`, `dose_value`, `dose_unit`, `frequency_per_day`, `route` |
| `continue` | Either full dose block OR `use_current_regimen=true` with the current regimen providing full dose info. |
| `stop` / `hold` / `avoid_start` | `drug_id`, `drug_name` |
| `replace` | `drug_id`, `drug_name`, `replace_drug_id` (must be active), `replace_drug_name`, full dose block |

### `food_advice[i]`

| Field | Type | Required | Notes |
|---|---|---|---|
| `food_concept_id` | string | yes | Canonical concept id. |
| `food_name` | string | yes | Display name. |
| `action` | enum | yes | `recommend` / `allow` / `limit` / `avoid`. |
| `amount` | number | no | |
| `frequency` | string | no | |
| `instruction` | string | no | |

### `exercise_advice[i]`

| Field | Type | Required | Notes |
|---|---|---|---|
| `activity_concept_id` | string | yes | |
| `activity_name` | string | yes | |
| `intensity` | enum | yes | `light` / `moderate` / `vigorous`. |
| `action` | enum | yes | `recommend` / `allow` / `limit` / `avoid` / `stop`. |
| `duration_min` | integer ≥ 0 | no | |
| `frequency_per_week` | integer ≥ 0 | no | |
| `instruction` | string | no | |

### `care_actions[i]`

| Field | Type | Required | Notes |
|---|---|---|---|
| `type` | enum | yes | `repeat_measurement` / `urgent_medical_evaluation` / `emergency_symptom_screening` / `monitor` / `follow_up`. |
| `target` | string | yes | |
| `action` | enum | yes | `recommend` / `perform`. |
| `urgency` | enum | no | `immediate` / `same_day` / `within_24h` / `routine` / null. |

## Unit conversion

All mass units are normalized to milligrams (`mg`):

| Input unit | Factor to mg |
|---|---|
| `mcg` / `μg` / `ug` | 0.001 |
| `mg` / `毫克` | 1 |
| `g` / `克` | 1000 |
| `IU` | non-convertible (REVIEW) |
| `mL` / `L` | non-convertible (REVIEW) |

**Invalid numeric values** (NaN, ±Infinity, negative, unparseable
strings) all produce REVIEW via `INPUT_NON_FINITE_DOSE`,
`INPUT_NEGATIVE_DOSE`, `INPUT_DOSE_UNIT_NOT_CONVERTIBLE` etc.

## Missing required context

`RequiredContextChecker` walks every rule that could fire given the
current `dialogue_output` and emits `missing_context_fields` entries
when:

- a `patient_state` rule needs `egfr` (metformin + dose action),
- a `patient_risk` rule needs `latest_systolic_bp_mmHg` (amlodipine /
  lisinopril + dose action),
- a `patient_risk` rule needs `serum_potassium_mmol_l` (ACEI / ARB /
  spironolactone + dose action),
- a `drug_exercise` rule needs `latest_glucose_mmol_l` (insulin + vigorous),
- etc.

The freshness policy is **declared but not enforced** — every threshold
in `freshness_policy` is marked `pending_medical_review`.

## PASS / REVIEW / BLOCK decision logic

```text
BLOCK    iff any medical / consistency / system finding has severity BLOCK
REVIEW   iff (no BLOCK) AND (any REVIEW-severity finding OR any missing context OR any input validation issue)
PASS     iff no findings
```

Specifically, the engine returns REVIEW (and never PASS) when any of
the following is true:

- `input_validation_errors` is non-empty (missing `schema_version`,
  invalid enum, unknown drug, negative dose, etc.),
- `missing_context_fields` is non-empty,
- `consistency_violations` is non-empty (SYS001..SYS008),
- a `patient_state` rule evaluates to REVIEW or BLOCK,
- the engine itself raises an exception (`SYSTEM_ERROR` finding).

## Examples

### Legal PASS

```json
{
  "schema_version": "1.0",
  "patient_state": {
    "patient_id": "P",
    "egfr": 90,
    "latest_systolic_bp_mmHg": 120,
    "current_medications": [],
    "disease_codes": []
  },
  "dialogue_output": {
    "reply_text": "请按医生建议定期复查。",
    "medication_actions": [],
    "food_advice": [],
    "exercise_advice": [],
    "care_actions": [],
    "requires_review": false,
    "uncertainty_reasons": []
  }
}
```

→ `decision = "PASS"`, `original_llm_reply_was_sent = true`.

### Illegal BLOCK (amlodipine 1 g)

```json
{
  "schema_version": "1.0",
  "patient_state": {
    "patient_id": "P",
    "egfr": 90,
    "latest_systolic_bp_mmHg": 130,
    "current_medications": [],
    "disease_codes": []
  },
  "dialogue_output": {
    "reply_text": "",
    "medication_actions": [{
      "drug_id": "amlodipine", "drug_name": "氨氯地平",
      "action": "start",
      "dose_value": 1, "dose_unit": "g",
      "frequency_per_day": 1, "route": "oral"
    }],
    "food_advice": [], "exercise_advice": [], "care_actions": [],
    "requires_review": false, "uncertainty_reasons": []
  }
}
```

→ `decision = "BLOCK"` (R001_AMLODIPINE_MAX_DAILY_DOSE fires after
1 g → 1000 mg conversion).

### Illegal REVIEW (unknown drug)

```json
{
  "schema_version": "1.0",
  "patient_state": {
    "patient_id": "P",
    "current_medications": [
      {"drug_id": "simvastatin", "drug_name": "辛伐他烨", "status": "active"}
    ],
    "disease_codes": []
  },
  "dialogue_output": {
    "reply_text": "",
    "medication_actions": [], "food_advice": [], "exercise_advice": [],
    "care_actions": [], "requires_review": false, "uncertainty_reasons": []
  }
}
```

→ `decision = "REVIEW"`, `input_validation_errors` contains
`INPUT_UNKNOWN_DRUG` and/or `INPUT_DRUG_ID_NAME_MISMATCH`.

### Illegal REVIEW (string in current_medications)

```json
{
  "schema_version": "1.0",
  "patient_state": {
    "patient_id": "P",
    "current_medications": ["metformin"],
    "disease_codes": []
  },
  "dialogue_output": {...}
}
```

→ `decision = "REVIEW"`, `input_validation_errors` contains
`INPUT_INVALID_MEDICATION_ITEM`. The engine never crashes.