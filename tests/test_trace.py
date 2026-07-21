"""v4.1.1 unit tests for trace/demo/index-isolation requirements.

Covers 10 named tests from the v4.1.1 spec:

1. test_shared_egfr_1000_exact_candidates
2. test_shared_egfr_10000_exact_candidates
3. test_patient_state_rules_not_in_field_only_index
4. test_patient_risk_not_in_field_only_index
5. test_true_field_only_rule_can_be_retrieved
6. test_trace_does_not_receive_expected_decision
7. test_trace_metformin_egfr_block
8. test_trace_metformin_hold_safe
9. test_debug_false_has_no_large_evaluation_trace
10. test_debug_true_contains_retrieval_and_evaluation_trace

These tests run alongside the existing 116 tests; they do NOT
modify or remove any pre-existing test.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from safety import DialogueSafetyEngine
from safety.candidate_selector import select_candidate_rule_ids
from safety.models import DrugContext, RiskFlag
from safety.rule_repository import Rule, RuleRepository, is_field_only_rule


ROOT = Path(__file__).resolve().parents[1]


def _write_rules(base: Path, files: dict) -> Path:
    for name, body in files.items():
        (base / name).write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return base


def _build_shared_egfr_ruleset(base: Path, total: int) -> Path:
    """Build a synthetic ruleset: N ``patient_state`` decoys sharing
    ``egfr``, plus one real metformin + egfr rule.
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
            },
            "message": f"decoy {i}",
        })
    rules.append({
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
        },
        "message": "metformin egfr",
    })
    return _write_rules(base, {
        "manifest.json": {
            "ruleset_version": "trace-test",
            "rule_files": ["aliases.json", "rules.json"],
        },
        "aliases.json": {"metformin": ["metformin", "二甲双胍"]},
        "rules.json": {"rules": rules},
    })


def _trace_audit_inputs():
    return (
        {
            "patient_id": "TRACE_AUDIT_INPUTS",
            "egfr": 24,
            "current_medications": [
                {"drug_id": "metformin", "drug_name": "二甲双胍",
                 "status": "active"},
            ],
        },
        {
            "reply_text": "建议继续使用二甲双胍500毫克，每日2次。",
            "medication_actions": [
                {
                    "drug_id": "metformin",
                    "drug_name": "二甲双胍",
                    "action": "continue",
                    "dose_value": 500,
                    "dose_unit": "mg",
                    "frequency_per_day": 2,
                    "route": "oral",
                },
            ],
            "food_advice": [],
            "exercise_advice": [],
            "care_actions": [],
        },
    )


# --------------------------------------------------------------------------
# 1 + 2. shared-egfr stress
# --------------------------------------------------------------------------


class SharedEgfrExactCandidatesTests(unittest.TestCase):
    def _assert_exact(self, total_decoys: int) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "rules"
            base.mkdir()
            rule_dir = _build_shared_egfr_ruleset(base, total_decoys)
            engine = DialogueSafetyEngine(rule_dir)
            patient_state, dialogue_output = _trace_audit_inputs()

            report = engine.audit(
                patient_state=patient_state,
                dialogue_output=dialogue_output,
            )

            self.assertEqual(report.decision, "BLOCK")
            self.assertEqual(
                report.candidate_rule_ids,
                ["R002_METFORMIN_EGFR_LT_30"],
            )
            self.assertEqual(
                report.evaluated_rule_ids,
                ["R002_METFORMIN_EGFR_LT_30"],
            )
            self.assertEqual(
                {v.rule_id for v in report.violations},
                {"R002_METFORMIN_EGFR_LT_30"},
            )
            self.assertLessEqual(len(report.candidate_rule_ids), 5)
            self.assertLessEqual(len(report.evaluated_rule_ids), 5)
            for rid in report.candidate_rule_ids:
                self.assertFalse(rid.startswith("R_DECOY_"))
            for rid in report.evaluated_rule_ids:
                self.assertFalse(rid.startswith("R_DECOY_"))

    def test_shared_egfr_1000_exact_candidates(self):
        self._assert_exact(1_000)

    def test_shared_egfr_10000_exact_candidates(self):
        self._assert_exact(10_000)


# --------------------------------------------------------------------------
# 3. patient_state rules MUST NOT enter field_only index
# --------------------------------------------------------------------------


