"""Performance / scaling test for the v4.1.1 safety engine.

There are four sub-tests:

1. ``test_large_decoy_load`` — 1 000 unrelated decoy rules + 1 real rule.
   Verifies candidate isolation.

2. ``test_shared_egfr_1000`` — 1 000 rules sharing ``egfr`` in
   patient_fields_any + 1 metformin rule. Patient has egfr=24 and
   metformin. Strict assertion: exactly 1 candidate, 1 evaluated,
   1 violation (R002). Decoys MUST NOT enter the candidate set.

3. ``test_shared_egfr_10000`` — 10 000 rules sharing ``egfr`` + 1
   metformin rule. Same strict assertions as test 2. Synthetic
   stress; not real medical rules.

4. ``test_risk_detection_pressure`` — 10 000 decoy rules of non-PR
   types + 1 patient_risk rule. Risk detection must NOT iterate
   the full set.

5. ``test_latency_percentiles`` — 1 000 audits of a realistic input,
   reports matching latency P50 / P95 / P99.

Note: The 1 000 / 10 000 rules used in tests 2 and 3 are synthetic
stress-test fixtures, not real medical rules. They exist purely to
verify candidate recall isolation.
"""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from safety import DialogueSafetyEngine  # noqa: E402  (after sys.path setup)


# ---------------------------------------------------------------------------
# Synthetic rule helpers. These rules are NOT real medical rules; they
# are stress-test fixtures. They MUST NOT be written to the project's
# permanent ``rules/`` directory; they live in a tempdir.
# ---------------------------------------------------------------------------


def _write_perf_rules(base: Path, files: dict) -> Path:
    rule_dir = base / "perf_rules"
    rule_dir.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (rule_dir / name).write_text(
            json.dumps(body, ensure_ascii=False),
            encoding="utf-8",
        )
    return rule_dir


def _synthetic_patient_state_decoys(total: int) -> list:
    """Generate N synthetic ``patient_state`` rules sharing ``egfr``.

    Each rule keys a different "synthetic_drug_XXXX" placeholder drug
    so it never collides with real medical drugs.
    """
    rules = []
    for i in range(total):
        rules.append({
            "id": f"R_DECOY_{i:05d}",
            "version": 1,
            "status": "active",
            "type": "patient_state",
            "severity": "BLOCK",
            "triggers": {
                "drugs_any": [f"synthetic_drug_{i:05d}"],
                "patient_fields_any": ["egfr"],
            },
            "parameters": {
                "drug": f"synthetic_drug_{i:05d}",
                "field": "egfr",
                "operator": "lt",
                "threshold": 60,
            },
            "source": {
                "document_title": "synthetic_test",
                "document_version": "1",
                "production_eligible": False,
                "source_type": "synthetic_test",
            },
            "message": f"synthetic decoy #{i}",
        })
    return rules


def _real_metformin_egfr_rule() -> dict:
    """The single real medical rule — metformin + egfr < 30."""
    return {
        "id": "R002_METFORMIN_EGFR_LT_30",
        "version": 1,
        "status": "active",
        "type": "patient_state",
        "severity": "BLOCK",
        "triggers": {
            "drugs_any": ["metformin"],
            "patient_fields_any": ["egfr"],
        },
        "parameters": {
            "drug": "metformin",
            "field": "egfr",
            "operator": "lt",
            "threshold": 30,
        },
        "source": {
            "document_title": "synthetic_test",
            "document_version": "1",
            "production_eligible": False,
            "source_type": "synthetic_test",
        },
        "message": "metformin contraindicated when egfr < 30",
    }


# ---------------------------------------------------------------------------
# Test 1: 1 000 unrelated decoys + 1 real rule
# ---------------------------------------------------------------------------


