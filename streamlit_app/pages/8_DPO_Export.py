"""DPO preference exporter — turns instructor edits into training pairs."""

from __future__ import annotations

import streamlit as st

from lib.api import APIError, NeuroAPI
from lib.files import list_jsonl
from lib.paths import data_dir
from lib.theme import apply_theme, banner

apply_theme("DPO export")
api = NeuroAPI.from_env()

st.title("DPO preference export")
banner("Reads data/feedback/log.jsonl and writes (prompt, chosen=instructor_edits, rejected=draft) pairs to data/dpo/preferences.jsonl. Training itself is external (TRL on GPU).")

if st.button("Run export", type="primary"):
    try:
        with st.spinner("Exporting…"):
            res = api.dpo_export({})
        st.success("Exported.")
        st.json(res)
    except APIError as exc:
        st.error(str(exc))

st.divider()
st.subheader("Latest preference pairs")
pairs = list_jsonl(data_dir() / "dpo" / "preferences.jsonl", limit=20)
if not pairs:
    st.caption("No preferences yet. Have instructors submit edited feedback in HITL Feedback first.")
else:
    rows = []
    for p in pairs:
        rows.append(
            {
                "prompt": (p.get("prompt") or "")[:160],
                "chosen": (p.get("chosen") or "")[:160],
                "rejected": (p.get("rejected") or "")[:160],
            }
        )
    st.dataframe(rows, use_container_width=True)
    with st.expander("Raw rows"):
        st.json(pairs)