class FieldOnlyIndexIsolationTests(unittest.TestCase):
    def test_patient_state_rules_not_in_field_only_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "rules"
            base.mkdir()
            rule_dir = _build_shared_egfr_ruleset(base, 50)
            repo = RuleRepository(rule_dir)
            # patient_state rules keyed on egfr must all be in drug_field_index.
            self.assertGreater(len(repo._drug_field_index), 0)
            # and NONE in field_only_rule_index.
            self.assertEqual(len(repo._field_only_rule_index), 0)

    def test_patient_risk_not_in_field_only_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "rules"
            base.mkdir()
            _write_rules(base, {
                "manifest.json": {
                    "ruleset_version": "trace-test",
                    "rule_files": ["aliases.json", "rules.json"],
                },
                "aliases.json": {},
                "rules.json": {"rules": [{
                    "id": "R_PR_EGFR",
                    "version": 1,
                    "status": "active",
                    "type": "patient_risk",
                    "severity": "REVIEW",
                    "triggers": {
                        "patient_fields_any": ["egfr"],
                    },
                    "parameters": {
                        "risk_code": "renal_impairment",
                        "field": "egfr",
                        "operator": "lt",
                        "threshold": 60,
                    },
                    "source": {
                        "document_title": "t",
                        "document_version": "1",
                    },
                    "message": "renal impairment",
                }]},
            })
            repo = RuleRepository(base)
            # patient_risk is gated on patient_risk_field_index.
            self.assertEqual(repo._field_only_rule_index, {})
            self.assertIn("egfr", repo._patient_risk_field_index)
            self.assertIn("R_PR_EGFR", repo._patient_risk_field_index["egfr"])

    def test_true_field_only_rule_can_be_retrieved(self):
        # A purely field-only rule (no drug binding) lives in
        # ``_field_only_rule_index`` and is recallable via
        # ``repository.field_rule_ids([field])``. v4.1 strict
        # validation does not currently permit a ``patient_state``
        # rule without ``drug/drugs``, so we directly construct the
        # ``Rule`` dataclass (skipping strict load validation) using
        # a forward-compatible ``field_only_example`` type. The
        # helper ``is_field_only_rule`` must classify such a rule
        # as field-only so it can be retrieved.
        from safety.rule_repository import Rule as RuleCls
        r = RuleCls(
            id="R_FIELD_ONLY_FAKE",
            type="field_only_example",
            severity="REVIEW",
            triggers={"patient_fields_any": ["albumin_g_per_l"]},
            parameters={
                "field": "albumin_g_per_l",
                "operator": "lt",
                "threshold": 35,
            },
            message="low albumin without drug binding",
            source={"document_title": "t", "document_version": "1"},
        )
        self.assertTrue(is_field_only_rule(r))

        # Manually inject it into a temporary repo's
        # ``_field_only_rule_index`` and verify recall.
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "rules"
            base.mkdir()
            _write_rules(base, {
                "manifest.json": {
                    "ruleset_version": "trace-test",
                    "rule_files": ["aliases.json", "rules.json"],
                },
                "aliases.json": {},
                "rules.json": {"rules": []},
            })
            repo = RuleRepository(base)
            # Make the synthetic rule visible to the recall layer by
            # registering it both in the field-only index AND in the
            # active rules dict (the latter is what
            # ``_is_evaluable`` checks).
            repo._rules_by_id[r.id] = r
            repo._active_rules_by_id[r.id] = r
            repo._field_only_rule_index.setdefault(
                "albumin_g_per_l", set()
            ).add("R_FIELD_ONLY_FAKE")
            self.assertEqual(
                repo.field_rule_ids(["albumin_g_per_l"]),
                {"R_FIELD_ONLY_FAKE"},
            )


# --------------------------------------------------------------------------
# 4. trace inputs are engine-clean (no test answers).
# --------------------------------------------------------------------------


