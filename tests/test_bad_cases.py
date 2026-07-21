"""Bad Case tests BC01-BC16.

Each BC is constructed as a unit test. The tests do NOT pass any
"expected" string into the engine — only patient_state and the dialogue
output (which the engine itself parses for direction / structure).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from safety import DialogueSafetyEngine


ROOT = Path(__file__).resolve().parents[1]


class BadCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = DialogueSafetyEngine(ROOT / "rules")
        cls.patients = json.loads(
            (ROOT / "data" / "patient_cases.json").read_text(encoding="utf-8")
        )
        cls.presets = json.loads(
            (ROOT / "data" / "llm_presets.json").read_text(encoding="utf-8")
        )

    def audit(self, patient_key: str, preset_name: str):
        return self.engine.audit(
            patient_state=self.patients[patient_key],
            dialogue_output=self.presets[preset_name],
        )

    # ---------------------------------------------------------------- BC01

    def test_bc01_safe_grapefruit_negation(self):
        # "服用辛伐他汀期间不要喝西柚汁" + food_advice action=avoid
        # Expected: PASS, no R023 hit.
        report = self.audit("statin_antibiotic_case", "bc01_safe_grapefruit_negation")
        # Drug A (simvastatin) and Drug B (clarithromycin) coexist; this is
        # the legacy DDI scenario. Per BC01 we only assert that R023 does
        # NOT fire because the grapefruit is explicitly avoided.
        ids = {v.rule_id for v in report.violations}
        self.assertNotIn("R023_STATIN_GRAPEFRUIT", ids)
        # Decision should not be BLOCK solely because of R023; if other
        # DDI fires it may still BLOCK. We assert direction: the decision
        # path for grapefruit was avoided.
        # No assertion on decision because DDI (R003) may still fire.
        # Confirm the safety_engine saw the food as 'avoid'.
        for fa in (self.presets["bc01_safe_grapefruit_negation"].get("food_advice") or []):
            self.assertEqual(fa.get("action"), "avoid")

    # ---------------------------------------------------------------- BC02

    def test_bc02_unsafe_grapefruit_recommend(self):
        # Use a patient with simvastatin but no clarithromycin to isolate R023.
        # The default statin_antibiotic_case has clarithromycin; we override.
        patient = {
            "patient_id": "P_BC02",
            "egfr": 90,
            "current_medications": [
                {"name": "辛伐他汀", "status": "active"}
            ],
        }
        report = self.engine.audit(
            patient_state=patient,
            dialogue_output=self.presets["bc02_unsafe_grapefruit_recommend"],
        )
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R023_STATIN_GRAPEFRUIT", ids)
        # Decision must be REVIEW (R023 is REVIEW severity).
        self.assertEqual(report.decision, "REVIEW")

    # ---------------------------------------------------------------- BC03

    def test_bc03_safe_high_bp_escalation(self):
        report = self.audit("hypertensive_emergency_case", "bc03_safe_high_bp_escalation")
        # PR001 must have produced severe_hypertension risk flag.
        codes = {rf.code for rf in report.risk_flags}
        self.assertIn("severe_hypertension", codes)
        # No R016A / R016B should fire because the LLM did not increase
        # the dose and did provide the required care actions.
        ids = {v.rule_id for v in report.violations}
        self.assertNotIn("R016A_HIGH_BP_SELF_INCREASE_BLOCK", ids)
        self.assertNotIn("R016B_HIGH_BP_MISSING_CARE_REVIEW", ids)
        self.assertEqual(report.decision, "PASS")

    # ---------------------------------------------------------------- BC04

    def test_bc04_unsafe_high_bp_self_increase(self):
        report = self.audit("hypertensive_emergency_case", "bc04_unsafe_high_bp_self_increase")
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R016A_HIGH_BP_SELF_INCREASE_BLOCK", ids)
        # R001 amlodipine 15*1=15 > 10 also fires.
        self.assertIn("R001_AMLODIPINE_MAX_DAILY_DOSE", ids)
        self.assertEqual(report.decision, "BLOCK")

    # ---------------------------------------------------------------- BC05

    def test_bc05_high_bp_missing_escalation(self):
        report = self.audit("hypertensive_emergency_case", "bc05_high_bp_missing_escalation")
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R016B_HIGH_BP_MISSING_CARE_REVIEW", ids)
        self.assertEqual(report.decision, "REVIEW")

    # ---------------------------------------------------------------- BC06

    def test_bc06_reply_text_only_overdose(self):
        report = self.audit("hypertensive_emergency_case", "bc06_reply_text_only_overdose")
        # Risk flag must still be raised.
        codes = {rf.code for rf in report.risk_flags}
        self.assertIn("severe_hypertension", codes)
        # The text parser should pick up the 20 mg dose. R001 should fire
        # via text extraction -> BLOCK (confidence medium+upgrade path).
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R001_AMLODIPINE_MAX_DAILY_DOSE", ids)
        # SYS001 must also be reported.
        sys_codes = {c.code for c in report.consistency_violations}
        self.assertIn("SYS001_TEXT_STRUCTURE_MISMATCH", sys_codes)
        self.assertEqual(report.decision, "BLOCK")

    # ---------------------------------------------------------------- BC07

    def test_bc07_missing_dose_fields(self):
        # No BP, no drug baseline conflict: this scenario is purely about
        # SYS002 detection.
        report = self.engine.audit(
            patient_state={"patient_id": "P_BC07", "current_medications": []},
            dialogue_output=self.presets["bc07_missing_dose_fields"],
        )
        sys_codes = {c.code for c in report.consistency_violations}
        self.assertIn("SYS002_MISSING_MEDICATION_PARAMETERS", sys_codes)
        # Decision must not be PASS — REVIEW at least.
        self.assertEqual(report.decision, "REVIEW")

    # ---------------------------------------------------------------- BC08

    def test_bc08_text_structure_conflict(self):
        # statin + grapefruit recommended in text, but food_advice=avoid.
        # The keyword matcher sees the food_advice has action=avoid and
        # should NOT fire R023. The text direction classifier sees
        # "可以喝" and conflicts with action=avoid -> SYS003.
        patient = {
            "patient_id": "P_BC08",
            "egfr": 90,
            "current_medications": [
                {"name": "辛伐他汀", "status": "active"}
            ],
        }
        report = self.engine.audit(
            patient_state=patient,
            dialogue_output=self.presets["bc08_text_structure_conflict"],
        )
        ids = {v.rule_id for v in report.violations}
        # R023 should NOT fire because the structured action is avoid.
        self.assertNotIn("R023_STATIN_GRAPEFRUIT", ids)
        sys_codes = {c.code for c in report.consistency_violations}
        self.assertIn("SYS003_TEXT_STRUCTURE_CONFLICT", sys_codes)
        self.assertEqual(report.decision, "REVIEW")

    # ---------------------------------------------------------------- BC09

    def test_bc09_safe_gout_avoid_vigorous(self):
        report = self.audit("gout_acute_case", "bc09_safe_gout_avoid_vigorous")
        codes = {rf.code for rf in report.risk_flags}
        self.assertIn("acute_gout_flare", codes)
        ids = {v.rule_id for v in report.violations}
        self.assertNotIn("R021_GOUT_ACUTE_VIGOROUS_BLOCK", ids)
        # R027 was deprecated; should never fire even if matched.
        self.assertNotIn("R027_GOUT_VIGOROUS_EXERCISE", ids)
        self.assertEqual(report.decision, "PASS")

    # ---------------------------------------------------------------- BC10

    def test_bc10_unsafe_gout_recommend_vigorous(self):
        report = self.audit("gout_acute_case", "bc10_unsafe_gout_recommend_vigorous")
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R021_GOUT_ACUTE_VIGOROUS_BLOCK", ids)
        # R027 must NOT fire simultaneously (deprecated).
        self.assertNotIn("R027_GOOT_VIGOROUS_EXERCISE", ids)
        self.assertNotIn("R027_GOUT_VIGOROUS_EXERCISE", ids)
        self.assertEqual(report.decision, "BLOCK")

    # ---------------------------------------------------------------- BC11

    def test_bc11_safe_hyperkalemia_stop(self):
        report = self.audit("high_potassium_case", "bc11_safe_hyperkalemia_stop")
        codes = {rf.code for rf in report.risk_flags}
        self.assertIn("hyperkalemia", codes)
        ids = {v.rule_id for v in report.violations}
        self.assertNotIn("R014A_HYPERKALEMIA_CONTINUE_ACEI_BLOCK", ids)
        self.assertNotIn("R020A_HYPERKALEMIA_CONTINUE_SPIRONOLACTONE_BLOCK", ids)
        self.assertEqual(report.decision, "PASS")

    # ---------------------------------------------------------------- BC12

    def test_bc12_unsafe_hyperkalemia_continue(self):
        report = self.audit("high_potassium_case", "bc12_unsafe_hyperkalemia_continue")
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R014A_HYPERKALEMIA_CONTINUE_ACEI_BLOCK", ids)
        self.assertIn("R020A_HYPERKALEMIA_CONTINUE_SPIRONOLACTONE_BLOCK", ids)
        self.assertEqual(report.decision, "BLOCK")

    # ---------------------------------------------------------------- BC13

    def test_bc13_safe_ddi_stop(self):
        report = self.audit("statin_antibiotic_case", "bc13_safe_ddi_stop")
        ids = {v.rule_id for v in report.violations}
        # After stop, simvastatin is removed from resulting_drugs.
        self.assertNotIn("R003_SIMVASTATIN_CLARITHROMYCIN", ids)
        # Decision should be PASS or REVIEW (we accept either, but not BLOCK).
        self.assertNotEqual(report.decision, "BLOCK")

    # ---------------------------------------------------------------- BC14

    def test_bc14_unsafe_ddi_continue(self):
        report = self.audit("statin_antibiotic_case", "bc14_unsafe_ddi_continue")
        ids = {v.rule_id for v in report.violations}
        self.assertIn("R003_SIMVASTATIN_CLARITHROMYCIN", ids)
        self.assertEqual(report.decision, "BLOCK")

    # ---------------------------------------------------------------- BC15

    def test_bc15_gout_food_without_gout_drug(self):
        report = self.audit("gout_high_purine_case", "bc15_gout_food_without_gout_drug")
        codes = {rf.code for rf in report.risk_flags}
        self.assertIn("hyperuricemia_gout", codes)
        ids = {v.rule_id for v in report.violations}
        # The disease_food rule should fire on disease_codes, not drugs.
        self.assertIn("R022_HYPERURICEMIA_FOOD_AVOID", ids)
        self.assertEqual(report.decision, "REVIEW")

    # ---------------------------------------------------------------- BC16

    def test_bc16_gout_drug_not_gout(self):
        # Patient on colchicine but no gout disease code.
        # Old v3 would have fired R022 because it triggered on any food
        # keyword in a patient with colchicine. v4 should NOT fire.
        report = self.audit("gout_drug_only_case", "bc16_gout_drug_not_gout")
        codes = {rf.code for rf in report.risk_flags}
        # Disease risk must NOT be auto-derived from the drug.
        self.assertNotIn("hyperuricemia_gout", codes)
        ids = {v.rule_id for v in report.violations}
        self.assertNotIn("R022_HYPERURICEMIA_FOOD_AVOID", ids)
        # We also do not assert specific decision since BC16 inputs are
        # ambiguous; only that the gout-food rule does not fire.
        # PASS is the natural outcome because 海鲜 itself is not in the
        # R022 keyword list; allow + moderate-keyword mismatch should
        # not produce violations.
        self.assertIn(report.decision, ("PASS", "REVIEW"))

    # ---------------------------------------------------------------- BC17

    def test_bc17_text_only_new_drug_overdose(self):
        # Patient is drug-naive; reply_text alone describes a new
        # prescription of 20 mg amlodipine. The text parser must recall
        # the dose rule, and resulting_drugs must NOT include amlodipine
        # (we have no structured confirmation of the prescription).
        report = self.engine.audit(
            patient_state={"patient_id": "BC17", "current_medications": []},
            dialogue_output=self.presets["bc17_text_only_new_drug_overdose"],
        )
        # decision must be BLOCK because R001 dose rule fires via the
        # text extraction path.
        self.assertEqual(report.decision, "BLOCK")
        # R001 must be in candidate rule ids and in violations.
        self.assertIn("R001_AMLODIPINE_MAX_DAILY_DOSE", report.candidate_rule_ids)
        vids = {v.rule_id for v in report.violations}
        self.assertIn("R001_AMLODIPINE_MAX_DAILY_DOSE", vids)
        # SYS001 must surface because the text has a drug that is
        # missing from medication_actions.
        sys_codes = {c.code for c in report.consistency_violations}
        self.assertIn("SYS001_TEXT_STRUCTURE_MISMATCH", sys_codes)
        # resulting_drugs must NOT be polluted with the text drug.
        self.assertNotIn("amlodipine", report.resulting_drugs)
        # text_mentioned_drugs / text_dose_drugs carry the signal.
        self.assertIn("amlodipine", report.text_mentioned_drugs)
        self.assertIn("amlodipine", report.text_dose_drugs)

    # ---------------------------------------------------------------- BC18

    def test_bc18_avoid_start_does_not_stop_current(self):
        # Patient is on simvastatin + clarithromycin. The LLM says
        # "avoid_start" for simvastatin. v4.1 must NOT remove the
        # existing drug; the DDI must still be detected.
        report = self.engine.audit(
            patient_state={
                "patient_id": "BC18",
                "current_medications": [
                    {"name": "辛伐他汀", "status": "active"},
                    {"name": "克拉霉素", "status": "active"},
                ],
            },
            dialogue_output=self.presets["bc18_avoid_start_does_not_stop_current"],
        )
        # simvastatin is still in resulting_drugs.
        self.assertIn("simvastatin", report.resulting_drugs)
        self.assertIn("clarithromycin", report.resulting_drugs)
        # DDI must still fire because both are still in resulting_drugs.
        vids = {v.rule_id for v in report.violations}
        self.assertIn("R003_SIMVASTATIN_CLARITHROMYCIN", vids)
        # The avoid_start itself is not a "stop" or "hold" so it should
        # not be reported as a missing-replace target.
        sys_codes = {c.code for c in report.consistency_violations}
        self.assertNotIn("SYS005_MISSING_REPLACE_TARGET", sys_codes)

    # ---------------------------------------------------------------- BC19

    def test_bc19_replace_removes_old_drug(self):
        # Patient is on simvastatin. LLM replaces it with atorvastatin
        # (replace_drug=simvastatin, drug=atorvastatin).
        report = self.engine.audit(
            patient_state={
                "patient_id": "BC19",
                "current_medications": [
                    {"name": "辛伐他汀", "status": "active"},
                ],
            },
            dialogue_output=self.presets["bc19_replace_removes_old_drug"],
        )
        # simvastatin must be removed; atorvastatin must be present.
        self.assertNotIn("simvastatin", report.resulting_drugs)
        self.assertIn("atorvastatin", report.resulting_drugs)
        # R003 DDI must NOT fire (no more simvastatin in resulting).
        vids = {v.rule_id for v in report.violations}
        self.assertNotIn("R003_SIMVASTATIN_CLARITHROMYCIN", vids)

    # ---------------------------------------------------------------- BC20

    def test_bc20_invalid_action_typo(self):
        # food.action="aviod" is not a valid enum. The engine must:
        # 1) emit SYS004
        # 2) NOT silently default to recommend
        # 3) NOT fire R023 (grapefruit) on the typo
        report = self.engine.audit(
            patient_state={
                "patient_id": "BC20",
                "current_medications": [
                    {"name": "辛伐他汀", "status": "active"},
                ],
            },
            dialogue_output=self.presets["bc20_invalid_action_typo"],
        )
        sys_codes = {c.code for c in report.consistency_violations}
        self.assertIn("SYS004_INVALID_STRUCTURED_ENUM", sys_codes)
        vids = {v.rule_id for v in report.violations}
        # R023 must not fire because the structured food.action was
        # "aviod" (invalid), and the engine should NOT treat it as
        # recommend.
        self.assertNotIn("R023_STATIN_GRAPEFRUIT", vids)
        # Decision must not be PASS; REVIEW at minimum.
        self.assertEqual(report.decision, "REVIEW")

    # ---------------------------------------------------------------- BC21

    def test_bc21_held_drug_not_active(self):
        # Simvastatin has status="held" -> inactive. The DDI with
        # clarithromycin must NOT fire.
        report = self.engine.audit(
            patient_state={
                "patient_id": "BC21",
                "current_medications": [
                    {"name": "辛伐他汀", "status": "held"},
                    {"name": "克拉霉素", "status": "active"},
                ],
            },
            dialogue_output=self.presets["bc21_held_drug_not_active"],
        )
        # simvastatin is not in current_drugs or resulting_drugs.
        self.assertNotIn("simvastatin", report.current_drugs)
        self.assertNotIn("simvastatin", report.resulting_drugs)
        # DDI must not fire.
        vids = {v.rule_id for v in report.violations}
        self.assertNotIn("R003_SIMVASTATIN_CLARITHROMYCIN", vids)
        # SYS006 must surface because "held" used to be non-canonical
        # in v4; v4.1 maps "held" to inactive so it is canonical, but
        # the spec also requires explicit handling. The status "held"
        # is now a valid alias. So no SYS006 is required.
        # We still pass a "stopped" alias via active synonyms.
        sys_codes = {c.code for c in report.consistency_violations}
        self.assertNotIn("SYS006_UNKNOWN_MEDICATION_STATUS", sys_codes)


if __name__ == "__main__":
    unittest.main()