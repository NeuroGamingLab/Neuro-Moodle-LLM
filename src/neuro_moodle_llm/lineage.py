"""Ingest lineage helpers.

Every vector point gets a small lineage block in its payload so we can answer
"which build of the system produced this answer?":

- `ingest_run_id`   — opaque ULID-ish id, one per `ingest_course` invocation
- `source_hash`     — sha1 of the raw chunk text (lets us skip unchanged chunks)
- `embed_model`     — exact Ollama embed model used
- `chunker_version` — semantic-v1, etc.
- `ingested_at`     — UTC ISO timestamp

Pair with `embedding_cache.py` to short-circuit re-embedding when `source_hash`
already exists in `embed_cache`.
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone

from .chunker import chunker_signature


def new_ingest_run_id() -> str:
    """Lex-sortable id: `<ts_ms>-<rand_hex>`."""
    return f"{int(time.time() * 1000):013d}-{os.urandom(4).hex()}"


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def source_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def lineage_payload(
    *,
    ingest_run_id: str,
    chunk_text: str,
    embed_model: str,
) -> dict[str, str]:
    return {
        "ingest_run_id": ingest_run_id,
        "source_hash": source_hash(chunk_text),
        "embed_model": embed_model,
        "ingested_at": now_iso(),
        **chunker_signature(),
    }
