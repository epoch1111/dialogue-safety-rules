"""Trace Demo for v4.1.1.

This script demonstrates the END-TO-END safety flow for two scenarios:

  - 场景 A: metformin + eGFR 24 -> BLOCK
  - 场景 B: same patient, but the LLM recommends HOLDING metformin and
    asking the doctor for an urgent renal eval -> PASS / REVIEW.

For each scenario, the script prints a 16-step trace to the console and
persists the same information to:

  logs/trace_demo_output.txt   (human-readable)
  logs/trace_demo_output.json  (machine-readable)

The audit() call only ever receives ``patient_state`` and the
``dialogue_output`` dict. NO expected_decision / expected_rule_ids
is ever passed in: the engine does not accept such arguments. All
assertions live in the unit test layer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from safety import DialogueSafetyEngine


ROOT = Path(__file__).parent
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)
TXT_LOG = LOGS / "trace_demo_output.txt"
JSON_LOG = LOGS / "trace_demo_output.json"


def _write_log_header(lines: List[str]):
    lines.append("=" * 80)
    lines.append("v4.1.1 TRACE DEMO OUTPUT")
    lines.append("=" * 80)
    lines.append("")
    lines.append("This file documents the end-to-end audit flow for two scenarios.")
    lines.append("The audit() call receives only patient_state and dialogue_output;")
    lines.append("NO expected_decision or expected_rule_ids is ever passed in.")
    lines.append("")


def _format_one_scenario(
    scenario_id: str,
    title: str,
    patient_state: Dict[str, Any],
    reply_text: str,
    dialogue_output: Dict[str, Any],
    engine: DialogueSafetyEngine,
) -> tuple:
    """Run audit (debug=True) and produce (text_lines, json_dict)."""
    text_lines: List[str] = []
    text_lines.append("=" * 80)
    text_lines.append(f"场景{scenario_id}: {title}")
    text_lines.append("=" * 80)
    text_lines.append("")

    # The audit() call only receives patient_state + dialogue_output.
    # NO expected_decision is passed in. Assertions live in tests.
    report = engine.audit(
        patient_state=patient_state,
        dialogue_output=dialogue_output,
        debug=True,
    )

    # 1. Patient state
    text_lines.append("【1. 输入患者病历 patient_state】")
    text_lines.append(json.dumps(_scrub_pii(patient_state), ensure_ascii=False, indent=2))
    text_lines.append("")

    # 2. Original reply text
    text_lines.append("【2. Dialogue Agent 原始文本】")
    text_lines.append(reply_text)
    text_lines.append("")

    # 3. Structured output
    text_lines.append("【3. Dialogue Agent 结构化输出】")
    text_lines.append(json.dumps(dialogue_output, ensure_ascii=False, indent=2))
    text_lines.append("")

    # 4. Normalized draft
    text_lines.append("【4. 标准化结果】")
    norm_lines: List[str] = []
    for ma in report.matched_entities.drugs:
        norm_lines.append(f"  - canonical drug: {ma}")
    for ma in dialogue_output.get("medication_actions") or []:
        norm_lines.append(f"  - action: drug={ma.get('drug')!r} -> {ma.get('action')!r}")
        if ma.get("dose_value") is not None:
            norm_lines.append(f"      dose_value={ma['dose_value']!r} unit={ma.get('dose_unit')!r} "
                              f"freq_per_day={ma.get('frequency_per_day')!r}")
    if not norm_lines:
        norm_lines.append("  (无)")
    text_lines.extend(norm_lines)
    text_lines.append("")

    # 5. DrugContext
    text_lines.append("【5. DrugContext】")
    text_lines.append(json.dumps({
        "current_drugs": report.current_drugs,
        "mentioned_drugs": report.matched_entities.drugs,
        "recommended_drugs": report.recommended_drugs,
        "resulting_drugs": report.resulting_drugs,
        "text_mentioned_drugs": report.text_mentioned_drugs,
        "text_dose_drugs": report.text_dose_drugs,
    }, ensure_ascii=False, indent=2))
    text_lines.append("")

    # 6. Text extraction
    text_lines.append("【6. 文本解析结果】")
    extras_payload = []
    for ext in report.text_extractions:
        extras_payload.append({
            "drug": ext.drug,
            "dose_value": ext.dose_value,
            "dose_unit": ext.dose_unit,
            "frequency_per_day": ext.frequency_per_day,
            "confidence": ext.confidence,
            "raw_match": ext.raw_match,
        })
    text_lines.append(json.dumps(extras_payload, ensure_ascii=False, indent=2))
    text_lines.append("")

    # 7. Risk flags
    text_lines.append("【7. 患者风险标志】")
    if not report.risk_flags:
        text_lines.append("  无")
    else:
        for rf in report.risk_flags:
            text_lines.append(
                f"  - {rf.code} (严重度={rf.severity}, 来源={rf.source_rule_id})"
            )
    text_lines.append("")

    # 8. Matched entities + patient fields
    text_lines.append("【8. 匹配到的实体和患者字段】")
    text_lines.append(json.dumps({
        "matched_drugs": report.matched_entities.drugs,
        "matched_keywords": report.matched_entities.keywords,
        "patient_fields": report.matched_entities.patient_fields,
        "disease_codes": report.matched_entities.disease_codes,
    }, ensure_ascii=False, indent=2))
    text_lines.append("")

    # 9. Retrieval channels (trace)
    text_lines.append("【9. 召回渠道 retrieval_channels】")
    if not report.retrieval_trace:
        text_lines.append("  无 (debug=False 时不填充)")
    else:
        for rc in report.retrieval_trace:
            text_lines.append(f"  - channel={rc.channel}")
            text_lines.append(f"      key       = {rc.key}")
            text_lines.append(f"      rule_ids  = {sorted(set(rc.rule_ids))}")
    text_lines.append(f"  channels 总集 = {list(report.retrieval_channels)}")
    text_lines.append("")

    # 10. Candidate rule ids
    text_lines.append("【10. 候选规则】")
    text_lines.append(json.dumps(list(report.candidate_rule_ids), ensure_ascii=False, indent=2))
    text_lines.append("")

    # 11. Evaluation trace
    text_lines.append("【11. 每条候选规则的执行过程】")
    if not report.evaluation_trace:
        text_lines.append("  无 (debug=False 时不填充)")
    else:
        for ev in report.evaluation_trace:
            text_lines.append(
                f"  - rule_id={ev.rule_id} type={ev.type} matched={ev.matched} "
                f"severity={ev.severity}"
            )
            for c in ev.conditions:
                marker = "PASS" if c.passed else "FAIL"
                text_lines.append(
                    f"      [{marker}] {c.description} "
                    f"(actual={c.actual!r}, operator={c.operator!r}, "
                    f"expected={c.expected!r})"
                )
    text_lines.append("")

    # 12. Consistency
    text_lines.append("【12. 一致性检查】")
    if not report.consistency_violations:
        text_lines.append("  无")
    else:
        for cv in report.consistency_violations:
            text_lines.append(
                f"  - [{cv.severity}] {cv.code}: {cv.message}"
            )
    text_lines.append("")

    # 13. Hit rules
    text_lines.append("【13. 命中规则】")
    if not report.violations:
        text_lines.append("  无命中规则")
    else:
        for v in report.violations:
            text_lines.append(
                f"  - [{v.severity}] {v.rule_id} (category={v.category}): {v.message}"
            )
    text_lines.append("")

    # 14. Final decision
    text_lines.append("【14. 最终结论】")
    text_lines.append(f"  decision = {report.decision}")
    text_lines.append("")

    # 15. Visible response
    text_lines.append("【15. 最终真正发送给患者的内容】")
    text_lines.append(f"  patient_visible_response = {report.patient_visible_response!r}")
    text_lines.append("")

    # 16. Original LLM reply was sent?
    original_sent = (report.decision == "PASS")
    text_lines.append("【16. 原始LLM回复是否发送】")
    text_lines.append(f"  original_llm_reply_was_sent = {original_sent}")
    text_lines.append("")

    # Build the JSON payload for this scenario.
    json_payload = {
        "scenario_id": scenario_id,
        "scenario_title": title,
        "engine_inputs": {
            "patient_state": _scrub_pii(patient_state),
            "dialogue_output": dialogue_output,
        },
        "normalized_input": {
            "medication_actions": dialogue_output.get("medication_actions", []),
            "food_advice": dialogue_output.get("food_advice", []),
            "exercise_advice": dialogue_output.get("exercise_advice", []),
            "care_actions": dialogue_output.get("care_actions", []),
        },
        "drug_context": {
            "current_drugs": report.current_drugs,
            "recommended_drugs": report.recommended_drugs,
            "resulting_drugs": report.resulting_drugs,
            "text_mentioned_drugs": report.text_mentioned_drugs,
            "text_dose_drugs": report.text_dose_drugs,
        },
        "text_extractions": [
            {
                "drug": ext.drug,
                "dose_value": ext.dose_value,
                "dose_unit": ext.dose_unit,
                "frequency_per_day": ext.frequency_per_day,
                "confidence": ext.confidence,
                "raw_match": ext.raw_match,
            }
            for ext in report.text_extractions
        ],
        "risk_flags": [
            {"code": rf.code, "severity": rf.severity, "source": rf.source_rule_id}
            for rf in report.risk_flags
        ],
        "matched_entities": {
            "drugs": report.matched_entities.drugs,
            "keywords": report.matched_entities.keywords,
            "patient_fields": report.matched_entities.patient_fields,
            "disease_codes": report.matched_entities.disease_codes,
        },
        "retrieval_trace": [
            {
                "channel": rc.channel,
                "key": rc.key,
                "rule_ids": sorted(set(rc.rule_ids)),
            }
            for rc in report.retrieval_trace
        ],
        "candidate_rule_ids": list(report.candidate_rule_ids),
        "evaluated_rule_ids": list(report.evaluated_rule_ids),
        "evaluation_trace": [
            {
                "rule_id": ev.rule_id,
                "type": ev.type,
                "matched": ev.matched,
                "severity": ev.severity,
                "conditions": [
                    {
                        "description": c.description,
                        "actual": c.actual,
                        "operator": c.operator,
                        "expected": c.expected,
                        "passed": c.passed,
                    }
                    for c in ev.conditions
                ],
            }
            for ev in report.evaluation_trace
        ],
        "consistency_violations": [
            {"code": c.code, "severity": c.severity, "message": c.message}
            for c in report.consistency_violations
        ],
        "violations": [
            {
                "rule_id": v.rule_id,
                "severity": v.severity,
                "category": v.category,
                "message": v.message,
            }
            for v in report.violations
        ],
        "decision": report.decision,
        "patient_visible_response": report.patient_visible_response,
        "original_llm_reply_was_sent": report.decision == "PASS",
    }

    return text_lines, json_payload


def _scrub_pii(state: Dict[str, Any]) -> Dict[str, Any]:
    """Demo uses synthetic patient_id only. Strip any accidental PII keys
    so we never persist names / phones / ID-card numbers."""
    cleaned: Dict[str, Any] = {}
    for k, v in (state or {}).items():
        klow = k.lower()
        if klow in {"name", "phone", "tel", "mobile", "id_card", "id_number",
                    "address", "national_id"}:
            cleaned[k] = "<redacted>"
            continue
        cleaned[k] = v
    return cleaned


# ---------------------------------------------------------------------------
# Scenarios.
# ---------------------------------------------------------------------------


def scenario_a() -> tuple:
    """A: metformin + eGFR 24 -> BLOCK."""
    patient_state = {
        "patient_id": "TRACE_METFORMIN_EGFR",
        "egfr": 24,
        "current_medications": [
            {"name": "二甲双胍", "status": "active"},
        ],
    }
    reply_text = "建议继续使用二甲双胍500毫克，每日2次。"
    dialogue_output = {
        "reply_text": reply_text,
        "medication_actions": [
            {
                "drug": "二甲双胍",
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
    }
    return ("A", "二甲双胍 + eGFR 24, should BLOCK", patient_state,
            reply_text, dialogue_output)


def scenario_b() -> tuple:
    """B: same patient, LLM recommends HOLDING metformin + urgent eval
    -> PASS / REVIEW."""
    patient_state = {
        "patient_id": "TRACE_METFORMIN_SAFE",
        "egfr": 24,
        "current_medications": [
            {"name": "二甲双胍", "status": "active"},
        ],
    }
    reply_text = (
        "您的肾功能指标需要医生重新评估,目前不要自行继续或调整二甲双胍,"
        "请尽快联系医生。"
    )
    dialogue_output = {
        "reply_text": reply_text,
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
    return ("B", "Same patient, but hold metformin -> should NOT fire R002",
            patient_state, reply_text, dialogue_output)


def main() -> int:
    print("[trace_demo] Loading engine from", ROOT / "rules")
    engine = DialogueSafetyEngine(ROOT / "rules")

    text_lines: List[str] = []
    json_payloads: List[Dict[str, Any]] = []

    _write_log_header(text_lines)
    text_lines.append("")

    for builder in (scenario_a, scenario_b):
        scenario_id, title, pstate, reply, output = builder()
        t_lines, payload = _format_one_scenario(
            scenario_id=scenario_id,
            title=title,
            patient_state=pstate,
            reply_text=reply,
            dialogue_output=output,
            engine=engine,
        )
        text_lines.extend(t_lines)
        json_payloads.append(payload)

    TXT_LOG.write_text("\n".join(text_lines), encoding="utf-8")
    JSON_LOG.write_text(
        json.dumps({"scenarios": json_payloads}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[trace_demo] Wrote {TXT_LOG}")
    print(f"[trace_demo] Wrote {JSON_LOG}")

    # Echo the text log to the console too.
    print()
    print(TXT_LOG.read_text(encoding="utf-8"))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
