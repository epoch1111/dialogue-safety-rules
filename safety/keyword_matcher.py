"""Multi-trie keyword matcher for v4.

Two independent tries are used:

- ``drug_trie``: drug aliases. Each keyword is registered with the canonical
  drug it maps to. Matches return the canonical drug.
- ``concept_trie``: anything else (food, exercise intensity, care types,
  food concepts). Each match returns ``(kind, keyword)``.

A single multi-trie walk per text field is enough; we do NOT rebuild
tries per request. The matcher is built once in
:class:`safety.safety_engine.DialogueSafetyEngine.__init__`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from safety.normalizer import normalize


# --------------------------------------------------------------------------
# Trie
# --------------------------------------------------------------------------


@dataclass
class _TrieNode:
    children: Dict[str, "_TrieNode"] = field(default_factory=dict)
    terminal_kind: Optional[str] = None
    terminal_payload: Optional[str] = None  # canonical drug or raw keyword
    terminal_rule_ids: Set[str] = field(default_factory=set)


class _Trie:
    def __init__(self) -> None:
        self._root = _TrieNode()
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def add(self, keyword: str, kind: str, payload: str, rule_id: Optional[str] = None) -> None:
        normalized = normalize(keyword)
        if not normalized:
            return
        node = self._root
        for ch in normalized:
            node = node.children.setdefault(ch, _TrieNode())
        node.terminal_kind = kind
        node.terminal_payload = payload
        if rule_id:
            node.terminal_rule_ids.add(rule_id)
        self._count += 1

    def scan(self, text: str) -> List[Tuple[str, str, str]]:
        """Return a list of ``(kind, payload, raw_match)`` triples."""
        if not text:
            return []
        normalized = normalize(text)
        if not normalized:
            return []

        hits: List[Tuple[str, str, str]] = []
        seen: Set[Tuple[str, str]] = set()
        for start in range(len(normalized)):
            node = self._root
            end = start
            for ch in normalized[start:]:
                child = node.children.get(ch)
                if child is None:
                    break
                end += 1
                if child.terminal_kind is not None and child.terminal_payload is not None:
                    key = (child.terminal_kind, child.terminal_payload)
                    if key not in seen:
                        seen.add(key)
                        hits.append(
                            (child.terminal_kind, child.terminal_payload, normalized[start:end])
                        )
                node = child
        return hits


# --------------------------------------------------------------------------
# Public matcher
# --------------------------------------------------------------------------


class KeywordMatcher:
    """Built once per engine; used many times."""

    def __init__(self) -> None:
        self._drug_trie = _Trie()
        self._concept_trie = _Trie()
        # Optional secondary map for rule-id lookups
        self._concept_index: Dict[Tuple[str, str], Set[str]] = {}

    # ------------------------------------------------------------------ build

    def add_drug_alias(self, alias: str, canonical: str) -> None:
        self._drug_trie.add(alias, "drug", canonical)

    def extend_drug_aliases(self, mapping: Dict[str, Iterable[str]]) -> None:
        for canonical, aliases in mapping.items():
            for alias in aliases:
                self.add_drug_alias(alias, canonical)

    def add_concept(
        self,
        keyword: str,
        kind: str,
        payload: Optional[str] = None,
        rule_id: Optional[str] = None,
    ) -> None:
        payload = payload or keyword
        self._concept_trie.add(keyword, kind, payload, rule_id=rule_id)
        if rule_id:
            self._concept_index.setdefault((kind, payload), set()).add(rule_id)

    def extend_concepts(
        self,
        items: Iterable[Tuple[str, str, str, str]],
    ) -> None:
        """Bulk-add ``(keyword, kind, payload, rule_id)`` tuples."""
        for keyword, kind, payload, rule_id in items:
            self.add_concept(keyword, kind, payload, rule_id=rule_id)

    @property
    def drug_count(self) -> int:
        return self._drug_trie.count

    @property
    def concept_count(self) -> int:
        return self._concept_trie.count

    # ------------------------------------------------------------------ scan

    def scan_drugs(self, text: str) -> Set[str]:
        return {payload for kind, payload, _ in self._drug_trie.scan(text) if kind == "drug"}

    def scan_concepts(self, text: str) -> List[Tuple[str, str, str]]:
        return self._concept_trie.scan(text)

    def scan_structured_drugs(
        self,
        items: Iterable[Dict[str, object]],
        fields: Iterable[str],
    ) -> Set[str]:
        hits: Set[str] = set()
        for item in items or []:
            for field_name in fields:
                if field_name in item:
                    hits |= self.scan_drugs(str(item[field_name]))
        return hits

    def scan_structured_concepts(
        self,
        items: Iterable[Dict[str, object]],
        fields: Iterable[str],
    ) -> List[Tuple[str, str, str]]:
        hits: List[Tuple[str, str, str]] = []
        for item in items or []:
            for field_name in fields:
                if field_name in item:
                    hits.extend(self.scan_concepts(str(item[field_name])))
        return hits

    def rule_ids_for(self, kind: str, payload: str) -> Set[str]:
        return set(self._concept_index.get((kind, payload), set()))