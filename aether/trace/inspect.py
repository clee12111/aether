# aether/trace/inspect.py
"""
Read-only trace inspection CLI for Aether.

Works during a live run (opens SQLite in immutable read-only mode via URI).

MODE A — single run, step by step:
    python -m aether.trace.inspect --run <run_id>
    python -m aether.trace.inspect --latest

MODE B — failure landscape across a suite batch:
    python -m aether.trace.inspect --suite latest [--n 15] --failures
    python -m aether.trace.inspect --suite <run_id> [--failures]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ── Colour helpers (disabled when stdout is not a TTY or piped) ───────────────

_USE_COLOR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def red(t: str) -> str:    return _c("31", t)
def yellow(t: str) -> str: return _c("33", t)
def green(t: str) -> str:  return _c("32", t)
def cyan(t: str) -> str:   return _c("36", t)
def bold(t: str) -> str:   return _c("1",  t)
def dim(t: str) -> str:    return _c("2",  t)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _open_ro(db_path: str) -> sqlite3.Connection:
    """Open the trace DB immutable read-only; safe during concurrent writes."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    except sqlite3.OperationalError:
        # Fallback: plain open (slightly less safe during heavy write bursts)
        conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_run(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM trace_events WHERE run_id=? ORDER BY created_at ASC, rowid ASC",
        (run_id,),
    ).fetchall()


