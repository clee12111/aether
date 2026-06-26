"""OpenAI-mode eval runner for the GTM lead-triage agent.

Runs the FULL agent loop with provider=openai. Two modes:

  python -m evals.run_eval_openai              # 22 golden leads (seen data)
  python -m evals.run_eval_openai --holdout    # 10 held-out leads (unseen data)
  python -m evals.run_eval_openai --model gpt-4o-mini

Reports tier accuracy %, route accuracy %, every disagreement with rationale,
and latency + cost per lead from the trace store.

If OPENAI_API_KEY is unset, skips gracefully with a clear message.
"""

from __future__ import annotations

import os
import sys
import time

from evals.cases import GOLDEN_LEADS
from evals.holdout import HOLDOUT_LEADS
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


def _load_env() -> None:
    """Try to load .env from parent directory if OPENAI_API_KEY not already set."""
    if os.environ.get("OPENAI_API_KEY"):
        return
    for path in ("../.env", ".env"):
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, val = line.partition("=")
                        os.environ.setdefault(key.strip(), val.strip())
            break


def main() -> int:
    _load_env()

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("\n  OPENAI_API_KEY not set. Skipping openai eval.")
        print("  Set it in your environment or in ../.env to run this eval.\n")
        return 0

    model = "gpt-4o-mini"
    if "--model" in sys.argv:
        idx = sys.argv.index("--model")
        if idx + 1 < len(sys.argv):
            model = sys.argv[idx + 1]

    use_holdout = "--holdout" in sys.argv
    leads = HOLDOUT_LEADS if use_holdout else GOLDEN_LEADS
    set_label = "held-out" if use_holdout else "golden"

    crm = SQLiteCRM(":memory:")
    trace = TraceStore(":memory:")

    # Pre-seed CRM for existing-customer test cases
    for case in leads:
        if "crm_seed" in case:
            seed = case["crm_seed"]
            crm.upsert(seed["email"], seed)

    registry = ToolRegistry([
        CRMLookupTool(crm),
        EnrichLeadTool(provider="openai", model=model),
        ScoreLeadTool(provider="openai", model=model),
        DraftOutreachTool(),
    ])
    executor = Executor(registry, trace)

    total = len(leads)
    tier_correct = 0
    route_correct = 0
    disagreements: list[dict] = []
    latencies: list[int] = []
    costs: list[float] = []
    llm_influence_cases: list[dict] = []

    print(f"\n{'='*80}")
    print(f"  GTM Lead-Triage Eval — {total} {set_label} leads, provider=openai, model={model}")
    print(f"{'='*80}\n")
    print(f"  {'#':<3} {'Email':<35} {'Expected':>18} {'Got':>18} {'Tier':>5} {'Route':>6}")
    print(f"  {'-'*3} {'-'*35} {'-'*18} {'-'*18} {'-'*5} {'-'*6}")

    for i, case in enumerate(leads, 1):
        lead = Lead(**case["lead"])
        expected_tier = case["expected_tier"]
        expected_route = case["expected_route"]
        rationale = case.get("rationale", "")
        review = case.get("review", False)

        t0 = time.time()
        result = run_triage(
            lead=lead,
            executor=executor,
            trace=trace,
            provider="openai",
            model=model,
        )
        wall_ms = int((time.time() - t0) * 1000)

        got_tier = result.final_tier or "???"
        got_route = result.final_route or "???"
        tier_ok = got_tier == expected_tier
        route_ok = got_route == expected_route

        if tier_ok:
            tier_correct += 1
        if route_ok:
            route_correct += 1

        # Get trace stats for this run
        stats = trace.get_run_stats(result.run_id)
        latencies.append(wall_ms)
        costs.append(stats["estimated_cost_usd"])

        tier_mark = "OK" if tier_ok else "MISS"
        route_mark = "OK" if route_ok else "MISS"
        expected_str = f"{expected_tier}/{expected_route}"
        got_str = f"{got_tier}/{got_route}"
        review_flag = " [review]" if review else ""
        print(f"  {i:<3} {lead.email:<35} {expected_str:>18} {got_str:>18} {tier_mark:>5} {route_mark:>6}{review_flag}")

        if not tier_ok or not route_ok:
            disagreements.append({
                "index": i,
                "email": lead.email,
                "expected_tier": expected_tier,
                "expected_route": expected_route,
                "got_tier": got_tier,
                "got_route": got_route,
                "human_rationale": rationale,
                "agent_reason": result.score.get("reason", "") if result.score else "",
                "llm_adjustment": result.score.get("llm_adjustment", 0) if result.score else 0,
                "llm_reason": result.score.get("llm_reason", "") if result.score else "",
                "points": result.score.get("points", 0) if result.score else 0,
                "rule_points": result.score.get("rule_points", 0) if result.score else 0,
                "review": review,
            })

        # Track cases where LLM changed the outcome vs rules-only
        if result.score:
            llm_adj = result.score.get("llm_adjustment", 0)
            rule_pts = result.score.get("rule_points", 0)
            total_pts = result.score.get("points", 0)
            if llm_adj != 0:
                # Check if enrichment used LLM
                enrich_sources = {}
                if result.enrichment:
                    enrich_sources = result.enrichment.get("field_sources", {})
                llm_influence_cases.append({
                    "email": lead.email,
                    "rule_points": rule_pts,
                    "llm_adjustment": llm_adj,
                    "total_points": total_pts,
                    "llm_reason": result.score.get("llm_reason", ""),
                    "enrich_llm_fields": {k: v for k, v in enrich_sources.items() if v == "llm"},
                })

    # Summary
    tier_pct = tier_correct / total * 100
    route_pct = route_correct / total * 100
    median_latency = sorted(latencies)[len(latencies) // 2] if latencies else 0
    total_cost = sum(costs)
    median_cost = sorted(costs)[len(costs) // 2] if costs else 0

    print(f"\n  {'='*80}")
    print(f"  AGREEMENT WITH HUMAN LABELS:")
    print(f"    Tier accuracy:  {tier_correct}/{total} ({tier_pct:.1f}%)")
    print(f"    Route accuracy: {route_correct}/{total} ({route_pct:.1f}%)")
    print(f"  {'='*80}")

    if disagreements:
        print(f"\n  DISAGREEMENTS ({len(disagreements)}):\n")
        for d in disagreements:
            review_tag = " [REVIEW — human label uncertain]" if d["review"] else ""
            print(f"  #{d['index']} {d['email']}{review_tag}")
            print(f"    Human:  {d['expected_tier']}/{d['expected_route']} — {d['human_rationale'][:100]}")
            print(f"    Agent:  {d['got_tier']}/{d['got_route']} — pts={d['points']} (rules={d['rule_points']}, llm_adj={d['llm_adjustment']})")
            if d["llm_reason"]:
                print(f"    LLM reason: {d['llm_reason']}")
            print()

    if llm_influence_cases:
        print(f"\n  LLM INFLUENCE (cases where llm_adjustment != 0):\n")
        for c in llm_influence_cases:
            print(f"    {c['email']}: rules={c['rule_points']}, llm_adj={c['llm_adjustment']:+d}, total={c['total_points']}")
            if c["llm_reason"]:
                print(f"      reason: {c['llm_reason']}")
            if c["enrich_llm_fields"]:
                print(f"      enrichment LLM fields: {c['enrich_llm_fields']}")
        print()

    print(f"\n  LATENCY & COST (provider=openai, model={model}):")
    print(f"    Median latency per lead: {median_latency} ms")
    print(f"    Total cost (all leads):  ${total_cost:.4f}")
    print(f"    Median cost per lead:    ${median_cost:.6f}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
