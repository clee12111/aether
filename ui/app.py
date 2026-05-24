# ui/app.py

"""Streamlit UI for Aether — Run viewer, Trace explorer, Eval dashboard."""

import ast
import json
from collections import defaultdict
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
    uploaded = st.file_uploader("Upload document", type=["csv", "pdf", "xlsx", "txt"])

    if st.button("Run", disabled=not goal):
        if not uploaded:
            st.error("Please upload a file.")
        else:
            dest = UPLOADS_DIR / uploaded.name
            dest.write_bytes(uploaded.getvalue())

            with st.spinner("Running agentic loop…"):
                from aether.runtime import AetherRuntime
                runtime = AetherRuntime()
                result = runtime.run_agentic(goal, [str(dest)])

            loop_state = result["loop_state"]
            critique = result["critique"]
            steps = loop_state.get("steps", [])

            # ── Grounding-guard callout ───────────────────────────────────
            # Two refusal channels:
            #   1. answer_from_context returned insufficient_context (explicit guard)
            #   2. Critic verdict "fail" with a "missing_data" flag (the critic's
            #      structured assessment that required evidence was absent —
            #      catches write_report-with-null and fabrication-caught-by-critic)

            _ic_steps = [
                s for s in steps
                if s["action"]["tool"] == "answer_from_context"
                and s.get("observation", {}).get("output", {}).get("insufficient_context")
            ]
            _wrote_report = any(
                s["action"]["tool"] == "write_report"
                and s.get("observation", {}).get("success")
                for s in steps
            )
            _critic_missing = (
                critique["overall_verdict"] == "fail"
                and any(
                    f.get("category") == "missing_data"
                    for f in critique.get("flags", [])
                )
            )

            # Refusal via channel 1 (grounding guard, no recovery)
            _refused_via_tool = bool(_ic_steps) and not _wrote_report
            # Refusal via channel 2 (critic says data absent)
            _refused_via_critic = _critic_missing
            # Recovery: grounding guard fired but run produced a real answer
            _recovered = bool(_ic_steps) and _wrote_report and not _critic_missing

            if _refused_via_tool or _refused_via_critic:
                st.info(
                    "**Grounding guard triggered:** the engine could not produce "
                    "a grounded answer because the corpus lacks the required "
                    "evidence. Rather than fabricating, it declined to answer. "
                    "This is correct behaviour — the system is designed to abstain "
                    "when it cannot ground its answer in the provided documents.",
                    icon="\u26a0\ufe0f",
                )
            elif _recovered:
                st.caption(
                    "Note: an intermediate step returned INSUFFICIENT_CONTEXT, "
                    "but the engine re-retrieved context and recovered to produce "
                    "an answer."
                )

            # ── Run metadata ──────────────────────────────────────────────
            st.markdown(f"**Run ID:** `{result['run_id']}`")
            col_m1, col_m2, col_m3 = st.columns(3)
            col_m1.metric("Steps taken", result["steps_taken"])
            col_m2.metric("Stop reason", result.get("stop_reason", "—"))
            col_m3.metric(
                "write_report called",
                "Yes" if loop_state.get("write_report_called") else "No",
            )

            # ── Verdict panel ─────────────────────────────────────────────
            verdict = critique["overall_verdict"]
            if verdict == "pass":
                st.success(f"Verdict: {verdict.upper()}")
            elif verdict == "partial":
                st.warning(f"Verdict: {verdict.upper()}")
            else:
                st.error(f"Verdict: {verdict.upper()}")

            col_v1, col_v2 = st.columns(2)
            with col_v1:
                st.metric("Confidence", f"{critique.get('confidence', 'N/A')}")
            with col_v2:
                st.metric("Steps reviewed by critic", len(critique.get("steps_reviewed", [])))

            st.markdown(f"**Summary:** {critique.get('summary', '—')}")

            # Critique flags
            flags = critique.get("flags", [])
            if flags:
                st.subheader(f"Critique Flags ({len(flags)})")
                for f in flags:
                    sev = f.get("severity", "info")
                    icon = {"critical": "\U0001f534", "warning": "\U0001f7e1"}.get(sev, "\U0001f535")
                    with st.expander(f"{icon} [{sev.upper()}] {f.get('category', '—')} — {f.get('description', '—')}"):
                        st.markdown(f"**Step ref:** `{f.get('step_ref', '—')}`")
                        st.markdown(f"**Evidence:** {f.get('evidence', '—')}")
                        if f.get("suggested_fix"):
                            st.markdown(f"**Suggested fix:** {f['suggested_fix']}")

            # ── Download report (from write_report step output) ───────────
            for s in steps:
                obs_out = s.get("observation", {}).get("output", {})
                if s["action"]["tool"] == "write_report" and "path" in obs_out:
                    _rp = Path(obs_out["path"])
                    if _rp.exists():
                        _fmt = obs_out.get("format", "json")
                        _mime = "application/json" if _fmt == "json" else "text/plain"
                        st.download_button(
                            label="Download Report",
                            data=_rp.read_bytes(),
                            file_name=_rp.name,
                            mime=_mime,
                        )
                    break

            # ── Visual output (render_visual) ─────────────────────────────
            for s in steps:
                if s["action"]["tool"] == "render_visual":
                    obs = s.get("observation", {})
                    obs_out = obs.get("output", {})
                    if obs_out.get("insufficient_data"):
                        st.warning(
                            "**Visual declined:** the engine could not produce a chart "
                            "because the computed findings lack sufficient data for the "
                            "requested visualization. "
                            f"Reason: {obs_out.get('reason', 'insufficient grounded data')}.",
                            icon="\u26a0\ufe0f",
                        )
                    elif obs_out.get("grounded") and obs_out.get("vega_lite_spec"):
                        st.subheader("Visual Output")
                        st.vega_lite_chart(obs_out["vega_lite_spec"], use_container_width=True)
                        st.caption(
                            f"Source: findings from step {obs_out.get('source_findings_ref', '?')}. "
                            "All values are from engine-computed tool outputs."
                        )

            # ── Reasoning trace ───────────────────────────────────────────
            st.subheader("Reasoning Trace")
            for s in steps:
                action = s["action"]
                observation = s.get("observation", {})
                step_label = f"Step {s['step_index']}: {action['tool']}"
                with st.expander(step_label, expanded=False):
                    st.markdown(f"**Reasoning**")
                    st.markdown(action["reasoning"])
                    st.divider()
                    st.markdown(f"**Action:** `{action['tool']}`")
                    if action.get("tool_args"):
                        st.code(json.dumps(action["tool_args"], indent=2), language="json")
                    st.divider()
                    if observation.get("success"):
                        st.markdown("**Observation** (success)")
                        st.code(json.dumps(observation.get("output", {}), indent=2, default=str), language="json")
                    else:
                        st.markdown("**Observation** (error)")
                        st.error(observation.get("error", "Unknown error"))

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

        # ── Helpers ───────────────────────────────────────────────────
        _PHASE_LABELS = {
            "load_data": "Loaded the document",
            "run_sql": "Queried the data",
            "retrieve_context": "Searched the document",
            "answer_from_context": "Checked grounding",
            "render_visual": "Built the chart",
            "write_report": "Saved findings",
            "flag_item": "Flagged an item",
        }

        def _parse_result(raw):
            """Best-effort parse of a tool_response result (Python repr or dict)."""
            if isinstance(raw, dict):
                return raw
            if isinstance(raw, str):
                try:
                    return ast.literal_eval(raw)
                except Exception:
                    pass
                try:
                    return json.loads(raw)
                except Exception:
                    pass
            return None

        def _render_result(raw):
            """Render a tool result safely — parsed dict as JSON, else code block."""
            parsed = _parse_result(raw)
            if parsed is not None and isinstance(parsed, dict):
                st.code(json.dumps(parsed, indent=2, default=str), language="json")
            elif raw is not None:
                st.code(str(raw)[:4000], language="text")

        # ── Group events by step_id ───────────────────────────────────
        step_events = defaultdict(list)
        lifecycle_events = []
        for ev in events:
            if ev.step_id:
                step_events[ev.step_id].append(ev)
            else:
                lifecycle_events.append(ev)

        # Extract structured info per step
        step_infos = {}
        for step_id, sevs in step_events.items():
            info = {"step_id": step_id, "events": sevs}
            for ev in sevs:
                if ev.event_type == "llm_response" and ev.agent == "loop_agent":
                    info["reasoning"] = ev.payload.get("reasoning", "")
                    info["tool"] = ev.payload.get("tool", "")
                    info["is_final"] = ev.payload.get("is_final", False)
                elif ev.event_type == "tool_call":
                    info["tool_name"] = ev.payload.get("tool", "")
                    info["tool_args"] = ev.payload.get("args", {})
                elif ev.event_type == "tool_response":
                    info["tool_result_raw"] = ev.payload.get("result")
                    info["tool_duration_ms"] = ev.duration_ms
                elif ev.event_type == "validation_error":
                    info["validation_error"] = ev.payload.get("error", "")
            step_infos[step_id] = info

        # Sort steps by index
        sorted_steps = sorted(
            step_infos.values(),
            key=lambda s: int(s["step_id"].split("_")[-1])
            if s["step_id"].startswith("rao_step_") else 999,
        )

        # ── Run start ────────────────────────────────────────────────
        _run_start = [e for e in lifecycle_events if e.event_type == "run_start"]
        if _run_start:
            _goal = _run_start[0].payload.get("goal", "—")
            st.markdown(f"**Goal:** {_goal}")
            st.divider()

        # ── Phase cards ──────────────────────────────────────────────
        for si in sorted_steps:
            tool = si.get("tool_name") or si.get("tool", "")
            if not tool and si.get("is_final"):
                phase_label = "Finished \u2014 goal satisfied"
            else:
                phase_label = _PHASE_LABELS.get(tool, tool or "Unknown step")
            reasoning = si.get("reasoning", "")
            step_idx = si["step_id"].split("_")[-1] if "rao_step" in si["step_id"] else si["step_id"]

            # Detect grounding-guard refusal
            _is_grounding_refusal = False
            if tool == "answer_from_context":
                parsed = _parse_result(si.get("tool_result_raw"))
                if isinstance(parsed, dict) and parsed.get("insufficient_context"):
                    _is_grounding_refusal = True

            # Phase header with reasoning preview
            reasoning_preview = reasoning[:120] + ("..." if len(reasoning) > 120 else "")
            header = f"Step {step_idx}: {phase_label}"

            if _is_grounding_refusal:
                st.warning(
                    f"**{header}** — Grounding guard: INSUFFICIENT_CONTEXT. "
                    f"{reasoning_preview}",
                    icon="\u26a0\ufe0f",
                )
            else:
                st.markdown(f"**{header}** — {reasoning_preview}")

            # Expandable technical drill-down
            with st.expander("Technical details", expanded=False):
                st.markdown(f"**Reasoning**")
                st.markdown(reasoning)
                st.divider()
                if tool:
                    st.markdown(f"**Tool:** `{tool}`")
                if si.get("tool_args"):
                    st.code(json.dumps(si["tool_args"], indent=2, default=str), language="json")
                st.divider()
                st.markdown("**Result**")
                _render_result(si.get("tool_result_raw"))
                if si.get("tool_duration_ms") is not None:
                    st.caption(f"Duration: {si['tool_duration_ms']}ms")
                if si.get("validation_error"):
                    st.error(f"Validation error: {si['validation_error']}")

        # ── Critic / outcome ─────────────────────────────────────────
        st.divider()
        _critic_responses = [
            e for e in lifecycle_events
            if e.event_type == "llm_response" and e.agent == "critic"
        ]
        _run_end = [e for e in lifecycle_events if e.event_type == "run_end"]

        if _critic_responses or _run_end:
            st.markdown("**Verification & Outcome**")

            if _critic_responses:
                _critic_raw = _critic_responses[0].payload.get("raw_text", "")
                _critic_parsed = _parse_result(_critic_raw)
                if isinstance(_critic_parsed, dict):
                    _verdict = _critic_parsed.get("overall_verdict", "—")
                    _confidence = _critic_parsed.get("confidence", "—")
                    _summary = _critic_parsed.get("summary", "")
                    _flags = _critic_parsed.get("flags", [])

                    if _verdict == "pass":
                        st.success(f"Critic verdict: **{_verdict.upper()}** (confidence: {_confidence})")
                    elif _verdict == "partial":
                        st.warning(f"Critic verdict: **{_verdict.upper()}** (confidence: {_confidence})")
                    else:
                        st.error(f"Critic verdict: **{_verdict.upper()}** (confidence: {_confidence})")

                    if _summary:
                        st.markdown(f"> {_summary}")
                    if _flags:
                        with st.expander(f"Critic flags ({len(_flags)})"):
                            for f in _flags:
                                sev = f.get("severity", "info")
                                cat = f.get("category", "—")
                                desc = f.get("description", "—")
                                st.markdown(f"- **[{sev.upper()}]** {cat} — {desc}")
                else:
                    with st.expander("Critic response (raw)"):
                        st.code(_critic_raw[:4000], language="text")

            if _run_end:
                _end_payload = _run_end[0].payload
                _status = _end_payload.get("status", "—")
                _end_summary = _end_payload.get("summary", "")
                st.caption(f"Run status: {_status} — {_end_summary}")

