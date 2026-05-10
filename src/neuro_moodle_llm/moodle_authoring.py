"""Write-side wrappers around Moodle web services.

Kept separate from ``moodle.py`` (which is read-only) so callers and reviewers
can tell at a glance which call mutates Moodle state. All functions are
idempotent on the Moodle side (the underlying WS look up by ``idnumber`` /
``shortname`` / section+name and update in place rather than duplicating).

The write functions used here come in two flavours:

* **Core** Moodle web services (``core_course_create_courses``,
  ``core_course_delete_courses``) for course-shell mutations.
* **Custom** ``local_neurollm_*`` external functions (declared in
  ``moodle_plugins/neurollm/db/services.php``) for activity creation that
  Moodle core doesn't expose via web services (Page resources, Quiz
  activities with multichoice questions).

The plugin's ``services`` declaration auto-attaches the custom functions to
the existing ``neurollm`` external service, so the same WS token already
issued by ``docker/bootstrap-webservice.php`` works.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from .config import Settings
from .moodle import MoodleClient, MoodleError

log = logging.getLogger(__name__)

SYNTH_CATEGORY_IDNUMBER = "neuro_synth"
SYNTH_CATEGORY_NAME = "Synthetic / Not for production"


@dataclass
class CreatedCourse:
    course_id: int
    shortname: str
    idnumber: str
    category_id: int
    created: bool
    view_url: str


@dataclass
class CreatedPage:
    cmid: int
    instance: int
    section_id: int
    created: bool
    view_url: str


@dataclass
class CreatedQuiz:
    cmid: int
    quiz_id: int
    created: bool
    question_ids: list[int]
    view_url: str
    eval_meta: list[dict[str, Any]]


def slugify(text: str, *, max_len: int = 32) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text or "").strip("-").lower()
    return (s or "synth")[:max_len].rstrip("-")


def synth_idnumber(slug: str, run_id: str) -> str:
    """Stable course `idnumber` so re-runs with the same run_id are idempotent.

    Moodle's `idnumber` column is `VARCHAR(100)`; we keep the result well under that.
    """
    return f"synth-{slug[:40]}-{run_id[:12]}"


def create_synth_course(
    *,
    moodle: MoodleClient,
    fullname: str,
    shortname: str,
    idnumber: str,
    summary: str = "",
    num_sections: int = 1,
    category_id: int = 0,
) -> CreatedCourse:
    """Call ``local_neurollm_create_course``. Idempotent by ``idnumber`` then ``shortname``."""
    res = moodle.call(
        "local_neurollm_create_course",
        fullname=fullname,
        shortname=shortname,
        idnumber=idnumber,
        summary=summary,
        numsections=num_sections,
        category_id=category_id,
        category_name=SYNTH_CATEGORY_NAME,
    )
    return CreatedCourse(
        course_id=int(res["course_id"]),
        shortname=str(res["shortname"]),
        idnumber=str(res["idnumber"]),
        category_id=int(res["category_id"]),
        created=bool(res["created"]),
        view_url=str(res["view_url"]),
    )


def add_page_resource(
    *,
    moodle: MoodleClient,
    course_id: int,
    section_num: int,
    name: str,
    content_html: str,
    section_name: str = "",
    section_summary: str = "",
    visible: bool = True,
) -> CreatedPage:
    res = moodle.call(
        "local_neurollm_create_page",
        course_id=course_id,
        section_num=section_num,
        section_name=section_name,
        section_summary=section_summary,
        name=name,
        content_html=content_html,
        visible=1 if visible else 0,
    )
    return CreatedPage(
        cmid=int(res["cmid"]),
        instance=int(res["instance"]),
        section_id=int(res["section_id"]),
        created=bool(res["created"]),
        view_url=str(res["view_url"]),
    )


def add_quiz_with_questions(
    *,
    moodle: MoodleClient,
    course_id: int,
    section_num: int,
    name: str,
    questions: list[dict[str, Any]],
    intro_html: str = "",
) -> CreatedQuiz:
    """Create a quiz with multichoice questions.

    Each question dict needs::

        {
          "stem": "<p>...</p>",
          "options": ["A", "B", "C", "D"],
          "correct_index": 0,
          "must_cite": "Week 1: Foo",
          "expected_topics": ["foo", "bar"],
        }
    """
    res = moodle.call(
        "local_neurollm_create_quiz_with_questions",
        course_id=course_id,
        section_num=section_num,
        name=name,
        intro_html=intro_html,
        questions=questions,
    )
    return CreatedQuiz(
        cmid=int(res["cmid"]),
        quiz_id=int(res["quiz_id"]),
        created=bool(res["created"]),
        question_ids=[int(q) for q in (res.get("question_ids") or [])],
        view_url=str(res["view_url"]),
        eval_meta=list(res.get("eval_meta") or []),
    )


def delete_synth_course(*, moodle: MoodleClient, course_id: int) -> dict[str, Any]:
    return moodle.call("local_neurollm_delete_course", course_id=course_id)


def get_quiz_attempt(*, moodle: MoodleClient, attempt_id: int) -> dict[str, Any]:
    return moodle.call("local_neurollm_get_quiz_attempt", attempt_id=attempt_id)


# Lightweight Markdown → HTML so we can hand Moodle Page resources real HTML
# without dragging in `markdown` as a runtime dependency. Handles the subset the
# synthetic generator emits: ``# / ## / ### headings``, ``- / *`` lists,
# ``**bold**``, `` `code` ``, blank-line paragraphs.

_HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")


def markdown_to_simple_html(md: str) -> str:
    lines = (md or "").splitlines()
    html: list[str] = []
    in_list = False
    para: list[str] = []

    def flush_para() -> None:
        nonlocal para
        if para:
            html.append("<p>" + " ".join(para) + "</p>")
            para = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            html.append("</ul>")
            in_list = False

    def inline(s: str) -> str:
        s = _BOLD_RE.sub(r"<strong>\1</strong>", s)
        s = _INLINE_CODE_RE.sub(r"<code>\1</code>", s)
        return s

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            flush_para()
            close_list()
            continue
        m = _HEAD_RE.match(line)
        if m:
            flush_para()
            close_list()
            level = min(6, len(m.group(1)))
            html.append(f"<h{level}>{inline(m.group(2).strip())}</h{level}>")
            continue
        if line.lstrip().startswith(("- ", "* ")):
            flush_para()
            if not in_list:
                html.append("<ul>")
                in_list = True
            html.append("<li>" + inline(line.lstrip()[2:].strip()) + "</li>")
            continue
        para.append(inline(line.strip()))

    flush_para()
    close_list()
    return "\n".join(html)


__all__ = [
    "CreatedCourse",
    "CreatedPage",
    "CreatedQuiz",
    "MoodleError",
    "SYNTH_CATEGORY_IDNUMBER",
    "SYNTH_CATEGORY_NAME",
    "add_page_resource",
    "add_quiz_with_questions",
    "create_synth_course",
    "delete_synth_course",
    "get_quiz_attempt",
    "markdown_to_simple_html",
    "slugify",
    "synth_idnumber",
]
