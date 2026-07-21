"""One-time migration script: port legacy fields (drug, food, activity,
concept) in dialogue_output to the strict v4.2.0 schema.

Run once during the refactor and keep under tools/ for reference.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    from audit_scenarios import load_all_scenarios
    from audit_scenarios.loader import invalidate_cache

    # Build drug name → drug_id mapping via the alias table.
    from safety.rule_repository import RuleRepository
    repo = RuleRepository(ROOT / "rules")
    name_to_id = {}
    for canonical, aliases in repo.aliases.canonical_to_aliases.items():
        for a in aliases:
            name_to_id[a] = canonical

    # Food / activity concept-id helpers — fall back to a lowercased
    # form so the consistency checker's normalized() comparison
    # matches the keywords known to the rule base.
    def food_id_for(name):
        n = (name or "").strip().lower()
        if not n:
            return ""
        if "西柚" in n or "葡萄柚" in n or "grapefruit" in n:
            return "grapefruit"
        if "老火鸡汤" in n or "鸡汤" in n:
            return "老火鸡汤"
        if "啤酒" in n or "beer" in n:
            return "啤酒"
        if "含钾" in n or "钾盐替代" in n or "低钠盐" in n:
            return "含钾盐替代品"
        if "猪肝" in n or "海鲜" in n or "动物内脏" in n:
            return n
        return n

    def activity_id_for(name):
        n = (name or "").strip().lower()
        if "跑步" in n:
            return "running"
        if "游泳" in n:
            return "swimming"
        if "快走" in n:
            return "brisk_walking"
        if "散步" in n or "步行" in n:
            return "walking"
        return n

    invalidate_cache()
    scenarios = load_all_scenarios()

    for s in scenarios:
        do = s["audit_input"]["dialogue_output"]
        # medication_actions
        new_meds = []
        for ma in (do.get("medication_actions") or []):
            if not isinstance(ma, dict):
                new_meds.append(ma)
                continue
            nma = dict(ma)
            if "drug" in nma:
                if "drug_name" not in nma:
                    nma["drug_name"] = nma["drug"]
                if not nma.get("drug_id"):
                    nma["drug_id"] = (
                        name_to_id.get(nma.get("drug_name", ""), "")
                        or "unknown_drug"
                    )
                # remove the legacy key so strict_mode does not flag it
                del nma["drug"]
            if "replace_drug" in nma:
                if "replace_drug_name" not in nma:
                    nma["replace_drug_name"] = nma["replace_drug"]
                if not nma.get("replace_drug_id"):
                    nma["replace_drug_id"] = (
                        name_to_id.get(nma.get("replace_drug_name", ""), "")
                        or "unknown_drug"
                    )
                del nma["replace_drug"]
            act = nma.get("action", "")
            if (act in ("start", "increase", "decrease", "replace",
                         "continue")
                and not nma.get("route")):
                nma["route"] = "oral"
            new_meds.append(nma)
        do["medication_actions"] = new_meds

        # food_advice
        new_foods = []
        for fa in (do.get("food_advice") or []):
            if not isinstance(fa, dict):
                new_foods.append(fa)
                continue
            nfa = dict(fa)
            if "food" in nfa:
                if "food_name" not in nfa:
                    nfa["food_name"] = nfa["food"]
                del nfa["food"]
            if "concept" in nfa:
                nfa["food_concept_id"] = nfa["concept"]
                del nfa["concept"]
            if "food_concept_id" not in nfa:
                nfa["food_concept_id"] = food_id_for(
                    nfa.get("food_name", ""))
            new_foods.append(nfa)
        do["food_advice"] = new_foods

        # exercise_advice
        new_ex = []
        for ea in (do.get("exercise_advice") or []):
            if not isinstance(ea, dict):
                new_ex.append(ea)
                continue
            nea = dict(ea)
            if "activity" in nea:
                if "activity_name" not in nea:
                    nea["activity_name"] = nea["activity"]
                del nea["activity"]
            if "concept" in nea:
                nea["activity_concept_id"] = nea["concept"]
                del nea["concept"]
            if "activity_concept_id" not in nea:
                nea["activity_concept_id"] = activity_id_for(
                    nea.get("activity_name", ""))
            new_ex.append(nea)
        do["exercise_advice"] = new_ex

        do.setdefault("requires_review", False)
        do.setdefault("uncertainty_reasons", [])

    (ROOT / "data" / "audit_scenarios.json").write_text(
        json.dumps(scenarios, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Migrated {len(scenarios)} scenarios.")


if __name__ == "__main__":
    main()
