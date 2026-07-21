# Dialogue Agent Safety Rule Engine Demo (Windows One-Click, v4.2.1)

This Demo verifies the chain:

```text
Patient
  -> Dialogue Agent (preset LLM output)
  -> Safety Rule Engine (strict input validation -> recall -> evaluation)
  -> PASS / REVIEW / BLOCK
  -> Patient
```

The rule engine is split into three stages:

1. **Strict input validation** — runtime JSON Schema validation,
   drug-id / drug-name cross-check, measurement contract, standard
   terminology validation. Invalid input can never produce `PASS`.
2. **Per-channel candidate recall** — precise composite indexes
   recall a small subset of rules that *could* fire for the current
   request. The `drug_only_rule_index` is intentionally tight.
   `RequiredContextChecker` consults only precise per-channel
   indexes — never the full rule set.
3. **Deterministic evaluation** — only the recalled rules are
   evaluated. The DSL supports `all` / `any` / `not` predicates and
   `range` blocks; unknown units cannot silently pass.

It does NOT use a vector database, additional LLM calls, or a complex
knowledge graph.

## v4.2.1 changes vs v4.2.0

| Area | v4.2.0 | v4.2.1 |
|---|---|---|
| Entry point | `audit(patient_state=..., dialogue_output=...)` | New `audit_payload(payload=..., strict_mode=True, compat_mode=False, debug=False)` |
| `schema_version` | auto-injected silently | caller must supply in strict mode |
| Legacy fields (`name` / `drug` / `food` / `concept` / `activity`) | silently accepted | rejected with `INPUT_LEGACY_FIELD_NOT_ALLOWED` |
| `compat_mode` | none | new; routes legacy fields through `LegacyInputAdapter`, emits `DEPRECATED_INPUT_SCHEMA` |
| `route` default | silent `"oral"` | removed; missing route for start/increase/decrease/replace → REVIEW |
| `dose_unit` default | silent `"mg"` | removed; missing unit → REVIEW |
| `drug_id` derivation | derived from `drug_name` | removed |
| `requires_review` / `uncertainty_reasons` | ignored | forces REVIEW via `LLM_DECLARED_UNCERTAINTY` |
| Measurement validation | optional | strict (`unit` enum per kind, ISO 8601 + TZ, `confirmed` bool) |
| Terminology validation | none | `disease_code`, `food_concept_id`, `activity_concept_id` cross-checked |
| RequiredContext retrieval | iterates active rules | 8 precise per-channel indexes + retrieval trace |
| RequiredContext logic | fired on any drug | action-aware (stop/hold/avoid no eGFR) + direction-aware (avoid no recommend) |
| Decision basis | 4 tokens | 5 (`+ LLM_DECLARED_UNCERTAINTY`) |
| Test count | 167 | 198 (167 + 31 new) |

## Three versions, one project

| Concept | Value | Where |
|---|---|---|
| Project version | `4.2.1` | `safety.safety_engine.DialogueSafetyEngine.PROJECT_VERSION`, `developer_diagnostics.project_version` |
| Rule set version | `dialogue-safety-rules-4.2.1` | `rules/manifest.json`, `engine.repository.ruleset_version`, `AuditReport.ruleset_version` |
| Input schema version | `1.0` | `safety/input_models.SUPPORTED_SCHEMA_VERSIONS`, `schemas/*.json`, `AuditReport.input_schema_version` |

These are independent: bumping the rule set does NOT require a new
schema version; bumping the schema version (a real breaking change)
requires a major bump.

## Quick start (Windows)

1. Unzip the whole folder.
2. Double-click:

```text
setup_and_test.bat
```

The script runs:

```text
check Python 3.10+
create .venv (if missing)
install requirements (stdlib-only)
run unittest discover -s tests
run run_demo.py (12 scenarios)
run tests/perf_test.py (4 sub-tests)
write logs to logs\
```

