# scripts/test_run.py
#
# Smoke-test / demo runner for Aether.
#
# Ingests two demo files (a fund CSV and a compliance policy text), then
# runs a single agentic question through the full reason-act-observe loop
# and prints the result + a trace summary.
#
# Usage:
#   uv run python scripts/test_run.py
#
# Requirements:
#   - OPENAI_API_KEY set in .env (or shell env)
#   - Run from the repo root (uv run handles this automatically)

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# ── Logging: suppress library noise ──────────────────────────────────────────

logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(message)s",
                    stream=sys.stdout)
for lib in ("chromadb", "sentence_transformers", "httpx", "httpcore",
            "openai", "hpack", "h2", "urllib3", "huggingface_hub", "aether",
            "filelock", "fsspec"):
    logging.getLogger(lib).setLevel(logging.ERROR)

# ── Demo files ────────────────────────────────────────────────────────────────

DEMO_DIR = Path("data/demo")
FILES = [
    str(DEMO_DIR / "demo_fund.csv"),
    str(DEMO_DIR / "compliance_policy.txt"),
]
GOAL = (
    "Which investors have a Q4 allocation percentage above 15%? "
    "Flag any that exceed the 20% single-investor concentration limit "
    "from the compliance policy."
)

# ── Verify demo files exist ───────────────────────────────────────────────────

missing = [f for f in FILES if not Path(f).exists()]
if missing:
    print("ERROR: demo files not found:", missing)
    print("Run from the repo root: uv run python scripts/test_run.py")
    sys.exit(1)

# ── Run ───────────────────────────────────────────────────────────────────────

print("=" * 68)
print("Aether — demo run")
print("=" * 68)
print(f"\nGoal: {GOAL}\n")
print(f"Files:")
for f in FILES:
    print(f"  {f}")
print()

from aether.runtime import AetherRuntime

runtime = AetherRuntime()
print("Ingesting + running agent loop …\n")

result = runtime.run_agentic(goal=GOAL, file_paths=FILES, max_steps=10)

# ── Print trace ───────────────────────────────────────────────────────────────

steps = result.get("loop_state", {}).get("steps", [])
print(f"Steps taken: {len(steps)}")
for i, step in enumerate(steps, start=1):
    action = step.get("action", {})
    obs    = step.get("observation", {})
    tool   = action.get("tool", "?")
    is_fin = action.get("is_final", False)
    status = obs.get("status", "?")
    print(f"  [{i}] {tool:25s}  status={status}  is_final={is_fin}")

# ── Critic verdict ────────────────────────────────────────────────────────────

critique = result.get("critique", {})
verdict  = critique.get("overall_verdict", "?")
flags    = critique.get("flags", [])
print(f"\nCritic verdict: {verdict.upper()}")
for f in flags:
    sev = f.get("severity", "?")
    cat = f.get("category", "?")
    msg = f.get("message", "")
    print(f"  [{sev:8s}] {cat}: {msg[:100]}")

# ── Stop reason ───────────────────────────────────────────────────────────────

stop_reason  = result.get("stop_reason", "?")
steps_taken  = result.get("steps_taken", len(steps))
print(f"\nStop reason   : {stop_reason}")
print(f"Steps taken   : {steps_taken}")

# ── Final answer ──────────────────────────────────────────────────────────────

answer_txt = None
for step in steps:
    action = step.get("action", {})
    if action.get("tool") == "write_report":
        rpt = action.get("tool_args", {}).get("results", {})
        if isinstance(rpt, dict):
            answer_txt = "\n".join(f"  {k}: {v}" for k, v in rpt.items())
        else:
            answer_txt = str(rpt)

if answer_txt:
    print(f"\nReport:\n{answer_txt}")
else:
    print("\n(no write_report call found in trace)")

print()
print("=" * 68)
print("Run complete.")
