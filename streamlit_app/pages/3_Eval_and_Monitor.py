"""Eval harness + drift / judge monitor."""

from __future__ import annotations

import streamlit as st

from lib.api import APIError, NeuroAPI
from lib.files import list_json_files, read_json
from lib.paths import data_dir
from lib.theme import apply_theme, banner

apply_theme("Eval & Monitor")
api = NeuroAPI.from_env()

st.title("Eval & Monitor")
banner("Run /v1/eval/run against the golden set and /v1/monitor/run for drift + LLM-as-judge probes. History is read from data/eval/ and data/monitoring/.")

eval_tab, mon_tab = st.tabs(["Eval", "Monitor"])

with eval_tab:
    prefill_label = st.session_state.pop("eval_prefill_label", "dashboard")
    prefill_golden = st.session_state.pop("eval_prefill_golden_path", "")
    if prefill_golden:
        st.info(f"Pre-filled from Synthetic Course page: {prefill_golden}")

    with st.form("eval"):
        cols = st.columns(3)
        label = cols[0].text_input("label", value=prefill_label)
        top_k = cols[1].number_input("top_k", value=5, step=1)
        candidate_k = cols[2].number_input("candidate_k", value=20, step=1)
        cols2 = st.columns(3)
        use_hybrid = cols2[0].toggle("use_hybrid", value=True)
        use_rerank = cols2[1].toggle("use_rerank", value=True)
        promote = cols2[2].toggle("promote (write champion.json)", value=False)
        golden_path = st.text_input(
            "golden_path (optional)",
            value=prefill_golden,
            placeholder="/app/data/eval/golden_synthetic_90001.jsonl",
        )
        run = st.form_submit_button("Run eval", type="primary")
    if run:
        try:
            body = {
                "label": label,
                "top_k": int(top_k),
                "candidate_k": int(candidate_k),
                "use_hybrid": bool(use_hybrid),
                "use_rerank": bool(use_rerank),
                "promote": bool(promote),
            }
            if golden_path.strip():
                body["golden_path"] = golden_path.strip()
            with st.spinner("Running eval…"):
                res = api.eval_run(body)
        except APIError as exc:
            st.error(str(exc))
        else:
            if res.get("promoted"):
                st.success("Eval complete and promoted to champion.")
            else:
                st.success("Eval complete.")

            summary = res.get("summary") or {}
            delta = (res.get("delta_vs_champion") or {}).get("delta") or {}
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

            with st.expander("Raw response"):
                st.json(res)

    st.subheader("Champion")
    champ = read_json(data_dir() / "eval" / "champion.json")
    if champ:
        st.json(champ)
    else:
        st.caption("No champion recorded yet. Run an eval with the promote toggle (or `--promote` on the CLI) to set one.")

    st.subheader("Recent runs")
    files = list_json_files(data_dir() / "eval" / "runs", limit=20)
    if not files:
        st.caption("No runs yet under data/eval/runs/.")
    else:
        rows = []
        for p in files:
            doc = read_json(p) or {}
            summary = doc.get("summary") or {}
            rows.append(
                {
                    "file": p.name,
                    "label": doc.get("label", ""),
                    "n": doc.get("n", len(doc.get("results") or [])),
                    "topic_recall@k": round(float(summary.get("topic_recall@k") or 0), 3),
                    "must_cite_hit@k": round(float(summary.get("must_cite_hit@k") or 0), 3),
                    "mrr@k": round(float(summary.get("mrr@k") or 0), 3),
                    "confidence": round(float(summary.get("confidence") or 0), 3),
                    "p50_ms": round(float(summary.get("latency_ms_p50") or 0), 1),
                }
            )
        st.dataframe(rows, use_container_width=True)

with mon_tab:
    new_run_id = st.text_input("Optional new ingest_run_id (compare new vs index)")
    if st.button("Run monitor", type="primary"):
        body = {"new_run_id": new_run_id.strip()} if new_run_id.strip() else {}
        try:
            with st.spinner("Computing drift + judge…"):
                res = api.monitor_run(body)
        except APIError as exc:
            st.error(str(exc))
        else:
            st.subheader("Latest snapshot")
            st.json(res)

    st.subheader("Recent monitoring snapshots")
    files = list_json_files(data_dir() / "monitoring", limit=20)
    if not files:
        st.caption("No snapshots yet under data/monitoring/.")
    else:
        rows = []
        for p in files:
            doc = read_json(p) or {}
            drift = doc.get("drift") or {}
            judge = doc.get("judge") or {}
            rows.append(
                {
                    "file": p.name,
                    "drift_score": round(float(drift.get("score") or 0), 3),
                    "judge_avg": round(float(judge.get("avg") or 0), 3),
                    "judge_n": judge.get("n", 0),
                    "ts": doc.get("ts", ""),
                }
            )
        st.dataframe(rows, use_container_width=True)
