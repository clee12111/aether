# eval_t2ragbench_sample.py
#
# Minimal T²-RAGBench adapter — 15-record FinQA baseline.
# TEXT-ONLY PATH: no SQL routing, no number normalization.
# Goal: find what breaks before any demo.
#
# Usage:
#   uv run python eval_t2ragbench_sample.py
#
# Output: side-by-side [question] / [model answer] / [gold] for eyeballing.

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.WARNING,          # suppress INFO noise during the run
    format="[%(levelname)s %(name)s] %(message)s",
    stream=sys.stdout,
)
# Suppress everything except our own progress prints
for lib in ("chromadb", "sentence_transformers", "httpx", "httpcore",
            "openai", "hpack", "h2", "urllib3", "huggingface_hub",
            "aether", "datasets", "filelock", "fsspec"):
    logging.getLogger(lib).setLevel(logging.ERROR)

DEMO_DIR   = Path("data/demo")
SAMPLE_N   = 15

# ── Step 1: stream the first 15 FinQA test records ────────────────────────────

print("=" * 70)
print("T²-RAGBench — 15-record FinQA baseline (text-only path)")
print("=" * 70)
print(f"\nStreaming FinQA test split, collecting first {SAMPLE_N} records …")

from datasets import load_dataset  # noqa: E402

# Config is required — FinQA is the single-turn numerical subset
ds = load_dataset("G4KMU/t2-ragbench", "FinQA", split="test", streaming=True)

records: list[dict] = []
for row in ds:
    records.append(row)
    if len(records) >= SAMPLE_N:
        break

print(f"Collected {len(records)} records.\n")

# ── Step 2: write context fields to data/demo/{id}.txt ───────────────────────

DEMO_DIR.mkdir(parents=True, exist_ok=True)
txt_paths: list[Path] = []

for rec in records:
    rid  = rec["id"]
    ctx  = rec.get("context") or ""
    path = DEMO_DIR / f"{rid}.txt"
    path.write_text(ctx, encoding="utf-8")
    txt_paths.append(path)

print(f"Wrote {len(txt_paths)} context files to {DEMO_DIR}/\n")

# ── Step 3: build ONE runtime (eval_mode=True → ephemeral Chroma) ─────────────

print("Initialising Aether runtime (ephemeral store) …")
from aether.runtime import AetherRuntime  # noqa: E402
runtime = AetherRuntime(eval_mode=True)
print("Runtime ready.\n")

# ── Step 4: per-record agentic run ───────────────────────────────────────────

results: list[dict] = []

for i, (rec, txt_path) in enumerate(zip(records, txt_paths), start=1):
    rid          = rec["id"]
    question     = rec["question"]
    gold         = str(rec.get("program_answer", rec.get("original_answer", "?")))

    print(f"[{i:02d}/{SAMPLE_N}] {rid}")
    print(f"  Q: {question[:110]}")

    # Per-case isolation: reset retrieval index and tool state
    runtime.retriever.reset_index()
    runtime.reset_tool_state()

    t0 = time.time()
    status     = "ok"
    answer_txt = "(no write_report step)"
    stop_reason  = "?"
    verdict      = "?"
    load_data_attempts = 0
    steps_taken  = 0
    error_msg    = ""

    try:
        result = runtime.run_agentic(
            goal=question,
            file_paths=[str(txt_path)],
            max_steps=10,
        )

        stop_reason  = result.get("stop_reason", "?")
        steps_taken  = result.get("steps_taken", 0)
        verdict      = result["critique"]["overall_verdict"]

        # Extract model answer from the write_report step (last one wins)
        for step in result["loop_state"]["steps"]:
            tool = step["action"]["tool"]
            if tool == "load_data":
                load_data_attempts += 1
            if tool == "write_report":
                rpt = step["action"]["tool_args"].get("results", {})
                # Flatten whatever the model wrote into a single prose string
                if isinstance(rpt, dict):
                    answer_txt = " | ".join(
                        f"{k}: {v}" for k, v in rpt.items()
                        if v is not None
                    )
                else:
                    answer_txt = str(rpt)

        if stop_reason == "max_steps":
            status = "MAX_STEPS"

    except Exception as exc:
        status    = "ERROR"
        error_msg = str(exc)[:120]
        answer_txt = f"ERROR: {error_msg}"

    elapsed = time.time() - t0

    rec_result = {
        "i":                  i,
        "id":                 rid,
        "question":           question,
        "gold":               gold,
        "model_answer":       answer_txt,
        "status":             status,
        "stop_reason":        stop_reason,
        "steps_taken":        steps_taken,
        "critic_verdict":     verdict,
        "load_data_attempts": load_data_attempts,
        "elapsed_s":          round(elapsed, 1),
    }
    results.append(rec_result)

    # Inline progress
    flag = "  CONTAM" if load_data_attempts else ""
    print(f"  A: {answer_txt[:110]}")
    print(f"  G: {gold}   [{status} / {stop_reason} / {verdict} / {elapsed:.0f}s]{flag}")
    print()

# ── Step 5: summary table ─────────────────────────────────────────────────────

print()
print("=" * 70)
print("SIDE-BY-SIDE RESULTS (15 records)")
print("=" * 70)

ok_count       = sum(1 for r in results if r["status"] == "ok")
error_count    = sum(1 for r in results if r["status"] == "ERROR")
maxsteps_count = sum(1 for r in results if r["status"] == "MAX_STEPS")
contam_total   = sum(r["load_data_attempts"] for r in results)
contam_records = sum(1 for r in results if r["load_data_attempts"] > 0)

for r in results:
    q_short = r["question"][:80] + ("…" if len(r["question"]) > 80 else "")
    a_short = r["model_answer"][:100] + ("…" if len(r["model_answer"]) > 100 else "")
    print(f"\n[{r['i']:02d}] {r['id']}  ({r['status']} / {r['stop_reason']} / {r['critic_verdict']})")
    print(f"  Q: {q_short}")
    print(f"  M: {a_short}")
    print(f"  G: {r['gold']}")

print()
print("=" * 70)
print("RUN SUMMARY")
print("=" * 70)
print(f"  Total records    : {len(results)}")
print(f"  Completed OK     : {ok_count}")
print(f"  Errors           : {error_count}")
print(f"  Max-steps        : {maxsteps_count}")
print(f"  load_data contam : {contam_records} records / {contam_total} total attempts")
print(f"  Critic verdicts  :")
for v in ("pass", "partial", "fail"):
    n = sum(1 for r in results if r["critic_verdict"] == v)
    print(f"    {v:8s}: {n}")

# Save raw results for inspection
out = Path("eval_t2ragbench_15.json")
out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\n  Raw results saved → {out}")
