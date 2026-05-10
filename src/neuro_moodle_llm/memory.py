"""Phase 3: per-learner memory.

A small Qdrant collection (`learner_memory`) stores embedded summaries of each
learner's prior questions and instructor corrections. `LearnerMemory.boost_terms`
returns a small set of keywords from the most relevant past memories that
`rag.ask` mixes into a secondary dense query (so retrieval gently prefers
material that addresses the learner's recurring gaps).

Stays local + opt-in (off unless `learner_id` is sent on the request).
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from .config import Settings
from .ollama import OllamaClient

log = logging.getLogger(__name__)


class LearnerMemory:
    def __init__(self, settings: Settings, *, collection: str = "learner_memory") -> None:
        self._settings = settings
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

    def remember(self, *, learner_id: str, kind: str, text: str) -> None:
        try:
            with OllamaClient(self._settings) as ollama:
                vec = ollama.embed(text)
            self.ensure(vector_size=len(vec))
            pid = int(hashlib.sha1(f"{learner_id}|{kind}|{time.time()}".encode()).hexdigest()[:15], 16)
            self._client.upsert(
                collection_name=self._collection,
                points=[
                    qm.PointStruct(
                        id=pid,
                        vector=vec,
                        payload={"learner_id": learner_id, "kind": kind, "text": text, "ts": int(time.time())},
                    )
                ],
            )
        except Exception as exc:
            log.warning("LearnerMemory.remember failed: %s", exc)

    def boost_terms(self, learner_id: str, *, top: int = 5) -> list[str]:
        if not self._exists():
            return []
        try:
            points, _ = self._client.scroll(
                collection_name=self._collection,
                scroll_filter=qm.Filter(
                    must=[qm.FieldCondition(key="learner_id", match=qm.MatchValue(value=learner_id))]
                ),
                limit=20,
                with_payload=True,
                with_vectors=False,
            )
        except Exception:
            return []
        seen: list[str] = []
        for p in points:
            for tok in (p.payload or {}).get("text", "").split():
                t = tok.strip(",.;:?!\"'()[]")
                if 4 <= len(t) <= 24 and t.lower() not in {x.lower() for x in seen}:
                    seen.append(t)
                if len(seen) >= top:
                    return seen
        return seen

    def _exists(self) -> bool:
        try:
            return any(c.name == self._collection for c in self._client.get_collections().collections)
        except Exception:
            return False