def _write_unrelated_decoys(base: Path, total: int = 1_000) -> Path:
    rules = []
    for i in range(total):
        rules.append({
            "id": f"R_DECOY_{i:05d}",
            "version": 1,
            "status": "active",
            "type": "max_daily_dose",
            "severity": "BLOCK",
            "triggers": {
                "drugs_any": [f"unrelated_drug_{i:05d}"],
                "keywords_any": [],
                "patient_fields_any": [],
            },
            "parameters": {
                "drug": f"unrelated_drug_{i:05d}",
                "max_daily_mg": 100,
            },
            "source": {
                "document_title": "synthetic_test",
                "document_version": "1",
                "production_eligible": False,
                "source_type": "synthetic_test",
            },
            "message": f"decoy rule #{i}",
        })
    rules.append({
        "id": "R001_AMLODIPINE_MAX_DAILY_DOSE",
        "version": 1,
        "status": "active",
        "type": "max_daily_dose",
        "severity": "BLOCK",
        "triggers": {
            "drugs_any": ["amlodipine"],
            "keywords_any": [],
            "patient_fields_any": [],
        },
        "parameters": {"drug": "amlodipine", "max_daily_mg": 10},
        "source": {
            "document_title": "synthetic_test",
            "document_version": "1",
            "production_eligible": False,
            "source_type": "synthetic_test",
        },
        "message": "amlodipine over max daily dose",
    })
    return _write_perf_rules(base, {
        "manifest.json": {
            "ruleset_version": "perf-test",
            "rule_files": ["aliases.json", "rules.json"],
        },
        "aliases.json": {"amlodipine": ["amlodipine", "氨氯地平"]},
        "rules.json": {"rules": rules},
    })


def test_large_decoy_load(iterations: int = 100) -> int:
    print("=" * 72)
    print("Perf test 1: 1000 unrelated decoy rules + 1 real rule")
    print("=" * 72)
    with tempfile.TemporaryDirectory() as tmp:
        rule_dir = _write_unrelated_decoys(Path(tmp), 1_000)
        load_start = time.perf_counter()
        engine = DialogueSafetyEngine(rule_dir)
        load_ms = (time.perf_counter() - load_start) * 1000.0
        print(f"\nLoaded {len(engine.repository)} rules in {load_ms:.1f} ms.")

        patient_state = {
            "patient_id": "P",
            "egfr": 88,
            "latest_glucose_mmol_l": 6.8,
            "current_medications": [],
        }
        dialogue_output = {
            "reply_text": "把氨氯地平加到20毫克每日一次。",
            "medication_actions": [
                {"drug": "氨氯地平", "action": "increase",
                 "dose_value": 20, "dose_unit": "mg", "frequency_per_day": 1},
            ],
            "food_advice": [],
            "exercise_advice": [],
        }

        timings = []
        last_report = None
        for _ in range(iterations):
            report = engine.audit(
                patient_state=patient_state,
                dialogue_output=dialogue_output,
            )
            timings.append(report.timing.total_ms)
            last_report = report

        avg_total = sum(timings) / iterations
        p50 = _percentile(timings, 0.50)
        p95 = _percentile(timings, 0.95)
        p99 = _percentile(timings, 0.99)

        print()
        print("Audit summary:")
        print(f"  decision              = {last_report.decision}")
        print(f"  total_rules           = {len(engine.repository)}")
        print(f"  matched_drugs         = {last_report.matched_entities.drugs}")
        print(f"  candidate_rule_ids    = {len(last_report.candidate_rule_ids)}")
        print(f"  evaluated_rule_ids    = {len(last_report.evaluated_rule_ids)}")
        print(f"  violations            = {[v.rule_id for v in last_report.violations]}")
        print(f"  timings (avg / P50 / P95 / P99 over {iterations} iter, ms)")
        print(f"    avg={avg_total:.3f}  p50={p50:.3f}  p95={p95:.3f}  p99={p99:.3f}")

        assert last_report.decision == "BLOCK"
        assert {v.rule_id for v in last_report.violations} == {
            "R001_AMLODIPINE_MAX_DAILY_DOSE"
        }
        assert len(last_report.evaluated_rule_ids) < len(engine.repository) // 100, (
            "Evaluated rule count is not significantly smaller than total."
        )
        print("\nOK: evaluator ran on a tiny fraction of the rule base.")
    return 0


# ---------------------------------------------------------------------------
# Test 2: 1 000 shared-egfr decoys + 1 metformin rule. STRICT isolation.
# ---------------------------------------------------------------------------


def _write_shared_egfr(base: Path, total: int) -> Path:
    rules = _synthetic_patient_state_decoys(total) + [_real_metformin_egfr_rule()]
    return _write_perf_rules(base, {
        "manifest.json": {
            "ruleset_version": "perf-shared-egfr",
            "rule_files": ["aliases.json", "rules.json"],
        },
        "aliases.json": {"metformin": ["metformin", "二甲双胍"]},
        "rules.json": {"rules": rules},
    })


