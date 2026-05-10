"""Embedding cache backed by a tiny Qdrant collection.

Goal: avoid re-embedding chunks whose text + embed_model are unchanged.
Keyed by `source_hash + embed_model`. Stores the embedding so a re-ingest
only pays Ollama for chunks that actually changed.

Implementation note: Qdrant requires every collection to have a vector. We
piggyback on that by storing the cached embedding *as* the point vector.
The lookup is a single `retrieve()` by deterministic point id, so we never
do a similarity search against this collection.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from .config import Settings


class EmbeddingCache:
    def __init__(self, settings: Settings, *, collection: str = "embed_cache") -> None:
        self._client = QdrantClient(url=settings.qdrant_url)
        self._collection = collection

    def _key(self, source_hash: str, embed_model: str) -> int:
        raw = f"{embed_model}|{source_hash}".encode("utf-8")
        return int(hashlib.sha1(raw).hexdigest()[:15], 16)

    def ensure(self, vector_size: int) -> None:
        existing = {c.name for c in self._client.get_collections().collections}
        if self._collection in existing:
            return
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config=qm.VectorParams(size=vector_size, distance=qm.Distance.COSINE),
        )

    def get(self, source_hash: str, embed_model: str) -> Optional[list[float]]:
        try:
            pid = self._key(source_hash, embed_model)
            pts = self._client.retrieve(
                collection_name=self._collection,
                ids=[pid],
                with_vectors=True,
                with_payload=False,
            )
        except Exception:
            return None
        if not pts:
            return None
        vec = pts[0].vector
        return list(vec) if isinstance(vec, (list, tuple)) else None

    def put(self, source_hash: str, embed_model: str, vector: list[float]) -> None:
        pid = self._key(source_hash, embed_model)
        self._client.upsert(
            collection_name=self._collection,
            points=[
                qm.PointStruct(
                    id=pid,
                    vector=list(vector),
                    payload={"source_hash": source_hash, "embed_model": embed_model},
                )
            ],
        )

    def stats(self) -> dict:
        try:
            return {
                "collection": self._collection,
                "points": self._client.count(self._collection, exact=True).count,
            }
        except Exception:
            return {"collection": self._collection, "points": 0}
