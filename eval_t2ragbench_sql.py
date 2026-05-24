# eval_t2ragbench_sql.py
#
# T2-RAGBench FinQA 15-record eval WITH SQL-routing path.
#
# For each record:
#   - If the `table` field parses cleanly → write a CSV alongside the txt
#     and pass BOTH paths to the runtime (SQL path)
#   - If table is absent or unparseable → txt only (text path / fallback)
#
# Re-scores with Number-Match and prints before/after vs eval_t2ragbench_15.json.
#
# Usage:
#   uv run python eval_t2ragbench_sql.py

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.WARNING, format="[%(levelname)s %(name)s] %(message)s",
                    stream=sys.stdout)
for lib in ("chromadb", "sentence_transformers", "httpx", "httpcore",
            "openai", "hpack", "h2", "urllib3", "huggingface_hub",
            "aether", "datasets", "filelock", "fsspec"):
    logging.getLogger(lib).setLevel(logging.ERROR)

DEMO_DIR   = Path("data/demo")
SAMPLE_N   = 15
BASELINE   = Path("eval_t2ragbench_15.json")

# ── Number-Match scorer (same as score_number_match.py) ───────────────────────
import re as _re

_NUM_RE = _re.compile(
    r"""(?<![a-zA-Z\d])([+-]?)([\d,]+(?:\.\d+)?)\s*(%|[a-zA-Z]+)?(?![a-zA-Z\d])""",
    _re.VERBOSE,
)
_REL_TOL = 0.01
_ABS_TOL = 1e-6

def _rel_close(a: float, b: float) -> bool:
    if b == 0:
        return abs(a) < _ABS_TOL
    return abs(a - b) / abs(b) < _REL_TOL

def _extract_candidates(text: str) -> list[float]:
    candidates: list[float] = []
    for m in _NUM_RE.finditer(text):
        sign_str, num_str, suffix = m.group(1), m.group(2), m.group(3)
        try:
            val = float(num_str.replace(",", ""))
        except ValueError:
            continue
        if sign_str == "-":
            val = -val
        candidates.append(val)
    return candidates

def _check_boolean(text: str, gold: float) -> bool | None:
    low = text.lower()
    has_yes = bool(_re.search(r"\byes\b", low))
    has_no  = bool(_re.search(r"\bno\b",  low))
    if has_yes and not has_no:
        return _rel_close(1.0, gold)
    if has_no and not has_yes:
        return _rel_close(0.0, gold)
    return None

def number_match(model_answer: str, gold_str: str) -> bool:
    try:
        gold = float(gold_str)
    except ValueError:
        return False
    if gold in (0.0, 1.0):
        b = _check_boolean(model_answer, gold)
        if b is not None:
            return b
    candidates = _extract_candidates(model_answer)
    if not candidates:
        return False
    for c in candidates:
        if _rel_close(c, gold):
            return True
        if abs(gold) <= 1.0 and abs(c) > 1.5:
            if _rel_close(c / 100.0, gold):
                return True
    return False

# ── Setup ─────────────────────────────────────────────────────────────────────

print("=" * 70)
print("T²-RAGBench — 15-record FinQA eval WITH SQL-routing path")
print("=" * 70)
print(f"\nStreaming FinQA test split, collecting first {SAMPLE_N} records …")

from datasets import load_dataset
from aether.ingestion.table_parser import table_to_csv
from aether.runtime import AetherRuntime

ds = load_dataset("G4KMU/t2-ragbench", "FinQA", split="test", streaming=True)
records: list[dict] = []
for row in ds:
    records.append(row)
    if len(records) >= SAMPLE_N:
        break
print(f"Collected {len(records)} records.\n")

# Write context txt + table csv files
DEMO_DIR.mkdir(parents=True, exist_ok=True)
file_plan: list[dict] = []  # per-record: {txt, csv, sql_path}

for rec in records:
    rid  = rec["id"]
    ctx  = rec.get("context") or ""
    txt  = DEMO_DIR / f"{rid}.txt"
    txt.write_text(ctx, encoding="utf-8")

    md_table = rec.get("table") or ""
    csv_path = DEMO_DIR / f"{rid}_table.csv"
    parsed_ok = False
    if md_table.strip():
        parsed_ok = table_to_csv(md_table, str(csv_path))

    file_plan.append({
        "rid":       rid,
        "txt":       str(txt),
        "csv":       str(csv_path) if parsed_ok else None,
        "sql_path":  parsed_ok,
    })

n_sql  = sum(1 for p in file_plan if p["sql_path"])
n_text = SAMPLE_N - n_sql
print(f"Table parse results: {n_sql}/{SAMPLE_N} parsed → CSV written, {n_text} text-only fallback\n")

# Load baseline for comparison
baseline: list[dict] = []
if BASELINE.exists():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

print("Initialising Aether runtime (ephemeral store) …")
runtime = AetherRuntime(eval_mode=True)
print("Runtime ready.\n")

# ── Per-record agentic run ────────────────────────────────────────────────────

results: list[dict] = []