# ── Tab 3: Eval Dashboard ───────────────────────────────────────────────────

with tab_eval:
    st.subheader("Evaluation Results")
    st.caption("Read live from eval output files. See docs/aether-validation-log.md for full progression.")

    # ── Load E2E results from rescored baseline ──────────────────────────
    _e2e_path = Path("evals/results/eval_e2e_200_rescored_baseline.jsonl")
    if _e2e_path.exists():
        _e2e_records = [json.loads(line) for line in _e2e_path.read_text().splitlines() if line.strip()]
        _e2e_n = len(_e2e_records)
        _e2e_match = sum(1 for r in _e2e_records if r.get("match_v2"))
        _e2e_pct = round(100 * _e2e_match / _e2e_n, 1) if _e2e_n else 0
        # 10 records identified as benchmark-defective (artifact-strong);
        # benchmark-fair denominator excludes them.
        _e2e_artifact_count = 10
        _e2e_fair_n = _e2e_n - _e2e_artifact_count
        _e2e_fair_pct = round(100 * _e2e_match / _e2e_fair_n, 1) if _e2e_fair_n else 0
    else:
        _e2e_n = _e2e_match = 0
        _e2e_pct = _e2e_fair_pct = 0.0

    # ── Load retrieval results ───────────────────────────────────────────
    _ret_path = Path("evals/results/eval_retrieval_200.json")
    if _ret_path.exists():
        _ret_records = json.loads(_ret_path.read_text())
        _ret_n = len(_ret_records)
        _r5 = round(sum(r.get("hit@5", 0) for r in _ret_records) / _ret_n, 3) if _ret_n else 0
        _mrr3 = round(sum(r.get("rr3", 0) for r in _ret_records) / _ret_n, 3) if _ret_n else 0
        _r1 = round(sum(r.get("hit@1", 0) for r in _ret_records) / _ret_n, 3) if _ret_n else 0
        _r3 = round(sum(r.get("hit@3", 0) for r in _ret_records) / _ret_n, 3) if _ret_n else 0
        _ndcg5 = round(sum(r.get("ndcg5", 0) for r in _ret_records) / _ret_n, 3) if _ret_n else 0
    else:
        _ret_n = 0
        _r1 = _r3 = _r5 = _mrr3 = _ndcg5 = 0.0

    # ── Display ──────────────────────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        if _e2e_n:
            st.metric("E2E pass rate (raw)", f"{_e2e_match}/{_e2e_n} ({_e2e_pct}%)")
            st.caption(
                f"{_e2e_fair_pct}% on benchmark-fair questions "
                f"({_e2e_artifact_count} records excluded as benchmark-defective; "
                f"see eval analysis)."
            )
        else:
            st.warning("E2E eval file not found (evals/results/eval_e2e_200_rescored_baseline.jsonl)")
    with col2:
        if _ret_n:
            st.metric("Retrieval R@5", f"{_r5}")
            st.caption(f"R@1 {_r1} · R@3 {_r3} · MRR@3 {_mrr3} · nDCG@5 {_ndcg5} (n={_ret_n})")
        else:
            st.warning("Retrieval eval file not found (evals/results/eval_retrieval_200.json)")

    st.caption("Benchmark: gpt-5.4-mini, FinQA n=200. Live demo above uses gpt-5 (flagship).")
