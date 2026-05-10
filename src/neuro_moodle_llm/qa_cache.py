"""Semantic answer cache: a tiny Qdrant collection of (question_vec, answer).

On `ask()`:
  1. Embed the question.
  2. Look it up in `qa_cache`; if cosine >= `min_similarity` AND scope matches,
     return the cached answer with a `cache: hit` marker.
  3. Otherwise, run the full RAG pipeline and (best-effort) cache the result.

Scope is a payload filter — typically `course_id` so different courses don't
collide on identical questions.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from .config import Settings


class AnswerCache:
    def __init__(self, settings: Settings, *, collection: str = "qa_cache") -> None:
        self._client = QdrantClient(url=settings.qdrant_url)
        self._collection = collection

    def ensure(self, vector_size: int) -> None:
        existing = {c.name for c in self._client.get_collections().collections}
        if self._collection in existing:
            return
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config=qm.VectorParams(size=vector_size, distance=qm.Distance.COSINE),
        )

    def lookup(
        self,
        qvec: list[float],
        *,
        course_id: Optional[int],
        min_similarity: float = 0.92,
    ) -> Optional[dict[str, Any]]:
        if not self._exists():
            return None
        flt = (
            qm.Filter(must=[qm.FieldCondition(key="course_id", match=qm.MatchValue(value=course_id))])
            if course_id is not None
            else None
        )
        try:
            resp = self._client.query_points(
                collection_name=self._collection,
                query=qvec,
                query_filter=flt,
                limit=1,
                with_payload=True,
            )
            hits = list(resp.points)
        except Exception:
            return None
        if not hits:
            return None
        h = hits[0]
        if h.score < min_similarity:
            return None
        payload = dict(h.payload or {})
        payload["cache_score"] = float(h.score)
        return payload

    def store(
        self,
        qvec: list[float],
        *,
        question: str,
        answer: str,
        sources: list[dict[str, Any]],
        course_id: Optional[int],
        chat_model: str,
        embed_model: str,
    ) -> None:
        try:
            pid = int(hashlib.sha1(f"{course_id}|{question}".encode("utf-8")).hexdigest()[:15], 16)
            self._client.upsert(
                collection_name=self._collection,
                points=[
                    qm.PointStruct(
                        id=pid,
                        vector=qvec,
                        payload={
                            "question": question,
                            "answer": answer,
                            "sources": sources,
                            "course_id": course_id,
                            "chat_model": chat_model,
                            "embed_model": embed_model,
                        },
                    )
                ],
            )
        except Exception:
            pass

    def _exists(self) -> bool:
        try:
            return any(c.name == self._collection for c in self._client.get_collections().collections)
        except Exception:
            return False
