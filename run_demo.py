from __future__ import annotations

import json
from pathlib import Path

from dialogue_agent import PresetDialogueAgent
from orchestrator import DialogueOrchestrator
from safety import DialogueSafetyEngine


ROOT = Path(__file__).parent

patients = json.loads(
    (ROOT / "data" / "patient_cases.json").read_text(encoding="utf-8")
)

dialogue_agent = PresetDialogueAgent(
    ROOT / "data" / "llm_presets.json"
)
safety_engine = DialogueSafetyEngine(ROOT / "rules")
orchestrator = DialogueOrchestrator(
    dialogue_agent=dialogue_agent,
    safety_engine=safety_engine,
)


def print_case(title: str, patient_key: str, preset_name: str) -> None:
    result = orchestrator.handle_message(
        user_message="请根据我的情况给出药物、饮食和运动建议。",
        patient_state=patients[patient_key],
        preset_name=preset_name,
    )

    print("\n" + "=" * 88)
    print(title)
    print("=" * 88)

    print("\n【Dialogue Agent 原始输出】")
    print(result["llm_draft"]["reply_text"])

    print("\n【规则引擎结论】")
    print(result["audit"]["decision"])

    print("\n【风险标志】")
    flags = result["audit"].get("risk_flags") or []
    if not flags:
        print("  (无)")
    else:
        for f in flags:
            print(f"  - {f['code']} (严重度={f['severity']}, 来源={f['source_rule_id']})")

    print("\n【药物上下文】")
    print(f"  当前用药     = {result['audit'].get('current_drugs')}")
    print(f"  LLM 推荐     = {result['audit'].get('recommended_drugs')}")
    print(f"  最终方案     = {result['audit'].get('resulting_drugs')}")

    print("\n【匹配 / 候选 / 评估】")
    me = result["audit"].get("matched_entities") or {}
    print(f"  匹配药物      = {me.get('drugs')}")
    print(f"  匹配关键词    = {me.get('keywords')}")
    print(f"  疾病编码      = {me.get('disease_codes')}")
    print(f"  候选规则数    = {len(result['audit'].get('candidate_rule_ids') or [])}")
    print(f"  实际评估数    = {len(result['audit'].get('evaluated_rule_ids') or [])}")
    print(f"  召回通道      = {result['audit'].get('retrieval_channels')}")

    print("\n【命中规则】")
    if not result["audit"]["violations"]:
        print("  (无)")
    else:
        for index, violation in enumerate(result["audit"]["violations"], start=1):
            print(
                f"  {index}. [{violation['severity']}] "
                f"{violation['rule_id']}: {violation['message']}"
            )

    cv = result["audit"].get("consistency_violations") or []
    if cv:
        print("\n【一致性违规】")
        for c in cv:
            print(f"  - [{c['severity']}] {c['code']}: {c['message']}")

    print("\n【发送给患者的内容】")
    print(result["sent_to_patient"])
    print("\n【原始 LLM 回复是否发送】", result["original_llm_reply_was_sent"])


if __name__ == "__main__":
    cases = [
        ("场景1：多个高风险问题，应 BLOCK", "unsafe_case", "unsafe_output"),
        ("场景2：只有药物-食物风险，应 REVIEW", "review_only_case", "review_only_output"),
        ("场景3：未命中规则，应 PASS", "safe_case", "safe_output"),
        ("场景4：高尿酸背景 + 高嘌呤食物建议，应 REVIEW", "gout_high_purine_case", "gout_purine_advice"),
        ("场景5：痛风急性发作 + 剧烈运动建议，应 BLOCK", "gout_acute_case", "gout_acute_vigorous"),
        ("场景6：高血压急症 (收缩压>180) 自行加量，应 BLOCK", "hypertensive_emergency_case", "high_bp_self_increase"),
        ("场景7：高血钾 + ACEI + 螺内酯，应 BLOCK", "high_potassium_case", "high_potassium_continue"),
        ("场景8：他汀 + 克拉霉素联用，应 BLOCK", "statin_antibiotic_case", "statin_clarithromycin_combo"),
        ("场景9：秋水仙碱超剂量，应 BLOCK", "colchicine_renal_impairment_case", "colchicine_renal_overdose"),
        ("场景10：他汀 + 西柚汁建议，应 REVIEW（或 BLOCK 因 DDI）", "statin_antibiotic_case", "statin_grapefruit_advice"),
        ("场景11：痛风建议游泳 + 低嘌呤食物，应 PASS", "gout_high_purine_case", "gout_safe_swimming"),
        ("场景12：二甲双胍 + eGFR 中度下降，应 REVIEW", "metformin_moderate_egfr_case", "metformin_egfr_moderate"),
    ]

    for title, patient_key, preset_name in cases:
        print_case(title, patient_key, preset_name)