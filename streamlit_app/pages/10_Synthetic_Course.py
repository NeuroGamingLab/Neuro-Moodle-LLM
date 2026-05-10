"""Generate a synthetic course with Ollama and (optionally) publish it into Moodle.

Two modes (controlled by the `publish_to_moodle` toggle):

* **Preview-only** — content is chunked, embedded, and stored under a
  high synthetic course_id (≥ 90000); useful for fast RAG/eval iteration.
* **Publish** — also creates a real Moodle course shell with Page resources
  per module and (optionally) a Quiz per module with multichoice questions.
  Vectors are then re-ingested under the real Moodle course id so the rest
  of the stack treats it like any other course.
"""

from __future__ import annotations

import streamlit as st

from lib.api import APIError, NeuroAPI
from lib.theme import apply_theme, banner

apply_theme("Synthetic course")
api = NeuroAPI.from_env()

st.title("Synthetic course (Ollama)")
banner(
    "Calls POST /v1/synth/course — outline + module bodies + eval questions, then either Qdrant-only "
    "(preview) or publish into Moodle as a real course shell + Page resources + Quiz activities."
)

with st.form("synth"):
    c1, c2, c3 = st.columns(3)
    course_id = c1.number_input("course_id (preview-only)", value=90001, step=1, min_value=1)
    topic = c2.text_input("Topic", value="Introduction to attention mechanisms in transformers")
    seed = c3.number_input("seed", value=42, step=1)
    w1, w2, w3 = st.columns(3)
    weeks = w1.number_input("weeks", value=2, min_value=1, max_value=12, step=1)
    modules_per_week = w2.number_input("modules_per_week", value=2, min_value=1, max_value=6, step=1)
    questions_per_module = w3.number_input("questions_per_module", value=2, min_value=0, max_value=6, step=1)

    st.markdown("**Publish-to-Moodle (Phase A/B)**")
    pcols = st.columns(2)
    publish_to_moodle = pcols[0].toggle(
        "publish_to_moodle",
        value=False,
        help="Create a real Moodle course shell + Page resources. Re-ingests under the real Moodle course id.",
    )
    publish_quizzes = pcols[1].toggle(
        "publish_quizzes (multichoice)",
        value=True,
        help="When publishing, also create a Quiz activity per module with multichoice questions and distractors.",
    )

    no_replace = st.checkbox("no_replace (merge with existing vectors for this course_id)", value=False)
    no_golden = st.checkbox("Skip writing golden JSONL", value=False)
    append_golden = st.checkbox("Append golden rows to primary golden.jsonl", value=False)
    allow_low = st.checkbox("allow_low_course_id (< 90000)", value=False)
    go = st.form_submit_button("Generate & ingest", type="primary")

if go:
    payload = {
        "course_id": int(course_id),
        "topic": topic,
        "weeks": int(weeks),
        "modules_per_week": int(modules_per_week),
        "questions_per_module": int(questions_per_module),
        "seed": int(seed),
        "no_replace": bool(no_replace),
        "write_golden": not bool(no_golden),
        "append_golden_to_primary": bool(append_golden),
        "allow_low_course_id": bool(allow_low),
        "publish_to_moodle": bool(publish_to_moodle),
        "publish_quizzes": bool(publish_quizzes),
    }
    try:
        with st.spinner("Calling Ollama + embedding (+ Moodle publish if enabled)…"):
            res = api.synth_course(payload)
    except APIError as exc:
        st.error(str(exc))
    else:
        st.success("Done.")
        final_cid = int(res.get("course_id") or 0)
        st.session_state["last_synth_course_id"] = final_cid
        st.session_state["last_synth_topic"] = topic
        publish = res.get("publish") or {}
        if publish.get("course_id"):
            st.session_state["last_publish"] = publish

        golden_info = res.get("golden") or {}
        if golden_info.get("written") and golden_info.get("golden_file"):
            st.session_state["last_synth_golden_path"] = golden_info["golden_file"]

        ingest = res.get("ingest") or {}
        reingest = (publish or {}).get("reingest_from_moodle") or {}
        eff_chunks = reingest.get("chunks", ingest.get("chunks", 0))
        eff_new = reingest.get("embeddings_new", ingest.get("embeddings_new", 0))

        m = st.columns(4)
        m[0].metric("course_id (effective)", final_cid)
        m[1].metric("chunks", eff_chunks)
        m[2].metric("embeds new", eff_new)
        m[3].metric("golden Qs", res.get("n_golden_questions", 0))

        if publish:
            st.markdown("### Moodle publish summary")
            pcols = st.columns(3)
            pcols[0].metric("pages", len(publish.get("pages") or []))
            pcols[1].metric("quizzes", len(publish.get("quizzes") or []))
            pcols[2].metric("created course", "yes" if publish.get("created_course") else "reused")
            view_url = publish.get("view_url")
            if view_url:
                st.markdown(f"Open the course in Moodle: [{view_url}]({view_url})")
            quizzes = publish.get("quizzes") or []
            if quizzes:
                st.dataframe(
                    [
                        {
                            "module": q.get("module_title"),
                            "quiz_id": q.get("quiz_id"),
                            "n_questions": q.get("n_questions"),
                            "view": q.get("view_url"),
                        }
                        for q in quizzes
                    ],
                    use_container_width=True,
                )

        with st.expander("Raw response"):
            st.json(res)

# --- Quick links to keep the loop on one screen -----------------------------

last_cid = st.session_state.get("last_synth_course_id")
last_path = st.session_state.get("last_synth_golden_path")
last_publish = st.session_state.get("last_publish") or {}

