# eval_e2e_200.py
#
# End-to-end agentic eval — 200 FinQA test records, SQL-routing ON.
#
# Incremental write: each result is appended to eval_e2e_200.jsonl as it
# completes. A mid-run crash (budget, network, timeout) loses at most the
# record currently in flight — all prior completions survive.
#
# On restart, already-completed record IDs are loaded from the .jsonl and
# skipped, so the run continues from where it stopped.
#
# Records: first 200 from the T2-RAGBench FinQA test split (deterministic).
# Model:   gpt-5.4-mini (PLANNER_PROVIDER=openai, default).
# Scoring: Number-Match with dual-guard /100 normalisation.
#
# Usage:
#   uv run python eval_e2e_200.py
#
# To resume after a crash:
#   uv run python eval_e2e_200.py   (same command — resumes automatically)

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

DEMO_DIR     = Path("data/demo_200")
OUT_JSONL    = Path("eval_e2e_200.jsonl")
SUMMARY_JSON = Path("eval_e2e_200_summary.json")
N_RECORDS    = 200

# ── Number-Match scorer ────────────────────────────────────────────────────────
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
    candidates = []
    for m in _NUM_RE.finditer(text):
        sign_str, num_str = m.group(1), m.group(2)
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
    # Python literal booleans (case-sensitive)
    has_True  = bool(_re.search(r"\bTrue\b",  text))
    has_False = bool(_re.search(r"\bFalse\b", text))
    if has_True and not has_False:
        return _rel_close(1.0, gold)
    if has_False and not has_True:
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
    for c in candidates:
        if _rel_close(c, gold):
            return True
        # /100 extended: covers gold>1.0 and small-candidate cases
        if gold != 0 and abs(c) > abs(gold) * 50:
            if _rel_close(c / 100.0, gold):
                return True
        # ÷1,000 (abs(c)≥100 guard blocks e.g. low_price≈93 matching ratio≈0.093)
        if abs(c) >= 100 and _rel_close(c / 1_000, gold):
            return True
        # ×1,000
        if _rel_close(c * 1_000, gold):
            return True
        # ÷1,000,000 (abs(c)>1000 guard blocks share-price c=7.47 matching 7.47e-6)
        if abs(c) > 1_000 and _rel_close(c / 1_000_000, gold):
            return True
        # ×1,000,000
        if _rel_close(c * 1_000_000, gold):
            return True
    return False

# ── Load already-completed records (resume support) ───────────────────────────

completed_ids: set[str] = set()
if OUT_JSONL.exists():
    for line in OUT_JSONL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rec = json.loads(line)
                completed_ids.add(rec["id"])
            except Exception:
                pass
    print(f"[RESUME] {len(completed_ids)} records already completed — skipping.")

# ── Stream first N_RECORDS ─────────────────────────────────────────────────────

print("=" * 70)
print(f"E2E Eval — {N_RECORDS} FinQA test records, SQL-routing ON")
print("=" * 70)
print(f"\nStreaming first {N_RECORDS} records from T2-RAGBench FinQA test …")

from datasets import load_dataset
from aether.ingestion.table_parser import table_to_csv

ds = load_dataset("G4KMU/t2-ragbench", "FinQA", split="test", streaming=True)

records: list[dict] = []
for row in ds:
    records.append(row)
    if len(records) >= N_RECORDS:
        break

print(f"Collected {len(records)} records.\n")

# ── Write context + table files ────────────────────────────────────────────────

DEMO_DIR.mkdir(parents=True, exist_ok=True)

file_plan: list[dict] = []
for rec in records:
    rid  = rec["id"]
    txt  = DEMO_DIR / f"{rid}.txt"
    txt.write_text(rec.get("context") or "", encoding="utf-8")

    csv_path  = DEMO_DIR / f"{rid}_table.csv"
    parsed_ok = False
    md_table  = rec.get("table") or ""
    if md_table.strip():
        parsed_ok = table_to_csv(md_table, str(csv_path))

    file_plan.append({
        "rid":       rid,
        "txt":       str(txt),
        "csv":       str(csv_path) if parsed_ok else None,
        "sql_path":  parsed_ok,
        "gold":      str(rec.get("program_answer", rec.get("original_answer", "?"))),
        "question":  rec["question"],
    })

n_sql = sum(1 for p in file_plan if p["sql_path"])
print(f"Table parse: {n_sql}/{N_RECORDS} → CSV, {N_RECORDS-n_sql} text-only\n")

# ── Budget check ───────────────────────────────────────────────────────────────

remaining_to_run = N_RECORDS - len(completed_ids)
est_tokens = remaining_to_run * 8_500
print(f"Budget check:")
print(f"  Records to run : {remaining_to_run} (skipping {len(completed_ids)} completed)")
print(f"  Estimated tokens: {est_tokens:,} (@ 8,500/record)")
print(f"  Today's spend so far: see aether_trace.db")
print()