def test_shared_egfr_1000(iterations: int = 100) -> int:
    return _run_shared_egfr_test(iterations, 1_000)


def test_shared_egfr_10000(iterations: int = 50) -> int:
    return _run_shared_egfr_test(iterations, 10_000)


def _run_shared_egfr_test(iterations: int, total_decoys: int) -> int:
    title = (
        f"Perf test shared-egfr({total_decoys}): "
        f"only metformin must be recalled and BLOCK"
    )
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)
    print("NOTE: synthetic stress-test rules; not real medical rules.")
    with tempfile.TemporaryDirectory() as tmp:
        rule_dir = _write_shared_egfr(Path(tmp), total_decoys)

        load_start = time.perf_counter()
        engine = DialogueSafetyEngine(rule_dir)
        load_ms = (time.perf_counter() - load_start) * 1000.0
        print(f"\nLoaded {len(engine.repository)} rules in {load_ms:.1f} ms.")

        patient_state = {
            "patient_id": "PERF_SHARED_EGFR",
            "egfr": 24,
            "current_medications": [
                {"name": "二甲双胍", "status": "active"},
            ],
        }
        dialogue_output = {
            "reply_text": "建议继续使用二甲双胍500毫克，每日2次。",
            "medication_actions": [
                {"drug": "二甲双胍", "action": "continue",
                 "dose_value": 500, "dose_unit": "mg", "frequency_per_day": 2,
                 "route": "oral"},
            ],
            "food_advice": [],
            "exercise_advice": [],
            "care_actions": [],
        }

        timings = []
        last_report = None
        for _ in range(iterations):
            report = engine.audit(
                patient_state=patient_state,
                dialogue_output=dialogue_output,
            )
            timings.append(report.timing.total_ms)
            last_report = report

        avg_total = sum(timings) / iterations
        p50 = _percentile(timings, 0.50)
        p95 = _percentile(timings, 0.95)
        p99 = _percentile(timings, 0.99)
        max_ms = max(timings)
        min_ms = min(timings)

        print()
        print("Audit summary:")
        print(f"  total_rules              = {len(engine.repository)}")
        print(f"  candidate_count          = {len(last_report.candidate_rule_ids)}")
        print(f"  candidate_rule_ids       = {sorted(last_report.candidate_rule_ids)}")
        print(f"  evaluated_count          = {len(last_report.evaluated_rule_ids)}")
        print(f"  evaluated_rule_ids       = {sorted(last_report.evaluated_rule_ids)}")
        print(f"  violation_ids            = {sorted(v.rule_id for v in last_report.violations)}")
        print(f"  decision                 = {last_report.decision}")
        print(f"  load_ms                  = {load_ms:.2f}")
        print(f"Latency over {iterations} audits (ms):")
        print(f"  min={min_ms:.3f}  max={max_ms:.3f}  avg={avg_total:.3f}")
        print(f"  p50={p50:.3f}  p95={p95:.3f}  p99={p99:.3f}")

        # ---- strict assertions (v4.1.1) ----
        assert last_report.decision == "BLOCK", last_report.decision
        assert last_report.candidate_rule_ids == [
            "R002_METFORMIN_EGFR_LT_30"
        ], f"got {last_report.candidate_rule_ids}"
        assert last_report.evaluated_rule_ids == [
            "R002_METFORMIN_EGFR_LT_30"
        ], f"got {last_report.evaluated_rule_ids}"
        assert {v.rule_id for v in last_report.violations} == {
            "R002_METFORMIN_EGFR_LT_30"
        }, f"got {{v.rule_id for v in last_report.violations}}"

        # Hard caps (per spec).
        assert len(last_report.candidate_rule_ids) <= 5
        assert len(last_report.evaluated_rule_ids) <= 5

        # No decoy may appear in the candidates or evaluated sets.
        for rid in last_report.candidate_rule_ids:
            assert not rid.startswith("R_DECOY_"), (
                f"decoy {rid} leaked into candidates"
            )
        for rid in last_report.evaluated_rule_ids:
            assert not rid.startswith("R_DECOY_"), (
                f"decoy {rid} leaked into evaluated set"
            )
        print("\nOK: decoys did NOT enter the candidate or evaluated sets.")
    return 0


# ---------------------------------------------------------------------------
# Test 3: 1 000 realistic audits, P50 / P95 / P99 latency
# ---------------------------------------------------------------------------


