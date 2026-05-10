"""Phase 3: synthetic question generation + course-coverage audit.

Walks every chunk in the index for a course, asks the chat model to invent
*plausible student questions* for that chunk, then runs the standard
retrieval pipeline against each synthetic question and checks whether the
*originating chunk* lands in top-k. If it doesn't, the chunk's coverage is
flagged as weak.

Output: `data/audit/<course>-<ts>.json` with per-chunk coverage + a summary
the instructor can use to spot under-indexed material ("teaching quality"
signal, not just retrieval ops).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from .config import Settings, get_settings
from .ollama import OllamaClient
from .rag import ask
from .vectorstore import VectorStore

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "audit"

GEN_PROMPT = (
    "You are designing study questions. Read the course chunk below and write "
    "three short, distinct questions a student could plausibly ask whose answer "
    "is contained in the chunk. Output ONLY the questions, one per line."
)


def synthesize_questions(text: str, *, ollama: OllamaClient) -> list[str]:
    raw = ollama.chat(
        messages=[
            {"role": "system", "content": GEN_PROMPT},
            {"role": "user", "content": text},
        ],
        options={"temperature": 0.4},
    )
    return [q.strip("- 0123456789.) ") for q in (raw or "").splitlines() if q.strip()][:3]


def audit_course(
    course_id: int,
    *,
    settings: Optional[Settings] = None,
    max_chunks: int = 50,
    top_k: int = 5,
) -> dict[str, Any]:
    settings = settings or get_settings()
    store = VectorStore(settings)
    chunks: list[tuple[int | str, dict]] = []
    for pid, payload in store.iter_points(course_id=course_id):
        chunks.append((pid, payload))
        if len(chunks) >= max_chunks:
            break
    results: list[dict[str, Any]] = []
    weak = 0
    with OllamaClient(settings) as ollama:
        for pid, payload in chunks:
            text = payload.get("text", "")
            if not text:
                continue
            qs = synthesize_questions(text, ollama=ollama)
            per_q = []
            for q in qs:
                r = ask(
                    q,
                    course_id=course_id,
                    ollama=ollama,
                    store=store,
                    top_k=top_k,
                    use_qa_cache=False,
                    settings=settings,
                )
                hit_ids = [s.get("title") for s in r.sources]
                per_q.append({"question": q, "found": payload.get("title") in hit_ids, "top": hit_ids})
            covered = sum(1 for x in per_q if x["found"]) / max(1, len(per_q))
            if covered < 0.5:
                weak += 1
            results.append({"point_id": pid, "title": payload.get("title"), "coverage": covered, "per_question": per_q})
    summary = {
        "course_id": course_id,
        "n_chunks_audited": len(results),
        "weak_chunks": weak,
        "avg_coverage": round(sum(r["coverage"] for r in results) / max(1, len(results)), 3),
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = {"summary": summary, "details": results, "ts": int(time.time())}
    (DATA_DIR / f"course-{course_id}-{out['ts']}.json").write_text(json.dumps(out, indent=2, default=str))
    return out
