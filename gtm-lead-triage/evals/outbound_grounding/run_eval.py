"""Outbound grounding eval — measure fabrication on a held-out company set.

Usage:
  python -m evals.outbound_grounding.run_eval [--live] [--offline]

  --live (default): enrich each company via Apollo + search, snapshot briefs.
  --offline:        replay from snapshots/ (no network).

Outputs:
  evals/outbound_grounding/results.jsonl   — per-variant verdicts
  evals/outbound_grounding/flagged.jsonl   — human-review dump of flagged sentences
  stdout: per-company table + fabrication rates
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from evals.outbound_grounding.score import run_deterministic_check, run_llm_judge, print_report

logger = logging.getLogger(__name__)

_EVAL_DIR = Path(__file__).resolve().parent
_HELDOUT = _EVAL_DIR / "heldout_companies.json"
_SNAPSHOTS = _EVAL_DIR / "snapshots"
_RESULTS = _EVAL_DIR / "results.jsonl"
_FLAGGED = _EVAL_DIR / "flagged.jsonl"

_CAMPAIGN = {
    "name": "Productboard ICP",
    "icp_keywords": ["product management", "saas", "customer feedback"],
    "icp_employee_ranges": ["201,1000", "1001,5000"],
    "value_prop": "centralize scattered customer feedback and tie it to roadmap decisions",
    "target_persona": "Head of Product",
}

# Adversarial subset: sparse-fact briefs (industry only, no signals)
_ADVERSARIAL_DOMAINS = {"launchdarkly.com", "posthog.com", "pendo.io"}


def _build_brief_live(domain: str, persona_role: str) -> dict:
    """Build a real brief via Apollo + search (live API calls)."""
    os.environ.setdefault("APOLLO_SOURCE", "live")
    os.environ.setdefault("SEARCH_PROVIDER", "fixture")
    os.environ.setdefault("PRODUCTBOARD_SOURCE", "off")

    from gtm_triage.tools.research_company import ResearchCompanyTool
    tool = ResearchCompanyTool(provider="mock")
    return tool.run({"domain": domain, "role": persona_role})


def _build_brief_adversarial(domain: str) -> dict:
    """Build a sparse brief — industry only, no signals. Tempts embellishment."""
    os.environ["APOLLO_SOURCE"] = "off"
    os.environ["SEARCH_PROVIDER"] = "off"
    os.environ["PRODUCTBOARD_SOURCE"] = "off"

    from gtm_triage.tools.research_company import ResearchCompanyTool
    tool = ResearchCompanyTool(provider="mock")
    brief = tool.run({"domain": domain})

    # Strip everything except industry to make it truly sparse
    brief["recent_signals"] = []
    brief["tech_stack"] = []
    brief["likely_problems"] = []
    brief["what_they_do"] = None
    brief["is_requester"] = False
    return brief


def _snapshot_brief(domain: str, brief: dict) -> None:
    """Save brief to snapshots/ for reproducibility."""
    _SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    path = _SNAPSHOTS / f"{domain}.json"
    path.write_text(json.dumps(brief, indent=2, ensure_ascii=True), encoding="utf-8")


def _load_snapshot(domain: str) -> dict | None:
    """Load a previously saved brief snapshot."""
    path = _SNAPSHOTS / f"{domain}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _draft_variants(brief: dict, company: str, persona: str) -> list[dict]:
    """Run the draft tool (LLM path) and return the variants."""
    from gtm_triage.tools.draft_outbound import DraftOutboundTool

    provider = os.environ.get("GTM_PROVIDER", "openai")
    model = os.environ.get("DRAFT_MODEL", os.environ.get("GTM_MODEL", "gpt-5.4-nano"))
    tool = DraftOutboundTool(provider=provider, model=model)

    result = tool.run({
        "brief": brief,
        "campaign": _CAMPAIGN,
        "persona_role": persona,
        "company": company,
    })
    return result.get("drafts", [])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="Replay from snapshots (no network)")
    parser.add_argument("--live", action="store_true", default=True, help="Build briefs live (default)")
    args = parser.parse_args()

    offline = args.offline
    companies = json.loads(_HELDOUT.read_text(encoding="utf-8"))

    all_results: list[dict] = []
    all_flagged: list[dict] = []

    for entry in companies:
        domain = entry["domain"]
        persona = entry["persona_role"]
        segment = entry["segment"]
        is_adversarial = domain in _ADVERSARIAL_DOMAINS

        print(f"\n{'[ADV] ' if is_adversarial else ''}Processing {domain}...", flush=True)

        # Step 1: get or build brief
        if is_adversarial:
            brief = _build_brief_adversarial(domain)
            _snapshot_brief(f"adv_{domain}", brief)
        elif offline:
            brief = _load_snapshot(domain)
            if brief is None:
                print(f"  SKIP: no snapshot for {domain}")
                continue
        else:
            brief = _build_brief_live(domain, persona)
            _snapshot_brief(domain, brief)

        # Reset env for draft tool
        os.environ["APOLLO_SOURCE"] = "fixture"

        # Step 2: draft
        company_name = brief.get("domain", domain)
        variants = _draft_variants(brief, company_name, persona)

        if not variants:
            print(f"  WARN: no variants produced for {domain}")
            continue

        # Step 3: check each variant
        from gtm_triage.tools.draft_outbound import _build_grounded_facts
        facts = _build_grounded_facts(brief)

        for variant in variants:
            det_result = run_deterministic_check(variant, facts)
            llm_result = run_llm_judge(variant, facts)

            record = {
                "domain": domain,
                "segment": segment,
                "adversarial": is_adversarial,
                "variant": variant.get("variant", "?"),
                "subject": variant.get("subject", ""),
                "det_pass": det_result["pass"],
                "det_ungrounded_tokens": det_result["ungrounded_tokens"],
                "soft_fabrication_count": llm_result["fabrication_count"],
                "human_review_count": llm_result.get("human_review_count", 0),
                "llm_sentences": llm_result["sentences"],
                "grounded_on": variant.get("grounded_on", []),
            }
            all_results.append(record)

            # Collect flagged sentences: fabrications + human review items
            for sent in llm_result["sentences"]:
                if sent.get("fabrication"):
                    all_flagged.append({
                        "domain": domain,
                        "variant": variant.get("variant", "?"),
                        "sentence": sent["sentence"],
                        "reasoning": sent.get("reasoning", ""),
                        "flag_type": "fabrication",
                        "body": variant.get("body", ""),
                    })
                elif sent.get("human_review"):
                    all_flagged.append({
                        "domain": domain,
                        "variant": variant.get("variant", "?"),
                        "sentence": sent["sentence"],
                        "reasoning": sent.get("reasoning", ""),
                        "flag_type": "human_review",
                        "asserts_specific": sent.get("asserts_specific"),
                        "supported": sent.get("supported"),
                        "hedged": sent.get("hedged"),
                        "body": variant.get("body", ""),
                    })

    # Write results
    with open(_RESULTS, "w", encoding="utf-8") as f:
        for r in all_results:
            f.write(json.dumps(r, ensure_ascii=True) + "\n")
    with open(_FLAGGED, "w", encoding="utf-8") as f:
        for r in all_flagged:
            f.write(json.dumps(r, ensure_ascii=True) + "\n")

    print_report(all_results, all_flagged)


if __name__ == "__main__":
    main()