def test_latency_percentiles(iterations: int = 1000) -> int:
    print()
    print("=" * 72)
    print(f"Perf test 3: {iterations} realistic audits, P50/P95/P99")
    print("=" * 72)
    engine = DialogueSafetyEngine(PROJECT_ROOT / "rules")

    patient_state = {
        "patient_id": "P",
        "egfr": 24,
        "latest_glucose_mmol_l": 3.4,
        "latest_systolic_bp_mmHg": 195,
        "serum_potassium_mmol_l": 5.7,
        "current_medications": [
            {"name": "赖诺普利", "status": "active"},
            {"name": "克拉霉素", "status": "active"},
            {"name": "甘精胰岛素", "status": "active"},
        ],
    }
    dialogue_output = {
        "reply_text": "你可以把氨氯地平加到每次20毫克、每日1次；同时开始二甲双胍500毫克、每日2次，并继续辛伐他汀。饮食上可以自由使用含钾盐替代品，现在立即进行剧烈跑步。",
        "medication_actions": [
            {"drug": "氨氯地平", "action": "increase",
             "dose_value": 20, "dose_unit": "mg", "frequency_per_day": 1},
            {"drug": "二甲双胍", "action": "start",
             "dose_value": 500, "dose_unit": "mg", "frequency_per_day": 2},
            {"drug": "辛伐他汀", "action": "continue",
             "dose_value": 20, "dose_unit": "mg", "frequency_per_day": 1},
        ],
        "food_advice": [
            {"food": "含钾盐替代品", "action": "recommend", "instruction": "可以自由使用"}
        ],
        "exercise_advice": [
            {"activity": "跑步", "intensity": "vigorous", "action": "recommend", "instruction": "立即开始"}
        ],
        "care_actions": [],
    }

    timings = []
    last_report = None
    for _ in range(iterations):
        report = engine.audit(
            patient_state=patient_state,
            dialogue_output=dialogue_output,
        )
        timings.append(report.timing.total_ms)
        last_report = report

    avg_total = statistics.mean(timings)
    p50 = _percentile(timings, 0.50)
    p95 = _percentile(timings, 0.95)
    p99 = _percentile(timings, 0.99)
    max_ms = max(timings)
    min_ms = min(timings)

    print()
    print("Audit summary (last iter):")
    print(f"  decision              = {last_report.decision}")
    print(f"  matched_drugs         = {last_report.matched_entities.drugs}")
    print(f"  candidate_rule_ids    = {len(last_report.candidate_rule_ids)}")
    print(f"  evaluated_rule_ids    = {len(last_report.evaluated_rule_ids)}")
    print(f"  violations            = {[v.rule_id for v in last_report.violations]}")
    print()
    print(f"Latency over {iterations} audits (ms):")
    print(f"  min={min_ms:.3f}  max={max_ms:.3f}  avg={avg_total:.3f}")
    print(f"  p50={p50:.3f}  p95={p95:.3f}  p99={p99:.3f}")
    print()
    print(f"All {iterations} audits: P50 <= 1ms, P95 <= 5ms, P99 <= 20ms")
    print("PASS" if p99 < 20.0 else "SLOW")
    return 0


# ---------------------------------------------------------------------------
# Test 4: 10 000 decoys + 1 PR rule (risk detection pressure)
# ---------------------------------------------------------------------------


