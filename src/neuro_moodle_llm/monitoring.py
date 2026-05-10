"""Phase 2: drift + LLM-as-judge monitoring.

- **Embedding drift**: compare the centroid of newly-ingested points (last N
  by `ingested_at`) against the centroid of the long-tail index. Cosine
  distance > `threshold` → alert.
- **Answer-quality probe**: re-runs the eval harness on a fixed probe set and
  scores answers with `llama3.2` as judge (1-5 helpfulness rubric). Drop
  beyond `quality_drop_threshold` vs the champion → alert.

Outputs land at `data/monitoring/<run_id>.json`. `cli.py monitor` schedules
trivially under cron; for in-API usage there's `POST /v1/monitor/run`.
"""

from __future__ import annotations

import json
import math
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .config import Settings, get_settings
from .eval import CHAMPION, evaluate
from .ollama import OllamaClient
from .vectorstore import VectorStore

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "monitoring"


@dataclass
class DriftReport:
    n_index: int
    n_new: int
    cosine_distance: float
    threshold: float
    drifted: bool


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def embedding_drift(
    settings: Optional[Settings] = None,
    *,
    new_run_id: Optional[str] = None,
    threshold: float = 0.05,
) -> DriftReport:
    settings = settings or get_settings()
    store = VectorStore(settings)
    new_vecs: list[list[float]] = []
    old_vecs: list[list[float]] = []
    if not store.collection_exists():
        return DriftReport(0, 0, 0.0, threshold, False)
    next_offset = None
    while True:
        from qdrant_client.http import models as qm  # noqa: F401

        points, next_offset = store.client.scroll(
            collection_name=store.collection,
            limit=512,
            offset=next_offset,
            with_payload=True,
            with_vectors=True,
        )
        for p in points:
            v = p.vector
            if not isinstance(v, (list, tuple)):
                continue
            tagged = (p.payload or {}).get("ingest_run_id")
            (new_vecs if (new_run_id and tagged == new_run_id) else old_vecs).append(list(v))
        if next_offset is None:
            break
    if not old_vecs and new_vecs:
        old_vecs = new_vecs
    if not new_vecs:
        return DriftReport(len(old_vecs), 0, 0.0, threshold, False)

    def centroid(vs: list[list[float]]) -> list[float]:
        if not vs:
            return []
        n = len(vs)
        return [sum(col) / n for col in zip(*vs)]

    cn = centroid(new_vecs)
    co = centroid(old_vecs) or cn
    dist = 1.0 - cosine(cn, co)
    return DriftReport(
        n_index=len(old_vecs),
        n_new=len(new_vecs),
        cosine_distance=round(dist, 4),
        threshold=threshold,
        drifted=dist > threshold,
    )


def llm_judge(
    *,
    settings: Optional[Settings] = None,
    quality_drop_threshold: float = 0.5,
) -> dict[str, Any]:
    settings = settings or get_settings()
    run = evaluate(label="probe", use_qa_cache=False, settings=settings)
    judged: list[float] = []
    if run["n"]:
        with OllamaClient(settings) as ollama:
            for case in run["results"]:
                prompt = (
                    "On a scale of 1 (useless) to 5 (excellent), rate the helpfulness "
                    "of the assistant answer for the question. Output ONLY the integer.\n\n"
                    f"Question: {case['question']}\n\nAnswer: {case['answer']}\n"
                )
                try:
                    out = ollama.chat(
                        messages=[{"role": "user", "content": prompt}],
                        options={"temperature": 0.0},
                    )
                    score = float(int("".join(c for c in out.strip() if c.isdigit())[:1] or "0"))
                except Exception:
                    score = 0.0
                judged.append(score)
    avg = round(statistics.fmean(judged), 3) if judged else 0.0
    champion_avg = None
    if CHAMPION.exists():
        try:
            champion_avg = json.loads(CHAMPION.read_text()).get("summary", {}).get("judge_avg")
        except Exception:
            pass
    drop = (champion_avg - avg) if (champion_avg is not None) else 0.0
    return {
        "judge_avg": avg,
        "champion_judge_avg": champion_avg,
        "drop": round(drop, 3),
        "alert": drop > quality_drop_threshold,
        "n": len(judged),
    }


def run_monitoring(
    *,
    new_run_id: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> dict[str, Any]:
    drift = embedding_drift(settings, new_run_id=new_run_id)
    judge = llm_judge(settings=settings)
    out = {
        "ts": int(time.time()),
        "drift": drift.__dict__,
        "judge": judge,
        "alert": bool(drift.drifted or judge.get("alert")),
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / f"{out['ts']}.json").write_text(json.dumps(out, indent=2))
    return out
