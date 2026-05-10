"""Eval harness — golden set + retrieval/answer metrics + diff report.

Golden set lives at `data/eval/golden.jsonl` (gitignored once it has real
content; an empty seed lives in `data/eval/golden.example.jsonl`). Each line:

    {"course_id": 2, "question": "What is...", "expected_topics": ["foo","bar"], "must_cite": "Section: Week 1"}

Metrics:
- `topic_recall@k`     — fraction of `expected_topics` substrings present in the
                         concatenated retrieved chunk text (case-insensitive).
- `must_cite_hit@k`    — 1.0 if any source `title` startswith `must_cite`, else 0.
- `mrr@k`              — mean reciprocal rank of first source whose title contains
                         any `expected_topics` token.
- `latency_ms`         — wall time per question.

A run writes `data/eval/runs/<run_id>.json` and prints a delta vs the previous
run (champion comparison stored at `data/eval/champion.json`).
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .config import Settings, get_settings
from .ollama import OllamaClient
from .rag import ask
from .vectorstore import VectorStore

_REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = _REPO_ROOT / "data" / "eval"
RUNS_DIR = DATA_DIR / "runs"
CHAMPION = DATA_DIR / "champion.json"


@dataclass
class EvalCase:
    course_id: int
    question: str
    expected_topics: list[str] = field(default_factory=list)
    must_cite: Optional[str] = None


@dataclass
class CaseResult:
    question: str
    course_id: int
    answer: str
    sources: list[dict[str, Any]]
    metrics: dict[str, float]
    latency_ms: float
    cache: str


def load_golden(path: Optional[Path] = None) -> list[EvalCase]:
    p = path or (DATA_DIR / "golden.jsonl")
    if not p.exists():
        seed = DATA_DIR / "golden.example.jsonl"
        if seed.exists():
            p = seed
        else:
            return []
    out: list[EvalCase] = []
    with p.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            d = json.loads(line)
            out.append(EvalCase(**d))
    return out


def evaluate(
    *,
    cases: Optional[list[EvalCase]] = None,
    top_k: int = 5,
    candidate_k: int = 20,
    use_hybrid: bool = True,
    use_rerank: bool = True,
    use_qa_cache: bool = False,
    settings: Optional[Settings] = None,
    label: str = "",
    golden_path: Optional[Path] = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    if cases is not None:
        pass
    elif golden_path is not None:
        cases = load_golden(golden_path)
    else:
        cases = load_golden()
    if not cases:
        return {"label": label, "n": 0, "summary": {}, "results": [], "note": "no golden set"}

    results: list[CaseResult] = []
    with OllamaClient(settings) as ollama:
        store = VectorStore(settings)
        for case in cases:
            t0 = time.perf_counter()
            r = ask(
                case.question,
                course_id=case.course_id,
                ollama=ollama,
                store=store,
                top_k=top_k,
                candidate_k=candidate_k,
                use_hybrid=use_hybrid,
                use_rerank=use_rerank,
                use_qa_cache=use_qa_cache,
                settings=settings,
            )
            dt_ms = (time.perf_counter() - t0) * 1000.0
            metrics = _score_case(case, r.sources, top_k=top_k)
            metrics["confidence"] = r.confidence
            results.append(
                CaseResult(
                    question=case.question,
                    course_id=case.course_id,
                    answer=r.answer,
                    sources=r.sources,
                    metrics=metrics,
                    latency_ms=dt_ms,
                    cache=r.cache,
                )
            )

    summary = _aggregate(results)
    run_id = f"{int(time.time())}-{label or 'run'}"
    run = {
        "run_id": run_id,
        "label": label,
        "knobs": {
            "top_k": top_k,
            "candidate_k": candidate_k,
            "use_hybrid": use_hybrid,
            "use_rerank": use_rerank,
            "use_qa_cache": use_qa_cache,
            "embed_model": settings.ollama_embed_model,
            "chat_model": settings.ollama_chat_model,
        },
        "n": len(results),
        "summary": summary,
        "results": [asdict(r) for r in results],
    }
    _persist_run(run)
    run["delta_vs_champion"] = _delta_vs_champion(summary)
    return run


def promote_champion(run: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CHAMPION.write_text(json.dumps({"run_id": run["run_id"], "summary": run["summary"], "knobs": run["knobs"]}, indent=2))


def _score_case(case: EvalCase, sources: list[dict[str, Any]], *, top_k: int) -> dict[str, float]:
    """Score one case.

    ``topic_recall@k`` and ``mrr@k`` match expected-topic substrings against
    each source's ``title + heading_path + text_snippet`` (case-insensitive).
    Body text is part of the haystack so topics that only appear inside a
    chunk's content still score; the snippet is bounded server-side
    (``rag.ask`` truncates to 800 chars) so this stays cheap.
    """
    titles = [(s.get("title") or "") for s in sources[:top_k]]
    haystacks = [
        " ".join(
            [
                str(s.get("title") or ""),
                str(s.get("heading_path") or ""),
                str(s.get("text_snippet") or ""),
            ]
        ).lower()
        for s in sources[:top_k]
    ]
    blob = " ".join(haystacks)
    expected = [e.lower() for e in case.expected_topics]
    recall = (sum(1 for e in expected if e in blob) / len(expected)) if expected else 0.0
    must_hit = 0.0
    if case.must_cite:
        must_hit = 1.0 if any(t.startswith(case.must_cite) for t in titles) else 0.0
    mrr = 0.0
    for i, hay in enumerate(haystacks, start=1):
        if any(e in hay for e in expected):
            mrr = 1.0 / i
            break
    return {
        "topic_recall@k": recall,
        "must_cite_hit@k": must_hit,
        "mrr@k": mrr,
    }


def _aggregate(results: list[CaseResult]) -> dict[str, float]:
    if not results:
        return {}
    keys = list(results[0].metrics.keys())
    out = {k: round(statistics.fmean(r.metrics[k] for r in results), 4) for k in keys}
    out["latency_ms_p50"] = round(statistics.median(r.latency_ms for r in results), 1)
    out["latency_ms_avg"] = round(statistics.fmean(r.latency_ms for r in results), 1)
    return out


def _persist_run(run: dict[str, Any]) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / f"{run['run_id']}.json").write_text(json.dumps(run, indent=2, default=str))


def _delta_vs_champion(summary: dict[str, float]) -> dict[str, Any]:
    if not CHAMPION.exists():
        return {"champion": None, "delta": {}}
    champ = json.loads(CHAMPION.read_text())
    csum = champ.get("summary", {})
    delta = {k: round(summary.get(k, 0.0) - csum.get(k, 0.0), 4) for k in summary if k in csum}
    return {"champion_run_id": champ.get("run_id"), "delta": delta}
