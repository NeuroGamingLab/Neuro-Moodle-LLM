"""Ingest control panel — course re-ingest and PDF ingest.

PDF paths must be readable inside the API container (mount your file in
the ``neuro-moodle-llm`` service, or copy it in with ``docker cp``).
"""

from __future__ import annotations

import streamlit as st

from lib.api import APIError, NeuroAPI
from lib.paths import default_course_id
from lib.theme import apply_theme, banner

apply_theme("Ingest")
api = NeuroAPI.from_env()

st.title("Ingest")
banner("Calls /v1/ingest/course (full re-ingest) and /v1/ingest/multimodal/pdf (single PDF). Watch the response for embeddings_from_cache vs embeddings_new.")

tab_course, tab_pdf = st.tabs(["Course", "PDF"])

with tab_course:
    course_id = st.number_input("course_id", value=default_course_id(), step=1)
    if st.button("Re-ingest course", type="primary"):
        try:
            with st.spinner("Ingesting…"):
                res = api.ingest_course(int(course_id))
        except APIError as exc:
            st.error(str(exc))
        else:
            cols = st.columns(4)
            cols[0].metric("chunks", res.get("chunks_total", 0))
            cols[1].metric("from cache", res.get("embeddings_from_cache", 0))
            cols[2].metric("new embeds", res.get("embeddings_new", 0))
            cols[3].metric("modules", res.get("modules", 0))
            with st.expander("Raw response"):
                st.json(res)

with tab_pdf:
    cols = st.columns([1, 3, 2])
    course_id_pdf = cols[0].number_input("course_id ", value=default_course_id(), step=1, key="pdf_cid")
    path = cols[1].text_input("Path inside API container", value="/app/data/uploads/example.pdf")
    title = cols[2].text_input("Title", value="Uploaded PDF")
    st.caption("Tip: bind-mount a folder into the neuro-moodle-llm service or run `docker cp file.pdf neuro-moodle-llm-neuro-moodle-llm-1:/app/data/uploads/`.")
    if st.button("Ingest PDF", type="primary"):
        try:
            with st.spinner("Parsing + embedding…"):
                res = api.ingest_pdf(int(course_id_pdf), path, title)
        except APIError as exc:
            st.error(str(exc))
        else:
            st.json(res)
