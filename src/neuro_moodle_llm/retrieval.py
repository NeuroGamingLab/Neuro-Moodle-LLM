"""BM25 sparse retriever and Reciprocal Rank Fusion.

Stays self-contained (no rank-bm25 dependency) so the FastAPI image is small
and offline-friendly. The index lives in process memory and is rebuilt lazily
from Qdrant payloads scoped to a single course (or the full collection).

`HybridResult` mirrors `qdrant_client.http.models.ScoredPoint` enough that
`rag.py` can treat both the same way.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]{2,}")


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


@dataclass
class _Doc:
    point_id: int | str
    payload: dict
    tokens: list[str]


@dataclass
class HybridHit:
    id: int | str
    score: float
    payload: dict
    components: dict[str, float] = field(default_factory=dict)


class BM25Index:
    """Tiny BM25 (Okapi) over a list of `_Doc`s. Built once per query batch."""

    def __init__(self, docs: Iterable[_Doc], *, k1: float = 1.5, b: float = 0.75) -> None:
        self._docs = list(docs)
        self._k1 = k1
        self._b = b
        self._df: Counter[str] = Counter()
        self._tf: list[Counter[str]] = []
        self._lens: list[int] = []
        for d in self._docs:
            tf = Counter(d.tokens)
            self._tf.append(tf)
            self._lens.append(len(d.tokens))
            for tok in tf:
                self._df[tok] += 1
        self._n = len(self._docs)
        self._avgdl = (sum(self._lens) / self._n) if self._n else 0.0

    def search(self, query: str, *, limit: int = 20) -> list[tuple[_Doc, float]]:
        if self._n == 0:
            return []
        q_tokens = tokenize(query)
        scores: list[float] = [0.0] * self._n
        for tok in q_tokens:
            df = self._df.get(tok, 0)
            if df == 0:
                continue
            idf = math.log(1 + (self._n - df + 0.5) / (df + 0.5))
            for i, tf in enumerate(self._tf):
                f = tf.get(tok, 0)
                if not f:
                    continue
                dl = self._lens[i] or 1
                denom = f + self._k1 * (1 - self._b + self._b * dl / (self._avgdl or 1))
                scores[i] += idf * (f * (self._k1 + 1)) / denom
        ranked = sorted(
            ((self._docs[i], s) for i, s in enumerate(scores) if s > 0),
            key=lambda x: x[1],
            reverse=True,
        )
        return ranked[:limit]


def reciprocal_rank_fusion(
    rankings: list[list[tuple[int | str, float, dict]]],
    *,
    k: int = 60,
    limit: int = 10,
) -> list[HybridHit]:
    """RRF: stable across heterogeneous score scales (cosine vs BM25)."""
    accum: dict[int | str, HybridHit] = {}
    for r_idx, ranking in enumerate(rankings):
        for rank, (pid, score, payload) in enumerate(ranking):
            contribution = 1.0 / (k + rank + 1)
            hit = accum.setdefault(
                pid,
                HybridHit(id=pid, score=0.0, payload=payload, components={}),
            )
            hit.score += contribution
            hit.components[f"src{r_idx}_rank"] = float(rank + 1)
            hit.components[f"src{r_idx}_raw"] = float(score)
    return sorted(accum.values(), key=lambda h: h.score, reverse=True)[:limit]


def docs_from_qdrant_payloads(
    iterator: Iterable[tuple[int | str, dict]],
    *,
    text_field: str = "text",
) -> list[_Doc]:
    out: list[_Doc] = []
    for pid, payload in iterator:
        out.append(_Doc(point_id=pid, payload=payload, tokens=tokenize(payload.get(text_field, ""))))
    return out


def build_bm25_for_course(
    store, *, course_id: Optional[int] = None, batch: int = 1024
) -> BM25Index:
    """Stream all points for a course (or all if None) and build a BM25 index."""
    docs: list[_Doc] = []
    if not store.collection_exists():
        return BM25Index(docs)
    for pid, payload in store.iter_points(course_id=course_id, batch=batch):
        docs.append(_Doc(point_id=pid, payload=payload, tokens=tokenize(payload.get("text", ""))))
    return BM25Index(docs)
