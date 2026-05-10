"""Phase 2: HPO over RAG knobs.

Default backend is a deterministic **grid search** so we ship with zero new
dependencies. If `optuna` is installed at runtime, an `optuna_search()`
function is available and uses TPE.

Both backends call `eval.evaluate()` with each candidate and rank by a
composite score (defaults to `topic_recall@k - 0.001 * latency_ms_avg`).
The best knobs are written to `data/eval/best.json`.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any, Callable, Optional

from .eval import DATA_DIR, evaluate

BEST_FILE = DATA_DIR / "best.json"


def default_score(summary: dict[str, float]) -> float:
    return float(summary.get("topic_recall@k", 0.0)) - 0.001 * float(summary.get("latency_ms_avg", 0.0))


def grid_search(
    *,
    grid: Optional[dict[str, list[Any]]] = None,
    score_fn: Callable[[dict[str, float]], float] = default_score,
) -> dict[str, Any]:
    grid = grid or {
        "top_k": [3, 5, 8],
        "candidate_k": [10, 20, 30],
        "use_hybrid": [True, False],
        "use_rerank": [True, False],
    }
    keys = list(grid.keys())
    best: tuple[float, dict[str, Any], dict[str, Any]] | None = None
    runs: list[dict[str, Any]] = []
    for combo in itertools.product(*[grid[k] for k in keys]):
        knobs = dict(zip(keys, combo))
        run = evaluate(label=f"hpo:{json.dumps(knobs, sort_keys=True)}", **knobs)
        score = score_fn(run.get("summary", {}))
        runs.append({"knobs": knobs, "summary": run.get("summary", {}), "score": score})
        if best is None or score > best[0]:
            best = (score, knobs, run)
    if best is None:
        return {"backend": "grid", "n": 0, "runs": [], "best": None}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BEST_FILE.write_text(json.dumps({"backend": "grid", "score": best[0], "knobs": best[1], "summary": best[2]["summary"]}, indent=2))
    return {"backend": "grid", "n": len(runs), "runs": runs, "best": {"score": best[0], "knobs": best[1]}}


def optuna_search(
    *,
    n_trials: int = 30,
    score_fn: Callable[[dict[str, float]], float] = default_score,
) -> dict[str, Any]:
    try:
        import optuna  # type: ignore
    except ImportError as exc:
        raise RuntimeError("optuna not installed; pip install optuna or use grid_search()") from exc

    def _obj(trial: "optuna.Trial") -> float:  # type: ignore[name-defined]
        knobs = {
            "top_k": trial.suggest_int("top_k", 3, 10),
            "candidate_k": trial.suggest_int("candidate_k", 10, 40),
            "use_hybrid": trial.suggest_categorical("use_hybrid", [True, False]),
            "use_rerank": trial.suggest_categorical("use_rerank", [True, False]),
        }
        run = evaluate(label=f"optuna:{trial.number}", **knobs)
        return score_fn(run.get("summary", {}))

    study = optuna.create_study(direction="maximize")
    study.optimize(_obj, n_trials=n_trials)
    BEST_FILE.write_text(json.dumps({"backend": "optuna", "score": study.best_value, "knobs": study.best_params}, indent=2))
    return {"backend": "optuna", "best": {"score": study.best_value, "knobs": study.best_params}}
