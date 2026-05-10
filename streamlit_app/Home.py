"""Neuro ML dashboard — entry page.

Run locally:
    streamlit run streamlit_app/Home.py

In Compose, the ``streamlit`` service launches this file automatically.
"""

from __future__ import annotations

import streamlit as st

from lib.api import APIError, NeuroAPI
from lib.paths import api_base, data_dir, default_course_id
from lib.theme import apply_theme, banner, pill

apply_theme("Home")

st.title("Neuro ML — operator dashboard")
st.markdown(
    pill("FastAPI: " + api_base()) + pill("data: " + str(data_dir())) + pill(f"default course: {default_course_id()}"),
    unsafe_allow_html=True,
)

banner(
    "This dashboard is the operator surface for the Neuro-Moodle-LLM enhancements. "
    "Students still chat inside Moodle (local_neurollm). Use the sidebar to open a page."
)

api = NeuroAPI.from_env()

st.subheader("Service status")

cols = st.columns(2)
with cols[0]:
    st.markdown("**/health (loose)**")
    try:
        h = api.health()
        st.json(h)
    except APIError as exc:
        st.error(str(exc))

with cols[1]:
    st.markdown("**/health/strict**")
    try:
        st.json(api.health_strict())
    except APIError as exc:
        st.warning(f"strict health failed: {exc}")

st.divider()
st.subheader("Pages")

st.markdown(
    """
- **RAG Playground** — call `/v1/rag/ask` with toggles for hybrid / rerank / cache; thumbs feedback.
- **Ingest** — re-ingest a course; ingest a PDF (path inside API container).
- **Eval & Monitor** — run the golden-set eval; run the drift + judge monitor; browse past runs.
- **HPO & Registry** — grid HPO over RAG knobs; view champion/challenger registry.
- **HITL Feedback** — pending feedback drafts inbox; edit and submit grades.
- **Audit** — synthetic-question coverage audit per course.
- **Symbolic** — pytest a code submission; check sympy equivalences.
- **DPO Export** — produce preference pairs from instructor edits; preview latest pairs.
- **Event Simulator** — fire a Moodle webhook payload at the API.
- **Synthetic Course** — Ollama-generated outline + modules + golden JSONL (`/v1/synth/course`; slow).
"""
)
