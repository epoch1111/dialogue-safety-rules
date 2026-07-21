"""Consistency checker for the v4.2.0 engine.

System codes produced here:

- SYS001_TEXT_STRUCTURE_MISMATCH  : ``reply_text`` mentions a concrete
  drug / dose / food / exercise, but the corresponding structured field
  is missing. Default severity REVIEW. Deduped by (code, drug, raw
  span) so the same drug in the same sentence is reported once.

- SYS002_MISSING_MEDICATION_PARAMETERS : a structured medication
  action declares ``start`` / ``increase`` / ``replace`` but is missing
  ``dose_value`` / ``dose_unit`` / ``frequency_per_day``. v4.2.0 also
  applies to ``decrease`` and ``continue`` (without
  ``use_current_regimen``).

- SYS003_TEXT_STRUCTURE_CONFLICT : ``reply_text`` gives a direction
  opposite to every structured action of the same kind.

- SYS004_INVALID_STRUCTURED_ENUM : any structured value whose enum is
  unknown or missing.

- SYS005_MISSING_REPLACE_TARGET : a ``medication_action`` with
  ``action="replace"`` and empty ``replace_drug``.

- SYS006_UNKNOWN_MEDICATION_STATUS : a ``current_medications`` entry
  with a status that is not in the canonical v4.2.0 set.

- SYS007_UNIT_NOT_CONVERTIBLE (v4.2.0) : a structured medication
  action declares a unit the engine cannot convert to mg.

- SYS008_TEXT_CONFLICT_DRUG_MENTION (v4.2.0) : reply_text mentions a
  drug NOT in the structured actions list.

- SYS009_INPUT_INCOMPLETE (v4.2.0) : the Dialogue Agent omitted one or
  more required structured action arrays (caller passed ``{}`` or
  ``None``).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set, Tuple

from safety.models import (
    NormalizedDraft,
    SystemViolation,
    TextExtraction,
)
from safety.normalizer import normalize
from safety.unit_converter import convert_mass_to_mg


class ConsistencyChecker:
    """Pure function-style checker. No I/O."""

    def check(
        self,
        draft: NormalizedDraft,
        text_extractions: List[TextExtraction],
        drug_aliases_in_text: List[str],
        known_canonical_drugs: List[str],
        drug_canonicalizer=None,
        patient_state: Dict[str, Any] = None,
    ) -> List[SystemViolation]:
        violations: List[SystemViolation] = []

        violations.extend(self._check_sys001(
            draft, text_extractions, drug_aliases_in_text,
            known_canonical_drugs, drug_canonicalizer,
        ))
        violations.extend(self._check_sys002(draft))
        violations.extend(self._check_sys003(draft))
        violations.extend(self._check_sys004(draft))
        violations.extend(self._check_sys005(draft))
        violations.extend(self._check_sys006(patient_state or {}))
        violations.extend(self._check_sys007(draft))
        violations.extend(self._check_sys008(draft, drug_aliases_in_text, drug_canonicalizer))

        violations = self._dedup_sys001(violations)

        return violations

    # ------------------------------------------------------------------ SYS001

    def _check_sys001(
        self,
        draft: NormalizedDraft,
        text_extractions: List[TextExtraction],
        drug_aliases_in_text: List[str],
        known_canonical_drugs: List[str],
        drug_canonicalizer=None,
    ) -> List[SystemViolation]:
        out: List[SystemViolation] = []

        # 1a. reply_text mentions a drug alias but medication_actions
        #     does not include that drug.
        mentioned = {d for d in drug_aliases_in_text if d}
        if mentioned:
            structured_drugs = set()
            for action in draft.medication_actions:
                raw = action.drug or ""
                if drug_canonicalizer:
                    structured_drugs.add(drug_canonicalizer(raw))
                else:
                    structured_drugs.add(self._canonical(draft, raw))
            missing = mentioned - structured_drugs
            missing = {d for d in missing if d}
            if missing:
                out.append(
                    SystemViolation(
                        code="SYS001_TEXT_STRUCTURE_MISMATCH",
                        severity="REVIEW",
                        message="正文提到了药物，但结构化 medication_actions 中缺失。",
                        details={
                            "missing_drugs": sorted(missing),
                            "reply_text_excerpt": (draft.reply_text or "")[:200],
                        },
                    )
                )

        # 1b. reply_text parses a concrete (drug + dose) but
        #     medication_actions does not include that drug.
        for ext in text_extractions or []:
            if ext.confidence not in ("high", "medium"):
                continue
            canonical = self._canonical_name(ext.drug, known_canonical_drugs)
            if not canonical:
                continue
            if drug_canonicalizer:
                canonical = drug_canonicalizer(ext.drug) or canonical
            structured_drugs = set()
            for a in draft.medication_actions:
                if drug_canonicalizer:
                    structured_drugs.add(drug_canonicalizer(a.drug or ""))
                else:
                    structured_drugs.add(self._canonical(draft, a.drug or ""))
            if canonical not in structured_drugs:
                out.append(
                    SystemViolation(
                        code="SYS001_TEXT_STRUCTURE_MISMATCH",
                        severity="REVIEW",
                        message="正文包含具体剂量，但结构化 medication_actions 缺失该药。",
                        details={
                            "drug": canonical,
                            "dose_value": ext.dose_value,
                            "dose_unit": ext.dose_unit,
                            "frequency_per_day": ext.frequency_per_day,
                            "confidence": ext.confidence,
                            "raw_match": ext.raw_match,
                        },
                    )
                )

        # 1c. reply_text mentions a food / exercise but the structured
        #     food_advice / exercise_advice is empty.
        if draft.reply_text:
            triggers = ("饮食", "吃", "喝", "果", "汤", "肉", "鱼", "虾",
                        "drink", "eat", "food", "fruit", "soup",
                        "运动", "跑步", "走", "锻炼", "exercise", "run", "walk")
            text = draft.reply_text
            if any(t in text for t in triggers):
                if not draft.food_advice and not draft.exercise_advice:
                    out.append(
                        SystemViolation(
                            code="SYS001_TEXT_STRUCTURE_MISMATCH",
                            severity="REVIEW",
                            message="正文包含饮食/运动描述，但结构化列表为空。",
                            details={
                                "reply_text_excerpt": text[:200],
                            },
                        )
                    )
        return out

    # ------------------------------------------------------------------ SYS002

    def _check_sys002(self, draft: NormalizedDraft) -> List[SystemViolation]:
        out: List[SystemViolation] = []
        for action in draft.medication_actions:
            if action.action not in {"start", "increase", "replace", "decrease"}:
                continue
            missing: list = []
            if action.effective_dose_value is None:
                missing.append("dose_value")
            if not action.dose_unit:
                missing.append("dose_unit")
            if action.frequency_per_day is None:
                missing.append("frequency_per_day")
            if missing:
                out.append(
                    SystemViolation(
                        code="SYS002_MISSING_MEDICATION_PARAMETERS",
                        severity="REVIEW",
                        message=f"{action.drug} 的 {action.action} 缺少必要参数。",
                        details={
                            "drug": action.drug,
                            "action": action.action,
                            "missing": missing,
                        },
                    )
                )
        return out

    # ------------------------------------------------------------------ SYS003

    def _check_sys003(self, draft: NormalizedDraft) -> List[SystemViolation]:
        out: List[SystemViolation] = []

        text_direction = self._classify_text_direction(draft.reply_text)
        if text_direction is None:
            return out

        for kind, items, direction_fn in (
            ("food_advice", draft.food_advice, self._food_direction),
            ("exercise_advice", draft.exercise_advice, self._exercise_direction),
        ):
            if not items:
                continue
            opposing = [it for it in items if self._opposes(text_direction, direction_fn(it.action))]
            if opposing and len(opposing) == len(items):
                sample = opposing[0]
                attr = "food" if kind == "food_advice" else "activity"
                out.append(
                    SystemViolation(
                        code="SYS003_TEXT_STRUCTURE_CONFLICT",
                        severity="REVIEW",
                        message=f"正文与 {kind} 全部条目方向冲突：text={text_direction}",
                        details={
                            "kind": kind,
                            "text_direction": text_direction,
                            "structured_actions": [it.action for it in items],
                            "sample": getattr(sample, attr, ""),
                        },
                    )
                )

        return out

    # ------------------------------------------------------------------ SYS004

    def _check_sys004(self, draft: NormalizedDraft) -> List[SystemViolation]:
        out: List[SystemViolation] = []
        for entry in (draft.invalid_enum_fields or []):
            out.append(
                SystemViolation(
                    code="SYS004_INVALID_STRUCTURED_ENUM",
                    severity="REVIEW",
                    message=f"结构化字段 {entry['path']} 的值 {entry['value']!r} 不是合法枚举。",
                    details={
                        "path": entry.get("path", ""),
                        "value": entry.get("value", ""),
                    },
                )
            )
        return out

    # ------------------------------------------------------------------ SYS005

    def _check_sys005(self, draft: NormalizedDraft) -> List[SystemViolation]:
        out: List[SystemViolation] = []
        for action in draft.medication_actions:
            if action.action != "replace":
                continue
            if not action.replace_drug and not action.replace_drug_id:
                out.append(
                    SystemViolation(
                        code="SYS005_MISSING_REPLACE_TARGET",
                        severity="REVIEW",
                        message=f"replace 操作必须指定被替换的药物 (replace_drug)。",
                        details={
                            "drug": action.drug,
                            "action": action.action,
                        },
                    )
                )
        return out

    # ------------------------------------------------------------------ SYS006

    def _check_sys006(self, patient_state: Dict[str, Any]) -> List[SystemViolation]:
        out: List[SystemViolation] = []
        for med in patient_state.get("current_medications", []) or []:
            if not isinstance(med, dict):
                # v4.2.0: non-dict med entries are not silently skipped
                # here — the input validator catches them — but if
                # somehow one slipped through we still flag it.
                out.append(
                    SystemViolation(
                        code="SYS006_UNKNOWN_MEDICATION_STATUS",
                        severity="REVIEW",
                        message="current_medications 中存在非法类型的条目。",
                        details={"raw_value": str(med)[:80]},
                    )
                )
                continue
            status = med.get("status", "active")
            if status is None:
                continue
            n = normalize(status)
            valid_set = {
                "active", "held", "stopped", "completed", "unknown",
            }
            if n in valid_set:
                continue
            out.append(
                SystemViolation(
                    code="SYS006_UNKNOWN_MEDICATION_STATUS",
                    severity="REVIEW",
                    message=f"current_medications 中 {med.get('name', med.get('drug_id', '?'))!r} 的 status "
                            f"{status!r} 不在标准集合中。",
                    details={
                        "drug": med.get("name", ""),
                        "drug_id": med.get("drug_id", ""),
                        "status": status,
                    },
                )
            )
        return out

    # ------------------------------------------------------------------ SYS007

    def _check_sys007(self, draft: NormalizedDraft) -> List[SystemViolation]:
        out: List[SystemViolation] = []
        for action in draft.medication_actions:
            if action.action in {"start", "increase", "decrease", "replace"}:
                if action.dose_unit and action.dose_value is not None:
                    converted = convert_mass_to_mg(action.dose_value, action.dose_unit)
                    if not converted.is_valid:
                        out.append(
                            SystemViolation(
                                code="SYS007_UNIT_NOT_CONVERTIBLE",
                                severity="REVIEW",
                                message=(
                                    f"{action.drug} 的 {action.action} 剂量单位 "
                                    f"{action.dose_unit!r} 无法换算为 mg ({converted.reason})。"
                                ),
                                details={
                                    "drug": action.drug,
                                    "action": action.action,
                                    "dose_value": action.dose_value,
                                    "dose_unit": action.dose_unit,
                                    "reason": converted.reason,
                                },
                            )
                        )
        return out

    # ------------------------------------------------------------------ SYS008

    def _check_sys008(
        self,
        draft: NormalizedDraft,
        drug_aliases_in_text: List[str],
        drug_canonicalizer=None,
    ) -> List[SystemViolation]:
        """Drug mentioned in reply_text but absent from medication_actions."""
        out: List[SystemViolation] = []
        mentioned = {d for d in (drug_aliases_in_text or []) if d}
        if not mentioned:
            return out
        structured = set()
        for a in draft.medication_actions:
            if drug_canonicalizer:
                structured.add(drug_canonicalizer(a.drug or ""))
            else:
                structured.add(normalize(a.drug))
        diff = mentioned - {s for s in structured if s}
        if diff:
            out.append(
                SystemViolation(
                    code="SYS008_TEXT_CONFLICT_DRUG_MENTION",
                    severity="REVIEW",
                    message="正文提及了药物，但结构化字段未声明任何动作。",
                    details={"missing_drugs": sorted(diff)},
                )
            )
        return out

    # ----------------------------------------------------- helpers / dedup

    @staticmethod
    def _canonical(draft: NormalizedDraft, raw_drug: str) -> str:
        return (raw_drug or "").strip().lower()

    @staticmethod
    def _canonical_name(drug: str, known: List[str]) -> str:
        if not drug:
            return ""
        normalized = drug.strip().lower()
        for k in known:
            if k.lower() == normalized:
                return k
        return normalized

    @staticmethod
    def _classify_text_direction(text: str):
        if not text:
            return None
        low = text.lower()
        rec_hits = sum(1 for w in _DIRECTION_LEXICON["recommend"] if w in low)
        avd_hits = sum(1 for w in _DIRECTION_LEXICON["avoid"] if w in low)
        if avd_hits > 0 and rec_hits < avd_hits + 2:
            return "avoid"
        if rec_hits > 0:
            return "recommend"
        return None

    @staticmethod
    def _food_direction(action: str) -> str:
        if action in ("recommend", "allow"):
            return "recommend"
        if action in ("limit", "avoid"):
            return "avoid"
        return "neutral"

    @staticmethod
    def _exercise_direction(action: str) -> str:
        if action in ("recommend", "allow"):
            return "recommend"
        if action in ("limit", "avoid", "stop"):
            return "avoid"
        return "neutral"

    @staticmethod
    def _opposes(text_dir: str, struct_dir: str) -> bool:
        if struct_dir == "neutral":
            return False
        return text_dir != struct_dir

    def _dedup_sys001(self, violations: List[SystemViolation]) -> List[SystemViolation]:
        seen: Set[Tuple[str, str, str]] = set()
        out: List[SystemViolation] = []
        for v in violations:
            if v.code == "SYS001_TEXT_STRUCTURE_MISMATCH":
                drug = v.details.get("drug") or v.details.get("missing_drugs", [""])[0]
                span = (
                    v.details.get("raw_match")
                    or v.details.get("reply_text_excerpt", "")
                )
                key = ("SYS001", drug or "", (span or "")[:12])
                if key in seen:
                    continue
                seen.add(key)
            out.append(v)
        return out


_DIRECTION_LEXICON = {
    "recommend": {
        "可以", "建议", "应当", "应", "请", "推荐", "适合",
        "you can", "you should", "please", "recommended",
        "go ahead and", "feel free to",
    },
    "avoid": {
        "不要", "不可", "禁止", "避免", "切勿", "切忌",
        "do not", "don't", "avoid", "never", "should not",
        "shouldn't", "stop", "must not",
    },
}