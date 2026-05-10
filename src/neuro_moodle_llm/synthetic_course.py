"""Generate synthetic courses with Ollama, chunk+embed like Moodle ingest, optional golden eval rows.

Uses the same pipeline as ``ingest_course`` via ``ingest_raw_docs``. Qdrant
payloads include ``provenance: synthetic`` plus ``synth_topic``, ``synth_seed``,
``synth_run_id`` so operators can filter or purge synthetic vectors.

Recommended ``course_id`` is **>= 90000** to avoid colliding with real Moodle
courses; lower IDs are still allowed when ``allow_low_course_id`` is true.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from .config import Settings, get_settings
from .ingest import _RawDoc, ingest_course, ingest_raw_docs
from .lineage import new_ingest_run_id
from .moodle import MoodleClient, MoodleError
from .moodle_authoring import (
    add_page_resource,
    add_quiz_with_questions,
    create_synth_course,
    markdown_to_simple_html,
    slugify,
    synth_idnumber,
)
from .ollama import OllamaClient
from .vectorstore import VectorStore

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
SYNTH_EVAL_DIR = _REPO_ROOT / "data" / "eval"

SYNTH_COURSE_ID_RECOMMENDED_MIN = 90000

_SKELETON_SYS = (
    "You design short online course outlines. Output ONLY valid JSON, no markdown fences, no commentary. "
    "Schema: {\"course_title\": string, \"weeks\": [{\"week_number\": int, \"title\": string, "
    "\"modules\": [{\"title\": string, \"objectives\": [string, ...]}]}]}. "
    "Use exactly the requested number of weeks; each week has the requested number of modules."
)

_MODULE_SYS = (
    "You write concise course module content for students. Use Markdown with ## and ### headings. "
    "Stay factual and self-contained; do not invent external URLs. "
    "Length: roughly 400–900 words."
)

_QUESTIONS_SYS = (
    "You write evaluation questions for RAG testing. Output ONLY valid JSON, no markdown. "
    "Schema: {\"questions\": [{\"question\": string, \"expected_topics\": [string], "
    "\"must_cite\": string}]}. "
    "Each question must be answerable from the module text alone. "
    "must_cite must equal the exact module_title string provided by the user (verbatim)."
)

_DISTRACTORS_SYS = (
    "You write multiple-choice answer options for course quizzes. Output ONLY valid JSON, no markdown. "
    "Schema: {\"correct\": string, \"distractors\": [string, string, string]}. "
    "The correct answer must be a short, factually accurate response to the question grounded in the supplied "
    "module text. Distractors must be plausible but clearly wrong on careful reading; they should NOT be the "
    "correct answer or paraphrases of it. Keep each option under 25 words."
)


def _strip_json_fences(raw: str) -> str:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def _parse_json_object(raw: str) -> dict[str, Any]:
    s = _strip_json_fences(raw)
    return json.loads(s)


def _chat_json(
    *,
    ollama: OllamaClient,
    system: str,
    user: str,
    seed: int,
    temperature: float,
) -> dict[str, Any]:
    """Two-attempt structured JSON chat: first with Ollama ``format=json``,
    then a retry with a stricter system prompt if parsing fails.
    """
    raw = ollama.chat(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        options={"temperature": temperature, "seed": seed},
        format="json",
    )
    try:
        return _parse_json_object(raw)
    except json.JSONDecodeError as exc:
        log.warning("First JSON parse failed (%s); retrying with stricter prompt", exc)

    strict = system + " Return STRICTLY valid JSON. Do not include any commentary, markdown, or trailing commas."
    raw2 = ollama.chat(
        messages=[
            {"role": "system", "content": strict},
            {"role": "user", "content": user + "\n\nRespond with valid JSON only."},
        ],
        options={"temperature": max(0.0, temperature - 0.2), "seed": seed + 1},
        format="json",
    )
    try:
        return _parse_json_object(raw2)
    except json.JSONDecodeError as exc:
        log.warning("Second JSON parse failed (%s); first 200 chars: %s", exc, (raw2 or "")[:200])
        raise


def generate_skeleton(
    *,
    topic: str,
    weeks: int,
    modules_per_week: int,
    ollama: OllamaClient,
    seed: int = 42,
) -> dict[str, Any]:
    user = (
        f"Topic: {topic!r}\n"
        f"Number of weeks: {weeks}\n"
        f"Modules per week: {modules_per_week}\n"
        f"Random seed (for variety): {seed}\n"
        "Vary module titles; make objectives concrete and testable."
    )
    return _chat_json(
        ollama=ollama, system=_SKELETON_SYS, user=user, seed=seed, temperature=0.55
    )


def generate_module_markdown(
    *,
    topic: str,
    course_title: str,
    week_number: int,
    week_title: str,
    module_title: str,
    objectives: list[str],
    ollama: OllamaClient,
    seed: int,
) -> str:
    obj_lines = "\n".join(f"- {o}" for o in objectives) or "- (no objectives listed)"
    user = (
        f"Course: {course_title}\n"
        f"Topic domain: {topic}\n"
        f"Week {week_number}: {week_title}\n"
        f"Module title: {module_title}\n"
        f"Learning objectives:\n{obj_lines}\n\n"
        "Write the module body in Markdown. Start with a single line "
        f"exactly: # {module_title}\n"
        "Then use ## and ### for structure."
    )
    return ollama.chat(
        messages=[
            {"role": "system", "content": _MODULE_SYS},
            {"role": "user", "content": user},
        ],
        options={"temperature": 0.45, "seed": seed + week_number * 17},
    ).strip()


def generate_questions_for_module(
    *,
    module_title: str,
    module_markdown: str,
    n_questions: int,
    ollama: OllamaClient,
    seed: int,
) -> list[dict[str, Any]]:
    text = module_markdown[:12000]
    user = (
        f"module_title (must_cite verbatim): {module_title!r}\n"
        f"Generate exactly {n_questions} questions.\n\n"
        f"--- module text ---\n{text}\n--- end ---"
    )
    try:
        data = _chat_json(
            ollama=ollama, system=_QUESTIONS_SYS, user=user, seed=seed, temperature=0.35
        )
    except json.JSONDecodeError:
        log.warning("Question generation produced unparseable JSON; skipping module %s", module_title)
        return []
    qs = data.get("questions") or []
    out: list[dict[str, Any]] = []
    for item in qs[:n_questions]:
        if not isinstance(item, dict):
            continue
        q = str(item.get("question", "")).strip()
        if not q:
            continue
        topics = item.get("expected_topics") or []
        if isinstance(topics, str):
            topics = [topics]
        topics = [str(t) for t in topics if str(t).strip()]
        out.append(
            {
                "question": q,
                "expected_topics": topics,
                "must_cite": module_title,
            }
        )
    return out


def generate_multichoice_for_question(
    *,
    question: str,
    module_markdown: str,
    ollama: OllamaClient,
    seed: int,
) -> Optional[dict[str, Any]]:
    """Return ``{correct, distractors}`` or ``None`` if the LLM output is unusable."""
    text = module_markdown[:8000]
    user = (
        f"Question: {question!r}\n\n"
        "Write the correct answer plus exactly THREE distractors. "
        "Distractors must be plausible-sounding but clearly wrong.\n\n"
        f"--- module text ---\n{text}\n--- end ---"
    )
    try:
        data = _chat_json(
            ollama=ollama, system=_DISTRACTORS_SYS, user=user, seed=seed, temperature=0.4
        )
    except json.JSONDecodeError:
        log.warning("Distractor generation produced unparseable JSON for %r; skipping", question[:60])
        return None
    correct = str(data.get("correct", "")).strip()
    distractors = data.get("distractors") or []
    if isinstance(distractors, str):
        distractors = [distractors]
    distractors = [str(d).strip() for d in distractors if str(d).strip()]
    distractors = [d for d in distractors if d.lower() != correct.lower()][:3]
    if not correct or len(distractors) < 2:
        return None
    while len(distractors) < 3:
        distractors.append(f"None of the above ({len(distractors)})")
    return {"correct": correct, "distractors": distractors[:3]}


def _write_golden_lines(
    course_id: int,
    rows: list[dict[str, Any]],
    *,
    append_to_primary: bool,
    dedicated_path: Optional[Path] = None,
) -> dict[str, Any]:
    SYNTH_EVAL_DIR.mkdir(parents=True, exist_ok=True)
    path = dedicated_path
    if path is None:
        path = SYNTH_EVAL_DIR / f"golden_synthetic_{course_id}.jsonl"
    lines = []
    for row in rows:
        rec = {"course_id": course_id, **row}
        lines.append(json.dumps(rec, ensure_ascii=False) + "\n")
    path.write_text("".join(lines), encoding="utf-8")
    primary = SYNTH_EVAL_DIR / "golden.jsonl"
    appended_primary = 0
    if append_to_primary and lines:
        with primary.open("a", encoding="utf-8") as f:
            for line in lines:
                f.write(line)
                appended_primary += 1
    return {"golden_file": str(path), "n_lines": len(lines), "appended_to_golden_jsonl": appended_primary}


def generate_and_ingest(
    *,
    course_id: int,
    topic: str,
    weeks: int = 2,
    modules_per_week: int = 2,
    questions_per_module: int = 2,
    seed: int = 42,
    replace: bool = True,
    write_golden: bool = True,
    append_golden_to_primary: bool = False,
    allow_low_course_id: bool = False,
    publish_to_moodle: bool = False,
    publish_quizzes: bool = True,
    settings: Optional[Settings] = None,
) -> dict[str, Any]:
    """Generate a synthetic course and ingest it into Qdrant.

    When ``publish_to_moodle`` is true, the course is also created in Moodle
    (Phase A: course shell + section names + Page resources per module). When
    ``publish_quizzes`` is also true, each module additionally gets a Quiz
    activity populated with multichoice questions (Phase B). After publishing
    the function re-ingests via ``ingest_course`` so Qdrant is keyed by the
    real Moodle course id rather than the synthetic ``course_id`` placeholder.
    """
    settings = settings or get_settings()
    if course_id < SYNTH_COURSE_ID_RECOMMENDED_MIN and not allow_low_course_id:
        raise ValueError(
            f"course_id {course_id} is below recommended minimum {SYNTH_COURSE_ID_RECOMMENDED_MIN}; "
            "use a high id for synthetic-only courses or pass allow_low_course_id=True"
        )

    synth_run_id = new_ingest_run_id()
    golden_rows: list[dict[str, Any]] = []
    module_assets: list[dict[str, Any]] = []  # populated below for Moodle publish flow

    with OllamaClient(settings) as ollama:
        store = VectorStore(settings)
        skel = generate_skeleton(
            topic=topic, weeks=weeks, modules_per_week=modules_per_week, ollama=ollama, seed=seed
        )
        course_title = str(skel.get("course_title") or topic)
        weeks_data = skel.get("weeks") or []
        if not isinstance(weeks_data, list):
            weeks_data = []

        docs: list[_RawDoc] = []
        mod_counter = 0
        for wk in weeks_data[:weeks]:
            if not isinstance(wk, dict):
                continue
            wn = int(wk.get("week_number") or len(docs) + 1)
            wt = str(wk.get("title") or f"Week {wn}")
            modules = wk.get("modules") or []
            if not isinstance(modules, list):
                modules = []
            for mod in modules[:modules_per_week]:
                if not isinstance(mod, dict):
                    continue
                mt = str(mod.get("title") or "Untitled module").strip()
                objectives = mod.get("objectives") or []
                if isinstance(objectives, str):
                    objectives = [objectives]
                objectives = [str(x) for x in objectives if str(x).strip()]

                body = generate_module_markdown(
                    topic=topic,
                    course_title=course_title,
                    week_number=wn,
                    week_title=wt,
                    module_title=mt,
                    objectives=objectives,
                    ollama=ollama,
                    seed=seed,
                )
                doc_title = f"Week {wn}: {mt}"
                mod_counter += 1
                synthetic_module_id = 900_000 + mod_counter
                docs.append(
                    _RawDoc(
                        course_id=course_id,
                        section_id=wn,
                        section_name=wt,
                        module_id=synthetic_module_id,
                        module_name=mt,
                        modtype="synthetic",
                        title=doc_title,
                        text=body,
                        url=None,
                    )
                )
                qs: list[dict[str, Any]] = []
                if questions_per_module > 0:
                    qs = generate_questions_for_module(
                        module_title=doc_title,
                        module_markdown=body,
                        n_questions=questions_per_module,
                        ollama=ollama,
                        seed=seed + mod_counter,
                    )
                    golden_rows.extend(qs)

                module_assets.append(
                    {
                        "section_num": wn,
                        "section_name": wt,
                        "module_title": mt,
                        "doc_title": doc_title,
                        "markdown": body,
                        "questions": qs,
                    }
                )

        if not docs:
            log.warning("Skeleton produced no modules; writing single fallback module")
            fallback = (
                f"# {topic}\n\n## Overview\n\n"
                f"This is auto-generated placeholder content for **{topic}** because "
                "the model did not return a usable week/module outline. Regenerate with a different seed.\n"
            )
            docs.append(
                _RawDoc(
                    course_id=course_id,
                    section_id=1,
                    section_name="Week 1",
                    module_id=900_001,
                    module_name="Overview",
                    modtype="synthetic",
                    title=f"Week 1: {topic}",
                    text=fallback,
                    url=None,
                )
            )
            if questions_per_module > 0:
                golden_rows.extend(
                    generate_questions_for_module(
                        module_title=f"Week 1: {topic}",
                        module_markdown=fallback,
                        n_questions=min(questions_per_module, 3),
                        ollama=ollama,
                        seed=seed,
                    )
                )

        if publish_to_moodle:
            # When publishing, the re-ingest from Moodle (after course creation
            # below) becomes the source of truth, so we skip the synthetic-keyed
            # Qdrant pass to avoid duplicate vectors under two course ids.
            ingest_stats = {"skipped": True, "reason": "publish_to_moodle=True; will re-ingest from Moodle"}
        else:
            ingest_stats = ingest_raw_docs(
                course_id,
                docs,
                ollama,
                store,
                replace=replace,
                payload_extra={
                    "provenance": "synthetic",
                    "synth_topic": topic,
                    "synth_seed": seed,
                    "synth_run_id": synth_run_id,
                    "synth_course_title": course_title,
                },
            )

    publish_result: Optional[dict[str, Any]] = None
    final_course_id = course_id
    if publish_to_moodle:
        publish_result = _publish_to_moodle(
            settings=settings,
            synth_course_id=course_id,
            course_title=course_title,
            topic=topic,
            synth_run_id=synth_run_id,
            module_assets=module_assets,
            publish_quizzes=publish_quizzes,
            seed=seed,
        )
        if publish_result.get("course_id"):
            final_course_id = int(publish_result["course_id"])
            # Retag both the course id (so the golden file targets the real
            # Moodle id) and the must_cite (so it matches the Moodle Page name).
            doc_to_module = {m["doc_title"]: m["module_title"] for m in module_assets}
            golden_rows = _retag_golden_rows(
                golden_rows,
                course_id=final_course_id,
                doc_to_module=doc_to_module,
            )

    golden_info: dict[str, Any] = {"written": False}
    if write_golden and golden_rows:
        golden_info = _write_golden_lines(
            final_course_id,
            golden_rows,
            append_to_primary=append_golden_to_primary,
        )
        golden_info["written"] = True

    return {
        "course_id": final_course_id,
        "synth_course_id": course_id,
        "topic": topic,
        "synth_run_id": synth_run_id,
        "skeleton": skel,
        "ingest": ingest_stats,
        "publish": publish_result,
        "golden": golden_info,
        "n_golden_questions": len(golden_rows),
    }


def _retag_golden_rows(
    rows: list[dict[str, Any]],
    *,
    course_id: int,
    doc_to_module: Optional[dict[str, str]] = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    mapping = doc_to_module or {}
    for r in rows:
        new = dict(r)
        new["course_id"] = course_id
        old_must = str(new.get("must_cite") or "")
        if old_must in mapping:
            new["must_cite"] = mapping[old_must]
        out.append(new)
    return out


def _publish_to_moodle(
    *,
    settings: Settings,
    synth_course_id: int,
    course_title: str,
    topic: str,
    synth_run_id: str,
    module_assets: list[dict[str, Any]],
    publish_quizzes: bool,
    seed: int,
) -> dict[str, Any]:
    """Create a Moodle course shell, publish module pages and (optionally) quizzes,
    then re-ingest from Moodle so vectors are keyed by the real Moodle course id.
    """
    slug = slugify(topic)
    idnumber = synth_idnumber(slug, synth_run_id)
    shortname = f"synth-{slug}-{synth_run_id[:6]}"

    pages: list[dict[str, Any]] = []
    quizzes: list[dict[str, Any]] = []
    quiz_eval_meta: list[dict[str, Any]] = []

    course_summary = (
        f"<p><em>Synthetic course generated by neuro-moodle-llm.</em></p>"
        f"<p>Topic: <strong>{topic}</strong>. Run id: <code>{synth_run_id}</code>.</p>"
    )

    n_sections = max(1, max(int(m["section_num"]) for m in module_assets))

    with MoodleClient(settings) as moodle:
        course = create_synth_course(
            moodle=moodle,
            fullname=course_title,
            shortname=shortname,
            idnumber=idnumber,
            summary=course_summary,
            num_sections=n_sections,
        )
        log.info(
            "Synthetic course published: id=%s shortname=%s created=%s url=%s",
            course.course_id, course.shortname, course.created, course.view_url,
        )

        with OllamaClient(settings) as ollama:
            for mod in module_assets:
                html = markdown_to_simple_html(mod["markdown"])
                page = add_page_resource(
                    moodle=moodle,
                    course_id=course.course_id,
                    section_num=int(mod["section_num"]),
                    section_name=mod["section_name"],
                    name=mod["module_title"],
                    content_html=html,
                )
                pages.append(
                    {
                        "module_title": mod["module_title"],
                        "section_num": int(mod["section_num"]),
                        "cmid": page.cmid,
                        "view_url": page.view_url,
                        "created": page.created,
                    }
                )

                if not publish_quizzes:
                    continue
                if not mod["questions"]:
                    continue

                multichoice = []
                for q_idx, q in enumerate(mod["questions"]):
                    mc = generate_multichoice_for_question(
                        question=q["question"],
                        module_markdown=mod["markdown"],
                        ollama=ollama,
                        seed=seed + 1000 + q_idx,
                    )
                    if not mc:
                        continue
                    options = [mc["correct"], *mc["distractors"]]
                    multichoice.append(
                        {
                            "stem": f"<p>{q['question']}</p>",
                            "options": options,
                            "correct_index": 0,
                            # Use the Moodle Page name (no "Week N:" prefix) so the
                            # downstream agentic-feedback citation eval can match
                            # this string against retrieved source titles, which
                            # are derived from `module.get('name')` during ingest.
                            "must_cite": mod["module_title"],
                            "expected_topics": q.get("expected_topics") or [],
                        }
                    )

                if not multichoice:
                    log.warning("Skipping quiz for module %r — no usable multichoice items", mod["module_title"])
                    continue

                quiz = add_quiz_with_questions(
                    moodle=moodle,
                    course_id=course.course_id,
                    section_num=int(mod["section_num"]),
                    name=f"Quiz: {mod['module_title']}",
                    intro_html=f"<p>Auto-generated quiz for the module <strong>{mod['module_title']}</strong>.</p>",
                    questions=multichoice,
                )
                quizzes.append(
                    {
                        "module_title": mod["module_title"],
                        "quiz_id": quiz.quiz_id,
                        "cmid": quiz.cmid,
                        "view_url": quiz.view_url,
                        "created": quiz.created,
                        "n_questions": len(quiz.question_ids),
                    }
                )
                quiz_eval_meta.extend(quiz.eval_meta)

        # Re-ingest from Moodle so Qdrant carries the real Moodle course id.
        with OllamaClient(settings) as ollama:
            store = VectorStore(settings)
            try:
                ingest_stats = ingest_course(course.course_id, moodle, ollama, store, replace=True)
            except MoodleError as exc:
                log.warning("Re-ingest from Moodle failed for course %s: %s", course.course_id, exc)
                ingest_stats = {"error": str(exc)}

    quiz_eval_path: Optional[str] = None
    if quiz_eval_meta:
        SYNTH_EVAL_DIR.mkdir(parents=True, exist_ok=True)
        path = SYNTH_EVAL_DIR / f"quiz_eval_meta_{course.course_id}.json"
        path.write_text(
            json.dumps(
                {
                    "course_id": course.course_id,
                    "synth_course_id": synth_course_id,
                    "synth_run_id": synth_run_id,
                    "questions": quiz_eval_meta,
                },
                indent=2,
            )
        )
        quiz_eval_path = str(path)

    return {
        "course_id": course.course_id,
        "shortname": course.shortname,
        "idnumber": course.idnumber,
        "category_id": course.category_id,
        "view_url": course.view_url,
        "created_course": course.created,
        "pages": pages,
        "quizzes": quizzes,
        "quiz_eval_meta_file": quiz_eval_path,
        "reingest_from_moodle": ingest_stats,
    }
