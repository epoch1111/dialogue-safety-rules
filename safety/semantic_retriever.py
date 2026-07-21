"""Stub for vector / semantic retrieval. Disabled by default.

v4 only ships this as an interface so future work can plug in embeddings
without touching the engine. The engine never imports anything beyond
:mod:`safety.keyword_matcher` while this stub is disabled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Set


@dataclass
class SemanticHit:
    rule_id: str
    score: float


class SemanticRetriever:
    """Vector-style retriever stub. Disabled by default."""

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self._index: dict = {}

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        self.enabled = False

    def add_rule(self, rule_id: str, embedding: Iterable[float], payload: dict) -> None:
        # We do not actually store embeddings in the stub. Keeping the API
        # so the engine can call .add_rule unconditionally.
        self._index[rule_id] = payload

    def query(self, text: str, top_k: int = 5) -> List[SemanticHit]:
        if not self.enabled:
            return []
        # No embedding model is wired. Returning empty list keeps the
        # behaviour strictly identical to keyword-only recall.
        return []

    def rule_ids_for(self, hits: Iterable[SemanticHit]) -> Set[str]:
        return {h.rule_id for h in hits if h.score > 0.0}