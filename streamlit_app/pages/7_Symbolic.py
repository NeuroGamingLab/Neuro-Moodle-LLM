"""Neuro-symbolic checks — pytest-based code grading and sympy math equivalence."""

from __future__ import annotations

import streamlit as st

from lib.api import APIError, NeuroAPI
from lib.theme import apply_theme, banner

apply_theme("Symbolic")
api = NeuroAPI.from_env()

st.title("Symbolic checks")
banner("Combines LLM critique with deterministic checkers: Python via subprocess + pytest, math via sympy equivalence.")

py_tab, math_tab = st.tabs(["Python (pytest)", "Math (sympy)"])

with py_tab:
    code = st.text_area("Submission code", value="def add(a, b):\n    return a + b\n", height=180)
    tests = st.text_area("pytest cases", value="def test_add():\n    assert add(2, 3) == 5\n", height=160)
    timeout = st.number_input("timeout_s", value=15, step=1, min_value=1, max_value=120)
    if st.button("Run tests", type="primary"):
        try:
            with st.spinner("Running pytest…"):
                res = api.symbolic_python({"code": code, "tests": tests, "timeout_s": int(timeout)})
        except APIError as exc:
            st.error(str(exc))
        else:
            cols = st.columns(3)
            cols[0].metric("ok", str(res.get("ok")))
            cols[1].metric("returncode", res.get("returncode", "?"))
            cols[2].metric("duration_ms", res.get("duration_ms", "?"))
            st.markdown("**stdout**")
            st.code(res.get("stdout") or "(empty)")
            st.markdown("**stderr**")
            st.code(res.get("stderr") or "(empty)")

with math_tab:
    st.caption("One pair per line, lhs and rhs separated by ``||``. Example: ``x**2-1 || (x-1)*(x+1)``")
    raw = st.text_area("Pairs", value="x**2-1 || (x-1)*(x+1)\nsin(x)**2 + cos(x)**2 || 1", height=160)
    if st.button("Check equivalences", type="primary"):
        pairs = []
        for line in raw.splitlines():
            if "||" not in line:
                continue
            lhs, rhs = (s.strip() for s in line.split("||", 1))
            if lhs and rhs:
                pairs.append([lhs, rhs])
        if not pairs:
            st.warning("No valid pairs parsed — use ``lhs || rhs`` per line.")
        else:
            try:
                with st.spinner("Calling sympy…"):
                    res = api.symbolic_math({"pairs": pairs})
            except APIError as exc:
                st.error(str(exc))
            else:
                if res.get("ok") is False and res.get("error"):
                    st.warning(f"sympy unavailable: {res.get('error')} — install with `pip install -e .[math]` in the API image.")
                st.json(res)