if last_cid or last_path:
    st.divider()
    st.subheader("Next steps")
    st.caption(
        "Last synthetic run: "
        f"course_id={last_cid!s}  ·  golden={last_path or '(not written)'}"
        + (f"  ·  Moodle shortname={last_publish.get('shortname')}" if last_publish else "")
    )
    cols = st.columns(3)

    if cols[0].button("Open in RAG Playground", disabled=last_cid is None, use_container_width=True):
        st.session_state["rag_prefill_course_id"] = int(last_cid)
        try:
            st.switch_page("pages/1_RAG_Playground.py")
        except Exception:
            st.info("Switch to the RAG Playground page from the sidebar; the course_id is pre-filled.")

    if cols[1].button(
        "Eval this golden now",
        disabled=not last_path,
        use_container_width=True,
        type="primary",
    ):
        body = {
            "label": f"synth-{last_cid}",
            "top_k": 5,
            "candidate_k": 20,
            "use_hybrid": True,
            "use_rerank": True,
            "golden_path": last_path,
        }
        try:
            with st.spinner("Running eval against the synthetic golden file…"):
                eval_res = api.eval_run(body)
            st.success("Eval complete.")
            summary = eval_res.get("summary") or {}
            delta = (eval_res.get("delta_vs_champion") or {}).get("delta") or {}
            mcols = st.columns(4)
            mcols[0].metric(
                "topic_recall@k",
                f"{summary.get('topic_recall@k', 0):.2f}",
                delta=f"{delta.get('topic_recall@k', 0):+.2f}" if delta else None,
            )
            mcols[1].metric(
                "must_cite_hit@k",
                f"{summary.get('must_cite_hit@k', 0):.2f}",
                delta=f"{delta.get('must_cite_hit@k', 0):+.2f}" if delta else None,
            )
            mcols[2].metric(
                "mrr@k",
                f"{summary.get('mrr@k', 0):.2f}",
                delta=f"{delta.get('mrr@k', 0):+.2f}" if delta else None,
            )
            mcols[3].metric(
                "confidence",
                f"{summary.get('confidence', 0):.2f}",
                delta=f"{delta.get('confidence', 0):+.2f}" if delta else None,
            )
            with st.expander("Raw eval response"):
                st.json(eval_res)
        except APIError as exc:
            st.error(str(exc))

    if cols[2].button("Send golden to Eval page", disabled=not last_path, use_container_width=True):
        st.session_state["eval_prefill_golden_path"] = last_path
        st.session_state["eval_prefill_label"] = f"synth-{last_cid}"
        try:
            st.switch_page("pages/3_Eval_and_Monitor.py")
        except Exception:
            st.info("Switch to the Eval & Monitor page from the sidebar; the golden_path is pre-filled.")

# --- Closed-loop: eval a quiz attempt the learner just submitted ------------

if last_publish:
    st.divider()
    st.subheader("Closed-loop quiz-attempt eval (Phase C)")
    st.caption(
        "After a learner submits one of the published quizzes, paste their attempt id below. "
        "The agentic feedback pipeline retrieves chunks against each question and is scored "
        "against the synthetic must_cite + expected_topics."
    )
    qcols = st.columns([1, 1, 2])
    attempt_id = qcols[0].number_input("attempt_id", value=0, min_value=0, step=1)
    quiz_topk = qcols[1].number_input("top_k", value=5, min_value=1, max_value=20, step=1)
    if qcols[2].button("Run agentic citation eval", disabled=attempt_id <= 0, use_container_width=True):
        try:
            with st.spinner("Pulling attempt + running retriever per question…"):
                qres = api.eval_quiz_attempt(
                    {"attempt_id": int(attempt_id), "course_id": int(last_cid), "top_k": int(quiz_topk)}
                )
            st.success("Closed-loop eval complete.")
            summary = qres.get("summary") or {}
            mcols = st.columns(3)
            mcols[0].metric("learner_score", f"{summary.get('learner_score', 0):.2f}")
            mcols[1].metric("agent_must_cite_hit", f"{summary.get('agent_must_cite_hit', 0):.2f}")
            mcols[2].metric("agent_topic_recall", f"{summary.get('agent_topic_recall', 0):.2f}")
            cases = qres.get("cases") or []
            if cases:
                st.dataframe(
                    [
                        {
                            "slot": c.get("slot"),
                            "stem": (c.get("stem") or "")[:80],
                            "learner_correct": c.get("learner_correct"),
                            "must_cite_hit": c.get("must_cite_hit"),
                            "topic_recall": c.get("topic_recall"),
                        }
                        for c in cases
                    ],
                    use_container_width=True,
                )
            with st.expander("Raw eval response"):
                st.json(qres)
        except APIError as exc:
            st.error(str(exc))

    st.divider()
    st.subheader("Cleanup")
    purge_cols = st.columns([2, 1, 1])
    purge_cid = purge_cols[0].number_input(
        "course_id to purge",
        value=int(last_publish.get("course_id") or 0),
        step=1,
    )
    keep_qdrant = purge_cols[1].checkbox("keep Qdrant", value=False)
    if purge_cols[2].button("Delete from Moodle", type="secondary", use_container_width=True):
        try:
            with st.spinner("Deleting…"):
                presp = api.synth_purge({"course_id": int(purge_cid), "delete_qdrant": not keep_qdrant})
            st.success("Purged.")
            st.json(presp)
        except APIError as exc:
            st.error(str(exc))
