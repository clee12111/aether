# scripts/debug_flag_case.py
"""Debug harness — runs one e2e case and prints everything the executor produces."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aether.runtime import AetherRuntime

GOAL = (
    "Load the capital accounts CSV and flag any partner whose "
    "distribution percentage deviates from their ownership "
    "percentage by more than 5 percentage points."
)
FILE_PATHS = ["data/demo/fund_capital_accounts.csv"]


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    runtime = AetherRuntime()
    result = runtime.run(GOAL, FILE_PATHS)

    plan = result["plan"]
    output = result["output"]
    critique = result["critique"]

    # ── Plan ──────────────────────────────────────────────────────
    print("=" * 60)
    print("=== PLAN ===")
    print("=" * 60)
    print(f"Goal: {plan['goal']}")
    print(f"Reasoning: {plan['reasoning']}")
    print(f"Steps: {len(plan['steps'])}")
    print()
    for i, step in enumerate(plan["steps"], 1):
        print(f"  Step {i}: {step['step_id']}")
        print(f"    tool: {step['tool']}")
        print(f"    args: {json.dumps(step['tool_args'], indent=6)}")
        print(f"    depends_on: {step['depends_on']}")
        print()

    # ── Per-step executor output ──────────────────────────────────
    step_ids = [s["step_id"] for s in plan["steps"]]
    for i, sid in enumerate(step_ids, 1):
        print("=" * 60)
        print(f"=== STEP {i} OUTPUT: {sid} ===")
        print("=" * 60)
        step_result = output.get(sid, "(no output)")
        print(json.dumps(step_result, indent=2, default=str))
        print()

    # ── Final output ──────────────────────────────────────────────
    print("=" * 60)
    print("=== FINAL OUTPUT (full state) ===")
    print("=" * 60)
    print(json.dumps(output, indent=2, default=str))
    print()

    # ── Critique ──────────────────────────────────────────────────
    print("=" * 60)
    print("=== CRITIQUE ===")
    print("=" * 60)
    print(f"Verdict: {critique['overall_verdict']}")
    print(f"Summary: {critique['summary']}")
    print(f"Revisions: {result['revisions']}")
    if critique["flags"]:
        print(f"Flags ({len(critique['flags'])}):")
        for f in critique["flags"]:
            print(f"  [{f['severity']}] {f['description']}")
    else:
        print("Flags: (none)")


if __name__ == "__main__":
    main()
