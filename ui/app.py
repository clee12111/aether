# ui/app.py

"""Streamlit UI for Aether — Run viewer, Trace explorer, Eval dashboard."""

import json
from pathlib import Path

import streamlit as st

from aether.config import settings
from aether.trace.store import TraceStore

UPLOADS_DIR = Path("data/uploads")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

st.set_page_config(page_title="Aether", layout="wide")
st.title("Aether — Workflow Reasoning Engine")

tab_run, tab_trace, tab_eval = st.tabs(["Run Aether", "Trace Explorer", "Eval Dashboard"])

# ── Tab 1: Run Aether ────────────────────────────────────────────────────────

with tab_run:
    goal = st.text_area("Goal", placeholder="e.g. Flag any partner whose distribution deviates from ownership by >5%")
    uploaded = st.file_uploader("Upload document", type=["csv", "pdf", "xlsx"])

    if st.button("Run", disabled=not goal):
        if not uploaded:
            st.error("Please upload a file.")
        else:
            dest = UPLOADS_DIR / uploaded.name
            dest.write_bytes(uploaded.getvalue())

            with st.spinner("Running Aether pipeline…"):
                from aether.runtime import AetherRuntime
                runtime = AetherRuntime()
                result = runtime.run(goal, [str(dest)])

            st.markdown(f"**Run ID:** `{result['run_id']}`")

            # Verdict banner
            critique = result["critique"]
            verdict = critique["overall_verdict"]
            if verdict == "pass":
                st.success(f"Verdict: {verdict.upper()}")
            elif verdict == "partial":
                st.warning(f"Verdict: {verdict.upper()}")
            else:
                st.error(f"Verdict: {verdict.upper()}")

            col1, col2 = st.columns(2)
            with col1:
                st.metric("Confidence", f"{critique.get('confidence', 'N/A')}")
            with col2:
                st.metric("Revisions", result.get("revisions", 0))

            st.markdown(f"**Summary:** {critique.get('summary', '—')}")

            # Download button for the final report file
            _report_path = None
            _report_fmt = "json"
            _executor_output = result.get("output", {})
            for _step_id, _step_val in _executor_output.items():
                if isinstance(_step_val, dict) and "path" in _step_val and "format" in _step_val:
                    _report_path = _step_val["path"]
                    _report_fmt = _step_val.get("format", "json")
            if _report_path:
                _rp = Path(_report_path)
                if _rp.exists():
                    _mime = "application/json" if _report_fmt == "json" else "text/plain"
                    st.download_button(
                        label="Download Report",
                        data=_rp.read_bytes(),
                        file_name=_rp.name,
                        mime=_mime,
                    )
                else:
                    import logging
                    logging.getLogger(__name__).warning("Report file not found: %s", _report_path)

            # Plan steps
            st.subheader("Plan")
            plan = result.get("plan", {})
            for step in plan.get("steps", []):
                with st.expander(f"Step: {step.get('name', step.get('step_id', '?'))}"):
                    st.write(step.get("description", ""))
                    st.caption(f"Tool: `{step.get('tool', '—')}`")

            # Executor output
            st.subheader("Executor Results")
            output = result.get("output", {})
            for step_id, val in output.items():
                with st.expander(f"Step `{step_id}`"):
                    st.json(val if isinstance(val, (dict, list)) else {"result": val})

            # Critique flags
            flags = critique.get("flags", [])
            if flags:
                st.subheader(f"Flags ({len(flags)})")
                for f in flags:
                    sev = f.get("severity", "info")
                    icon = {"critical": "🔴", "warning": "🟡"}.get(sev, "🔵")
                    with st.expander(f"{icon} {f.get('description', '—')}"):
                        st.json(f)

# ── Tab 2: Trace Explorer ────────────────────────────────────────────────────

with tab_trace:
    store = TraceStore(settings.db_path)
    runs = store.get_all_runs()

    if not runs:
        st.info("No runs recorded yet.")
    else:
        labels = [f"{r['run_id'][:8]}… — {r.get('started_at', '?')}" for r in runs]
        idx = st.selectbox("Select run", range(len(runs)), format_func=lambda i: labels[i])
        run_meta = runs[idx]
        run_id = run_meta["run_id"]

        col1, col2, col3 = st.columns(3)
        col1.metric("Total tokens", f"{run_meta.get('total_input_tokens', 0) + run_meta.get('total_output_tokens', 0):,}")
        col2.metric("Duration (ms)", f"{run_meta.get('total_duration_ms', 0):,}")
        col3.metric("Events", run_meta.get("event_count", 0))

        events = store.get_run_events(run_id)
        for ev in events:
            label = f"[{ev.agent}] {ev.event_type}"
            if ev.duration_ms is not None:
                label += f" ({ev.duration_ms}ms)"
            with st.expander(label):
                st.caption(f"event_id: {ev.event_id} | status: {ev.error or 'ok'}")
                st.json(ev.payload)

# ── Tab 3: Eval Dashboard ───────────────────────────────────────────────────

with tab_eval:
    st.subheader("Evaluation Results")
    st.caption("Results as of most recent eval run. See docs/eval_analysis.md for detailed breakdown.")

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Retrieval precision@5", "24/25 (96%)", delta="passing")
    with col2:
        st.metric("E2E pass rate", "11/15 (73%)")
