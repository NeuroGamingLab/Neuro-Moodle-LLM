"""Retrieval-augmented generation over a Moodle course.

Phase 1 upgrades:
- Hybrid dense + BM25 retrieval with Reciprocal Rank Fusion.
- Pluggable reranker (`reranker.LexicalReranker` by default).
- Semantic answer cache (`qa_cache.AnswerCache`).
- Confidence + per-source scores + heading paths in `sources`.

Phase 3 hooks:
- Optional per-learner memory boost via `memory.LearnerMemory`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .config import Settings
from .ollama import OllamaClient
from .qa_cache import AnswerCache
from .reranker import Reranker, get_reranker
from .retrieval import build_bm25_for_course, reciprocal_rank_fusion
from .vectorstore import VectorStore

SYSTEM_PROMPT = (
    "You are an assistant for a specific Moodle course. Answer using only the "
    "course context provided in the user message. If the context is insufficient, "
    "say so explicitly. Cite the source titles you used in parentheses. "
    "Prefer concise, factual answers grounded in the cited material."
)

REFUSAL_HINT = (
    "I don't have enough course material on this. The closest references are "
    "listed below — please consult them or refine your question."
)


@dataclass
class RagResult:
    answer: str
    sources: list[dict[str, Any]]
    cache: str = "miss"
    confidence: float = 0.0
    components: dict[str, Any] = field(default_factory=dict)


def ask(
    question: str,
    *,
    course_id: Optional[int],
    ollama: OllamaClient,
    store: VectorStore,
    top_k: int = 5,
    candidate_k: int = 20,
    use_hybrid: bool = True,
    use_rerank: bool = True,
    use_qa_cache: bool = True,
    confidence_floor: float = 0.10,
    reranker: Optional[Reranker] = None,
    settings: Optional[Settings] = None,
    learner_id: Optional[str] = None,
) -> RagResult:
    settings = settings or Settings()
    qvec = ollama.embed(question)

    cache = AnswerCache(settings)
    if use_qa_cache:
        hit = cache.lookup(qvec, course_id=course_id)
        if hit is not None:
            return RagResult(
                answer=hit.get("answer", ""),
                sources=hit.get("sources", []),
                cache="hit",
                confidence=float(hit.get("cache_score") or 0.0),
                components={"cache_score": float(hit.get("cache_score") or 0.0)},
            )

    dense = store.search_dense_payloads(qvec, limit=candidate_k, course_id=course_id)
    rankings = [dense]

    if use_hybrid:
        bm25 = build_bm25_for_course(store, course_id=course_id)
        sparse = [(d.point_id, score, d.payload) for d, score in bm25.search(question, limit=candidate_k)]
        if sparse:
            rankings.append(sparse)

    if learner_id:
        try:
            from .memory import LearnerMemory

            mem = LearnerMemory(settings)
            boosts = mem.boost_terms(learner_id)
            if boosts:
                boosted_q = f"{question} {' '.join(boosts)}"
                bvec = ollama.embed(boosted_q)
                rankings.append(store.search_dense_payloads(bvec, limit=candidate_k, course_id=course_id))
        except Exception:
            pass

    fused = reciprocal_rank_fusion(rankings, limit=candidate_k)

    rr = reranker or get_reranker("lexical") if use_rerank else None
    final_hits = rr.rerank(question, fused, top_n=top_k) if rr else fused[:top_k]

    confidence = final_hits[0].score if final_hits else 0.0
    sources: list[dict[str, Any]] = []
    context_blocks: list[str] = []
    for h in final_hits:
        title = h.payload.get("title", "(untitled)")
        text = h.payload.get("text", "")
        context_blocks.append(f"### {h.payload.get('heading_path') or title}\n{text}")
        sources.append(
            {
                "score": float(h.score),
                "components": h.components,
                "title": title,
                "heading_path": h.payload.get("heading_path"),
                "modtype": h.payload.get("modtype"),
                "module_name": h.payload.get("module_name"),
                "section_name": h.payload.get("section_name"),
                "url": h.payload.get("url"),
                "course_id": h.payload.get("course_id"),
                "ingest_run_id": h.payload.get("ingest_run_id"),
                "text_snippet": (text or "")[:800],
            }
        )

    if not final_hits or confidence < confidence_floor:
        return RagResult(
            answer=REFUSAL_HINT,
            sources=sources,
            cache="miss",
            confidence=confidence,
            components={"reason": "low_confidence", "floor": confidence_floor},
        )

    user_msg = (
        "Course context:\n\n" + "\n\n".join(context_blocks)
        + f"\n\nQuestion: {question}"
    )
    answer = ollama.chat(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        options={"temperature": 0.2},
    )
    answer = answer.strip()

    if use_qa_cache:
        cache.ensure(vector_size=len(qvec))
        cache.store(
            qvec,
            question=question,
            answer=answer,
            sources=sources,
            course_id=course_id,
            chat_model=ollama.chat_model,
            embed_model=ollama.embed_model,
        )

    return RagResult(
        answer=answer,
        sources=sources,
        cache="miss",
        confidence=confidence,
        components={"reranker": getattr(rr, "name", None), "candidate_k": candidate_k, "top_k": top_k},
    )
