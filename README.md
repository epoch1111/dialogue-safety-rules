# Dialogue Agent Safety Rule Engine Demo (Windows One-Click, v4.2.0)

This Demo verifies the chain:

```text
Patient
  -> Dialogue Agent (preset LLM output)
  -> Safety Rule Engine (strict input validation -> recall -> evaluation)
  -> PASS / REVIEW / BLOCK
  -> Patient
```

The rule engine is split into three stages:

1. **Strict input validation** — JSON Schema + type + enum + drug-id
   cross-check + unit conversion. Invalid input can never produce
   `PASS`.
2. **Per-channel candidate recall** — composite indexes recall a small
   subset of rules that *could* fire for the current request. The
   `drug_only_rule_index` is intentionally tight (only `max_daily_dose`
   in production). The legacy `simple_index` is no longer used.
3. **Deterministic evaluation** — only the recalled rules are
   evaluated. The DSL supports `all` / `any` / `not` predicates and
   `range` blocks; unknown units cannot silently pass.

It does NOT use a vector database, additional LLM calls, or a complex
knowledge graph.

## v4.2.0 changes vs v4.1.1

| Area | v4.1.1 | v4.2.0 |
|---|---|---|
| Input contract | Implicit | Strict JSON Schema (`schemas/`) |
| Input validator | None | `safety.input_validator.InputValidator` |
| Unit conversion | `mg` only | `safety.unit_converter` supports `mcg`, `mg`, `g` |
| Required context | None | `safety.required_context_checker.RequiredContextChecker` |
| Fail-closed | None | `audit()` catches every exception → REVIEW |
| DSL | Single predicate | `conditions` (`all`/`any`/`not`) + `range` |
| Candidate recall | `simple_index` fallback | `drug_only_rule_index` + composite only |
| Report | `violations` | `decision_basis`, `medical_violations`, `input_validation_errors`, `missing_context_fields`, `consistency_violations`, `system_findings`, `all_findings`, `patient_visible_response`, `reviewer_message`, `developer_diagnostics` |
| Decision basis | implicit | explicit `decision_basis` array |
| Status enums | 2 | 5 (active / held / stopped / completed / unknown) |

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
`logs\demo_output.txt`, `logs\perf_output.txt`, and `logs\trace_demo_output.{txt,json}`.

Each `DialogueSafetyEngine.audit()` call also writes a JSON record to
`logs/audit/audit_<timestamp>.json`.

## Subsequent individual runs

```text
run_tests.bat         # all tests
run_demo.bat          # 12 demo scenarios
run_perf.bat          # 4 perf sub-tests
run_trace_demo.bat    # trace demo
audit_web.bat         # open the audit web UI in a browser
```

## Directory layout (v4.2.0)

```text
dialogue_agent_safety_demo_windows_fixed_v4_2_0\
+-- setup_and_test.bat
+-- run_tests.bat
+-- run_demo.bat
+-- run_perf.bat
+-- run_trace_demo.bat
+-- audit_web.bat
+-- audit_web.py
+-- requirements.txt           # empty (stdlib only)
+-- models.py                  # PresetDialogueAgent / DialogueDraft
+-- dialogue_agent.py          # PresetDialogueAgent
+-- orchestrator.py            # DialogueOrchestrator
+-- run_demo.py                # 12 demo scenarios
+-- run_trace_demo.py          # 2-scenario trace demo
+-- schemas/
|   +-- audit_input.schema.json
|   +-- patient_state.schema.json
|   +-- dialogue_output.schema.json
+-- safety\                    # 16-module safety package
|   +-- __init__.py
|   +-- models.py              # AuditReport + 11-phase TimingBreakdown
|   +-- input_models.py        # strict v4.2.0 input dataclasses
|   +-- input_validator.py     # strict validator
|   +-- unit_converter.py      # dose unit normalization
|   +-- required_context_checker.py
|   +-- normalizer.py          # text + structured-field normalization
|   +-- keyword_matcher.py     # trie-based keyword scanner
|   +-- rule_repository.py     # load + validate + composite indexes
|   +-- candidate_selector.py  # per-channel recall (tight)
|   +-- rule_evaluator.py      # deterministic per-type evaluation
|   +-- audit_logger.py        # writes logs/audit/<timestamp>.json
|   +-- semantic_retriever.py  # disabled stub
|   +-- consistency_checker.py # SYS001..SYS008
|   +-- text_dose_parser.py    # free-text dose extraction
|   +-- safety_engine.py       # DialogueSafetyEngine.audit()
+-- rules\
|   +-- manifest.json          # ruleset_version = dialogue-safety-rules-4.2.0
|   +-- aliases.json
|   +-- patient_risk_rules.json
|   +-- response_compliance_rules.json
|   +-- dose_rules.json
|   +-- patient_state_rules.json
|   +-- drug_drug_rules.json
|   +-- drug_food_rules.json
|   +-- drug_exercise_rules.json
|   +-- disease_food_rules.json
|   +-- disease_exercise_rules.json
+-- data\
|   +-- llm_presets.json
|   +-- patient_cases.json
+-- tests\
|   +-- test_demo.py
|   +-- test_recall.py
|   +-- test_extended_rules.py
|   +-- test_bad_cases.py
|   +-- test_consistency.py
|   +-- test_text_parser.py
|   +-- test_engine_invariants.py
|   +-- test_repository_validation.py
|   +-- test_trace.py
|   +-- perf_test.py
|   +-- test_v420_strict_input.py       # NEW v4.2.0
|   +-- test_v420_recall_pressure.py    # NEW v4.2.0
+-- audit_web\
|   +-- index.html
|   +-- style.css
|   +-- app.js
+-- INPUT_SCHEMA.md
+-- RULE_AUTHORING.md
+-- CHANGELOG.md
```

