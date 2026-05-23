# scratch/test_planner_ollama.py — one-shot planner isolation test (Ollama path)
import sys
import time
import pathlib

# Ensure project root is on path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from aether.config import settings
from aether.models.chunk import Chunk, ChunkMetadata
from aether.agents.planner import PlannerAgent

# ── 1. Confirm provider resolution ───────────────────────────────────────────
print("=== Settings ===")
print(f"  planner_provider : {settings.planner_provider}")
print(f"  planner_model_local: {settings.planner_model_local}")
print(f"  ollama_base_url  : {settings.ollama_base_url}")
assert settings.planner_provider == "ollama", (
    f"Expected 'ollama', got {settings.planner_provider!r} — check .env or defaults"
)

# ── 2. Build a minimal context chunk from the demo CSV ────────────────────────
demo_csv = pathlib.Path(__file__).parents[1] / "data/demo/fund_capital_accounts.csv"
csv_text = demo_csv.read_text(encoding="utf-8")

chunk = Chunk(
    document_id="demo-fund-capital-accounts",
    content=f"[CSV: fund_capital_accounts.csv]\n{csv_text}",
    metadata=ChunkMetadata(
        source_path=str(demo_csv),
        document_type="csv",
    ),
)

# ── 3. Run the planner ────────────────────────────────────────────────────────
goal = (
    "Check if any partner has an ownership percentage exceeding 20%. "
    "Flag them if so."
)

print("\n=== Planner run ===")
print(f"  goal: {goal}")
print(f"  context chunks: 1  ({len(csv_text)} chars)")
print()

agent = PlannerAgent()
t0 = time.time()
try:
    plan = agent.run(
        goal=goal,
        context_chunks=[chunk],
        file_paths=[str(demo_csv)],
    )
    elapsed = time.time() - t0

    # ── 4. Report ─────────────────────────────────────────────────────────────
    print(f"  result        : VALID ExecutionPlan")
    print(f"  plan_id       : {plan.plan_id}")
    print(f"  steps         : {len(plan.steps)}")
    for i, step in enumerate(plan.steps, 1):
        print(f"    step {i}: [{step.tool}] {step.name}")
    print(f"  wall-clock    : {elapsed:.1f}s")
    print()
    print("  (token counts are in the SQLite trace — check aether_trace.db)")

except Exception as exc:
    elapsed = time.time() - t0
    print(f"  result        : FAILED after {elapsed:.1f}s")
    print(f"  error         : {exc}")
    sys.exit(1)
