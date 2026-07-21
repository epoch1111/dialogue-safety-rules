"""Tests for v4.1 RuleRepository strict validation.

Each test below corresponds to one of the 14 spec requirements.

The shared-egfr pressure test in :class:`RiskDetectionPressureTest` builds
a rule directory with 10 000 non-patient_risk rules and confirms the
engine's risk detection does not iterate the full set.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from safety import DialogueSafetyEngine
from safety.rule_repository import RuleLoadError, RuleRepository


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_rules_dir(base: Path, files: dict):
    for name, body in files.items():
        (base / name).write_text(
            json.dumps(body, ensure_ascii=False),
            encoding="utf-8",
        )


def _base_manifest(files: list):
    return {
        "ruleset_version": "test",
        "rule_files": ["aliases.json"] + list(files),
    }


# --------------------------------------------------------------------------
# 14 strict-load validations
# --------------------------------------------------------------------------


class StrictLoadValidations(unittest.TestCase):
    def _assert_rejected(self, files: dict, err_fragment: str = ""):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_rules_dir(base, files)
            with self.assertRaises(RuleLoadError) as ctx:
                RuleRepository(base)
            if err_fragment:
                self.assertIn(err_fragment, str(ctx.exception))

    # 1. id must be a non-empty string
    def test_id_must_be_non_empty_string(self):
        self._assert_rejected({
            "manifest.json": _base_manifest(["r.json"]),
            "aliases.json": {},
            "r.json": {"rules": [
                {"id": "", "type": "max_daily_dose", "severity": "BLOCK",
                 "triggers": {"drugs_any": ["amlodipine"], "keywords_any": [],
                              "patient_fields_any": []},
                 "parameters": {"drug": "amlodipine", "max_daily_mg": 10},
                 "message": "x"}
            ]},
        })

    # 2. version must be a positive int
    def test_version_must_be_positive_int(self):
        self._assert_rejected({
            "manifest.json": _base_manifest(["r.json"]),
            "aliases.json": {},
            "r.json": {"rules": [
                {"id": "R_X", "version": 0, "type": "max_daily_dose",
                 "severity": "BLOCK",
                 "triggers": {"drugs_any": ["amlodipine"], "keywords_any": [],
                              "patient_fields_any": []},
                 "parameters": {"drug": "amlodipine", "max_daily_mg": 10},
                 "message": "x"}
            ]},
        })

    # 3. triggers.* must be list[str]
    def test_triggers_lists_must_be_str_list(self):
        self._assert_rejected({
            "manifest.json": _base_manifest(["r.json"]),
            "aliases.json": {},
            "r.json": {"rules": [
                {"id": "R_X", "type": "max_daily_dose", "severity": "BLOCK",
                 "triggers": {"drugs_any": "amlodipine", "keywords_any": [],
                              "patient_fields_any": []},
                 "parameters": {"drug": "amlodipine", "max_daily_mg": 10},
                 "message": "x"}
            ]},
        })

    # 4. operator must be in allowed set
    def test_operator_must_be_allowed(self):
        self._assert_rejected({
            "manifest.json": _base_manifest(["r.json"]),
            "aliases.json": {},
            "r.json": {"rules": [
                {"id": "R_X", "type": "patient_state", "severity": "BLOCK",
                 "triggers": {"drugs_any": ["amlodipine"],
                              "patient_fields_any": ["egfr"]},
                 "parameters": {"drug": "amlodipine", "field": "egfr",
                                "operator": "approx", "threshold": 30},
                 "message": "x"}
            ]},
        })

    # 5. threshold must match operator type
    def test_threshold_type_matches_operator(self):
        self._assert_rejected({
            "manifest.json": _base_manifest(["r.json"]),
            "aliases.json": {},
            "r.json": {"rules": [
                {"id": "R_X", "type": "patient_state", "severity": "BLOCK",
                 "triggers": {"drugs_any": ["amlodipine"],
                              "patient_fields_any": ["egfr"]},
                 "parameters": {"drug": "amlodipine", "field": "egfr",
                                "operator": "lt",
                                "threshold": "thirty"},
                 "message": "x"}
            ]},
        })

    # 6. max_daily_mg must be positive
    def test_max_daily_mg_must_be_positive(self):
        self._assert_rejected({
            "manifest.json": _base_manifest(["r.json"]),
            "aliases.json": {},
            "r.json": {"rules": [
                {"id": "R_X", "type": "max_daily_dose", "severity": "BLOCK",
                 "triggers": {"drugs_any": ["amlodipine"], "keywords_any": [],
                              "patient_fields_any": []},
                 "parameters": {"drug": "amlodipine", "max_daily_mg": 0},
                 "message": "x"}
            ]},
        })

    # 7. parameters.drug / drugs must agree with triggers.drugs_any
    def test_parameters_drug_agrees_with_triggers(self):
        self._assert_rejected({
            "manifest.json": _base_manifest(["r.json"]),
            "aliases.json": {},
            "r.json": {"rules": [
                {"id": "R_X", "type": "max_daily_dose", "severity": "BLOCK",
                 "triggers": {"drugs_any": ["metformin"], "keywords_any": [],
                              "patient_fields_any": []},
                 "parameters": {"drug": "amlodipine", "max_daily_mg": 10},
                 "message": "x"}
            ]},
        })

    # 8. parameters.field must be in triggers.patient_fields_any
    def test_parameters_field_in_patient_fields(self):
        self._assert_rejected({
            "manifest.json": _base_manifest(["r.json"]),
            "aliases.json": {},
            "r.json": {"rules": [
                {"id": "R_X", "type": "patient_state", "severity": "BLOCK",
                 "triggers": {"drugs_any": ["amlodipine"],
                              "patient_fields_any": ["egfr"]},
                 "parameters": {"drug": "amlodipine", "field": "creatinine",
                                "operator": "gt", "threshold": 2.0},
                 "message": "x"}
            ]},
        })

    # 9. disease_code must be in risk_flags_any OR disease_codes field
    def test_disease_code_linked_to_risk(self):
        self._assert_rejected({
            "manifest.json": _base_manifest(["r.json"]),
            "aliases.json": {},
            "r.json": {"rules": [
                {"id": "R_X", "type": "disease_food", "severity": "REVIEW",
                 "triggers": {"risk_flags_any": ["hypertension"],
                              "keywords_any": ["x"]},
                 "parameters": {"disease_code": "gout", "keywords": ["x"]},
                 "message": "x"}
            ]},
        })

    # 10. response_compliance.kind must be valid
    def test_response_compliance_kind_must_be_valid(self):
        self._assert_rejected({
            "manifest.json": _base_manifest(["r.json"]),
            "aliases.json": {},
            "r.json": {"rules": [
                {"id": "R_X", "type": "response_compliance", "severity": "BLOCK",
                 "triggers": {"risk_flags_any": ["severe_hypertension"]},
                 "parameters": {"kind": "made_up_kind"},
                 "message": "x"}
            ]},
        })

    # 11. forbidden_actions / required_care_types must be valid
    def test_required_care_types_must_be_strings(self):
        self._assert_rejected({
            "manifest.json": _base_manifest(["r.json"]),
            "aliases.json": {},
            "r.json": {"rules": [
                {"id": "R_X", "type": "response_compliance", "severity": "REVIEW",
                 "triggers": {"risk_flags_any": ["severe_hypertension"]},
                 "parameters": {"kind": "required_care_action",
                                "required_care_types": [123]},
                 "message": "x"}
            ]},
        })

    # 12. source must include document_title and document_version
    def test_source_must_have_title_and_version(self):
        self._assert_rejected({
            "manifest.json": _base_manifest(["r.json"]),
            "aliases.json": {},
            "r.json": {"rules": [
                {"id": "R_X", "type": "max_daily_dose", "severity": "BLOCK",
                 "triggers": {"drugs_any": ["amlodipine"], "keywords_any": [],
                              "patient_fields_any": []},
                 "parameters": {"drug": "amlodipine", "max_daily_mg": 10},
                 "source": {"section": "x"},
                 "message": "x"}
            ]},
        })

    # 13. duplicate id (including across pending and active) is rejected
    def test_duplicate_id_with_pending_also_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_rules_dir(base, {
                "manifest.json": _base_manifest(["r.json"]),
                "aliases.json": {},
                "r.json": {"rules": [
                    {"id": "R_X", "status": "active",
                     "type": "max_daily_dose", "severity": "BLOCK",
                     "triggers": {"drugs_any": ["amlodipine"],
                                  "keywords_any": [],
                                  "patient_fields_any": []},
                     "parameters": {"drug": "amlodipine", "max_daily_mg": 10},
                     "source": {"document_title": "t", "document_version": "1"},
                     "message": "x"},
                    {"id": "R_X", "status": "pending_medical_review",
                     "type": "max_daily_dose", "severity": "BLOCK",
                     "triggers": {"drugs_any": ["amlodipine"],
                                  "keywords_any": [],
                                  "patient_fields_any": []},
                     "parameters": {"drug": "amlodipine", "max_daily_mg": 10},
                     "message": "x"},
                ]},
            })
            with self.assertRaises(RuleLoadError):
                RuleRepository(base)

    # 14. pending rules stay in _rules_by_id
    def test_pending_rule_visible_in_all_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_rules_dir(base, {
                "manifest.json": _base_manifest(["r.json"]),
                "aliases.json": {},
                "r.json": {"rules": [
                    {"id": "R_PEND", "status": "pending_medical_review",
                     "type": "max_daily_dose", "severity": "BLOCK",
                     "triggers": {"drugs_any": ["amlodipine"],
                                  "keywords_any": [],
                                  "patient_fields_any": []},
                     "parameters": {"drug": "amlodipine", "max_daily_mg": 10},
                     "message": "x"}
                ]},
            })
            repo = RuleRepository(base)
            # Rule is registered in all_rules_by_id.
            self.assertIn("R_PEND", repo.rule_ids())
            # But is NOT in active rules.
            self.assertEqual(repo.active_rule_count, 0)
            # And the evaluator skips it (returns None).
            from safety.rule_evaluator import RuleEvaluator
            ev = RuleEvaluator()
            result = ev.evaluate(
                repo.get("R_PEND"),
                patient_state={},
                draft=type("D", (), {
                    "medication_actions": [], "food_advice": [],
                    "exercise_advice": [], "reply_text": "",
                })(),
                drug_ctx=type("C", (), {
                    "current_drugs": [], "mentioned_drugs": [],
                    "recommended_drugs": [], "resulting_drugs": [],
                    "text_mentioned_drugs": [], "text_dose_drugs": [],
                })(),
                risk_flags=[],
                text_extractions=[],
            )
            self.assertIsNone(result)


# --------------------------------------------------------------------------
# Risk detection pressure test: 10 000 unrelated rules, 1 PR rule.
# Risk detection must NOT iterate the full set.
# --------------------------------------------------------------------------


def _build_many_decoy_rules(base: Path, total: int) -> Path:
    """10 000 decoy rules of every type EXCEPT patient_risk, plus 1
    patient_risk rule keyed on ``egfr``."""
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
                "source": {"document_title": "stress", "document_version": "1"},
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
                "source": {"document_title": "stress", "document_version": "1"},
                "message": f"decoy {i}",
            })
        elif rt == "drug_drug":
            decoys.append({
                "id": rule_id, "status": "active", "type": rt,
                "severity": "WARN",
                "triggers": {"drugs_any": [f"unrelated_{i}", f"unrelated_{i+1}"]},
                "parameters": {"drug_a": f"unrelated_{i}",
                                "drug_b": f"unrelated_{i+1}"},
                "source": {"document_title": "stress", "document_version": "1"},
                "message": f"decoy {i}",
            })
        elif rt == "drug_food":
            decoys.append({
                "id": rule_id, "status": "active", "type": rt,
                "severity": "WARN",
                "triggers": {"drugs_any": [f"unrelated_{i}"],
                             "keywords_any": ["x"]},
                "parameters": {"drug": f"unrelated_{i}", "keywords": ["x"]},
                "source": {"document_title": "stress", "document_version": "1"},
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
                "source": {"document_title": "stress", "document_version": "1"},
                "message": f"decoy {i}",
            })
        elif rt == "disease_food":
            decoys.append({
                "id": rule_id, "status": "active", "type": rt,
                "severity": "WARN",
                "triggers": {"risk_flags_any": ["x_disease"],
                             "keywords_any": ["x"]},
                "parameters": {"disease_code": "x_disease", "keywords": ["x"]},
                "source": {"document_title": "stress", "document_version": "1"},
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
                "source": {"document_title": "stress", "document_version": "1"},
                "message": f"decoy {i}",
            })
        else:  # response_compliance
            decoys.append({
                "id": rule_id, "status": "active", "type": rt,
                "severity": "WARN",
                "triggers": {"risk_flags_any": ["x_risk"]},
                "parameters": {"kind": "required_care_action",
                                "required_care_types": ["repeat_measurement"]},
                "source": {"document_title": "stress", "document_version": "1"},
                "message": f"decoy {i}",
            })

    (rule_dir / "decoy_rules.json").write_text(
        json.dumps({"rules": decoys}),
        encoding="utf-8",
    )

    # One real patient_risk rule keyed on egfr.
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
                "source": {"document_title": "stress", "document_version": "1"},
                "message": "renal impairment risk",
            }
        ]}),
        encoding="utf-8",
    )

    return rule_dir


class RiskDetectionPressureTest(unittest.TestCase):
    def test_only_pr_rule_evaluated(self):
        with tempfile.TemporaryDirectory() as tmp:
            rule_dir = _build_many_decoy_rules(Path(tmp), 10_000)
            engine = DialogueSafetyEngine(rule_dir)

            # Patient has egfr=50 (below 60) and takes metformin.
            report = engine.audit(
                patient_state={
                    "patient_id": "P",
                    "egfr": 50,
                    "current_medications": [
                        {"name": "二甲双胍", "status": "active"},
                    ],
                },
                dialogue_output={
                    "reply_text": "",
                    "medication_actions": [],
                    "food_advice": [],
                    "exercise_advice": [],
                },
            )

            # Only R_PR_REAL was evaluated for risk detection.
            self.assertEqual(report.evaluated_risk_rule_ids, ["R_PR_REAL"])
            # The risk flag is raised.
            codes = {rf.code for rf in report.risk_flags}
            self.assertIn("renal_impairment", codes)
            # No decoy rule was evaluated for risk.
            for rid in report.evaluated_risk_rule_ids:
                self.assertFalse(rid.startswith("R_DECOY_"))

            # Sanity: total candidate set can include decoys via
            # drug_field_index / simple_index, but risk detection is
            # gated on patient_risk_field_index which only knows about
            # the single PR rule.
            self.assertGreater(len(engine.repository.rule_ids()), 9000)


if __name__ == "__main__":
    unittest.main()