"""Phase 3: agentic assignment feedback flow with HITL approval.

End-to-end:

1. `draft_feedback(course_id, assignment_id, submission_text)` runs the agent
   pipeline (Retriever → Critic → Validator) and returns a feedback object
   with `qid`, raw draft, sources, and validator verdict.
2. `submit_feedback_to_moodle(qid, instructor_edits)` posts the
   instructor-approved version back via `mod_assign_save_grade` (Moodle web
   service). The `(submission, draft, edits)` triple is logged to
   `data/feedback/log.jsonl` for DPO export.

This is the "credible AI teaching assistant" surface from the enhancement
review — every output is human-approved before students see it.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from .agents import RetrieverAgent, CriticAgent, ValidatorAgent
from .config import Settings, get_settings
from .moodle import MoodleClient

LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "feedback" / "log.jsonl"
DRAFT_DIR = Path(__file__).resolve().parents[2] / "data" / "feedback" / "drafts"

log = logging.getLogger(__name__)


def _qid() -> str:
    return f"fb-{int(time.time() * 1000)}-{os.urandom(3).hex()}"


def draft_feedback(
    *,
    course_id: int,
    assignment_id: int,
    submission_text: str,
    rubric: str = "",
    settings: Optional[Settings] = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    qid = _qid()
    rq = f"Assignment {assignment_id}: rubric and relevant course material"
    retrieved = RetrieverAgent(settings).run({"course_id": course_id, "question": rq, "top_k": 8})
    critic = CriticAgent(settings).run(
        {"rubric": rubric, "submission": submission_text, "sources": retrieved["sources"]}
    )
    verdict = ValidatorAgent(settings).run(
        {"draft_feedback_raw": critic["draft_feedback_raw"], "sources": retrieved["sources"]}
    )
    out = {
        "qid": qid,
        "course_id": course_id,
        "assignment_id": assignment_id,
        "draft_feedback_raw": critic["draft_feedback_raw"],
        "validator_verdict_raw": verdict["verdict_raw"],
        "sources": retrieved["sources"],
        "confidence": retrieved.get("confidence", 0.0),
        "needs_human_review": True,
    }
    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    (DRAFT_DIR / f"{qid}.json").write_text(json.dumps(out, indent=2, default=str))
    return out


def submit_feedback_to_moodle(
    *,
    qid: str,
    instructor_edits: str,
    user_id: int,
    grade: float | None = None,
    settings: Optional[Settings] = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    draft_path = DRAFT_DIR / f"{qid}.json"
    if not draft_path.exists():
        raise FileNotFoundError(f"no draft for qid={qid}")
    draft = json.loads(draft_path.read_text())
    assignment_id = int(draft["assignment_id"])

    posted = None
    try:
        with MoodleClient(settings) as moodle:
            posted = moodle.call(
                "mod_assign_save_grade",
                assignmentid=assignment_id,
                userid=user_id,
                grade=float(grade) if grade is not None else -1,
                attemptnumber=-1,
                addattempt=0,
                workflowstate="",
                applytoall=0,
                plugindata={"assignfeedbackcomments_editor": {"text": instructor_edits, "format": 1}},
            )
    except Exception as exc:
        log.warning("Moodle grade-post failed for qid=%s: %s", qid, exc)
        posted = {"error": str(exc)}

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(json.dumps({
            "qid": qid,
            "ts": int(time.time()),
            "course_id": draft["course_id"],
            "assignment_id": assignment_id,
            "submission_text": "(redacted: see drafts dir)",
            "draft": draft["draft_feedback_raw"],
            "instructor_edits": instructor_edits,
            "moodle_response": posted,
        }) + "\n")
    return {"qid": qid, "posted": posted}