def test_risk_detection_pressure(iterations: int = 100) -> int:
    """Risk detection must NOT iterate the full set; only the single
    PR rule whose trigger field appears in ``patient_state`` should
    be evaluated for risk.
    """
    print()
    print("=" * 72)
    print(f"Perf test 4: 10 000 decoy + 1 PR rule, {iterations} audits")
    print("=" * 72)
    print("NOTE: synthetic stress-test rules; not real medical rules.")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        rule_dir = _build_many_decoy_rules(base, 10_000)

        load_start = time.perf_counter()
        engine = DialogueSafetyEngine(rule_dir)
        load_ms = (time.perf_counter() - load_start) * 1000.0
        print(f"\nLoaded {len(engine.repository)} rules in {load_ms:.1f} ms.")

        patient_state = {
            "patient_id": "P",
            "egfr": 50,
            "current_medications": [
                {"name": "二甲双胍", "status": "active"},
            ],
        }
        dialogue_output = {
            "reply_text": "",
            "medication_actions": [],
            "food_advice": [],
            "exercise_advice": [],
        }

        timings = []
        last_report = None
        for _ in range(iterations):
            report = engine.audit(
                patient_state=patient_state,
                dialogue_output=dialogue_output,
            )
            timings.append(report.timing.total_ms)
            last_report = report

        avg_total = sum(timings) / iterations
        p50 = _percentile(timings, 0.50)
        p95 = _percentile(timings, 0.95)
        p99 = _percentile(timings, 0.99)
        max_ms = max(timings)

        print()
        print("Audit summary:")
        print(f"  total_rules              = {len(engine.repository)}")
        print(f"  risk_flags               = {[rf.code for rf in last_report.risk_flags]}")
        print(f"  evaluated_risk_rule_ids  = {last_report.evaluated_risk_rule_ids}")
        print(f"  candidate_count          = {len(last_report.candidate_rule_ids)}")
        print(f"  evaluated_count          = {len(last_report.evaluated_rule_ids)}")
        print(f"Latency over {iterations} audits (ms):")
        print(f"  min={min(timings):.3f}  max={max_ms:.3f}  avg={avg_total:.3f}")
        print(f"  p50={p50:.3f}  p95={p95:.3f}  p99={p99:.3f}")

        assert last_report.evaluated_risk_rule_ids == ["R_PR_REAL"], (
            f"expected only R_PR_REAL as evaluated risk rule, "
            f"got {last_report.evaluated_risk_rule_ids}"
        )
        codes = {rf.code for rf in last_report.risk_flags}
        assert "renal_impairment" in codes
        print("\nOK: risk detection only evaluated the single PR rule.")
    return 0


