"""Synthetic-question coverage audit per course."""

from __future__ import annotations

import streamlit as st

from lib.api import APIError, NeuroAPI
from lib.files import list_json_files, read_json
from lib.paths import data_dir, default_course_id
from lib.theme import apply_theme, banner

apply_theme("Audit")
api = NeuroAPI.from_env()

st.title("Course coverage audit")
banner("Generates synthetic questions per chunk and asks the RAG pipeline to answer them. Low scores indicate weak coverage. Many Ollama calls — runs slowly.")

cols = st.columns(3)
course_id = cols[0].number_input("course_id", value=default_course_id(), step=1)
max_chunks = cols[1].number_input("max_chunks", value=5, step=1, min_value=1, max_value=50)
go = cols[2].button("Run audit", type="primary")

if go:
    try:
        with st.spinner("Auditing… (this can take a while)"):
            res = api.audit_course(int(course_id), int(max_chunks))
    except APIError as exc:
        st.error(str(exc))
    else:
        st.subheader("Summary")
        st.json(res.get("summary") or res)
        with st.expander("Full result"):
            st.json(res)

st.divider()
st.subheader("Past audits")
files = list_json_files(data_dir() / "audit", limit=20)
if not files:
    st.caption("No audits yet under data/audit/.")
else:
    rows = []
    for p in files:
        doc = read_json(p) or {}
        summary = doc.get("summary") or {}
        rows.append(
            {
                "file": p.name,
                "course_id": doc.get("course_id", "?"),
                "n_questions": doc.get("n_questions", len(doc.get("results") or [])),
                "avg_score": round(float(summary.get("avg_score") or 0), 3),
                "weak": summary.get("weak", 0),
            }
        )
    st.dataframe(rows, use_container_width=True)
