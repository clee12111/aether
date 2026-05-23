# scratch/test_rao_loop.py — RAO loop end-to-end test
import sys
import time
import pathlib
import logging

# Basic logging so we see what's happening
logging.basicConfig(level=logging.WARNING)  # suppress debug noise; runtime logs at INFO
logging.getLogger("aether").setLevel(logging.INFO)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from aether.runtime import AetherRuntime

GOAL = "Check if any partner has an ownership percentage exceeding 20%. Flag them if so."
DEMO_CSV = str(pathlib.Path(__file__).parents[1] / "data/demo/fund_capital_accounts.csv")

print("=== RAO Loop Test ===")
print(f"goal : {GOAL}")
print(f"file : {DEMO_CSV}")
print(f"max_steps: 10")
print()

runtime = AetherRuntime()

t0 = time.time()
try:
    result = runtime.run_agentic(goal=GOAL, file_paths=[DEMO_CSV], max_steps=10)
    elapsed = time.time() - t0

    loop_state = result["loop_state"]
    critique = result["critique"]
    steps = loop_state["steps"]

    print("=== Step-by-step trace ===")
    flag_item_called = False
    for ls in steps:
        idx = ls["step_index"]
        tool = ls["action"]["tool"]
        reasoning = ls["action"]["reasoning"]
        success = ls["observation"]["success"]
        output = ls["observation"]["output"]
        error = ls["observation"]["error"]

        if tool == "flag_item":
            flag_item_called = True

        # Short observation summary
        if success:
            obs_summary = str(output)[:120]
        else:
            obs_summary = f"ERROR: {error}"

        # Truncate reasoning to one line
        reasoning_short = reasoning.replace("\n", " ")[:100]

        print(f"  [{idx}] tool={tool!r}")
        print(f"       reasoning: {reasoning_short}")
        print(f"       success={success}  obs: {obs_summary}")
        print()

    print("=== Summary ===")
    print(f"  stop_reason     : {result['stop_reason']}")
    print(f"  total steps     : {result['steps_taken']}")
    print(f"  flag_item called: {flag_item_called}")
    print(f"  critic verdict  : {critique['overall_verdict']} (confidence={critique.get('confidence', '?')})")
    print(f"  wall-clock      : {elapsed:.1f}s")
    if critique.get("flags"):
        print(f"  critic flags    :")
        for f in critique["flags"]:
            print(f"    [{f['severity']}] {f['description']}")
    print(f"  critic summary  : {critique.get('summary', '')[:200]}")

except Exception as exc:
    elapsed = time.time() - t0
    print(f"\nCRASHED after {elapsed:.1f}s: {type(exc).__name__}: {exc}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