# ── Initialise runtime ─────────────────────────────────────────────────────────

print("Initialising runtime (ephemeral store) …")
from aether.runtime import AetherRuntime
runtime = AetherRuntime(eval_mode=True)
print("Runtime ready.\n")

# ── Per-record agentic run ─────────────────────────────────────────────────────

out_fh = OUT_JSONL.open("a", encoding="utf-8")

try:
    for i, (rec, plan) in enumerate(zip(records, file_plan), start=1):
        rid      = plan["rid"]
        gold     = plan["gold"]
        question = plan["question"]

        if rid in completed_ids:
            print(f"[{i:03d}/{N_RECORDS}] {rid}  SKIP (already done)")
            continue

        paths = [plan["txt"]]
        if plan["csv"]:
            paths.append(plan["csv"])

        print(f"[{i:03d}/{N_RECORDS}] {rid}")
        print(f"  Q: {question[:110]}")

        runtime.retriever.reset_index()
        runtime.reset_tool_state()

        t0            = time.time()
        status        = "ok"
        answer_txt    = "(no write_report)"
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
                    if args.get("file_path", "").endswith(".csv"):
                        used_csv = True
                if tool == "write_report":
                    rpt = args.get("results", {})
                    if isinstance(rpt, dict):
                        answer_txt = " | ".join(
                            f"{k}: {v}" for k, v in rpt.items() if v is not None
                        )
                    else:
                        answer_txt = str(rpt)

            if stop_reason == "max_steps":
                status = "MAX_STEPS"

        except Exception as exc:
            status    = "ERROR"
            error_msg = str(exc)[:200]
            answer_txt = f"ERROR: {error_msg}"

        elapsed = time.time() - t0
        matched = number_match(answer_txt, gold)

        rec_result = {
            "i":              i,
            "id":             rid,
            "question":       question,
            "gold":           gold,
            "model_answer":   answer_txt,
            "sql_available":  plan["sql_path"],
            "used_csv":       used_csv,
            "status":         status,
            "stop_reason":    stop_reason,
            "steps_taken":    steps_taken,
            "critic_verdict": verdict,
            "load_attempts":  load_attempts,
            "elapsed_s":      round(elapsed, 1),
            "match":          matched,
        }

        # Append immediately — survive crash
        out_fh.write(json.dumps(rec_result, ensure_ascii=False) + "\n")
        out_fh.flush()

        flag = "  ✓ MATCH" if matched else "  ✗"
        path_label = "SQL" if used_csv else ("sql-avail" if plan["sql_path"] else "text")
        print(f"  A: {answer_txt[:110]}")
        print(f"  G: {gold}   {flag}  [{path_label}/{status}/{stop_reason}/{verdict}/{elapsed:.0f}s]")
        print()

finally:
    out_fh.close()

# ── Final summary ──────────────────────────────────────────────────────────────

all_results: list[dict] = []
for line in OUT_JSONL.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line:
        try:
            all_results.append(json.loads(line))
        except Exception:
            pass

# Sort by i for consistent ordering
all_results.sort(key=lambda r: r["i"])

n_done    = len(all_results)
n_match   = sum(1 for r in all_results if r["match"])
n_sql_used= sum(1 for r in all_results if r["used_csv"])
n_sql_av  = sum(1 for r in all_results if r["sql_available"])
n_ok      = sum(1 for r in all_results if r["status"] == "ok")
n_err     = sum(1 for r in all_results if r["status"] == "ERROR")
n_max     = sum(1 for r in all_results if r["status"] == "MAX_STEPS")

print()
print("=" * 70)
print("FINAL SUMMARY")
print("=" * 70)
print(f"  Records completed  : {n_done}/{N_RECORDS}")
print(f"  Number-Match score : {n_match}/{n_done} ({n_match/n_done*100:.1f}%)")
print(f"  SQL path available : {n_sql_av}/{n_done}")
print(f"  SQL path used      : {n_sql_used}/{n_done}")
print(f"  Status: ok={n_ok}  error={n_err}  max_steps={n_max}")
print()
for v in ("pass", "partial", "fail"):
    n = sum(1 for r in all_results if r.get("critic_verdict") == v)
    print(f"  Critic {v:7s}: {n}")

# Failure-mode breakdown for misses
misses = [r for r in all_results if not r["match"]]
print(f"\n  Misses: {len(misses)}/{n_done}")

summary = {
    "n_done":   n_done,
    "n_match":  n_match,
    "score_pct": round(n_match / n_done * 100, 1) if n_done else 0,
    "n_sql_available": n_sql_av,
    "n_sql_used": n_sql_used,
    "n_ok": n_ok, "n_error": n_err, "n_max_steps": n_max,
}
SUMMARY_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(f"\n  JSONL results → {OUT_JSONL}")
print(f"  Summary      → {SUMMARY_JSON}")
