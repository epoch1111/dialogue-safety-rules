# Rule Authoring Guide (v4.2.1)

> Rule-set version: `dialogue-safety-rules-4.2.1`. Input schema
> version: `1.0`.

## File layout

Every rule lives in `rules/<group>_rules.json`. The exact file name is
free-form; `rules/manifest.json` enumerates them. Each file contains a
top-level object with a `"rules"` array.

```json
{
  "rules": [
    { ... rule ... },
    { ... rule ... }
  ]
}
```

## Required fields

| Field | Type | Notes |
|---|---|---|
| `id` | string | Unique, non-empty. Convention: `R002_*`, `PR001_*`. |
| `version` | integer ≥ 1 | Increment on semantic changes. |
| `status` | enum | `active` / `pending_medical_review` / `inactive` / `deprecated`. |
| `type` | enum | See rule types below. |
| `severity` | enum | `INFO` / `WARN` / `REVIEW` / `BLOCK` / `EMERGENCY`. |
| `triggers` | object | See triggers below. |
| `parameters` | object | See parameters per rule type. |
| `message` | string | Non-empty human-readable message. |
| `source` | object | Required unless `status=pending_medical_review`. Must include `document_title` and `document_version`. |

## Triggers

```json
{
  "drugs_any": ["metformin", "二甲双胍"],
  "keywords_any": ["vigorous"],
  "patient_fields_any": ["egfr"],
  "risk_flags_any": ["severe_hypertension"]
}
```

Triggers are **only used for the audit pipeline**:

- `drugs_any` — the rule fires only if at least one of these drugs is
  in the resulting regimen.
- `keywords_any` — the rule fires only if the matcher found any of
  these tokens in `reply_text` or in `food_advice.instruction`.
- `patient_fields_any` — the rule fires only if `patient_state` has
  the listed field as a top-level key.
- `risk_flags_any` — the rule fires only if at least one of these
  risk codes has been raised during risk detection.

The exact recall path is decided by the rule type and which composite
index the rule is registered under (see *Indexes* below).

## Rule types

| Type | Description | Indexed under |
|---|---|---|
| `max_daily_dose` | Daily dose ceiling for a single drug. | `drug_only_rule_index`, `drug_field_index[(drug, __dose__)]` |
| `patient_state` | A drug + patient numeric field. v4.2.0 supports `range` and `conditions`. | `drug_field_index[(drug, field)]` |
| `patient_risk` | A patient numeric field → `RiskFlag` (never a BLOCK). | `patient_risk_field_index` |
| `drug_drug` | Two specific drugs together → BLOCK. | `drug_pair_index` |
| `drug_food` | A drug + a food keyword + a structured food action. | `drug_food_index`, `keyword_rule_index` |
| `drug_exercise` | A drug + a patient field + an exercise intensity. | `drug_exercise_index` |
| `disease_food` | A risk flag + a food keyword. | `disease_food_index` |
| `disease_exercise` | A risk flag + an exercise intensity. | `disease_exercise_index` |
| `response_compliance` | A risk-gated response pattern check. | `risk_compliance_index`, simple_drug_index |

## Indexes

The repository builds these indexes at load time. Understanding them
is the key to writing a rule that does NOT bloat the candidate set:

| Index | Key | Used when |
|---|---|---|
| `drug_only_rule_index` | `drug` | Rule binds ONLY a drug (no patient field, no pair, no food/exercise, no disease, no risk). Currently only `max_daily_dose`. |
| `drug_field_index` | `(drug, field)` | `max_daily_dose` (`__dose__`) and `patient_state` (actual field). |
| `drug_pair_index` | `sorted([drug_a, drug_b])` | `drug_drug`. |
| `drug_food_index` | `drug` | `drug_food`. |
| `drug_exercise_index` | `drug` | `drug_exercise`. |
| `disease_food_index` | `disease_code` | `disease_food`. |
| `disease_exercise_index` | `disease_code` | `disease_exercise`. |
| `risk_compliance_index` | `risk_code` | `response_compliance`. |
| `keyword_rule_index` | `(drug, keyword)` | `drug_food` with explicit keywords. |
| `patient_risk_field_index` | `field` | `patient_risk`. |
| `field_only_rule_index` | `field` | Reserved for future field-only rules. **Do not** add `patient_state` rules here. |

### Which rules can enter `drug_only_rule_index`?

Only rules whose evaluation depends **exclusively** on:

- the presence of a drug in the resulting regimen (or text extraction),
- a numeric dose in `dialogue_output` (or text).

Specifically, in v4.2.0 the only production rule type that qualifies
is `max_daily_dose`. `patient_state`, `drug_food`, `drug_exercise`,
`drug_drug`, `disease_*` and `response_compliance` MUST NOT enter
this index.

### What if my rule uses multiple drugs?

Add `parameters.drugs` (list). The `drug_only_rule_index` and
`drug_field_index` will register the rule under every drug. The
evaluator will only fire the rule when **at least one** of those
drugs is in `resulting_drugs`.

## DSL: parameters

### Single-predicate `patient_state` (legacy v4.1)

```json
{
  "type": "patient_state",
  "parameters": {
    "drugs": ["metformin"],
    "field": "egfr",
    "operator": "lt",
    "threshold": 30,
    "unit": "mL/min/1.73m2"
  }
}
```

Operators: `lt`, `lte`, `gt`, `gte`, `eq`, `contains`, `in`.

### Range condition (v4.2.0)

