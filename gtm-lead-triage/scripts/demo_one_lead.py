"""Demo: run the GTM triage agent on a single lead and print the trace.

Usage:
    cd gtm-lead-triage
    python -m scripts.demo_one_lead
"""

from __future__ import annotations

import json

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


def main() -> None:
    crm = SQLiteCRM(":memory:")
    trace = TraceStore(":memory:")

    registry = ToolRegistry([
        CRMLookupTool(crm),
        EnrichLeadTool(),
        ScoreLeadTool(),
        DraftOutreachTool(),
    ])
    executor = Executor(registry, trace)

    lead = Lead(
        email="j.martinez@acmefintech.com",
        name="Julia Martinez, VP of Sales",
        company="Acme Fintech International",
        message="We'd like to schedule a demo for our trading desk. Urgent need.",
    )

    print(f"\n{'='*60}")
    print(f"  GTM Triage — Single Lead Demo (provider=mock)")
    print(f"{'='*60}\n")
    print(f"  Lead: {lead.email} ({lead.name} @ {lead.company})")
    print(f"  Message: {lead.message}\n")

    result = run_triage(
        lead=lead,
        executor=executor,
        trace=trace,
        provider="mock",
        model="mock",
    )

    print(f"  --- Step-by-step trace ---\n")
    for step in result.steps:
        if step.action.tool:
            print(f"  Step {step.step_index}: {step.action.tool}")
            print(f"    Reasoning: {step.action.reasoning}")
            if step.observation.output:
                out_str = json.dumps(step.observation.output, indent=2)
                # Indent each line for readability
                for line in out_str.split("\n"):
                    print(f"    {line}")
            if step.observation.error:
                print(f"    ERROR: {step.observation.error}")
        else:
            print(f"  Step {step.step_index}: [FINAL]")
            print(f"    Reasoning: {step.action.reasoning}")
        print()

    print(f"  --- Result ---\n")
    print(f"  Tier:  {result.final_tier}")
    print(f"  Route: {result.final_route}")
    if result.score:
        print(f"  Score: {result.score.get('points')} "
              f"(rules={result.score.get('rule_points')}, "
              f"llm_adj={result.score.get('llm_adjustment')})")
        print(f"  Reason: {result.score.get('reason')}")
    print()

    # Print trace events
    events = trace.get_run_events(result.run_id)
    print(f"  Trace events: {len(events)} rows in SQLite")
    for e in events:
        print(f"    [{e['event_type']:<15}] agent={e['agent']}")
    print()


if __name__ == "__main__":
    main()
