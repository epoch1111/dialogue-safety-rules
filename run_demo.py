"""Console Demo for v4.2.1 — unified scenario source.

All scenarios live in ``data/audit_scenarios.json`` and are loaded
through :mod:`audit_scenarios`. This script iterates over every
console-enabled scenario in id order, runs the strict safety engine,
and prints:

- scenario title / summary
- the real AuditReport decision
- DrugContext (current / recommended / resulting drugs)
- candidate rules + hit rules
- patient visible response
- whether the original LLM reply would have been sent

No fixtures, no fake expected results — every value comes from the
real :class:`DialogueSafetyEngine.audit_payload` call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from audit_scenarios import list_console_scenarios
from safety import DialogueSafetyEngine


ROOT = Path(__file__).parent
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)
TXT_LOG = LOGS / "demo_output.txt"


def _run_scenario(scenario: Dict[str, Any], engine: DialogueSafetyEngine) -> Dict[str, Any]:
    audit_input = scenario["audit_input"]
    report = engine.audit_payload(
        payload=audit_input,
        strict_mode=True,
        compat_mode=False,
        debug=True,
    )
    return {"scenario": scenario, "report": report}


def _format_one(scenario: Dict[str, Any], report) -> List[str]:
    lines: List[str] = []
    lines.append("=" * 88)
    lines.append(f"[{scenario.get('category','?')}] {scenario['id']}: {scenario['title']}")
    lines.append("=" * 88)
    lines.append("")
    if scenario.get("summary"):
        lines.append(f"  {scenario['summary']}")
        lines.append("")

    aid = scenario["audit_input"]
    patient_state = aid.get("patient_state", {})
    dialogue_output = aid.get("dialogue_output", {})

    lines.append("【Dialogue Agent 原始输出】")
    lines.append(str(dialogue_output.get("reply_text", "")))
    lines.append("")

    lines.append("【规则引擎结论】")
    lines.append(f"  decision              = {report.decision}")
    lines.append(f"  decision_basis        = {report.decision_basis}")
    lines.append(f"  ruleset_version       = {report.ruleset_version}")
    lines.append(f"  input_schema_version  = {report.input_schema_version}")
    lines.append("")

    lines.append("【风险标志】")
    flags = report.risk_flags or []
    if not flags:
        lines.append("  (无)")
    else:
        for f in flags:
            lines.append(f"  - {f.code} (严重度={f.severity}, 来源={f.source_rule_id})")
    lines.append("")

    lines.append("【药物上下文】")
    lines.append(f"  当前用药     = {report.current_drugs}")
    lines.append(f"  LLM 推荐     = {report.recommended_drugs}")
    lines.append(f"  最终方案     = {report.resulting_drugs}")
    lines.append("")

    lines.append("【匹配 / 候选 / 评估】")
    me = report.matched_entities or {}
    lines.append(f"  匹配药物      = {me.drugs}")
    lines.append(f"  匹配关键词    = {me.keywords}")
    lines.append(f"  疾病编码      = {me.disease_codes}")
    lines.append(f"  候选规则数    = {len(report.candidate_rule_ids or [])}")
    lines.append(f"  实际评估数    = {len(report.evaluated_rule_ids or [])}")
    lines.append(f"  召回通道      = {report.retrieval_channels}")
    lines.append("")

    lines.append("【命中规则】")
    if not report.medical_violations:
        lines.append("  (无)")
    else:
        for v in report.medical_violations:
            lines.append(f"  - [{v.severity}] {v.rule_id}: {v.message}")
    lines.append("")

    consistency_violations = report.consistency_violations or []
    if consistency_violations:
        lines.append("【一致性违规】")
        for c in consistency_violations:
            lines.append(f"  - [{c.severity}] {c.code}: {c.message}")
        lines.append("")

    lines.append("【发送给患者的内容】")
    lines.append(f"  {report.patient_visible_response}")
    lines.append("")
    lines.append(f"【原始 LLM 回复是否发送】 {report.original_llm_reply_was_sent}")
    lines.append("")
    return lines


def main() -> int:
    engine = DialogueSafetyEngine(ROOT / "rules")
    scenarios = list_console_scenarios()
    if not scenarios:
        print("[run_demo] No console scenarios found in data/audit_scenarios.json")
        return 1

    print(f"[run_demo] Loading engine from {ROOT / 'rules'}")
    print(f"[run_demo] Running {len(scenarios)} console scenarios from "
          f"data/audit_scenarios.json")

    all_lines: List[str] = []
    for scenario in scenarios:
        result = _run_scenario(scenario, engine)
        all_lines.extend(_format_one(scenario, result["report"]))

    TXT_LOG.write_text("\n".join(all_lines), encoding="utf-8")
    print(f"[run_demo] wrote {TXT_LOG}")

    print()
    print(TXT_LOG.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
