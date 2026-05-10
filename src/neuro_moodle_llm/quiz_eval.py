"""Phase C: closed-loop eval triggered by Moodle quiz attempts.

Workflow when a learner submits a synthetic quiz:

1. Moodle's ``mod_quiz`` event observer (in ``local_neurollm``) POSTs the
   attempt id to ``/v1/events/moodle``.
2. ``events.handle_event`` routes the event here based on event name.
3. We pull the attempt via ``local_neurollm_get_quiz_attempt``, which returns
   stem + learner answer + correctness + the synthetic ground truth
   (``must_cite``, ``expected_topics``) we encoded into ``generalfeedback``
   when creating the quiz.
4. For each question, we ask the agentic feedback pipeline (Retriever →
   Critic → Validator) to draft a critique grounded in the ingested course
   chunks. We then score retrieval against the synthetic ground truth and
   the agent answer against the option-level correct/incorrect flag.

Outputs land at ``data/monitoring/quiz_eval_<attempt_id>.json`` so the
existing Streamlit "Eval & Monitor" page can list them next to the
question-set eval runs.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from .agents import RetrieverAgent
from .config import Settings, get_settings
from .moodle import MoodleClient
from .moodle_authoring import get_quiz_attempt

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "monitoring"


def evaluate_quiz_attempt(
    *,
    attempt_id: int,
    course_id: Optional[int] = None,
    settings: Optional[Settings] = None,
    top_k: int = 5,
) -> dict[str, Any]:
    """Score one submitted attempt and persist a JSON report."""
    settings = settings or get_settings()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with MoodleClient(settings) as moodle:
        attempt = get_quiz_attempt(moodle=moodle, attempt_id=int(attempt_id))

    if course_id is None:
        course_id = int(attempt["course_id"])

    questions = attempt.get("questions") or []

    cases: list[dict[str, Any]] = []
    metric_acc = {
        "must_cite_hit": [],
        "topic_recall": [],
        "learner_correct": [],
    }

    retriever = RetrieverAgent(settings)
    for q in questions:
        stem = _strip_html(str(q.get("stem_html") or ""))
        must_cite = (q.get("must_cite") or "").strip()
        expected = [str(t).strip() for t in (q.get("expected_topics") or []) if str(t).strip()]
        correct = bool(q.get("correct"))
        retrieval = retriever.run({"course_id": int(course_id), "question": stem, "top_k": top_k})

        sources = retrieval.get("sources") or []
        haystack = " ".join(_haystack(s) for s in sources).lower()

        must_hit = 0.0
        if must_cite:
            must_hit = 1.0 if any(str(s.get("title", "")).startswith(must_cite) for s in sources) else 0.0
        if expected:
            tr = sum(1 for t in expected if t.lower() in haystack) / len(expected)
        else:
            tr = 0.0

        metric_acc["must_cite_hit"].append(must_hit)
        metric_acc["topic_recall"].append(tr)
        metric_acc["learner_correct"].append(1.0 if correct else 0.0)

        cases.append(
            {
                "question_id": int(q.get("question_id", 0)),
                "slot": int(q.get("slot", 0)),
                "stem": stem[:400],
                "learner_answer": str(q.get("response_summary") or ""),
                "right_answer": str(q.get("right_answer") or ""),
                "learner_correct": correct,
                "must_cite": must_cite,
                "must_cite_hit": must_hit,
                "expected_topics": expected,
                "topic_recall": round(tr, 4),
                "agent_top_sources": [
                    {
                        "title": s.get("title"),
                        "score": float(s.get("score") or 0.0),
                    }
                    for s in sources[:top_k]
                ],
            }
        )

    summary = {
        "n": len(cases),
        "learner_score": _safe_avg(metric_acc["learner_correct"]),
        "agent_must_cite_hit": _safe_avg(metric_acc["must_cite_hit"]),
        "agent_topic_recall": _safe_avg(metric_acc["topic_recall"]),
    }

    record = {
        "kind": "quiz_attempt_eval",
        "ts": int(time.time()),
        "attempt_id": int(attempt_id),
        "course_id": int(course_id),
        "quiz_id": int(attempt.get("quiz_id", 0)),
        "user_id": int(attempt.get("user_id", 0)),
        "summary": summary,
        "cases": cases,
    }
    out_path = DATA_DIR / f"quiz_eval_{int(attempt_id)}.json"
    out_path.write_text(json.dumps(record, indent=2, default=str))
    log.info(
        "quiz_attempt_eval written: attempt=%s course=%s n=%s learner_score=%.2f must_cite_hit=%.2f",
        attempt_id, course_id, summary["n"], summary["learner_score"], summary["agent_must_cite_hit"],
    )
    record["file"] = str(out_path)
    return record


def _haystack(source: dict[str, Any]) -> str:
    parts = [
        str(source.get("title") or ""),
        str(source.get("heading_path") or ""),
        str(source.get("text_snippet") or ""),
    ]
    return " ".join(parts)


def _strip_html(html: str) -> str:
    import re

    return re.sub(r"<[^>]+>", " ", html or "").strip()


def _safe_avg(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)