A successful run prints `SUCCESS: setup, tests, demo, and performance
test completed.` Logs are written to `logs\test_output.txt`,
`logs\demo_output.txt`, `logs\perf_output.txt`, and
`logs\trace_demo_output.{txt,json}`.

Each `DialogueSafetyEngine.audit()` call also writes a JSON record
to `logs/audit/audit_<timestamp>.json`.

## Subsequent individual runs

```text
run_tests.bat         # all tests
run_demo.bat          # 12 demo scenarios
run_perf.bat          # 4 perf sub-tests
run_trace_demo.bat    # trace demo (v4.2.1 strict input)
audit_web.bat         # open the audit web UI in a browser
```

## Production defaults

- `strict_mode=True` is the production default. Legacy fields are
  rejected.
- `compat_mode=False` is the production default. Old callers must
  migrate to the strict input shape.
- `original_llm_reply_was_sent` is `True` **only** when `decision == "PASS"`.
- Any uncaught exception in `audit()` / `audit_payload()` is
  converted to `REVIEW` with `decision_basis = ["SYSTEM_ERROR"]`.
  The original LLM reply is **never** sent.

## Strict input fields (production)

```json
{
  "schema_version": "1.0",
  "patient_state": {
    "patient_id": "P",
    "current_medications": [
      {"drug_id": "metformin", "drug_name": "二甲双胍", "status": "active"}
    ],
    "disease_codes": ["diabetes"],
    "measurements": {
      "egfr": {
        "value": 24,
        "unit": "mL/min/1.73m2",
        "observed_at": "2026-07-20T09:00:00+08:00",
        "source": "laboratory",
        "confirmed": true
      }
    },
    "clinical_flags": {},
    "allergies": []
  },
  "dialogue_output": {
    "reply_text": "...",
    "medication_actions": [
      {
        "drug_id": "amlodipine", "drug_name": "氨氯地平",
        "action": "start",
        "dose_value": 5, "dose_unit": "mg",
        "frequency_per_day": 1, "route": "oral"
      }
    ],
    "food_advice": [], "exercise_advice": [], "care_actions": [],
    "requires_review": false, "uncertainty_reasons": []
  }
}
```

`reply_text` is for display and consistency checking only; rule
evaluation uses the structured fields.

## Decision matrix

| Trigger | Decision |
|---|---|
| Any `BLOCK` medical rule fires | `BLOCK` |
| `INPUT_VALIDATION` (REVIEW+) | `REVIEW` |
| `MISSING_CONTEXT` | `REVIEW` |
| `TEXT_STRUCTURE_CONSISTENCY` | `REVIEW` |
| `MEDICAL_RULE` (REVIEW-severity) | `REVIEW` |
| `requires_review=true` or `uncertainty_reasons` non-empty | `REVIEW` |
| `DEPRECATED_INPUT_SCHEMA` (compat_mode) | INFO finding, does not force REVIEW by itself |
| All clear | `PASS` |

`original_llm_reply_was_sent` is `True` iff `decision == "PASS"`.

## AuditReport shape (v4.2.1)

```json
{
  "decision": "BLOCK",
  "decision_basis": ["MEDICAL_RULE"],
  "ruleset_version": "dialogue-safety-rules-4.2.1",
  "input_schema_version": "1.0",
  "medical_violations": [...],
  "input_validation_errors": [...],
  "missing_context_fields": [...],
  "consistency_violations": [...],
  "system_findings": [...],
  "all_findings": [...],
  "patient_visible_response": "...",
  "reviewer_message": "decision=BLOCK; basis=MEDICAL_RULE",
  "developer_diagnostics": {
    "project_version": "4.2.1",
    "ruleset_version": "dialogue-safety-rules-4.2.1",
    "input_schema_version": "1.0",
    "strict_mode": true,
    "compat_mode": false,
    "required_context_retrieval_trace": [...]
  },
  "original_llm_reply_was_sent": false,
  "candidate_rule_ids": [...],
  "evaluated_rule_ids": [...],
  "retrieval_channels": [...],
  "retrieval_trace": [...],
  "evaluation_trace": [...],
  "timing_ms": {...},
  "violations": [...]   // legacy alias of medical_violations
}
```

