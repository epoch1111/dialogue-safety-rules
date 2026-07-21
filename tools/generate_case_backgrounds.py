"""Synthesize ``case_profile`` + ``retrieved_evidence`` for every Web
scenario that does NOT already have them.

Sources of truth are all real (audit_input + the rule base) so the
synthesized cards never invent clinical facts that aren't grounded
in the scenario's data.

Adds a marker field ``_generated_background: true`` so the data
provenance is obvious to reviewers.

Run:

    python tools/generate_case_backgrounds.py

This script will WRITE IN PLACE to ``data/audit_scenarios.json``.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _chief_complaint_from(reply_text: str,
                          patient_state: dict) -> str:
    """Pick a plausible chief complaint from the scenario.

    We never invent; we always reference what's actually present.
    Falls back to the reply_text first sentence (truncated to 40
    chars) when no structured labels exist.
    """
    disease_codes = patient_state.get("disease_codes") or []
    if disease_codes:
        return f"基于现有诊断字段：{','.join(disease_codes)}"
    if reply_text:
        first = reply_text.split("。")[0].strip()
        if first:
            return first[:60]
    return "演示场景"


def _case_profile(patient_state: dict, reply_text: str,
                  scenario_id: str) -> dict:
    meds = patient_state.get("current_medications") or []
    measurements = patient_state.get("measurements") or {}
    disease_codes = patient_state.get("disease_codes") or []
    flags = patient_state.get("clinical_flags") or {}

    # Age / sex are not part of v4.2.x strict schema. Keep null so
    # the UI shows the "—" placeholder rather than fabricating
    # demographics.
    age = None
    sex = None

    visit_type = "follow_up"
    if scenario_id.startswith("full_case_08"):
        visit_type = "urgent"
    elif "emergency" in scenario_id:
        visit_type = "urgent"

    history_summary_bits: list[str] = []
    if meds:
        med_names = []
        for m in meds:
            if not isinstance(m, dict):
                med_names.append(str(m))
                continue
            name = m.get("drug_name") or m.get("drug_id") or "?"
            med_names.append(name)
        history_summary_bits.append("当前用药：" + "、".join(med_names))
    measurements_brief = []
    for k, entry in measurements.items():
        if isinstance(entry, dict):
            measurements_brief.append(
                f"{k}={entry.get('value')} {entry.get('unit','')}"
            )
    if measurements_brief:
        history_summary_bits.append("指标：" + "; ".join(measurements_brief))
    history_summary = "；".join(history_summary_bits) or "（演示场景，未提供历史）"

    current_condition = "演示场景；与 audit_input.patient_state 保持一致"
    active_flags = [k for k, v in flags.items() if v is True]
    if active_flags:
        current_condition = "active_flags: " + ", ".join(active_flags)

    known_conditions = list(disease_codes) if disease_codes else []
    if not known_conditions and meds:
        # Use drug name as the "active condition" the patient is being
        # treated for. This is evidence, not invention.
        for m in meds:
            if not isinstance(m, dict):
                continue
            name = m.get("drug_name")
            status = m.get("status")
            if name and status == "active":
                known_conditions.append(name)
    known_conditions = sorted(set(c for c in known_conditions if c)) or \
        ["（演示）"]
    if not known_conditions:
        known_conditions = ["演示场景"]

    return {
        "age": age,
        "sex": sex,
        "visit_type": visit_type,
        "chief_complaint": _chief_complaint_from(reply_text, patient_state),
        "history_summary": history_summary,
        "current_condition": current_condition,
        "known_conditions": known_conditions,
        "case_notes": [
            "演示案例；不含真实患者信息。",
            f"内部标记 _generated_background=true （scenario_id={scenario_id}）。",
        ],
        "_generated_background": True,
    }


def _retrieved_evidence(report, scenario_id: str) -> list:
    """Build evidence cards directly from the rule sources that the
    real engine evaluates for this scenario.

    We do NOT invent rules. We re-use ``report.developer_diagnostics``
    + ``medical_violations`` + ``missing_context_fields`` to pick the
    rule ids, then look up each rule's own ``source`` document from
    the loaded repository.
    """
    # The repository isn't passed in; reload from the engine.
    from safety import DialogueSafetyEngine
    eng = DialogueSafetyEngine(ROOT / "rules")
    repo = eng.repository

    rule_ids: list[str] = []
    for v in report.medical_violations:
        rule_ids.append(v.rule_id)
    for mc in report.missing_context_fields:
        for rid in (mc.get("related_rule_ids") or []):
            if rid not in rule_ids:
                rule_ids.append(rid)

    seen_rule_ids: set[str] = set()
    out: list = []
    for rid in rule_ids:
        try:
            rule = repo.get(rid)
        except KeyError:
            continue
        if rid in seen_rule_ids:
            continue
        seen_rule_ids.add(rid)
        src = rule.source or {}
        out.append({
            "evidence_id": f"REF-{rid}",
            "source_title": src.get("document_title", "（未提供来源）"),
            "section": src.get("section", ""),
            "excerpt": (
                src.get("evidence_text")
                or rule.message
                or "（无摘要）"
            )[:240],
            "rule_id": rid,
            "retrieval_score": 0.9 if rid in (
                v.rule_id for v in report.medical_violations) else 0.6,
            "is_demo_evidence": True,
            "_generated_background": True,
        })

    # Always add at least one "engine fallback" card so the panel is
    # never empty.
    if not out:
        out.append({
            "evidence_id": "REF-NONE",
            "source_title": "未触发任何医学规则",
            "section": "",
            "excerpt": (
                "本次审计未命中命中规则与必要上下文；属于 PASS 或纯 "
                "兼容性输错路径。"
            ),
            "retrieval_score": 0.0,
            "is_demo_evidence": True,
            "_generated_background": True,
        })
    return out


def main():
    from audit_scenarios import load_all_scenarios
    from audit_scenarios.loader import invalidate_cache
    from safety import DialogueSafetyEngine

    invalidate_cache()
    scenarios = load_all_scenarios()
    eng = DialogueSafetyEngine(ROOT / "rules")

    filled_case_profile = 0
    filled_evidence = 0
    for s in scenarios:
        if not s.get("enabled_for_web"):
            continue
        aid = s["audit_input"]
        ps = aid.get("patient_state") or {}
        do = aid.get("dialogue_output") or {}
        reply_text = do.get("reply_text", "") if isinstance(do, dict) else ""
        scenario_id = s["id"]

        # Fill case_profile only if missing or generated.
        existing = s.get("case_profile") or {}
        already = (existing.get("_generated_background")
                   or s["id"].startswith("full_case_"))
        if not already:
            s["case_profile"] = _case_profile(ps, reply_text, scenario_id)
            filled_case_profile += 1

        # Run a real audit to ground the evidence.
        try:
            report = eng.audit_payload(
                payload=aid,
                strict_mode=True, compat_mode=False, debug=False,
            )
        except Exception:
            report = None

        existing_ev = s.get("retrieved_evidence") or []
        ev_is_generated = bool(existing_ev) and existing_ev[0].get(
            "_generated_background")
        if report is not None and not ev_is_generated:
            s["retrieved_evidence"] = _retrieved_evidence(report, scenario_id)
            filled_evidence += 1

    out_path = ROOT / "data" / "audit_scenarios.json"
    out_path.write_text(
        json.dumps(scenarios, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Filled {filled_case_profile} case_profiles, "
          f"{filled_evidence} retrieved_evidences.")
    print(f"Wrote {len(scenarios)} scenarios to {out_path}")


if __name__ == "__main__":
    main()
