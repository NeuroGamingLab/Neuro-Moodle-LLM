"""HPO grid + champion/challenger registry."""

from __future__ import annotations

import streamlit as st

from lib.api import APIError, NeuroAPI
from lib.files import read_json
from lib.paths import data_dir
from lib.theme import apply_theme, banner

apply_theme("HPO & Registry")
api = NeuroAPI.from_env()

st.title("HPO & Registry")
banner("HPO runs the eval harness across a small grid (slow). Registry is the champion/challenger ledger written by registry.py.")

hpo_tab, reg_tab = st.tabs(["HPO", "Registry"])

with hpo_tab:
    st.warning("HPO runs many evals; this can take several minutes.")
    if st.button("Run HPO grid", type="primary"):
        try:
            with st.spinner("Searching grid…"):
                res = api.hpo_grid({})
        except APIError as exc:
            st.error(str(exc))
        else:
            st.subheader("Best")
            st.json(res.get("best") or res)
            with st.expander("Full result"):
                st.json(res)

    st.subheader("Best knobs on disk")
    best = read_json(data_dir() / "eval" / "best.json")
    if best:
        st.json(best)
    else:
        st.caption("No best.json yet — run HPO first.")

with reg_tab:
    try:
        reg = api.registry()
    except APIError as exc:
        st.error(str(exc))
        reg = None
    if reg:
        champion = reg.get("champion")
        challengers = reg.get("challengers") or []
        st.subheader("Champion")
        if champion:
            st.json(champion)
        else:
            st.caption("No champion registered yet.")
        st.subheader("Challengers")
        if challengers:
            st.dataframe(challengers, use_container_width=True)
        else:
            st.caption("No challengers registered.")
        with st.expander("Raw"):
            st.json(reg)
