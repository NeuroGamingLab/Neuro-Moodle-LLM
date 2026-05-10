"""RAG playground — POST /v1/rag/ask with toggles, sources, thumbs feedback."""

from __future__ import annotations

import streamlit as st

from lib.api import APIError, NeuroAPI
from lib.paths import default_course_id
from lib.theme import apply_theme, banner

apply_theme("RAG playground")
api = NeuroAPI.from_env()

st.title("RAG playground")
banner("Calls /v1/rag/ask. Toggle hybrid / rerank / cache to A/B retrieval; rate the answer to feed the active-learning loop.")

prefill_course_id = st.session_state.pop("rag_prefill_course_id", None)
if prefill_course_id is not None:
    st.info(f"Pre-filled course_id={prefill_course_id} from Synthetic Course page.")

with st.form("ask"):
    cols = st.columns([3, 1, 1])
    question = cols[0].text_area("Question", value="What topics does this course mention?", height=120)
    course_id = cols[1].number_input(
        "course_id",
        value=int(prefill_course_id) if prefill_course_id is not None else default_course_id(),
        step=1,
    )
    learner_id = cols[2].text_input("learner_id (optional)", value="")
    cols2 = st.columns(4)
    top_k = cols2[0].number_input("top_k", value=5, step=1, min_value=1, max_value=20)
    candidate_k = cols2[1].number_input("candidate_k", value=20, step=1, min_value=1, max_value=100)
    use_hybrid = cols2[2].toggle("use_hybrid", value=True)
    use_rerank = cols2[3].toggle("use_rerank", value=True)
    use_qa_cache = st.toggle("use_qa_cache", value=True)
    submitted = st.form_submit_button("Ask", type="primary")

if submitted:
    payload = {
        "question": question,
        "course_id": int(course_id),
        "top_k": int(top_k),
        "candidate_k": int(candidate_k),
        "use_hybrid": bool(use_hybrid),
        "use_rerank": bool(use_rerank),
        "use_qa_cache": bool(use_qa_cache),
    }
    if learner_id.strip():
        payload["learner_id"] = learner_id.strip()
    try:
        with st.spinner("Asking…"):
            res = api.rag_ask(payload)
    except APIError as exc:
        st.error(str(exc))
        st.stop()

    st.session_state["last_qid"] = res.get("qid")
    st.session_state["last_course_id"] = int(course_id)
    st.session_state["last_learner_id"] = learner_id.strip()

    cols = st.columns(3)
    cols[0].metric("cache", res.get("cache", "?"))
    cols[1].metric("confidence", f"{float(res.get('confidence') or 0):.2f}")
    cols[2].metric("sources", len(res.get("sources") or []))

    st.subheader("Answer")
    st.write(res.get("answer", ""))

    sources = res.get("sources") or []
    if sources:
        st.subheader("Sources")
        rows = []
        for i, s in enumerate(sources, start=1):
            comp = s.get("components") or {}
            rows.append(
                {
                    "#": i,
                    "title": s.get("title", ""),
                    "score": round(float(s.get("score") or 0), 3),
                    "dense": round(float(comp.get("dense") or 0), 3),
                    "sparse": round(float(comp.get("sparse") or 0), 3),
                    "rerank": round(float(comp.get("rerank") or 0), 3),
                    "reason": comp.get("reason", ""),
                    "module_id": s.get("module_id"),
                }
            )
        st.dataframe(rows, use_container_width=True)

    with st.expander("Raw response"):
        st.json(res)

st.divider()
st.subheader("Feedback on last answer")

qid = st.session_state.get("last_qid")
if not qid:
    st.caption("Ask a question first to enable thumbs feedback.")
else:
    cols = st.columns([1, 1, 4])
    note = cols[2].text_input("Optional note", key="fb_note")
    if cols[0].button("👍  Thumbs up", use_container_width=True):
        try:
            api.feedback(
                {
                    "qid": qid,
                    "vote": "up",
                    "course_id": st.session_state.get("last_course_id"),
                    "learner_id": st.session_state.get("last_learner_id") or None,
                    "note": note or None,
                }
            )
            st.success("Recorded thumbs up.")
        except APIError as exc:
            st.error(str(exc))
    if cols[1].button("👎  Thumbs down", use_container_width=True):
        try:
            api.feedback(
                {
                    "qid": qid,
                    "vote": "down",
                    "course_id": st.session_state.get("last_course_id"),
                    "learner_id": st.session_state.get("last_learner_id") or None,
                    "note": note or None,
                }
            )
            st.success("Recorded thumbs down.")
        except APIError as exc:
            st.error(str(exc))
