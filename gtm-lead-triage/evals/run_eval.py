"""Mock-mode eval runner (CI gate) for the GTM lead-triage agent.

Runs the FULL agent loop on the 5 MOCK_LEADS subset with provider=mock.
Asserts BOTH tier AND route. Exits nonzero on any miss.
This is the deterministic CI gate — no API key needed.

Usage:
    cd gtm-lead-triage
    python -m evals.run_eval
"""

from __future__ import annotations

import sys

from evals.cases import MOCK_LEADS
from gtm_triage.agents.executor import Executor
from gtm_triage.agents.loop_agent import run_triage
from gtm_triage.crm.sqlite_crm import SQLiteCRM
from gtm_triage.models.lead import Lead
from gtm_triage.tools.crm_lookup import CRMLookupTool
from gtm_triage.tools.draft_outreach import DraftOutreachTool
from gtm_triage.tools.enrich_lead import EnrichLeadTool
from gtm_triage.tools.registry import ToolRegistry
from gtm_triage.tools.score_lead import ScoreLeadTool
from gtm_triage.trace.store import TraceStore


def main() -> int:
    crm = SQLiteCRM(":memory:")
    trace = TraceStore(":memory:")

    registry = ToolRegistry([
        CRMLookupTool(crm),
        EnrichLeadTool(provider="mock"),
        ScoreLeadTool(provider="mock"),
        DraftOutreachTool(),
    ])
    executor = Executor(registry, trace)

    correct = 0
    total = len(MOCK_LEADS)

    print(f"\n{'='*72}")
    print(f"  GTM Lead-Triage Eval — {total} mock leads, provider=mock (CI gate)")
    print(f"{'='*72}\n")
    print(f"  {'Email':<35} {'Expected':>18} {'Got':>18} {'Match':>6}")
    print(f"  {'-'*35} {'-'*18} {'-'*18} {'-'*6}")

    for case in MOCK_LEADS:
        lead = Lead(**case["lead"])
        expected_tier = case["expected_tier"]
        expected_route = case["expected_route"]

        result = run_triage(
            lead=lead,
            executor=executor,
            trace=trace,
            provider="mock",
            model="mock",
        )

        got_tier = result.final_tier or "???"
        got_route = result.final_route or "???"
        tier_ok = got_tier == expected_tier
        route_ok = got_route == expected_route
        match = tier_ok and route_ok

        if match:
            correct += 1

        marker = "OK" if match else "FAIL"
        expected_str = f"{expected_tier}/{expected_route}"
        got_str = f"{got_tier}/{got_route}"
        print(f"  {lead.email:<35} {expected_str:>18} {got_str:>18} {marker:>6}")

        if not match:
            if not tier_ok:
                print(f"    ^ tier mismatch: expected={expected_tier} got={got_tier}")
            if not route_ok:
                print(f"    ^ route mismatch: expected={expected_route} got={got_route}")
            if result.score:
                print(f"    ^ score detail: points={result.score.get('points')} "
                      f"rule_points={result.score.get('rule_points')} "
                      f"llm_adj={result.score.get('llm_adjustment')} "
                      f"reason={result.score.get('reason')}")

    print(f"\n  {'='*72}")
    print(f"  Score: {correct}/{total}")
    print(f"  {'='*72}\n")

    if correct == total:
        print("  All leads triaged correctly.\n")
        return 0
    else:
        print(f"  FAILED: {total - correct} lead(s) misclassified.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
