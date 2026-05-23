# scripts/test_run.py

import traceback

from aether.runtime import AetherRuntime

GOAL = (
    "Reconcile Q4 2024 capital accounts. "
    "Compute each partner's distribution percentage as distributions / sum(distributions). "
    "Compare that to their ownership_pct column value. "
    "Flag any partner where the absolute deviation exceeds 5 percentage points."
)
FILE_PATHS = ["data/demo/fund_capital_accounts.csv"]


def main() -> None:
    runtime = AetherRuntime()
    result = runtime.run(goal=GOAL, file_paths=FILE_PATHS)

    print(f"\nrun_id : {result['run_id']}")

    print("\nPlan steps:")
    for step in result["plan"]["steps"]:
        print(f"  - {step['name']}")

    critique = result["critique"]
    print(f"\nVerdict     : {critique['overall_verdict']}")
    print(f"Confidence  : {critique['confidence']:.2f}")
    print(f"Summary     : {critique['summary']}")

    if critique["flags"]:
        print("\nFlags found:")
        for flag in critique["flags"]:
            print(f"  [{flag['severity'].upper()}] {flag['description']}")
            print(f"    Evidence : {flag['evidence']}")
            if flag.get("suggested_fix"):
                print(f"    Fix      : {flag['suggested_fix']}")
    else:
        print("\nNo flags found.")

    flags_raised = any(
        isinstance(v, dict) and v.get("flagged")
        for v in result["output"].values()
    )
    verdict_ok = critique["overall_verdict"] in ("pass", "partial", "fail")
    print(f"\n{'PASS' if flags_raised and verdict_ok else 'FAIL'}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
