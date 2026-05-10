"""Pluggable reranker.

Default `LexicalReranker` is dependency-free: it scores (query, candidate)
by token-overlap weighted with IDF-ish weights derived from the candidate
set. It is *not* as good as a real cross-encoder, but it is portable and
gives a measurable lift over pure cosine in the eval harness.

Drop in a `CrossEncoderReranker` (e.g. via `sentence-transformers`) when
the GPU/CPU budget allows; the interface (`rerank(query, hits) -> hits`)
is intentionally tiny so swapping is a one-line change in `rag.py`.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Protocol

from .retrieval import HybridHit, tokenize


class Reranker(Protocol):
    name: str

    def rerank(self, query: str, hits: list[HybridHit], *, top_n: int) -> list[HybridHit]:
        ...


class LexicalReranker:
    name = "lexical-overlap-v1"

    def rerank(self, query: str, hits: list[HybridHit], *, top_n: int) -> list[HybridHit]:
        if not hits:
            return hits
        q_tokens = set(tokenize(query))
        if not q_tokens:
            return hits[:top_n]
        df: Counter[str] = Counter()
        per_doc_tokens: list[set[str]] = []
        for h in hits:
            tt = set(tokenize(h.payload.get("text", "")))
            per_doc_tokens.append(tt)
            for tok in tt:
                df[tok] += 1
        n = len(hits)
        rescored: list[HybridHit] = []
        for h, tt in zip(hits, per_doc_tokens):
            score = 0.0
            for tok in q_tokens & tt:
                idf = math.log(1 + n / (df[tok] or 1))
                score += idf
            blended = 0.5 * h.score + 0.5 * (score / (math.sqrt(len(tt) or 1)))
            new_components = dict(h.components)
            new_components["rerank"] = score
            new_components["reranker"] = self.name  # type: ignore[assignment]
            rescored.append(HybridHit(id=h.id, score=blended, payload=h.payload, components=new_components))
        return sorted(rescored, key=lambda x: x.score, reverse=True)[:top_n]


def get_reranker(name: str = "lexical") -> Reranker:
    if name == "lexical":
        return LexicalReranker()
    raise ValueError(f"unknown reranker: {name!r}")