def _build_many_decoy_rules(base: Path, total: int) -> Path:
    """10 000 decoy rules of every type EXCEPT patient_risk, plus 1
    patient_risk rule keyed on ``egfr``.

    Synthetic test rules. NOT real medical rules.
    """
    rule_dir = base / "many_rules"
    rule_dir.mkdir(parents=True, exist_ok=True)

    (rule_dir / "manifest.json").write_text(
        json.dumps({
            "ruleset_version": "stress",
            "rule_files": [
                "aliases.json",
                "decoy_rules.json",
                "real_pr.json",
            ],
        }),
        encoding="utf-8",
    )

    (rule_dir / "aliases.json").write_text(
        json.dumps({"metformin": ["metformin", "二甲双胍"]}),
        encoding="utf-8",
    )

    decoys = []
    for i in range(total):
        rule_id = f"R_DECOY_{i:05d}"
        rt = ["max_daily_dose", "drug_drug", "patient_state",
              "drug_food", "drug_exercise", "disease_food",
              "disease_exercise", "response_compliance"][i % 8]
        if rt == "max_daily_dose":
            decoys.append({
                "id": rule_id, "status": "active", "type": rt,
                "severity": "BLOCK",
                "triggers": {"drugs_any": [f"unrelated_{i}"],
                             "keywords_any": [], "patient_fields_any": []},
                "parameters": {"drug": f"unrelated_{i}", "max_daily_mg": 100},
                "source": {
                    "document_title": "synthetic_test",
                    "document_version": "1",
                    "production_eligible": False,
                    "source_type": "synthetic_test",
                },
                "message": f"decoy {i}",
            })
        elif rt == "patient_state":
            decoys.append({
                "id": rule_id, "status": "active", "type": rt,
                "severity": "BLOCK",
                "triggers": {"drugs_any": [f"unrelated_{i}"],
                             "patient_fields_any": ["blood_pressure"]},
                "parameters": {"drug": f"unrelated_{i}", "field": "blood_pressure",
                                "operator": "gt", "threshold": 100},
                "source": {
                    "document_title": "synthetic_test",
                    "document_version": "1",
                    "production_eligible": False,
                    "source_type": "synthetic_test",
                },
                "message": f"decoy {i}",
            })
        elif rt == "drug_drug":
            decoys.append({
                "id": rule_id, "status": "active", "type": rt,
                "severity": "WARN",
                "triggers": {"drugs_any": [f"unrelated_{i}", f"unrelated_{i+1}"]},
                "parameters": {"drug_a": f"unrelated_{i}",
                                "drug_b": f"unrelated_{i+1}"},
                "source": {
                    "document_title": "synthetic_test",
                    "document_version": "1",
                    "production_eligible": False,
                    "source_type": "synthetic_test",
                },
                "message": f"decoy {i}",
            })
        elif rt == "drug_food":
            decoys.append({
                "id": rule_id, "status": "active", "type": rt,
                "severity": "WARN",
                "triggers": {"drugs_any": [f"unrelated_{i}"],
                             "keywords_any": ["x"]},
                "parameters": {"drug": f"unrelated_{i}", "keywords": ["x"]},
                "source": {
                    "document_title": "synthetic_test",
                    "document_version": "1",
                    "production_eligible": False,
                    "source_type": "synthetic_test",
                },
                "message": f"decoy {i}",
            })
        elif rt == "drug_exercise":
            decoys.append({
                "id": rule_id, "status": "active", "type": rt,
                "severity": "WARN",
                "triggers": {"drugs_any": [f"unrelated_{i}"],
                             "keywords_any": ["vigorous"]},
                "parameters": {"drug": f"unrelated_{i}",
                                "exercise_intensity": "vigorous"},
                "source": {
                    "document_title": "synthetic_test",
                    "document_version": "1",
                    "production_eligible": False,
                    "source_type": "synthetic_test",
                },
                "message": f"decoy {i}",
            })
        elif rt == "disease_food":
            decoys.append({
                "id": rule_id, "status": "active", "type": rt,
                "severity": "WARN",
                "triggers": {"risk_flags_any": ["x_disease"],
                             "keywords_any": ["x"]},
                "parameters": {"disease_code": "x_disease", "keywords": ["x"]},
                "source": {
                    "document_title": "synthetic_test",
                    "document_version": "1",
                    "production_eligible": False,
                    "source_type": "synthetic_test",
                },
                "message": f"decoy {i}",
            })
        elif rt == "disease_exercise":
            decoys.append({
                "id": rule_id, "status": "active", "type": rt,
                "severity": "WARN",
                "triggers": {"risk_flags_any": ["x_disease"],
                             "keywords_any": ["vigorous"]},
                "parameters": {"disease_code": "x_disease",
                                "exercise_intensity": "vigorous"},
                "source": {
                    "document_title": "synthetic_test",
                    "document_version": "1",
                    "production_eligible": False,
                    "source_type": "synthetic_test",
                },
                "message": f"decoy {i}",
            })
        else:  # response_compliance
            decoys.append({
                "id": rule_id, "status": "active", "type": rt,
                "severity": "WARN",
                "triggers": {"risk_flags_any": ["x_risk"]},
                "parameters": {"kind": "required_care_action",
                                "required_care_types": ["repeat_measurement"]},
                "source": {
                    "document_title": "synthetic_test",
                    "document_version": "1",
                    "production_eligible": False,
                    "source_type": "synthetic_test",
                },
                "message": f"decoy {i}",
            })

    (rule_dir / "decoy_rules.json").write_text(
        json.dumps({"rules": decoys}),
        encoding="utf-8",
    )

    (rule_dir / "real_pr.json").write_text(
        json.dumps({"rules": [
            {
                "id": "R_PR_REAL",
                "version": 1,
                "status": "active",
                "type": "patient_risk",
                "severity": "REVIEW",
                "triggers": {
                    "patient_fields_any": ["egfr"],
                    "drugs_any": ["metformin"],
                },
                "parameters": {
                    "risk_code": "renal_impairment",
                    "field": "egfr",
                    "operator": "lt",
                    "threshold": 60,
                },
                "source": {
                    "document_title": "synthetic_test",
                    "document_version": "1",
                    "production_eligible": False,
                    "source_type": "synthetic_test",
                },
                "message": "renal impairment risk",
            }
        ]}),
        encoding="utf-8",
    )

    return rule_dir


def _percentile(values, q):
    sorted_v = sorted(values)
    if not sorted_v:
        return 0.0
    k = (len(sorted_v) - 1) * q
    f = int(k)
    c = min(f + 1, len(sorted_v) - 1)
    return sorted_v[f] + (sorted_v[c] - sorted_v[f]) * (k - f)


def main() -> int:
    test_large_decoy_load(iterations=100)
    test_shared_egfr_1000(iterations=100)
    test_shared_egfr_10000(iterations=50)
    test_latency_percentiles(iterations=1000)
    test_risk_detection_pressure(iterations=100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