**Total: 167 unit tests** + 4 perf sub-tests.

## 1000 / 10000 unique-field recall-pressure tests

`tests/test_v420_recall_pressure.py` constructs a synthetic rule base:

- N synthetic `patient_state` rules all bound to `drug=metformin` but
  with **unique** patient fields (`lab_0000` … `lab_NNNN`).
- ONE real rule: `R002_METFORMIN_EGFR_LT_30`.

The patient only carries `egfr` and only takes metformin.

Strict assertions for both 1000 and 10000 variants:

```text
decision                == "BLOCK"
candidate_rule_ids     == ["R002_METFORMIN_EGFR_LT_30"]
evaluated_rule_ids     == ["R002_METFORMIN_EGFR_LT_30"]
violations             == {"R002_METFORMIN_EGFR_LT_30"}
```

All synthetic rules carry `source_type = synthetic_test` /
`production_eligible = false` and live in a `tempfile.TemporaryDirectory`
that is never written to `rules/`.

## Bad Case coverage (BC01..BC21)

The 21 v4.1.1 Bad Case scenarios are still exercised by
`tests/test_bad_cases.py`. The expected outcomes are unchanged except
where v4.2.0 strict input validation surfaces additional findings
(e.g. BC07 now also flags `INPUT_MEDICATION_ACTION_MISSING_FIELDS`).

## Web UI

`audit_web.bat` opens the browser to `http://127.0.0.1:8765/`. The UI
exposes:

1. Patient state + dialogue output editors.
2. 11 preset scenarios (A–K plus the two trace scenarios).
3. `decision` + `decision_basis` bar.
4. Patient-visible response, reviewer message, original-sent flag.
5. Four finding lists: input validation, missing context, consistency,
   medical.
6. Normalized patient state, drug context, candidate rule ids,
   evaluated rule ids, retrieval channels, timing breakdown.
7. Raw AuditReport JSON.

## Rule inputs and outputs

### Strict `patient_state` example

```json
{
  "patient_id": "P",
  "current_medications": [
    {
      "drug_id": "metformin",
      "drug_name": "二甲双胍",
      "status": "active",
      "dose_value": 500,
      "dose_unit": "mg",
      "frequency_per_day": 2,
      "route": "oral"
    }
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
  "clinical_flags": {
    "gout_acute_flare": null
  }
}
```

### Strict `dialogue_output` example

```json
{
  "reply_text": "...",
  "medication_actions": [
    {
      "drug_id": "amlodipine",
      "drug_name": "氨氯地平",
      "action": "start",
      "dose_value": 5,
      "dose_unit": "mg",
      "frequency_per_day": 1,
      "route": "oral"
    }
  ],
  "food_advice": [],
  "exercise_advice": [],
  "care_actions": [],
  "requires_review": false,
  "uncertainty_reasons": []
}
```

### `AuditReport` shape (v4.2.0)

```json
{
  "decision": "BLOCK",
  "decision_basis": ["MEDICAL_RULE"],
  "ruleset_version": "dialogue-safety-rules-4.2.0",
  "input_schema_version": "1.0",
  "medical_violations": [...],
  "input_validation_errors": [...],
  "missing_context_fields": [...],
  "consistency_violations": [...],
  "system_findations": [...],
  "all_findings": [...],
  "patient_visible_response": "...",
  "reviewer_message": "decision=BLOCK; basis=MEDICAL_RULE",
  "developer_diagnostics": {...},
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

See [`RULE_AUTHORING.md`](RULE_AUTHORING.md) for the full authoring guide,
including the new `conditions` / `range` DSL and the `production_eligible`
flag for synthetic test rules.

## Performance notes

- v4.2.0 adds 3 new phases (`input_validation`, `required_context`,
  `unit_conversion`) but they all run in O(rules + drugs) time.
- p50 ≈ 1–2 ms on a single realistic input.
- p99 ≈ 2–10 ms with 1000 / 10000 synthetic decoys (recall-pressure
  tests).

## Compatibility

`strict_mode=True` is the default. The audit wrapper silently adds
`schema_version="1.0"` so legacy callers (e.g. the bundled demo
presets) continue to work; v4.2.0 strict validation is enforced against
the wrapped payload.

If you have an existing v4.1.1 caller that depends on the legacy
`violations` field, that field is preserved as an alias of
`medical_violations` for backward compatibility.

## Known limitations

1. The bundled rule base is intentionally small (~30 active rules).
   The recall-pressure tests use synthetic decoys written to a
   `tempfile.TemporaryDirectory`; they never pollute `rules/`.
2. `freshness_policy.json` thresholds are explicitly marked
   `pending_medical_review`. A clinician must confirm them before the
   RequiredContextChecker starts rejecting data based on staleness.
3. The semantic retriever is a stub. The engine never performs vector
   retrieval or external I/O at audit time.
4. `audit(debug=True)` populates `retrieval_trace` and
   `evaluation_trace` and may be slow on huge candidate sets. Use
   `debug=False` in production.

## Disclaimer

The rules in this Demo are for software-flow validation only; they are
not a clinical decision support system. The 1000 / 10000 rules used in
the recall-pressure tests are SYNTHETIC STRESS-TEST fixtures, NOT real
medical rules.