def _latest_run_id(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT run_id FROM trace_events ORDER BY created_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    if not row:
        sys.exit("trace store is empty — no runs found")
    return row[0]


def _runs_summary(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    rows = conn.execute("""
        SELECT run_id,
               MIN(created_at) AS started_at,
               MAX(created_at) AS last_event_at,
               COUNT(*)        AS event_count
        FROM trace_events
        GROUP BY run_id
        ORDER BY started_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return [dict(r) for r in rows]


# ── Parsing helpers ────────────────────────────────────────────────────────────

def _payload(row: sqlite3.Row) -> dict[str, Any]:
    raw = row["payload"]
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return raw or {}


def _ts(row: sqlite3.Row) -> str:
    return str(row["created_at"])[11:19]


def _trunc(s: str, n: int) -> str:
    s = str(s).replace("\n", " ").strip()
    return s[:n] + "…" if len(s) > n else s


def _parse_action_from_raw(raw: str) -> dict:
    """Best-effort: extract JSON object from a model response."""
    import re
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        text = m.group(1).strip()
    start = text.find("{")
    if start == -1:
        return {}
    depth, end = 0, -1
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# MODE A — single run, step by step
# ══════════════════════════════════════════════════════════════════════════════

def mode_a(run_id: str, db_path: str) -> None:
    conn = _open_ro(db_path)
    events = _fetch_run(conn, run_id)
    conn.close()

    if not events:
        sys.exit(f"no events for run_id={run_id}")

    # Partition into lifecycle vs per-step
    lifecycle: list[sqlite3.Row] = []
    steps: dict[str, list[sqlite3.Row]] = defaultdict(list)
    step_order: list[str] = []

    for ev in events:
        sid = ev["step_id"]
        if not sid:
            lifecycle.append(ev)
        else:
            if sid not in steps:
                step_order.append(sid)
            steps[sid].append(ev)

    # ── Header ──────────────────────────────────────────────────────────────

    print(bold(f"\n{'═' * 72}"))
    print(bold(f"  RUN  {run_id}"))
    print(bold(f"{'═' * 72}"))

    run_start = next((e for e in lifecycle if e["event_type"] == "run_start"), None)
    run_end   = next((e for e in lifecycle if e["event_type"] == "run_end"),   None)

    if run_start:
        p = _payload(run_start)
        print(f"\n{bold('GOAL')}  {p.get('goal', '?')}")
        print(f"{dim('mode:')} {p.get('mode', 'one-shot')}   "
              f"{dim('started:')} {_ts(run_start)}\n")

    # ── Steps ────────────────────────────────────────────────────────────────

    # Track (tool, frozen_args) → step_id for stall detection
    seen_calls: dict[str, str] = {}

    for sid in step_order:
        evs = steps[sid]

        llm_calls  = [e for e in evs if e["event_type"] == "llm_call"]
        llm_resps  = [e for e in evs if e["event_type"] == "llm_response"]
        val_errors = [e for e in evs if e["event_type"] == "validation_error"]
        tool_calls = [e for e in evs if e["event_type"] == "tool_call"]
        tool_resps = [e for e in evs if e["event_type"] == "tool_response"]

        max_attempt = max((e["attempt"] for e in evs), default=1)
        step_label  = sid  # e.g. "rao_step_3"

        # ── Step header flags ────────────────────────────────────────────────
        flags: list[str] = []
        if max_attempt > 1:
            flags.append(yellow(f"RETRY×{max_attempt}"))
        if val_errors:
            flags.append(red(f"INVALID-JSON×{len(val_errors)}"))
        flag_str = ("  " + "  ".join(flags)) if flags else ""

        print(bold(f"┌─ {step_label}") + flag_str)

        # ── LLM timing ──────────────────────────────────────────────────────
        total_llm_ms = sum(
            (e["duration_ms"] or 0) for e in llm_resps
        )
        n_llm = len(llm_calls)
        if n_llm:
            print(f"│  {dim('llm')}  calls={n_llm}  "
                  f"wall={total_llm_ms / 1000:.1f}s"
                  f"  in_tok={sum(e['input_tokens'] or 0 for e in llm_resps)}"
                  f"  out_tok={sum(e['output_tokens'] or 0 for e in llm_resps)}")

        # ── Validation errors ────────────────────────────────────────────────
        for ve in val_errors:
            p   = _payload(ve)
            err = _trunc(p.get("error", ve["error"] or ""), 110)
            raw = _trunc(p.get("raw", p.get("raw_response", "")), 90)
            print(f"│  {red('✗ invalid-json')}  {err}")
            if raw:
                print(f"│      {dim('raw: ' + raw)}")

        # ── Reasoning + is_final from the last good LLM response ────────────
        for lr in llm_resps:
            raw_text = _payload(lr).get("raw_text", "")
            action   = _parse_action_from_raw(raw_text)
            if action.get("reasoning"):
                print(f"│  {dim('reasoning:')} {_trunc(action['reasoning'], 110)}")
            if action.get("is_final"):
                print(f"│  {green('→ is_final=true')}")

        # ── Tool calls + stall detection ─────────────────────────────────────
        for tc in tool_calls:
            p         = _payload(tc)
            tool_name = p.get("tool", "?")
            args      = p.get("args", {})
            args_json = json.dumps(args, sort_keys=True)
            call_key  = f"{tool_name}|{args_json}"

            stall_flag = ""
            if call_key in seen_calls:
                stall_flag = "  " + red(f"⚑ STALL (identical to {seen_calls[call_key]})")
            else:
                seen_calls[call_key] = sid

            print(f"│  {cyan('tool')}  {bold(tool_name)}  "
                  f"{dim(_trunc(args_json, 120))}{stall_flag}")

        # ── Tool responses ───────────────────────────────────────────────────
        for tr in tool_resps:
            p   = _payload(tr)
            err = tr["error"]
            dur = tr["duration_ms"]
            if err:
                print(f"│  {red('✗ err')}  {_trunc(err, 110)}")
            else:
                result = _trunc(str(p.get("result", "")), 120)
                dur_str = f"  {dim(str(dur) + 'ms')}" if dur else ""
                print(f"│  {green('✓ ok')}  {result}{dur_str}")

        print("│")

    # ── Run end / stop reason ────────────────────────────────────────────────
    if run_end:
        p       = _payload(run_end)
        status  = p.get("status", "?")
        summary = p.get("summary", "")
        color   = green if status == "success" else red
        print(color(bold(f"└─ RUN END  {status}  {summary}")))
    else:
        print(yellow(bold("└─ RUN END  (not recorded — killed or still running)")))

    # ── Token / timing summary ───────────────────────────────────────────────
    in_tok  = sum(e["input_tokens"]  or 0 for e in events if e["event_type"] == "llm_response")
    out_tok = sum(e["output_tokens"] or 0 for e in events if e["event_type"] == "llm_response")
    llm_ms  = sum(e["duration_ms"]   or 0 for e in events if e["event_type"] == "llm_response")
    n_val   = sum(1 for e in events if e["event_type"] == "validation_error")

    print(f"\n{dim('tokens:')} in={in_tok} out={out_tok}  "
          f"{dim('llm wall:')} {llm_ms / 1000:.1f}s  "
          f"{dim('validation_errors:')} {n_val}\n")


# ══════════════════════════════════════════════════════════════════════════════
# MODE B — failure landscape across a suite batch
# ══════════════════════════════════════════════════════════════════════════════

# Bucket definitions — checked in priority order
_BUCKETS = [
    ("invalid_json",         "Invalid JSON from model",         "red"),
    ("max_steps",            "Hit max_steps (loop stall)",       "red"),
    ("tool_error",           "Tool execution error",             "yellow"),
    ("missing_write_report", "write_report never called",        "yellow"),
    ("verdict_fail",         "Critic verdict = fail",            "red"),
    ("verdict_partial",      "Critic verdict = partial",         "yellow"),
    ("clean_pass",           "Clean pass",                       "green"),
    ("no_run_end",           "Killed / no run_end recorded",     "dim"),
]


def _classify(events: list[sqlite3.Row]) -> list[tuple[str, str]]:
    """Return list of (bucket_id, evidence_str) for a run.  May have multiple."""
    results: list[tuple[str, str]] = []

    n_val_errors  = sum(1 for e in events if e["event_type"] == "validation_error")
    tool_calls    = [e for e in events if e["event_type"] == "tool_call"]
    tool_errors   = [e for e in events if e["event_type"] == "tool_response" and e["error"]]
    tools_used    = {_payload(e).get("tool") for e in tool_calls}
    run_end       = next((e for e in events if e["event_type"] == "run_end"), None)

    # Parse stop_reason and verdict from run_end summary
    # Format: "pass, 3 step(s), is_final"  or  "fail, 7 step(s), max_steps"
    stop_reason = None
    verdict     = None
    if run_end:
        summary = _payload(run_end).get("summary", "")
        parts   = [p.strip() for p in summary.split(",")]
        if parts:
            verdict     = parts[0]
            stop_reason = parts[-1].strip()

    # Critique event carries the authoritative verdict
    crit_ev = next((e for e in events if e["event_type"] == "critique"), None)
    if crit_ev:
        verdict = _payload(crit_ev).get("overall_verdict", verdict)

    if n_val_errors:
        first_ve = next(e for e in events if e["event_type"] == "validation_error")
        p        = _payload(first_ve)
        err      = _trunc(p.get("error", first_ve["error"] or ""), 90)
        results.append(("invalid_json", f"step={first_ve['step_id']}  {err}"))

    if stop_reason == "max_steps":
        results.append(("max_steps", f"verdict={verdict}"))

    if tool_errors:
        e0 = tool_errors[0]
        results.append(("tool_error",
                         f"step={e0['step_id']}  "
                         f"tool={_payload(e0).get('tool')}  "
                         f"err={_trunc(str(e0['error']), 60)}"))

    if "write_report" not in tools_used:
        results.append(("missing_write_report", "write_report never dispatched"))

    if verdict == "fail":
        results.append(("verdict_fail", "critic=fail"))
    elif verdict == "partial":
        results.append(("verdict_partial", "critic=partial"))

    if not run_end:
        results.append(("no_run_end", "process killed or still running"))

    if not results:
        results.append(("clean_pass", f"verdict={verdict} stop={stop_reason}"))

    return results


def mode_b(anchor: str, n: int, db_path: str, failures_only: bool) -> None:
    conn = _open_ro(db_path)
    all_runs = _runs_summary(conn)
    conn.close()

    if not all_runs:
        sys.exit("trace store is empty")

    # Select the run batch
    if anchor == "latest":
        # Most recent N runs by start time
        batch = all_runs[:n]
    else:
        # anchor is a run_id — collect all runs within ±2h of its start time
        anchor_row = next((r for r in all_runs if r["run_id"] == anchor), None)
        if not anchor_row:
            # Try prefix match
            anchor_row = next((r for r in all_runs if r["run_id"].startswith(anchor)), None)
        if not anchor_row:
            sys.exit(f"run_id not found: {anchor}")

        raw_ts = anchor_row["started_at"]
        if raw_ts.endswith("Z"):
            raw_ts = raw_ts[:-1] + "+00:00"
        anchor_dt = datetime.fromisoformat(raw_ts)
        window    = timedelta(hours=2)

        batch = []
        for r in all_runs:
            ts = r["started_at"]
            if ts.endswith("Z"):
                ts = ts[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(ts)
            except ValueError:
                continue
            if abs((dt - anchor_dt).total_seconds()) <= window.total_seconds():
                batch.append(r)

    if not batch:
        sys.exit("no runs matched the selection criteria")

    # Classify each run
    bucket_entries: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    # bucket_id -> [(run_id_short, goal_short, evidence)]

    for meta in batch:
        rid  = meta["run_id"]
        conn = _open_ro(db_path)
        evs  = _fetch_run(conn, rid)
        conn.close()

        if not evs:
            continue

        run_start = next((e for e in evs if e["event_type"] == "run_start"), None)
        goal      = _trunc(_payload(run_start).get("goal", "?"), 65) if run_start else "?"

        for bucket_id, evidence in _classify(evs):
            bucket_entries[bucket_id].append((rid[:8], goal, evidence))

    # ── Print ────────────────────────────────────────────────────────────────
    total = len(batch)
    print(bold(f"\n{'═' * 72}"))
    print(bold(f"  SUITE FAILURE LANDSCAPE  ({total} runs, anchor={anchor})"))
    print(bold(f"{'═' * 72}"))

    _color_fn = {"red": red, "yellow": yellow, "green": green, "dim": dim}

    for bucket_id, label, color_name in _BUCKETS:
        entries = bucket_entries.get(bucket_id, [])
        if not entries:
            continue
        if failures_only and bucket_id == "clean_pass":
            continue

        color_fn = _color_fn.get(color_name, str)
        count    = len(entries)

        print(f"\n  {color_fn(bold(label))}  {bold(str(count))} run(s)")

        for run_short, goal, evidence in entries:
            print(f"    {dim(run_short)}  {goal}")
            if evidence:
                print(f"    {dim(' ' * 9 + '↳ ' + evidence)}")

    # ── Pass rate footer ─────────────────────────────────────────────────────
    n_clean = len(bucket_entries.get("clean_pass", []))
    n_issues = total - n_clean
    pct = n_clean * 100 // total if total else 0
    print(f"\n  {bold('pass rate:')} {green(str(n_clean))}/{total} ({pct}%)  "
          f"{red(str(n_issues))} with issues\n")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m aether.trace.inspect",
        description="Read-only Aether trace inspector. Safe during live runs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m aether.trace.inspect --latest
  python -m aether.trace.inspect --run f89ff91f-f0f4-46ae-b9ab-77dbe228da00
  python -m aether.trace.inspect --suite latest --failures
  python -m aether.trace.inspect --suite f89ff91f --n 10
""",
    )

    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--run",    metavar="RUN_ID",
                       help="Mode A: inspect one run by id")
    group.add_argument("--latest", action="store_true",
                       help="Mode A: inspect the most recent run")
    group.add_argument("--suite",  metavar="ANCHOR",
                       help="Mode B: failure landscape for a suite batch "
                            "(use 'latest' or a run_id as anchor)")

    p.add_argument("--n", type=int, default=15,
                   help="Mode B: max runs to include (default 15)")
    p.add_argument("--failures", action="store_true",
                   help="Mode B: omit clean passes from output")
    p.add_argument("--db", metavar="PATH",
                   help="Path to trace DB (default: from aether config)")

    return p


def main() -> None:
    # Force UTF-8 on Windows (default cp1252 can't encode box-drawing chars)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args   = _build_parser().parse_args()
    # Resolve DB path
    if args.db:
        db_path = args.db
    else:
        try:
            from aether.config import settings
            db_path = str(settings.db_path)
        except Exception:
            db_path = "./aether_trace.db"

    if not Path(db_path).exists():
        sys.exit(f"trace DB not found: {db_path}")

    if args.suite:
        mode_b(anchor=args.suite, n=args.n, db_path=db_path,
               failures_only=args.failures)
    else:
        conn   = _open_ro(db_path)
        run_id = args.run if args.run else _latest_run_id(conn)
        conn.close()
        mode_a(run_id=run_id, db_path=db_path)


if __name__ == "__main__":
    main()
