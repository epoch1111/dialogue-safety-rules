"""v4.2.0 recall-pressure tests.

v4.2.0 differs from v4.1.1: instead of asking for 1000 patient_state
rules that ALL share ``egfr`` (which forced a downgrade to the
``simple_drug_index``), we now construct a synthetic rule base where:

- N synthetic patient_state rules exist, each bound to
  ``drug=metformin`` BUT with a unique patient field
  (``lab_0000`` … ``lab_NNNN``).
- A single real rule ``R002_METFORMIN_EGFR_LT_30`` is loaded.
- The patient only carries the single field ``egfr`` and only takes
  metformin.

Strict assertions:
- ``candidate_rule_ids == ["R002_METFORMIN_EGFR_LT_30"]``
- ``evaluated_rule_ids == ["R002_METFORMIN_EGFR_LT_30"]``
- ``violations`` only contains ``R002_METFORMIN_EGFR_LT_30``
- ``decision == "BLOCK"``

All synthetic rules carry ``source_type = synthetic_test`` and
``production_eligible = false`` and are written to a tempdir so the
production rule base is never polluted.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from safety import DialogueSafetyEngine
from safety.rule_repository import RuleRepository


ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = ROOT / "rules"


def _build_synthetic_rules(tmpdir: Path, count: int) -> Path:
    """Materialize ``count`` synthetic patient_state rules plus the real
    metformin eGFR < 30 rule into a tempdir that mirrors the production
    manifest structure. Returns the tempdir path.

    For the v4.2.0 recall-pressure test we use a STRIPPED production
    rule base: only the alias table, the metformin patient_state rule
    R002, and the synthetic decoys. Other metformin rules (R008,
    R010, R011) are deliberately omitted so the candidate set
    contains ONLY R002, which is what the spec asks us to prove.
    """
    rules_dir = tmpdir / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)

    # Copy only the alias table; we will build a minimal manifest that
    # loads only R002 + decoys.
    shutil.copy(RULES_DIR / "aliases.json", rules_dir / "aliases.json")

    # Minimal rule base: aliases, R002, decoys.
    minimal_manifest = {
        "ruleset_version": "dialogue-safety-rules-synthetic",
        "schema_version": 4,
        "decision_policy": {
            "BLOCK": "拦截原始回复，不允许发送给患者",
            "REVIEW": "暂缓发送，进入人工复核",
            "PASS": "允许发送",
        },
        "rule_files": [
            "aliases.json",
            "_r002.json",
            "_synthetic_decoys.json",
        ],
    }

    r002_rule = {
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
            "drugs": ["metformin"],
            "field": "egfr",
            "operator": "lt",
            "threshold": 30,
        },
        "message": "患者 eGFR < 30，不能直接给出新增或继续二甲双胍的建议。",
        "source": {
            "document_title": "synthetic stress test fixture (R002 only)",
            "document_version": "v4.2.0",
            "section": "synthetic",
            "evidence_text": "",
        },
    }
    (rules_dir / "_r002.json").write_text(
        json.dumps({"rules": [r002_rule]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    decoys = []
    for i in range(count):
        field_name = f"lab_{i:04d}"
        decoys.append({
            "id": f"SYN_DECOY_{i:06d}",
            "version": 1,
            "status": "active",
            "type": "patient_state",
            "severity": "BLOCK",
            "triggers": {
                "drugs_any": ["metformin"],
                "patient_fields_any": [field_name],
            },
            "parameters": {
                "drugs": ["metformin"],
                "field": field_name,
                "operator": "lt",
                "threshold": 1.0,
            },
            "message": f"synthetic decoy #{i}",
            "source": {
                "document_title": "synthetic stress test fixture",
                "document_version": "v4.2.0",
                "section": "synthetic",
                "evidence_text": "",
            },
        })

    synthetic_payload = {
        "rules": decoys,
        "_synthetic_meta": {
            "source_type": "synthetic_test",
            "production_eligible": False,
            "count": count,
        },
    }
    (rules_dir / "_synthetic_decoys.json").write_text(
        json.dumps(synthetic_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    (rules_dir / "manifest.json").write_text(
        json.dumps(minimal_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return rules_dir


class SyntheticRecallPressureTests(unittest.TestCase):
    """Both 1000 and 10000 variants are exercised here."""

    PATIENT = {
        "patient_id": "P_SYN",
        "egfr": 24,
        "current_medications": [
            {"drug_id": "metformin", "drug_name": "二甲双胍", "status": "active"},
        ],
        "disease_codes": ["diabetes"],
    }
    DIALOGUE = {
        "reply_text": "建议继续二甲双胍",
        "medication_actions": [
            {
                "drug_id": "metformin",
                "drug_name": "二甲双胍",
                "action": "continue",
                "dose_value": 500,
                "dose_unit": "mg",
                "frequency_per_day": 2,
                "route": "oral",
            }
        ],
        "food_advice": [],
        "exercise_advice": [],
        "care_actions": [],
    }
    EXPECTED_RULE_ID = "R002_METFORMIN_EGFR_LT_30"

    def _run_pressure(self, count: int) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            rules_dir = _build_synthetic_rules(tmp_path, count)
            engine = DialogueSafetyEngine(rules_dir)
            report = engine.audit(
                patient_state=self.PATIENT,
                dialogue_output=self.DIALOGUE,
            )

            self.assertEqual(
                report.candidate_rule_ids,
                [self.EXPECTED_RULE_ID],
                f"With {count} synthetic decoys the candidate set must "
                f"contain ONLY {self.EXPECTED_RULE_ID}, got "
                f"{report.candidate_rule_ids}",
            )
            self.assertEqual(report.evaluated_rule_ids, [self.EXPECTED_RULE_ID])
            violation_ids = {v.rule_id for v in report.medical_violations}
            self.assertEqual(violation_ids, {self.EXPECTED_RULE_ID})
            self.assertEqual(report.decision, "BLOCK")

            # No synthetic decoys must appear anywhere in the report.
            for rid in report.candidate_rule_ids + report.evaluated_rule_ids:
                self.assertFalse(rid.startswith("SYN_"))

            # The synthetic rules are still registered (visible via
            # iter_all_rules) but they must NEVER be evaluated.
            self.assertGreater(len(list(engine.repository.iter_all_rules())),
                              count)

    def test_1000_unique_fields_does_not_pollute_recall(self):
        self._run_pressure(1000)

    def test_10000_unique_fields_does_not_pollute_recall(self):
        self._run_pressure(10000)

    def test_synthetic_repository_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_dir = _build_synthetic_rules(Path(tmp), 100)
            engine = DialogueSafetyEngine(rules_dir)
            summary = engine.repository.describe_indexes()
            # drug_field_keys must be > 1 (we added 100 synthetic decoys
            # + R002 + R010 + R011).
            self.assertGreater(summary["drug_field_keys"], 100)


if __name__ == "__main__":
    unittest.main()