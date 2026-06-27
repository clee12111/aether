"""Eval metrics harness: per-tier precision/recall, confusion matrix, false-hot/false-cold.

Runs the full agent loop on a lead set, compares predicted vs. expected tiers,
and writes structured JSONL to evals/results/.

Output format (one JSON object per line in the JSONL):
  Line 1: {"type": "meta", ...}       — run metadata
  Lines 2-N+1: {"type": "case", ...}   — per-case results
  Line N+2: {"type": "summary", ...}   — aggregate metrics

Usage:
    cd gtm-lead-triage
    python -m evals.metrics --set holdout_v2 --provider mock
    python -m evals.metrics --set golden --provider mock
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

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

_TIERS = ["hot", "warm", "cold", "disqualified"]
_ROUTES = {"hot": "ae_immediate", "warm": "sdr_nurture", "cold": "marketing_nurture", "disqualified": "drop"}


def _load_leads(set_name: str) -> list[dict]:
    if set_name == "holdout_v2":
        from evals.holdout_v2 import INDEPENDENT_LEADS
        return INDEPENDENT_LEADS
    elif set_name == "dev_split":
        from evals.dev_split import DEV_LEADS
        return DEV_LEADS
    elif set_name == "golden":
        from evals.cases import GOLDEN_LEADS
        return GOLDEN_LEADS
    elif set_name == "holdout":
        from evals.holdout import HOLDOUT_LEADS
        return HOLDOUT_LEADS
    elif set_name == "mock":
        from evals.cases import MOCK_LEADS
        return MOCK_LEADS
    else:
        raise ValueError(f"Unknown lead set: {set_name!r}")


def compute_metrics(
    expected: list[str],
    predicted: list[str],
) -> dict:
    """Compute per-tier precision/recall, confusion matrix, false-hot, false-cold."""

    # Confusion matrix: confusion[expected][predicted] = count
    confusion: dict[str, dict[str, int]] = {t: {p: 0 for p in _TIERS} for t in _TIERS}
    for exp, pred in zip(expected, predicted):
        if exp in confusion and pred in confusion[exp]:
            confusion[exp][pred] += 1

    # Per-tier precision and recall
    tier_metrics: dict[str, dict] = {}
    for tier in _TIERS:
        tp = confusion[tier][tier]
        # Predicted as this tier (column sum)
        predicted_as = sum(confusion[e][tier] for e in _TIERS)
        # Actually this tier (row sum)
        actual = sum(confusion[tier][p] for p in _TIERS)

        precision = tp / predicted_as if predicted_as > 0 else 0.0
        recall = tp / actual if actual > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        tier_metrics[tier] = {
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
            "support": actual,
            "predicted_count": predicted_as,
            "true_positives": tp,
        }

    # Overall accuracy
    total = len(expected)
    correct = sum(1 for e, p in zip(expected, predicted) if e == p)

    # False-hot: predicted hot, actually NOT hot
    # Business cost: wasted AE time on a non-buyer
    false_hot = sum(1 for e, p in zip(expected, predicted) if p == "hot" and e != "hot")
    predicted_hot_total = sum(1 for p in predicted if p == "hot")

    # False-cold: predicted cold or disqualified, actually warm or hot
    # Business cost: lost deal — a real buyer was ignored
    false_cold = sum(
        1 for e, p in zip(expected, predicted)
        if p in ("cold", "disqualified") and e in ("hot", "warm")
    )
    actual_hot_warm = sum(1 for e in expected if e in ("hot", "warm"))

    return {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 3) if total > 0 else 0.0,
        "per_tier": tier_metrics,
        "confusion_matrix": confusion,
        "false_hot_count": false_hot,
        "false_hot_rate": round(false_hot / predicted_hot_total, 3) if predicted_hot_total > 0 else 0.0,
        "false_cold_count": false_cold,
        "false_cold_rate": round(false_cold / actual_hot_warm, 3) if actual_hot_warm > 0 else 0.0,
        "small_n_caveat": total < 100,
    }


def _build_enrichment_provider(enrichment_mode: str):
    """Build an EnrichmentProvider based on mode string.

    Returns None for 'regex' (legacy path), or a real provider instance.
    """
    if enrichment_mode == "regex":
        return None

    from pathlib import Path
    cassettes_path = Path(__file__).parent.parent / "gtm_triage" / "enrichment" / "cache" / "pdl_cassettes.json"

    if enrichment_mode == "pdl":
        from gtm_triage.enrichment.pdl_provider import PDLProvider
        from gtm_triage.enrichment.waterfall import WaterfallProvider
        pdl = PDLProvider(cache_path=cassettes_path)
        return WaterfallProvider(pdl, skip_dns=True, skip_website=True)

    raise ValueError(f"Unknown enrichment mode: {enrichment_mode!r}")


def run_eval(
    lead_set: list[dict],
    provider: str = "mock",
    model: str = "gpt-4o-mini",
    enrichment_mode: str = "regex",
    extractor: str = "B",
) -> tuple[list[dict], dict]:
    """Run the full agent loop on all leads, return (case_results, summary_metrics)."""

    crm = SQLiteCRM(":memory:")
    trace = TraceStore(":memory:")

    # Seed CRM for leads with crm_seed
    for case in lead_set:
        if "crm_seed" in case:
            crm.upsert(case["crm_seed"]["email"], case["crm_seed"])

    enrichment_provider = _build_enrichment_provider(enrichment_mode)

    registry = ToolRegistry([
        CRMLookupTool(crm),
        EnrichLeadTool(provider=provider, model=model, enrichment_provider=enrichment_provider, extractor=extractor),
        ScoreLeadTool(provider=provider, model=model),
        DraftOutreachTool(),
    ])
    executor = Executor(registry, trace)

    case_results = []
    expected_tiers = []
    predicted_tiers = []

    for case in lead_set:
        lead = Lead(**case["lead"])
        result = run_triage(
            lead=lead,
            executor=executor,
            trace=trace,
            provider=provider,
            model=model,
        )

        got_tier = result.final_tier or "???"
        got_route = result.final_route or "???"
        exp_tier = case["expected_tier"]
        exp_route = case["expected_route"]

        tier_match = got_tier == exp_tier
        route_match = got_route == exp_route

        case_result = {
            "type": "case",
            "email": lead.email,
            "expected_tier": exp_tier,
            "expected_route": exp_route,
            "predicted_tier": got_tier,
            "predicted_route": got_route,
            "tier_match": tier_match,
            "route_match": route_match,
            "match": tier_match and route_match,
            "review": case.get("review", False),
            "rationale": case.get("rationale", ""),
            "score_detail": result.score,
            "enrichment_detail": result.enrichment,
            "steps_taken": len(result.steps),
            "trace_path": result.trace_path,
        }
        case_results.append(case_result)

        expected_tiers.append(exp_tier)
        predicted_tiers.append(got_tier)

    summary = compute_metrics(expected_tiers, predicted_tiers)
    return case_results, summary


def print_report(case_results: list[dict], summary: dict, set_name: str, provider: str) -> None:
    """Print a human-readable report to stdout."""
    total = summary["total"]
    correct = summary["correct"]

    print(f"\n{'='*80}")
    print(f"  GTM Lead-Triage Eval — {set_name} ({total} leads, provider={provider})")
    print(f"{'='*80}\n")

    # Per-case results
    print(f"  {'Email':<40} {'Expected':>13} {'Got':>13} {'Match':>6}")
    print(f"  {'-'*40} {'-'*13} {'-'*13} {'-'*6}")
    for c in case_results:
        marker = "OK" if c["match"] else "FAIL"
        review = " [R]" if c["review"] else ""
        print(f"  {c['email']:<40} {c['expected_tier']:>13} {c['predicted_tier']:>13} {marker:>6}{review}")
        if not c["match"]:
            sd = c.get("score_detail") or {}
            print(f"    ^ points={sd.get('points', '?')} rule={sd.get('rule_points', '?')} reason={sd.get('reason', '?')[:80]}")

    # Aggregate
    print(f"\n  {'='*80}")
    print(f"  Overall: {correct}/{total} ({summary['accuracy']:.1%})")
    if summary["small_n_caveat"]:
        print(f"  [!] Small-N caveat: {total} leads -- per-tier metrics have wide confidence intervals.")

    # Per-tier table
    print(f"\n  {'Tier':<15} {'Prec':>8} {'Recall':>8} {'F1':>8} {'Support':>8} {'Predicted':>10}")
    print(f"  {'-'*15} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*10}")
    for tier in _TIERS:
        m = summary["per_tier"][tier]
        print(f"  {tier:<15} {m['precision']:>8.3f} {m['recall']:>8.3f} {m['f1']:>8.3f} {m['support']:>8} {m['predicted_count']:>10}")

    # False-hot / false-cold
    print(f"\n  False-hot (predicted hot, actually not):  {summary['false_hot_count']} "
          f"({summary['false_hot_rate']:.1%} of predicted-hot)")
    print(f"  False-cold (predicted cold/disq, actually warm+): {summary['false_cold_count']} "
          f"({summary['false_cold_rate']:.1%} of actual-warm+)")

    # Confusion matrix
    print(f"\n  Confusion Matrix (rows=expected, cols=predicted):")
    print(f"  {'':>15}", end="")
    for t in _TIERS:
        print(f" {t:>13}", end="")
    print()
    for exp in _TIERS:
        print(f"  {exp:>15}", end="")
        for pred in _TIERS:
            print(f" {summary['confusion_matrix'][exp][pred]:>13}", end="")
        print()

    print(f"\n  {'='*80}\n")


def write_jsonl(
    case_results: list[dict],
    summary: dict,
    set_name: str,
    provider: str,
    output_path: Path,
) -> None:
    """Write structured JSONL output for diffing and dashboard consumption."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        # Meta line
        meta = {
            "type": "meta",
            "set_name": set_name,
            "provider": provider,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_leads": summary["total"],
        }
        f.write(json.dumps(meta) + "\n")

        # Case lines
        for c in case_results:
            f.write(json.dumps(c) + "\n")

        # Summary line
        summary_record = {"type": "summary", **summary}
        f.write(json.dumps(summary_record) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="GTM Lead-Triage eval harness")
    parser.add_argument("--set", default="holdout_v2", choices=["holdout_v2", "dev_split", "golden", "holdout", "mock"],
                        help="Lead set to evaluate")
    parser.add_argument("--provider", default="mock", choices=["mock", "openai"],
                        help="LLM provider")
    parser.add_argument("--enrichment", default="regex", choices=["regex", "pdl"],
                        help="Enrichment mode: regex (legacy) or pdl (waterfall via cassettes)")
    parser.add_argument("--extractor", default="B", choices=["A", "B"],
                        help="Extractor: A (Phase E flat) or B (Phase E.2 atomic signals)")
    parser.add_argument("--model", default="gpt-4o-mini", help="Model name")
    parser.add_argument("--output", default=None, help="Output JSONL path (default: auto-generated)")
    args = parser.parse_args()

    lead_set = _load_leads(args.set)
    case_results, summary = run_eval(
        lead_set, provider=args.provider, model=args.model,
        enrichment_mode=args.enrichment, extractor=args.extractor,
    )
    label = f"{args.set} (enrichment={args.enrichment}, extractor={args.extractor})"
    print_report(case_results, summary, label, args.provider)

    # Write JSONL
    if args.output:
        output_path = Path(args.output)
    else:
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        results_dir = Path(__file__).parent / "results"
        enrich_tag = f"_{args.enrichment}" if args.enrichment != "regex" else ""
        ext_tag = f"_ext{args.extractor}" if args.extractor != "B" else ""
        output_path = results_dir / f"eval_{args.set}_{args.provider}{enrich_tag}{ext_tag}_{date_str}.jsonl"

    write_jsonl(case_results, summary, label, args.provider, output_path)
    print(f"  Results written to: {output_path}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
