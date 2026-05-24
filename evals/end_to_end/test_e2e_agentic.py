# evals/end_to_end/test_e2e_agentic.py

"""Agentic e2e eval — drives AetherRuntime.run_agentic() and checks verdict,
flags, and partner against the same cases.json used by the baseline harness.

Output shape differs from run():
  run_agentic() -> {
      "loop_state": {
          "steps": [{"observation": {"output": {flagged, total_flagged, ...}}, ...}],
          ...
      },
      "critique": {"overall_verdict": str, "flags": [...], ...},
      ...
  }

test_e2e.py (baseline / run()) is NOT touched — it remains the control.
"""

import json
from pathlib import Path

import pytest

from aether.runtime import AetherRuntime

CASES_PATH = Path(__file__).parent / "cases.json"

_cases = json.loads(CASES_PATH.read_text())
_quick_ids = {c["case_id"] for c in _cases[:3]}


def pytest_configure(config):
    config.addinivalue_line("markers", "quick: only run first 3 cases")


def _count_flagged_items(result: dict) -> int:
    """Count flagged items from the agentic result shape.

    Walks loop_state.steps[*].observation.output looking for dicts with
    flagged=True and sums total_flagged. Falls back to len(critique.flags).
    """
    steps = result.get("loop_state", {}).get("steps", [])
    count = 0
    for step in steps:
        output = step.get("observation", {}).get("output", {})
        if isinstance(output, dict) and output.get("flagged"):
            count += output.get("total_flagged", 1)
    if count > 0:
        return count
    # Fallback: critic flags (conservative — critic may over- or under-count)
    return len(result.get("critique", {}).get("flags", []))


def _observation_outputs_text(result: dict) -> str:
    """Serialise all observation outputs to a single string for substring checks."""
    steps = result.get("loop_state", {}).get("steps", [])
    outputs = [step.get("observation", {}).get("output", {}) for step in steps]
    return json.dumps(outputs, default=str)


# ── Fixtures ──────────────────────────────────────────────────────────────────
# scope="module": encoder + reranker load once for all 15 cases (~10-32s total,
# not per-case). eval_mode=True uses EphemeralClient (in-memory Chroma) so the
# persistent ./chroma_db used by dev/scratch runs is never touched or polluted.

@pytest.fixture(scope="module")
def runtime():
    return AetherRuntime(eval_mode=True)


@pytest.fixture(autouse=True)
def reset_retriever(runtime):
    # Per-case isolation: reset all stateful objects held by the module-scoped
    # runtime before each test.
    #   - retriever: clears Chroma index + BM25 state
    #   - tool state: clears FlagItemTool._flags and LoadDataTool._registry so
    #     flags and loaded tables from case N don't bleed into case N+1.
    runtime.retriever.reset_index()
    runtime.reset_tool_state()
    yield


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "case", _cases, ids=[c["case_id"] for c in _cases]
)
def test_e2e_agentic(runtime, case, request):
    if request.node.get_closest_marker("quick") or "quick" in request.config.getoption("-m", default=""):
        if case["case_id"] not in _quick_ids:
            pytest.skip("skipped in quick mode")

    result = runtime.run_agentic(case["goal"], case["file_paths"], max_steps=10)
    critique = result["critique"]

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

    # -- Flag count --
    min_flags = case["expected_flags_min"]
    if min_flags > 0:
        found = _count_flagged_items(result)
        critique_flags = len(critique.get("flags", []))
        assert found >= min_flags, (
            f"[{case['case_id']}] flags: got {found} "
            f"(steps={found}, critique={critique_flags}), "
            f"expected >= {min_flags}"
        )

    # -- Must-flag partner --
    partner = case.get("must_flag_partner")
    if partner:
        obs_text = _observation_outputs_text(result)
        assert partner in obs_text, (
            f"[{case['case_id']}] expected '{partner}' in observation outputs"
        )


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print agentic e2e pass rate summary."""
    passed = len(terminalreporter.stats.get("passed", []))
    failed = len(terminalreporter.stats.get("failed", []))
    total = passed + failed
    if total:
        pct = passed * 100 // total
        terminalreporter.write_line(f"\nAgentic E2E pass rate: {passed}/{total} ({pct}%)")