for i, (rec, plan) in enumerate(zip(records, file_plan), start=1):
    rid      = rec["id"]
    question = rec["question"]
    gold     = str(rec.get("program_answer", rec.get("original_answer", "?")))
    sql_path = plan["sql_path"]
    paths    = [plan["txt"]]
    if plan["csv"]:
        paths.append(plan["csv"])

    path_label = "SQL+text" if sql_path else "text-only"
    print(f"[{i:02d}/{SAMPLE_N}] {rid}  [{path_label}]")
    print(f"  Q: {question[:110]}")

    runtime.retriever.reset_index()
    runtime.reset_tool_state()

    t0            = time.time()
    status        = "ok"
    answer_txt    = "(no write_report step)"
    stop_reason   = "?"
    verdict       = "?"
    used_csv      = False
    load_attempts = 0
    steps_taken   = 0
    error_msg     = ""

    try:
        result = runtime.run_agentic(
            goal=question,
            file_paths=paths,
            max_steps=10,
        )

        stop_reason = result.get("stop_reason", "?")
        steps_taken = result.get("steps_taken", 0)
        verdict     = result["critique"]["overall_verdict"]

        for step in result["loop_state"]["steps"]:
            tool = step["action"]["tool"]
            args = step["action"].get("tool_args", {})
            if tool == "load_data":
                load_attempts += 1
                fp = args.get("file_path", "")
                if fp.endswith(".csv"):
                    used_csv = True
            if tool == "write_report":
                rpt = args.get("results", {})
                if isinstance(rpt, dict):
                    answer_txt = " | ".join(f"{k}: {v}" for k, v in rpt.items() if v is not None)
                else:
                    answer_txt = str(rpt)

        if stop_reason == "max_steps":
            status = "MAX_STEPS"

    except Exception as exc:
        status    = "ERROR"
        error_msg = str(exc)[:120]
        answer_txt = f"ERROR: {error_msg}"

    elapsed = time.time() - t0
    matched = number_match(answer_txt, gold)

    # Baseline comparison
    base_match = False
    base_ans   = ""
    if baseline and i <= len(baseline):
        base_ans   = baseline[i - 1].get("model_answer", "")
        base_match = number_match(base_ans, gold)

    rec_result = {
        "i":            i,
        "id":           rid,
        "question":     question,
        "gold":         gold,
        "model_answer": answer_txt,
        "path":         "SQL+text" if used_csv else ("text-only" if not sql_path else "SQL-avail-not-used"),
        "sql_available":sql_path,
        "used_csv":     used_csv,
        "status":       status,
        "stop_reason":  stop_reason,
        "steps_taken":  steps_taken,
        "critic_verdict": verdict,
        "load_attempts":load_attempts,
        "elapsed_s":    round(elapsed, 1),
        "match_sql":    matched,
        "match_base":   base_match,
    }
    results.append(rec_result)

    delta = ""
    if base_match and not matched:
        delta = "  ⚠ REGRESSION"
    elif matched and not base_match:
        delta = "  ✓ GAIN"

    print(f"  A: {answer_txt[:110]}")
    print(f"  G: {gold}   match={matched}  base={base_match}{delta}")
    print(f"  path={rec_result['path']}  [{status}/{stop_reason}/{verdict}/{elapsed:.0f}s]")
    print()

# ── Summary ───────────────────────────────────────────────────────────────────

print()
print("=" * 70)
print("BEFORE / AFTER COMPARISON")
print("=" * 70)
print(f"{'#':>2}  {'ID':<20} {'SQL?':<5} {'USED?':<6} {'BASE':<5} {'SQL':<5}  DELTA")
print("-" * 70)

for r in results:
    sql_av = "Y" if r["sql_available"] else "N"
    used   = "Y" if r["used_csv"] else "N"
    bm     = "✓" if r["match_base"] else "✗"
    sm     = "✓" if r["match_sql"] else "✗"
    if r["match_base"] and not r["match_sql"]:
        delta = "REGRESSION"
    elif not r["match_base"] and r["match_sql"]:
        delta = "GAIN"
    elif r["match_sql"]:
        delta = "same ✓"
    else:
        delta = "same ✗"
    print(f"{r['i']:>2}  {r['id']:<20} {sql_av:<5} {used:<6} {bm:<5} {sm:<5}  {delta}")

base_total = sum(1 for r in results if r["match_base"])
sql_total  = sum(1 for r in results if r["match_sql"])
gains      = sum(1 for r in results if r["match_sql"] and not r["match_base"])
regressions= sum(1 for r in results if r["match_base"] and not r["match_sql"])

print("-" * 70)
print(f"\nBASELINE (text-only)  : {base_total}/{SAMPLE_N}")
print(f"SQL-ROUTING           : {sql_total}/{SAMPLE_N}")
print(f"Gains (✗→✓)           : {gains}")
print(f"Regressions (✓→✗)     : {regressions}")
print()
print("Table-parse breakdown:")
print(f"  Parsed → CSV (SQL path available) : {n_sql}/{SAMPLE_N}")
print(f"  Unparseable / no table → text only: {n_text}/{SAMPLE_N}")
print()
csv_used = sum(1 for r in results if r["used_csv"])
csv_avail = sum(1 for r in results if r["sql_available"])
print(f"  CSV available: {csv_avail}, agent actually used load_data on CSV: {csv_used}")

ok_count  = sum(1 for r in results if r["status"] == "ok")
err_count = sum(1 for r in results if r["status"] == "ERROR")
max_count = sum(1 for r in results if r["status"] == "MAX_STEPS")
print()
print(f"Run health: ok={ok_count}, error={err_count}, max_steps={max_count}")

out = Path("eval_t2ragbench_sql_15.json")
out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\nRaw results → {out}")
