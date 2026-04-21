# evals/end_to_end/test_e2e.py

"""End-to-end eval — runs AetherRuntime.run() and checks verdict, flags, partner."""

import json
from pathlib import Path

import pytest

from aether.runtime import AetherRuntime

CASES_PATH = Path(__file__).parent / "cases.json"

_cases = json.loads(CASES_PATH.read_text())
_quick_ids = {c["case_id"] for c in _cases[:3]}


def pytest_configure(config):
    config.addinivalue_line("markers", "quick: only run first 3 cases")


def _count_flagged_items(output: dict) -> int:
    """Count flagged items in the executor state dict."""
    count = 0
    for val in output.values():
        if not isinstance(val, dict):
            continue
        if val.get("flagged"):
            count += val.get("total_flagged", 1)
    return count


@pytest.fixture(scope="module")
def runtime():
    return AetherRuntime()


@pytest.mark.parametrize(
    "case", _cases, ids=[c["case_id"] for c in _cases]
)
def test_e2e(runtime, case, request):
    if request.node.get_closest_marker("quick") or "quick" in request.config.getoption("-m", default=""):
        if case["case_id"] not in _quick_ids:
            pytest.skip("skipped in quick mode")

    result = runtime.run(case["goal"], case["file_paths"])
    critique = result["critique"]
    output = result.get("output", {})

    # -- Verdict --
    verdict = critique["overall_verdict"]
    expected = case["expected_verdict"]
    if expected == "fail":
        assert verdict == "fail", (
            f"[{case['case_id']}] verdict: got {verdict!r}, expected 'fail'"
        )
    elif expected != "any":
        assert verdict in ("pass", "partial"), (
            f"[{case['case_id']}] verdict: got {verdict!r}, expected 'pass' or 'partial'"
        )

    # -- Flag count: prefer executor output, fall back to critique flags --
    min_flags = case["expected_flags_min"]
    if min_flags > 0:
        executor_flags = _count_flagged_items(output)
        critique_flags = len(critique.get("flags", []))
        found = max(executor_flags, critique_flags)
        assert found >= min_flags, (
            f"[{case['case_id']}] flags: got {found} "
            f"(executor={executor_flags}, critique={critique_flags}), "
            f"expected >= {min_flags}"
        )

    # -- Must-flag partner: search executor output --
    partner = case.get("must_flag_partner")
    if partner:
        output_text = json.dumps(output)
        assert partner in output_text, (
            f"[{case['case_id']}] expected '{partner}' in executor output"
        )


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print e2e pass rate summary."""
    passed = len(terminalreporter.stats.get("passed", []))
    failed = len(terminalreporter.stats.get("failed", []))
    total = passed + failed
    if total:
        pct = passed * 100 // total
        terminalreporter.write_line(f"\nE2E pass rate: {passed}/{total} ({pct}%)")
