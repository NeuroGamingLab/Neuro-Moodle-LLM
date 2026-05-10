"""Qdrant collection wrapper.

Adds:
- `search_dense_payloads()` for hybrid retrieval (returns (id, score, payload)
  tuples so the fusion code stays decoupled from qdrant types).
- `iter_points()` to stream payloads for BM25 index construction.
- richer `stats()` (vector size, points).
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from .config import Settings


class VectorStore:
    def __init__(self, settings: Settings) -> None:
        self._collection = settings.qdrant_collection
        self._client = QdrantClient(url=settings.qdrant_url)

    @property
    def client(self) -> QdrantClient:
        return self._client

    @property
    def collection(self) -> str:
        return self._collection

    def ensure_collection(self, vector_size: int) -> None:
        existing = {c.name for c in self._client.get_collections().collections}
        if self._collection in existing:
            return
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config=qm.VectorParams(size=vector_size, distance=qm.Distance.COSINE),
        )

    def upsert(self, points: list[qm.PointStruct]) -> None:
        if not points:
            return
        self._client.upsert(collection_name=self._collection, points=points)

    def search(
        self,
        vector: list[float],
        *,
        limit: int = 5,
        course_id: Optional[int] = None,
    ) -> list[qm.ScoredPoint]:
        flt = self._course_filter(course_id)
        if not self.collection_exists():
            return []
        resp = self._client.query_points(
            collection_name=self._collection,
            query=vector,
            query_filter=flt,
            limit=limit,
            with_payload=True,
        )
        return list(resp.points)

    def search_dense_payloads(
        self,
        vector: list[float],
        *,
        limit: int = 20,
        course_id: Optional[int] = None,
    ) -> list[tuple[int | str, float, dict]]:
        hits = self.search(vector, limit=limit, course_id=course_id)
        return [(h.id, float(h.score), dict(h.payload or {})) for h in hits]

    def iter_points(
        self,
        *,
        course_id: Optional[int] = None,
        batch: int = 1024,
    ) -> Iterable[tuple[int | str, dict]]:
        if not self.collection_exists():
            return
        flt = self._course_filter(course_id)
        next_offset = None
        while True:
            points, next_offset = self._client.scroll(
                collection_name=self._collection,
                scroll_filter=flt,
                limit=batch,
                offset=next_offset,
                with_payload=True,
                with_vectors=False,
            )
            for p in points:
                yield p.id, dict(p.payload or {})
            if next_offset is None:
                break

    def delete_course(self, course_id: int) -> None:
        self._client.delete(
            collection_name=self._collection,
            points_selector=qm.FilterSelector(filter=self._course_filter(course_id)),
        )

    def delete_module(self, course_id: int, module_id: int) -> None:
        self._client.delete(
            collection_name=self._collection,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(
                    must=[
                        qm.FieldCondition(key="course_id", match=qm.MatchValue(value=course_id)),
                        qm.FieldCondition(key="module_id", match=qm.MatchValue(value=module_id)),
                    ]
                )
            ),
        )

    def count(self) -> int:
        info = self._client.count(collection_name=self._collection, exact=True)
        return info.count

    def collection_exists(self) -> bool:
        return any(
            c.name == self._collection for c in self._client.get_collections().collections
        )

    def stats(self) -> dict[str, Any]:
        return {
            "collection": self._collection,
            "exists": self.collection_exists(),
            "points": self.count() if self.collection_exists() else 0,
        }

    def _course_filter(self, course_id: Optional[int]) -> Optional[qm.Filter]:
        if course_id is None:
            return None
        return qm.Filter(
            must=[qm.FieldCondition(key="course_id", match=qm.MatchValue(value=course_id))]
        )
