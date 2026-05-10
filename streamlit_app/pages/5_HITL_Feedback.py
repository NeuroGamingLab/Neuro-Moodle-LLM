"""Human-in-the-loop assignment feedback queue.

Lists pending drafts under ``data/feedback/drafts/*.json`` (written by the
``/v1/agents/feedback/draft`` endpoint), lets the instructor edit and submit
the final feedback via ``/v1/agents/feedback/submit`` (which posts the grade
to Moodle and appends to ``data/feedback/log.jsonl`` for DPO export).
"""

from __future__ import annotations

import streamlit as st

from lib.api import APIError, NeuroAPI
from lib.files import iter_drafts, read_json
from lib.paths import default_course_id
from lib.theme import apply_theme, banner

apply_theme("HITL feedback")
api = NeuroAPI.from_env()

st.title("HITL feedback queue")
banner("Drafts come from /v1/agents/feedback/draft. Submitting posts to Moodle via mod_assign_save_grade and logs the (draft → edits) pair for DPO.")

with st.expander("Draft a new one"):
    with st.form("new_draft"):
        cols = st.columns(3)
        course_id = cols[0].number_input("course_id", value=default_course_id(), step=1)
        assignment_id = cols[1].number_input("assignment_id", value=1, step=1)
        rubric = cols[2].text_input("Rubric (short)", value="Return a value; clarity matters.")
        submission_text = st.text_area("Submission text", value="def f():\n    return 1\n", height=160)
        go = st.form_submit_button("Create draft", type="primary")
    if go:
        try:
            with st.spinner("Drafting…"):
                res = api.feedback_draft(
                    {
                        "course_id": int(course_id),
                        "assignment_id": int(assignment_id),
                        "submission_text": submission_text,
                        "rubric": rubric,
                    }
                )
            st.success(f"Draft created: {res.get('qid')}")
            st.json(res)
        except APIError as exc:
            st.error(str(exc))

st.subheader("Pending drafts")

drafts = list(iter_drafts())
if not drafts:
    st.caption("No drafts yet under data/feedback/drafts/. Create one above or call /v1/agents/feedback/draft.")
else:
    options = {}
    for p in drafts:
        meta = read_json(p) or {}
        label = f"{p.stem}  (course={meta.get('course_id', '?')}, asn={meta.get('assignment_id', '?')})"
        options[label] = p
    chosen = st.selectbox("Pick a draft", list(options.keys()))
    path = options[chosen]
    doc = read_json(path) or {}

    cols = st.columns(3)
    cols[0].metric("course_id", doc.get("course_id", "?"))
    cols[1].metric("assignment_id", doc.get("assignment_id", "?"))
    cols[2].metric("confidence", f"{float(doc.get('confidence') or 0):.2f}")

    st.markdown("**Validator verdict**")
    st.code(doc.get("validator_verdict_raw", "(none)"))

    edits = st.text_area(
        "Instructor edits (final feedback to post to Moodle)",
        value=doc.get("draft_feedback_raw", ""),
        height=240,
    )

    cols = st.columns(3)
    user_id = cols[0].number_input("user_id (Moodle)", value=3, step=1)
    grade_raw = cols[1].text_input("grade (blank = no numeric grade)", value="")
    submit = cols[2].button("Submit to Moodle", type="primary")

    if submit:
        body: dict = {
            "qid": doc.get("qid"),
            "instructor_edits": edits,
            "user_id": int(user_id),
        }
        if grade_raw.strip():
            try:
                body["grade"] = float(grade_raw)
            except ValueError:
                st.error("grade must be a number or blank")
                st.stop()
        try:
            with st.spinner("Posting grade…"):
                res = api.feedback_submit(body)
            st.success("Submitted.")
            st.json(res)
        except APIError as exc:
            st.error(str(exc))

    with st.expander("Raw draft JSON"):
        st.json(doc)