```json
{
  "type": "patient_state",
  "parameters": {
    "drugs": ["metformin"],
    "range": {
      "field": "egfr",
      "gte": 30,
      "lt": 45
    }
  }
}
```

Combine `gte` / `gt` / `lte` / `lt` to express an interval. The rule
fires only when **all** bound predicates pass.

### Compound predicates (v4.2.0)

```json
{
  "type": "patient_state",
  "parameters": {
    "drugs": ["metformin"],
    "conditions": {
      "all": [
        {"field": "egfr", "operator": "lt", "value": 30}
      ],
      "any": [
        {"field": "systolic_bp", "operator": "gt", "value": 180}
      ],
      "not": [
        {"field": "egfr", "operator": "eq", "value": 0}
      ]
    }
  }
}
```

Semantics:

- `all` — every predicate must pass (AND).
- `any` — at least one predicate must pass (OR).
- `not` — the inner predicate must fail (NOT).

### `match_mode = "all" | "any"`

When a parameter is a list (e.g. `parameters.drugs`), declare
`parameters.match_mode` to make the list semantics explicit:

```json
{
  "type": "patient_state",
  "match_mode": "all",
  "parameters": {
    "drugs": ["metformin", "lisinopril"],
    "field": "egfr",
    "operator": "lt",
    "threshold": 30
  }
}
```

`match_mode = "all"` (default if omitted for backward compat) means
**all** listed drugs must be in `resulting_drugs`. `match_mode = "any"`
means **any one** of them.

## Synthetic stress-test rules

Tests in `tests/test_v420_recall_pressure.py` build rule bases with
N synthetic decoys to prove that the candidate selector isolates via
the composite `drug_field_index`. To keep these rules out of
production:

```json
{
  "id": "SYN_DECOY_000001",
  "status": "active",
  "type": "patient_state",
  "severity": "BLOCK",
  "source": {
    "document_title": "synthetic stress test fixture",
    "document_version": "v4.2.0",
    "section": "synthetic"
  }
}
```

Synthetic rules MUST live in a `tempfile.TemporaryDirectory` so they
never pollute `rules/`. The default rule loader will not skip them; it
is the test author's responsibility to point the engine at a
temporary `rules/` directory. Any rule with
`source.document_title` starting with `synthetic` should not be
copied to production.

## Required context

For every rule you add, ask: *which patient fields would the
RequiredContextChecker flag as missing?* If a `patient_state` rule
binds `field=egfr`, the checker will demand `patient_state.egfr` for
any request that involves the rule's drug in a non-trivial action
(start, continue, increase, decrease, replace). If you forget this,
the engine returns REVIEW instead of the desired BLOCK / PASS.

## Medical review status

Any rule whose **clinical semantics** changes (different threshold,
different operator, different drug list) MUST be marked:

```json
{
  "status": "pending_medical_review",
  ...
}
```

Such a rule is registered in `_rules_by_id` and visible to test code
but excluded from every execution index. Once a clinician confirms the
change, flip the status back to `active`.

## Examples

### Single-predicate `patient_state`

```json
{
  "id": "R011_COLCHICINE_EGFR_LT_30",
  "version": 3,
  "status": "active",
  "type": "patient_state",
  "severity": "BLOCK",
  "triggers": {
    "drugs_any": ["colchicine"],
    "patient_fields_any": ["egfr"]
  },
  "parameters": {
    "drugs": ["colchicine"],
    "field": "egfr",
    "operator": "lt",
    "threshold": 30
  },
  "message": "秋水仙碱在 eGFR < 30 时毒性显著升高，需由医生重新评估剂量。",
  "source": {
    "document_title": "痛风抗炎症治疗指南（2025版）",
    "document_version": "2025"
  }
}
```

### `max_daily_dose`

```json
{
  "id": "R001_AMLODIPINE_MAX_DAILY_DOSE",
  "version": 3,
  "status": "active",
  "type": "max_daily_dose",
  "severity": "BLOCK",
  "triggers": {"drugs_any": ["amlodipine"]},
  "parameters": {
    "drug": "amlodipine",
    "max_daily_mg": 10
  },
  "message": "氨氯地平总日剂量超过演示规则上限 10 mg/day。",
  "source": {
    "document_title": "中国高血压防治指南（2024年修订版）",
    "document_version": "2024"
  }
}
```

### `response_compliance`

```json
{
  "id": "R016A_HIGH_BP_SELF_INCREASE_BLOCK",
  "version": 2,
  "status": "active",
  "type": "response_compliance",
  "severity": "BLOCK",
  "triggers": {
    "risk_flags_any": ["severe_hypertension"],
    "drugs_any": ["amlodipine", "lisinopril", "ramipril", "irbesartan"]
  },
  "parameters": {
    "kind": "forbidden_medication_action",
    "forbidden_actions": ["start", "increase", "replace"],
    "drugs": ["amlodipine", "lisinopril", "ramipril", "irbesartan"]
  },
  "message": "高血压急症下不应自行新增或增加降压药剂量。",
  "source": {
    "document_title": "中国高血压防治指南（2024年修订版）",
    "document_version": "2024"
  }
}
```

## Loader validation

The loader enforces 14 validation rules (id, version, types, operator
support, threshold type match, `max_daily_mg` positivity, drug
agreement with triggers, field agreement with `patient_fields_any`,
disease_code agreement with `risk_flags_any`, `response_compliance`
kind, `forbidden_actions` / `required_care_types` values, source
metadata). Loading fails immediately if any rule is malformed.

## Re-running the tests

After adding or editing a rule:

```text
run_tests.bat
```

The loader will reject the rule if validation fails.