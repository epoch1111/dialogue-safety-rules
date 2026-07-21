"""Tests migrated from v3 -> v4 semantics.

These tests cover entity recall, alias resolution, repository validation,
and the candidate-selector behavior. They are renumbered but the test
function names are preserved.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from safety import DialogueSafetyEngine
from safety.candidate_selector import (
    SelectionResult,
    select_candidate_rule_ids,
)
from safety.keyword_matcher import KeywordMatcher
from safety.models import MatchedEntities
from safety.normalizer import normalize
from safety.rule_repository import (
    Rule,
    RuleLoadError,
    RuleRepository,
)


ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# Normalizer
# --------------------------------------------------------------------------


class NormalizerTests(unittest.TestCase):
    def test_lowercase(self):
        self.assertEqual(normalize("AMLODIPINE"), "amlodipine")

    def test_strip_whitespace(self):
        self.assertEqual(normalize("  amlodipine  "), "amlodipine")

    def test_collapse_whitespace(self):
        self.assertEqual(normalize("amlodipine   5mg"), "amlodipine 5mg")

    def test_fullwidth_punctuation(self):
        self.assertEqual(normalize("含钾盐替代品，自由使用。"), "含钾盐替代品,自由使用.")

    def test_none_and_empty(self):
        self.assertEqual(normalize(None), "")
        self.assertEqual(normalize(""), "")


# --------------------------------------------------------------------------
# Keyword matcher (single trie walks)
# --------------------------------------------------------------------------


class KeywordMatcherTests(unittest.TestCase):
    def test_single_keyword_hit(self):
        m = KeywordMatcher()
        m.add_drug_alias("含钾盐替代品", "amlodipine")
        self.assertEqual(m.scan_drugs("请使用含钾盐替代品。"), {"amlodipine"})

    def test_drug_trie_returns_canonical(self):
        m = KeywordMatcher()
        m.add_drug_alias("氨氯地平", "amlodipine")
        m.add_drug_alias("amlodipine", "amlodipine")
        self.assertEqual(m.scan_drugs("amlodipine 5mg"), {"amlodipine"})

    def test_concept_trie(self):
        m = KeywordMatcher()
        m.add_concept("vigorous", "exercise_intensity", payload="vigorous", rule_id="R_X")
        hits = m.scan_concepts("vigorous running")
        kinds = [h[0] for h in hits]
        self.assertIn("exercise_intensity", kinds)

    def test_no_keywords_returns_empty(self):
        m = KeywordMatcher()
        self.assertEqual(m.scan_drugs(""), set())
        self.assertEqual(m.scan_drugs("nothing here"), set())


# --------------------------------------------------------------------------
# Alias resolution
# --------------------------------------------------------------------------


class AliasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = RuleRepository(DEFAULT_RULES_DIR)

    def test_canonical_known_alias(self):
        self.assertEqual(self.repo.canonical_drug("氨氯地平"), "amlodipine")
        self.assertEqual(self.repo.canonical_drug("苯磺酸氨氯地平"), "amlodipine")
        self.assertEqual(self.repo.canonical_drug("AMlodipine"), "amlodipine")

    def test_canonical_unknown_returns_lowercase(self):
        self.assertEqual(self.repo.canonical_drug("Aspirin"), "aspirin")
        self.assertEqual(self.repo.canonical_drug("未知药"), "未知药")

    def test_disease_code_resolution(self):
        self.assertEqual(self.repo.disease_code("痛风"), "hyperuricemia_gout")
        self.assertEqual(self.repo.disease_code("高脂血症"), "dyslipidemia")


# --------------------------------------------------------------------------
# Composite-index recall
# --------------------------------------------------------------------------


class CandidateSelectionTests(unittest.TestCase):
    DEFAULT_RULES_DIR = ROOT / "rules"

    @classmethod
    def setUpClass(cls):
        cls.repo = RuleRepository(cls.DEFAULT_RULES_DIR)
        cls.engine = DialogueSafetyEngine(cls.DEFAULT_RULES_DIR)

    def test_drug_pair_recall(self):
        # Both simvastatin and clarithromycin present -> R003 must be a candidate.
        from safety.models import DrugContext
        drug_ctx = DrugContext(
            current_drugs=[],
            mentioned_drugs=[],
            recommended_drugs=[],
            resulting_drugs=["simvastatin", "clarithromycin"],
        )
        result: SelectionResult = select_candidate_rule_ids(
            self.repo, drug_ctx, [], [], []
        )
        self.assertIn("R003_SIMVASTATIN_CLARITHROMYCIN", result.candidate_rule_ids)

    def test_unrelated_drug_recalls_no_ddi_rule(self):
        from safety.models import DrugContext
        drug_ctx = DrugContext(
            current_drugs=[],
            mentioned_drugs=[],
            recommended_drugs=[],
            resulting_drugs=["aspirin"],
        )
        result = select_candidate_rule_ids(
            self.repo, drug_ctx, [], [], []
        )
        self.assertNotIn("R003_SIMVASTATIN_CLARITHROMYCIN", result.candidate_rule_ids)

    def test_patient_field_recall_for_dose(self):
        from safety.models import DrugContext
        drug_ctx = DrugContext(
            current_drugs=[],
            mentioned_drugs=[],
            recommended_drugs=[],
            resulting_drugs=["amlodipine"],
        )
        result = select_candidate_rule_ids(
            self.repo, drug_ctx, [], [], ["latest_systolic_bp_mmHg"]
        )
        self.assertIn("R001_AMLODIPINE_MAX_DAILY_DOSE", result.candidate_rule_ids)

    def test_risk_flag_compliance_recall(self):
        # When a hyperkalemia risk is raised, R014A and R020A must be candidates.
        from safety.models import DrugContext, RiskFlag
        rf = RiskFlag(code="hyperkalemia", severity="REVIEW", source_rule_id="PR003")
        drug_ctx = DrugContext(
            current_drugs=[], mentioned_drugs=[], recommended_drugs=[], resulting_drugs=[]
        )
        result = select_candidate_rule_ids(
            self.repo, drug_ctx, [rf], [], []
        )
        self.assertIn("R014A_HYPERKALEMIA_CONTINUE_ACEI_BLOCK",
                      result.candidate_rule_ids)
        self.assertIn("R020A_HYPERKALEMIA_CONTINUE_SPIRONOLACTONE_BLOCK",
                      result.candidate_rule_ids)

    def test_disease_food_recall(self):
        from safety.models import DrugContext, RiskFlag
        rf = RiskFlag(code="hyperuricemia_gout", severity="WARN", source_rule_id="PR005")
        drug_ctx = DrugContext(
            current_drugs=[], mentioned_drugs=[], recommended_drugs=[], resulting_drugs=[]
        )
        result = select_candidate_rule_ids(
            self.repo, drug_ctx, [rf], [], []
        )
        self.assertIn("R022_HYPERURICEMIA_FOOD_AVOID", result.candidate_rule_ids)

    def test_disease_exercise_recall(self):
        from safety.models import DrugContext, RiskFlag
        rf = RiskFlag(code="acute_gout_flare", severity="REVIEW", source_rule_id="PR004")
        drug_ctx = DrugContext(
            current_drugs=[], mentioned_drugs=[], recommended_drugs=[], resulting_drugs=[]
        )
        result = select_candidate_rule_ids(
            self.repo, drug_ctx, [rf], [], []
        )
        self.assertIn("R021_GOUT_ACUTE_VIGOROUS_BLOCK", result.candidate_rule_ids)


# --------------------------------------------------------------------------
# Audit-level tests: BLOCK / REVIEW / PASS via the new engine.
# --------------------------------------------------------------------------


DEFAULT_RULES_DIR = ROOT / "rules"


class AuditPipelineTests(unittest.TestCase):
    DEFAULT_RULES_DIR = ROOT / "rules"

    @classmethod
    def setUpClass(cls):
        cls.engine = DialogueSafetyEngine(cls.DEFAULT_RULES_DIR)

    def _patient(self, **kwargs):
        base = {"patient_id": "P", "current_medications": []}
        base.update(kwargs)
        return base

    def test_dose_rule_blocks_via_structured(self):
        report = self.engine.audit(
            patient_state=self._patient(),
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "氨氯地平", "action": "increase",
                     "dose_value": 20, "dose_unit": "mg", "frequency_per_day": 1}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R001_AMLODIPINE_MAX_DAILY_DOSE", ids)
        self.assertEqual(report.decision, "BLOCK")

    def test_patient_condition_blocks(self):
        report = self.engine.audit(
            patient_state=self._patient(egfr=24),
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "二甲双胍", "action": "continue"}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R002_METFORMIN_EGFR_LT_30", ids)

    def test_drug_drug_blocks(self):
        report = self.engine.audit(
            patient_state=self._patient(
                current_medications=[
                    {"name": "辛伐他汀", "status": "active"},
                    {"name": "克拉霉素", "status": "active"},
                ]
            ),
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "辛伐他汀", "action": "continue"}
                ],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R003_SIMVASTATIN_CLARITHROMYCIN", ids)

    def test_drug_food_reviews_when_recommended(self):
        report = self.engine.audit(
            patient_state=self._patient(
                current_medications=[{"name": "赖诺普利", "status": "active"}]
            ),
            dialogue_output={
                "reply_text": "",
                "medication_actions": [],
                "food_advice": [
                    {"food": "含钾盐替代品", "action": "recommend", "instruction": "全部替换普通盐"}
                ],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R004_LISINOPRIL_POTASSIUM_SALT", ids)

    def test_drug_food_does_not_fire_when_avoided(self):
        # Same as above but food_advice.action=avoid -> must NOT fire.
        report = self.engine.audit(
            patient_state=self._patient(
                current_medications=[{"name": "赖诺普利", "status": "active"}]
            ),
            dialogue_output={
                "reply_text": "",
                "medication_actions": [],
                "food_advice": [
                    {"food": "含钾盐替代品", "action": "avoid", "instruction": "不要使用"}
                ],
                "exercise_advice": [],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertNotIn("R004_LISINOPRIL_POTASSIUM_SALT", ids)

    def test_drug_exercise_blocks_for_vigorous_low_glucose(self):
        report = self.engine.audit(
            patient_state=self._patient(
                latest_glucose_mmol_l=3.4,
                current_medications=[{"name": "甘精胰岛素", "status": "active"}],
            ),
            dialogue_output={
                "reply_text": "",
                "medication_actions": [],
                "food_advice": [],
                "exercise_advice": [
                    {"activity": "跑步", "intensity": "vigorous", "action": "recommend"}
                ],
            },
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R005_INSULIN_LOW_GLUCOSE_VIGOROUS_EXERCISE", ids)

    def test_safe_output_passes(self):
        report = self.engine.audit(
            patient_state=self._patient(egfr=88, latest_glucose_mmol_l=6.8,
                                        latest_systolic_bp_mmHg=120),
            dialogue_output={
                "reply_text": "",
                "medication_actions": [
                    {"drug": "氨氯地平", "action": "start",
                     "dose_value": 5, "dose_unit": "mg", "frequency_per_day": 1}
                ],
                "food_advice": [],
                "exercise_advice": [
                    {"activity": "快走", "intensity": "moderate", "action": "recommend"}
                ],
                "care_actions": [],
            },
        )
        self.assertEqual(report.decision, "PASS")

    def test_report_includes_v4_fields(self):
        report = self.engine.audit(
            patient_state=self._patient(),
            dialogue_output={
                "reply_text": "",
                "medication_actions": [],
                "food_advice": [],
                "exercise_advice": [],
            },
        )
        payload = report.to_dict()
        self.assertIn("risk_flags", payload)
        self.assertIn("matched_entities", payload)
        self.assertIn("candidate_rule_ids", payload)
        self.assertIn("evaluated_rule_ids", payload)
        self.assertIn("regeneration_constraints", payload)
        self.assertIn("timing_ms", payload)
        self.assertIn("current_drugs", payload)
        self.assertIn("recommended_drugs", payload)
        self.assertIn("resulting_drugs", payload)
        self.assertIn("text_extractions", payload)
        self.assertIn("consistency_violations", payload)
        self.assertIn("retrieval_channels", payload)
        # Legacy keys preserved.
        self.assertIn("decision", payload)
        self.assertIn("violations", payload)
        self.assertIn("patient_visible_response", payload)


# --------------------------------------------------------------------------
# Repository validation
# --------------------------------------------------------------------------


class RepositoryValidationTests(unittest.TestCase):
    def _write_rules_dir(self, base, files):
        for name, body in files.items():
            (base / name).write_text(
                json.dumps(body, ensure_ascii=False),
                encoding="utf-8",
            )

    def _manifest(self, files):
        return {
            "ruleset_version": "test",
            "rule_files": ["aliases.json"] + list(files),
        }

    def test_missing_required_field_fails(self):
        with __import__("tempfile").TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._write_rules_dir(
                base,
                {
                    "manifest.json": self._manifest(["dose_rules.json"]),
                    "aliases.json": {},
                    "dose_rules.json": {
                        "rules": [
                            {
                                "id": "R_X",
                                # missing 'severity'
                                "type": "max_daily_dose",
                                "triggers": {"drugs_any": ["amlodipine"], "keywords_any": [], "patient_fields_any": []},
                                "parameters": {"drug": "amlodipine", "max_daily_mg": 10},
                                "message": "x",
                            }
                        ]
                    },
                },
            )
            with self.assertRaises(RuleLoadError):
                RuleRepository(base)

    def test_unknown_type_fails(self):
        with __import__("tempfile").TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._write_rules_dir(
                base,
                {
                    "manifest.json": self._manifest(["dose_rules.json"]),
                    "aliases.json": {},
                    "dose_rules.json": {
                        "rules": [
                            {
                                "id": "R_X",
                                "type": "made_up_type",
                                "severity": "BLOCK",
                                "triggers": {"drugs_any": [], "keywords_any": [], "patient_fields_any": []},
                                "parameters": {},
                                "message": "x",
                            }
                        ]
                    },
                },
            )
            with self.assertRaises(RuleLoadError):
                RuleRepository(base)

    def test_unknown_severity_fails(self):
        with __import__("tempfile").TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._write_rules_dir(
                base,
                {
                    "manifest.json": self._manifest(["dose_rules.json"]),
                    "aliases.json": {},
                    "dose_rules.json": {
                        "rules": [
                            {
                                "id": "R_X",
                                "type": "max_daily_dose",
                                "severity": "NOPE",
                                "triggers": {"drugs_any": [], "keywords_any": [], "patient_fields_any": []},
                                "parameters": {"drug": "amlodipine", "max_daily_mg": 10},
                                "message": "x",
                            }
                        ]
                    },
                },
            )
            with self.assertRaises(RuleLoadError):
                RuleRepository(base)


if __name__ == "__main__":
    unittest.main()