## How to add a new rule

See [`RULE_AUTHORING.md`](RULE_AUTHORING.md) for the full authoring
guide, including the new `conditions` / `range` DSL and the
`production_eligible` flag for synthetic test rules.

## 1000 / 10000 unique-field recall-pressure tests

`tests/test_v421_strict_input.py::RequiredContextPressureTests`
constructs a synthetic rule base:

- N synthetic `patient_state` rules all bound to `drug=metformin`
  but with **unique** patient fields (`lab_0000` … `lab_NNNN`).
- ONE real rule: `R002_METFORMIN_EGFR_LT_30`.
- The patient only carries `egfr` and only takes metformin.

Strict assertions for both 1000 and 10000 variants:

```text
decision                == "BLOCK"
candidate_rule_ids     == ["R002_METFORMIN_EGFR_LT_30"]
evaluated_rule_ids     == ["R002_METFORMIN_EGFR_LT_30"]
total_rules_consulted  < 100   (precise-index path)
```

All synthetic rules carry `source_type = synthetic_test` /
`production_eligible = false` and live in a
`tempfile.TemporaryDirectory` that is never written to `rules/`.

## Bad Case coverage (BC01..BC21)

The 21 v4.1.1 Bad Case scenarios are still exercised by
`tests/test_bad_cases.py`. The expected outcomes are unchanged
except where v4.2.1 strict input validation surfaces additional
findings.

## Web UI

`audit_web.bat` opens the browser to `http://127.0.0.1:8765/`. The
UI exposes 12 scenarios:

- **A** Legal new-schema input → PASS
- **B** Metformin + eGFR=24 → BLOCK
- **C** Metformin + missing eGFR → REVIEW
- **D** `current_medications` contains a string → REVIEW
- **E** Unknown drug `辛伐他烨` → REVIEW
- **F** `drug_id ↔ drug_name` mismatch → REVIEW
- **G** Amlodipine 1 g (= 1000 mg/day) → BLOCK
- **H** `dose_value = -5` → REVIEW
- **I** Replace with atorvastatin not in regimen → REVIEW
- **J** Text says avoid grapefruit, structured says recommend → REVIEW
- **K** Simulated engine exception → REVIEW
- **L** `requires_review=true` → REVIEW (LLM_DECLARED_UNCERTAINTY)
- **M** `uncertainty_reasons` non-empty → REVIEW (LLM_DECLARED_UNCERTAINTY)
- **N** `start` without `route` → REVIEW
- **O** measurement unit error + bad date → REVIEW
- **P** hold metformin without eGFR → does NOT require eGFR

The UI also exposes:

- `decision` + `decision_basis` bar
- Patient-visible response, reviewer message, original-sent flag
- Four finding lists: input validation, missing context,
  consistency, medical
- Normalized patient state, drug context, candidate rule ids,
  evaluated rule ids, retrieval channels, timing breakdown
- Raw AuditReport JSON

## Known limitations

1. The bundled rule base is intentionally small (~30 active rules).
2. `freshness_policy.json` thresholds are explicitly marked
   `pending_medical_review`. A clinician must confirm them before the
   RequiredContextChecker starts rejecting data based on staleness.
3. The semantic retriever is a stub. The engine never performs vector
   retrieval or external I/O at audit time.
4. `audit(debug=True)` populates `retrieval_trace` and
   `evaluation_trace` and may be slow on huge candidate sets. Use
   `debug=False` in production.

## Disclaimer

The rules in this Demo are for software-flow validation only; they
are not a clinical decision support system. The 1000 / 10000 rules
used in the recall-pressure tests are SYNTHETIC STRESS-TEST fixtures,
NOT real medical rules.