"""Finite, deterministic text-dose parser.

This is NOT a free-form medical reasoning module. It uses a small set of
regular expressions to extract a (drug, dose, frequency) triple from a
short reply text. The output always carries a ``confidence`` field:

- ``high``   : drug + numeric dose + unit + frequency all matched
- ``medium`` : drug + numeric dose, but unit or frequency missing
- ``low``    : numeric dose only, no drug recognised
- ``none``   : no usable extraction

The engine never uses a ``low``/``none`` parse to BLOCK. ``medium`` is
only allowed to upgrade SYS001 to BLOCK when paired with a known rule
match.

v4.1: ``dedup_overlaps`` collapses overlapping extractions for the same
drug (e.g. when pattern A and pattern C both match "氨氯地平 20") so
the audit report does not list the same dose twice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from safety.models import TextExtraction
from safety.normalizer import normalize


# Drug names we are willing to recognise in free text. Aliases are handled
# upstream by the matcher; here we only need a small, well-known list for
# capturing drug mentions adjacent to a dose.
_DRUG_PATTERNS = [
    "amlodipine", "metformin", "simvastatin", "atorvastatin",
    "clarithromycin", "lisinopril", "ramipril", "irbesartan",
    "insulin", "colchicine", "allopurinol", "febuxostat",
    "benzbromarone", "celecoxib", "etoricoxib", "ibuprofen",
    "hydrochlorothiazide", "furosemide", "spironolactone",
    "aspirin", "glimepiride", "gliclazide", "sitagliptin",
    "empagliflozin", "dapagliflozin",
    # Chinese
    "氨氯地平", "二甲双胍", "辛伐他汀", "阿托伐他汀",
    "克拉霉素", "赖诺普利", "雷米普利", "厄贝沙坦",
    "胰岛素", "秋水仙碱", "别嘌醇", "非布司他",
    "苯溴马隆", "塞来昔布", "依托考昔", "布洛芬",
    "氢氯噻嗪", "呋塞米", "螺内酯",
    "阿司匹林", "格列美脲", "格列齐特", "西格列汀",
    "恩格列净", "达格列净",
]

# Sorted by length desc so longer names win in alternation.
_DRUG_PATTERN = "|".join(sorted(_DRUG_PATTERNS, key=lambda s: -len(s)))


# Numeric dose: e.g. 20, 1.5, 0.5
_NUM_RE = r"(\d+(?:\.\d+)?)"

# Unit: mg, g, ml, 毫克, 克
_UNIT_RE = r"(mg|毫克|g|克|ml|毫升)"

# Frequency keywords (Chinese + English)
_FREQ_MAP = {
    "qd": 1, "qd_zh": 1, "od": 1, "once daily": 1,
    "bid": 2, "twice daily": 2, "每日两次": 2, "每日2次": 2, "一天两次": 2, "bid_zh": 2,
    "tid": 3, "three times daily": 3, "每日三次": 3, "每日3次": 3, "一天三次": 3, "tid_zh": 3,
    "qid": 4, "four times daily": 4, "每日四次": 4, "每日4次": 4, "一天四次": 4, "qid_zh": 4,
    "每日一次": 1, "每天一次": 1, "每日1次": 1, "每天1次": 1,
    "每天": 1, "每日": 1,
}


def _build_freq_regex() -> re.Pattern:
    """Combine all frequency keywords (longest first) into a single regex."""
    keys = sorted(_FREQ_MAP.keys(), key=lambda s: -len(s))
    pattern = "|".join(re.escape(k) for k in keys)
    return re.compile(pattern)


_FREQ_RE = _build_freq_regex()

# Patterns A/B/C — see parse() comments below.
_PATTERN_A = re.compile(
    rf"(?P<drug>{_DRUG_PATTERN})[一-鿿\s,，]*{_NUM_RE}\s*{_UNIT_RE}?",
    re.IGNORECASE,
)
_PATTERN_B = re.compile(
    rf"{_NUM_RE}\s*{_UNIT_RE}\s*(?P<drug>{_DRUG_PATTERN})",
    re.IGNORECASE,
)
_PATTERN_C = re.compile(
    rf"(?P<drug>{_DRUG_PATTERN})[一-鿿\s,，]*{_NUM_RE}",
    re.IGNORECASE,
)


class TextDoseParser:
    """Deterministic parser. No LLM, no embeddings."""

    def parse(self, text: str) -> List[TextExtraction]:
        if not text:
            return []
        normalized = normalize(text)
        results: List[TextExtraction] = []

        # Pattern A: drug + dose + optional unit (e.g. "amlodipine 20 mg")
        for m in _PATTERN_A.finditer(normalized):
            dose_value = float(m.group(2))
            unit_raw = m.group(3)
            unit = self._normalize_unit(unit_raw)
            drug = m.group(1)
            window_end = m.end()
            freq = self._extract_freq(normalized, window_end)
            raw = normalized[m.start():window_end + (freq[1] - window_end if freq else 0)]
            results.append(
                TextExtraction(
                    drug=drug,
                    dose_value=dose_value,
                    dose_unit=unit,
                    frequency_per_day=freq[0] if freq else None,
                    confidence="high" if (unit and freq) else "medium",
                    raw_match=m.group(0) if not freq else f"{m.group(0)} {freq[2]}",
                )
            )

        # Pattern B: dose + unit + drug (e.g. "20 mg amlodipine")
        for m in _PATTERN_B.finditer(normalized):
            dose_value = float(m.group(1))
            unit_raw = m.group(2)
            unit = self._normalize_unit(unit_raw)
            drug = m.group("drug")
            window_end = m.end()
            freq = self._extract_freq(normalized, window_end)
            results.append(
                TextExtraction(
                    drug=drug,
                    dose_value=dose_value,
                    dose_unit=unit,
                    frequency_per_day=freq[0] if freq else None,
                    confidence="high" if (unit and freq) else "medium",
                    raw_match=m.group(0) if not freq else f"{m.group(0)} {freq[2]}",
                )
            )

        # Pattern C: drug + dose (no unit) -- e.g. "amlodipine 20"
        for m in _PATTERN_C.finditer(normalized):
            dose_value = float(m.group(2))
            drug = m.group("drug")
            window_end = m.end()
            freq = self._extract_freq(normalized, window_end)
            results.append(
                TextExtraction(
                    drug=drug,
                    dose_value=dose_value,
                    dose_unit=None,
                    frequency_per_day=freq[0] if freq else None,
                    confidence="medium" if freq else "low",
                    raw_match=m.group(0) if not freq else f"{m.group(0)} {freq[2]}",
                )
            )

        return self.dedup_overlaps(results)

    @staticmethod
    def dedup_overlaps(extractions: List[TextExtraction]) -> List[TextExtraction]:
        """v4.1: per-drug, per-span dedup keeping highest confidence.

        For the same drug, if two extractions' raw_match strings overlap
        (one is a substring of the other), keep the one with the highest
        confidence (and break ties by the longer raw_match).
        """
        if not extractions:
            return []
        confidence_rank = {"high": 3, "medium": 2, "low": 1, "none": 0}
        groups: Dict[Optional[str], List[TextExtraction]] = {}
        for ext in extractions:
            groups.setdefault(ext.drug, []).append(ext)
        out: List[TextExtraction] = []
        for drug, items in groups.items():
            kept: List[TextExtraction] = []
            for ext in items:
                replaced = False
                for i, existing in enumerate(kept):
                    if _spans_overlap(ext.raw_match, existing.raw_match):
                        ext_score = (
                            confidence_rank.get(ext.confidence, 0),
                            len(ext.raw_match or ""),
                        )
                        existing_score = (
                            confidence_rank.get(existing.confidence, 0),
                            len(existing.raw_match or ""),
                        )
                        if ext_score > existing_score:
                            kept[i] = ext
                        replaced = True
                        break
                if not replaced:
                    kept.append(ext)
            out.extend(kept)
        return out

    @staticmethod
    def _normalize_unit(unit: Optional[str]) -> Optional[str]:
        if not unit:
            return None
        u = unit.lower()
        mapping = {"毫克": "mg", "克": "g", "毫升": "ml"}
        return mapping.get(u, u)

    @staticmethod
    def _extract_freq(text: str, start: int) -> Optional[Tuple[float, int, str]]:
        """Look for a frequency keyword starting at position ``start``.

        Returns (freq_per_day, end_offset, raw_match) or None.
        """
        window = text[start:start + 20].lstrip()
        if not window:
            return None
        consumed_ws = len(text[start:start + 20]) - len(window)
        adjusted_start = start + consumed_ws
        m = _FREQ_RE.match(window)
        if not m:
            return None
        key = m.group(0)
        freq = _FREQ_MAP.get(key)
        if freq is None:
            return None
        return (freq, adjusted_start + len(key), key)


def _spans_overlap(a: str, b: str) -> bool:
    """Heuristic: do ``a`` and ``b`` describe overlapping drug+dose spans?
    Substring / shared 4-char-prefix detection."""
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return _shared_substring_at_least(a, b, length=4)


def _shared_substring_at_least(a: str, b: str, length: int) -> bool:
    if len(a) < length or len(b) < length:
        return False
    seen = {a[i:i + length] for i in range(len(a) - length + 1)}
    for j in range(len(b) - length + 1):
        if b[j:j + length] in seen:
            return True
    return False