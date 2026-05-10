"""Phase 2: event-driven ingest from Moodle.

Two paths to keep the index fresh without manually running `ingest-course`:

1. **Webhook** (`POST /v1/events/moodle`) — register this URL in Moodle's
   *Site administration → Server → Webhooks*, or hit it from a custom
   scheduled task / event observer. Body shape:

       {
         "eventname": "\\core\\event\\course_module_updated",
         "courseid": 2,
         "objecttable": "course_modules",
         "objectid": 41,
         "timecreated": 1731288000,
         "secret": "<NEURO_EVENT_SECRET>"
       }

2. **Polling** — not implemented as a CLI in this repo; use the webhook or cron `ingest-course`.

Both ultimately call `ingest_course()` or `ingest_module()`. A shared-secret
header keeps the webhook from being a free-fire-zone.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from typing import Any, Optional

from .config import Settings, get_settings
from .ingest import ingest_course, ingest_module
from .moodle import MoodleClient
from .ollama import OllamaClient
from .vectorstore import VectorStore

log = logging.getLogger(__name__)


_INGEST_EVENTS = {
    r"\core\event\course_module_updated",
    r"\core\event\course_module_created",
    r"\core\event\course_module_deleted",
    r"\core\event\course_updated",
    r"\mod_assign\event\assignment_updated",
    r"\mod_resource\event\course_module_updated",
}

_QUIZ_ATTEMPT_EVENTS = {
    r"\mod_quiz\event\attempt_submitted",
}


@dataclass
class EventResult:
    accepted: bool
    action: str
    course_id: Optional[int]
    module_id: Optional[int]
    detail: dict[str, Any]


def verify_secret(provided: str, settings: Optional[Settings] = None) -> bool:
    settings = settings or get_settings()
    expected = (settings.neuro_event_secret or "").strip()
    if not expected:
        return True
    return hmac.compare_digest(provided or "", expected)


def handle_event(event: dict[str, Any], settings: Optional[Settings] = None) -> EventResult:
    settings = settings or get_settings()
    name = str(event.get("eventname") or "").strip()
    course_id = event.get("courseid") or event.get("course_id")
    module_id = None
    if event.get("objecttable") == "course_modules":
        module_id = event.get("objectid")

    # Quiz-attempt events route to the closed-loop agentic eval (Phase C).
    if name in _QUIZ_ATTEMPT_EVENTS:
        return _handle_quiz_attempt(event, settings)

    if name and name not in _INGEST_EVENTS:
        return EventResult(False, "skipped:event-not-watched", course_id, module_id, {"eventname": name})
    if not course_id:
        return EventResult(False, "skipped:no-course-id", None, module_id, {"event": event})

    with MoodleClient(settings) as moodle, OllamaClient(settings) as ollama:
        store = VectorStore(settings)
        if module_id:
            detail = ingest_module(int(course_id), int(module_id), moodle, ollama, store)
            return EventResult(True, "reingest:module", int(course_id), int(module_id), detail)
        detail = ingest_course(int(course_id), moodle, ollama, store, replace=True)
        return EventResult(True, "reingest:course", int(course_id), None, detail)


def _handle_quiz_attempt(event: dict[str, Any], settings: Settings) -> EventResult:
    """Trigger ``quiz_eval.evaluate_quiz_attempt`` for a submitted attempt."""
    from . import quiz_eval as quiz_eval_mod

    attempt_id = event.get("objectid") or event.get("attempt_id")
    course_id = event.get("courseid") or event.get("course_id")
    if not attempt_id:
        return EventResult(
            False, "skipped:no-attempt-id", course_id, None, {"event": event}
        )
    try:
        record = quiz_eval_mod.evaluate_quiz_attempt(
            attempt_id=int(attempt_id),
            course_id=int(course_id) if course_id else None,
            settings=settings,
        )
    except Exception as exc:
        log.warning("quiz_eval failed for attempt %s: %s", attempt_id, exc)
        return EventResult(False, "error:quiz-eval", course_id, None, {"error": str(exc)})

    return EventResult(
        True,
        "eval:quiz-attempt",
        record["course_id"],
        None,
        {
            "attempt_id": record["attempt_id"],
            "summary": record["summary"],
            "file": record.get("file"),
        },
    )