class TraceInputsTests(unittest.TestCase):
    def setUp(self):
        self.engine = DialogueSafetyEngine(ROOT / "rules")

    def test_trace_does_not_receive_expected_decision(self):
        # engine.audit() does not accept expected_decision / expected_rule_ids.
        patient_state, dialogue_output = _trace_audit_inputs()
        # This call must work WITHOUT any expected value keyword argument.
        import inspect
        sig = inspect.signature(self.engine.audit)
        param_names = list(sig.parameters.keys())
        self.assertNotIn("expected_decision", param_names)
        self.assertNotIn("expected_rule_ids", param_names)
        self.assertNotIn("expected", param_names)
        # Sanity: the call actually works.
        report = self.engine.audit(
            patient_state=patient_state,
            dialogue_output=dialogue_output,
        )
        # The decision is computed from rule evaluation, not from input.
        self.assertEqual(report.decision, "BLOCK")

    def test_trace_metformin_egfr_block(self):
        # Scenario A from the trace demo.
        # Use the SHARED-EGFR stress ruleset so the candidate set is
        # exactly {R002_METFORMIN_EGFR_LT_30}; the production rule
        # base contains additional metformin rules (R008 max-dose and
        # R010 egfr 30-45) that legitimately fire alongside.
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "rules"
            base.mkdir()
            rule_dir = _build_shared_egfr_ruleset(base, 0)  # 0 decoys
            engine_a = DialogueSafetyEngine(rule_dir)
            patient_state, dialogue_output = _trace_audit_inputs()
            report = engine_a.audit(
                patient_state=patient_state,
                dialogue_output=dialogue_output,
            )
            self.assertEqual(report.decision, "BLOCK")
            self.assertEqual(
                report.candidate_rule_ids,
                ["R002_METFORMIN_EGFR_LT_30"],
            )
            self.assertEqual(
                report.evaluated_rule_ids,
                ["R002_METFORMIN_EGFR_LT_30"],
            )
            self.assertEqual(
                {v.rule_id for v in report.violations},
                {"R002_METFORMIN_EGFR_LT_30"},
            )

    def test_trace_metformin_hold_safe(self):
        # Scenario B: same patient, but LLM recommends HOLDING metformin
        # + urgent_medical_evaluation. R002 must not fire because
        # resulting_drugs no longer includes metformin.
        patient_state = {
            "patient_id": "TRACE_METFORMIN_SAFE",
            "egfr": 24,
            "current_medications": [
                {"name": "二甲双胍", "status": "active"},
            ],
        }
        dialogue_output = {
            "reply_text": (
                "您的肾功能指标需要医生重新评估,目前不要自行继续或调整二甲双胍,"
                "请尽快联系医生。"
            ),
            "medication_actions": [
                {
                    "drug": "二甲双胍",
                    "action": "hold",
                    "dose_value": None,
                    "dose_unit": None,
                    "frequency_per_day": None,
                    "route": "oral",
                },
            ],
            "food_advice": [],
            "exercise_advice": [],
            "care_actions": [
                {
                    "type": "urgent_medical_evaluation",
                    "target": "renal_function",
                    "action": "recommend",
                },
            ],
        }
        report = self.engine.audit(
            patient_state=patient_state,
            dialogue_output=dialogue_output,
        )
        # metformin must be removed from resulting_drugs.
        self.assertNotIn("metformin", report.resulting_drugs)
        # R002 must not be in violations.
        rule_ids = {v.rule_id for v in report.violations}
        self.assertNotIn("R002_METFORMIN_EGFR_LT_30", rule_ids)
        # Decision is either PASS or REVIEW, never BLOCK for this scenario
        # (decision MUST be a definite value, not ambiguous).
        self.assertIn(report.decision, ("PASS", "REVIEW"))


# --------------------------------------------------------------------------
# 5. debug flag behavior
# --------------------------------------------------------------------------


class DebugTraceBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.engine = DialogueSafetyEngine(ROOT / "rules")

    def test_debug_false_has_no_large_evaluation_trace(self):
        patient_state, dialogue_output = _trace_audit_inputs()
        report = self.engine.audit(
            patient_state=patient_state,
            dialogue_output=dialogue_output,
            debug=False,
        )
        # Debug traces must be empty in production mode.
        self.assertEqual(report.retrieval_trace, [])
        self.assertEqual(report.evaluation_trace, [])

    def test_debug_true_contains_retrieval_and_evaluation_trace(self):
        patient_state, dialogue_output = _trace_audit_inputs()
        report = self.engine.audit(
            patient_state=patient_state,
            dialogue_output=dialogue_output,
            debug=True,
        )
        # retrieval_trace has at least one row.
        self.assertGreater(len(report.retrieval_trace), 0)
        # evaluation_trace must include one row per evaluated rule.
        self.assertGreater(len(report.evaluation_trace), 0)
        evaluated_ids = sorted(et.rule_id for et in report.evaluation_trace)
        self.assertEqual(evaluated_ids, sorted(set(report.evaluated_rule_ids)))
        # Find R002's evaluation trace row and confirm there is at
        # least one condition with a description, even if not matched.
        r002_evals = [
            et for et in report.evaluation_trace
            if et.rule_id == "R002_METFORMIN_EGFR_LT_30"
        ]
        self.assertEqual(len(r002_evals), 1)
        conditions = r002_evals[0].conditions
        self.assertGreater(len(conditions), 0)
        self.assertTrue(r002_evals[0].matched)


if __name__ == "__main__":
    unittest.main()
