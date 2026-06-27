"""Extraction eval — measures signal decomposition quality on dev_split.

Separate from tier accuracy. Reports:
  - Subject attribution accuracy (sender vs third_party vs company)
  - Over-extraction rate on thin-input leads
  - Evidence span correctness (is evidence a real substring of message?)

Usage:
    cd gtm-lead-triage
    python -m evals.eval_extraction --provider mock
    python -m evals.eval_extraction --provider openai
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from evals.dev_split import DEV_LEADS


def run_extraction_eval(provider: str = "mock", model: str = "gpt-4o-mini") -> dict:
    """Run extraction on dev_split leads and compare against expected_signals."""

    if provider == "openai":
        from gtm_triage.enrichment.signals import extract_signals_llm as extract_fn
    else:
        from gtm_triage.enrichment.signals import extract_signals_mock as extract_fn

    results = []
    total_expected = 0
    total_predicted = 0
    subject_correct = 0
    subject_total = 0
    evidence_valid = 0
    evidence_total = 0
    over_extraction_cases = 0
    thin_input_cases = 0

    for case in DEV_LEADS:
        lead = case["lead"]
        expected = case.get("expected_signals", [])
        message = lead.get("message", "")

        extraction = extract_fn(
            name=lead.get("name", ""),
            message=message,
            email=lead.get("email", ""),
        )

        predicted_signals = extraction.signals
        total_expected += len(expected)
        total_predicted += len(predicted_signals)

        # Thin-input check: if expected_signals is empty, any predicted signal is over-extraction
        is_thin = len(expected) == 0
        if is_thin:
            thin_input_cases += 1
            if len(predicted_signals) > 0:
                over_extraction_cases += 1

        # Evidence span check: every predicted evidence must be a substring of message
        for sig in predicted_signals:
            if sig.evidence:
                evidence_total += 1
                if sig.evidence.lower() in message.lower():
                    evidence_valid += 1

        # Subject attribution check: match predicted to expected by type
        for exp_sig in expected:
            exp_type = exp_sig["type"]
            exp_subject = exp_sig["subject"]

            # Find a predicted signal of the same type
            matching = [s for s in predicted_signals if _type_matches(s.type, exp_type)]
            if matching:
                subject_total += 1
                # Check if any matching signal has the correct subject
                if any(s.subject == exp_subject for s in matching):
                    subject_correct += 1

        results.append({
            "email": lead["email"],
            "expected_count": len(expected),
            "predicted_count": len(predicted_signals),
            "predicted_types": [s.type for s in predicted_signals],
            "predicted_subjects": [s.subject for s in predicted_signals],
            "is_thin": is_thin,
            "over_extracted": is_thin and len(predicted_signals) > 0,
        })

    summary = {
        "provider": provider,
        "total_leads": len(DEV_LEADS),
        "total_expected_signals": total_expected,
        "total_predicted_signals": total_predicted,
        "subject_attribution_accuracy": round(subject_correct / subject_total, 3) if subject_total > 0 else 0.0,
        "subject_correct": subject_correct,
        "subject_total": subject_total,
        "evidence_span_accuracy": round(evidence_valid / evidence_total, 3) if evidence_total > 0 else 0.0,
        "evidence_valid": evidence_valid,
        "evidence_total": evidence_total,
        "thin_input_cases": thin_input_cases,
        "over_extraction_cases": over_extraction_cases,
        "over_extraction_rate": round(over_extraction_cases / thin_input_cases, 3) if thin_input_cases > 0 else 0.0,
    }

    return {"cases": results, "summary": summary}


def _type_matches(predicted_type: str, expected_type: str) -> bool:
    """Check if a predicted signal type matches an expected type."""
    if predicted_type == expected_type:
        return True
    # Map atomic types to expected types
    mapping = {
        "opt_out": "intent",
        "legal": "intent",
        "spam": "intent",
    }
    return mapping.get(predicted_type, predicted_type) == expected_type


def main() -> int:
    parser = argparse.ArgumentParser(description="Extraction eval on dev_split")
    parser.add_argument("--provider", default="mock", choices=["mock", "openai"])
    parser.add_argument("--model", default="gpt-4o-mini")
    args = parser.parse_args()

    result = run_extraction_eval(provider=args.provider, model=args.model)
    summary = result["summary"]

    print(f"\n{'='*70}")
    print(f"  Extraction Eval — dev_split ({summary['total_leads']} leads, provider={args.provider})")
    print(f"{'='*70}\n")

    print(f"  Subject attribution accuracy:  {summary['subject_correct']}/{summary['subject_total']} "
          f"({summary['subject_attribution_accuracy']:.1%})")
    print(f"  Evidence span accuracy:        {summary['evidence_valid']}/{summary['evidence_total']} "
          f"({summary['evidence_span_accuracy']:.1%})")
    print(f"  Over-extraction on thin input: {summary['over_extraction_cases']}/{summary['thin_input_cases']} "
          f"({summary['over_extraction_rate']:.1%})")
    print(f"  Signals: {summary['total_expected_signals']} expected, {summary['total_predicted_signals']} predicted")

    # Per-case details for failures
    print(f"\n  {'Email':<35} {'Exp':>4} {'Pred':>5} {'Notes'}")
    print(f"  {'-'*35} {'-'*4} {'-'*5} {'-'*30}")
    for c in result["cases"]:
        notes = ""
        if c["over_extracted"]:
            notes = "OVER-EXTRACTED (thin input)"
        elif c["expected_count"] > 0 and c["predicted_count"] == 0:
            notes = "MISSED ALL"
        print(f"  {c['email']:<35} {c['expected_count']:>4} {c['predicted_count']:>5} {notes}")

    # Write JSONL
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)
    output_path = results_dir / f"extraction_eval_{args.provider}_{date_str}.jsonl"
    with open(output_path, "w") as f:
        f.write(json.dumps({"type": "summary", **summary}) + "\n")
        for c in result["cases"]:
            f.write(json.dumps({"type": "case", **c}) + "\n")
    print(f"\n  Results written to: {output_path}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
