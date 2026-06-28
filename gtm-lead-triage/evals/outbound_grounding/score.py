"""Outbound grounding scorer — two SEPARATE layers, never blended.

1. Deterministic: fact_id + token verifier (reuses draft_outbound patterns).
2. LLM-judge: STRUCTURED judgment with three booleans per sentence, label
   derived IN CODE — the LLM can't contradict its own reasoning.

Disagreement routing: if the deterministic verifier and the judge disagree,
or the judge's booleans are internally inconsistent, the sentence is routed
to flagged.jsonl for human review — never silently resolved.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


# ── Layer 1: Deterministic check ────────────────────────────────────────────

_SPECIFIC_PATTERNS = [
    re.compile(r"\$[\d,.]+[MBKmk]?"),              # dollar amounts
    re.compile(r"\b\d{1,3}(?:,\d{3})+\b"),          # large numbers with commas
    re.compile(r"\bSeries [A-Z]\b"),                # specific funding rounds
    re.compile(r"\bIPO\b"),                         # IPO mention
]


def run_deterministic_check(variant: dict, facts: list[dict]) -> dict:
    """Check that every specific token in the body is grounded in a fact.

    Returns {pass: bool, ungrounded_tokens: list[str]}.
    """
    body = variant.get("body", "")
    all_fact_text = " ".join(f["text"] for f in facts).lower()
    fact_ids = {f["fact_id"] for f in facts}

    # Check grounded_on fact_ids exist
    bad_ids = [fid for fid in variant.get("grounded_on", []) if fid not in fact_ids]

    # Scan body for specific tokens
    specifics = []
    for pat in _SPECIFIC_PATTERNS:
        specifics.extend(pat.findall(body))

    ungrounded = []
    for token in specifics:
        if token.lower() not in all_fact_text:
            ungrounded.append(token)

    ungrounded.extend(bad_ids)
    return {"pass": len(ungrounded) == 0, "ungrounded_tokens": ungrounded}


# ── Layer 2: Structured LLM-judge ──────────────────────────────────────────

_JUDGE_SYSTEM = """You are a factual accuracy auditor. Given a sales email draft and a list of GROUNDED FACTS, analyze each sentence that makes a claim about the target company.

For each such sentence, return a JSON object with EXACTLY these boolean fields:
  {
    "sentence": "the verbatim sentence",
    "asserts_specific_fact_about_target": true/false,
    "fact_supported_by_brief": true/false,
    "is_hedged_or_generalized": true/false,
    "reasoning": "one-line explanation"
  }

FIELD DEFINITIONS:
  asserts_specific_fact_about_target: true if the sentence states something specific about THIS company (not about the sender, not a question, not a CTA). Examples: "you launched X", "your team uses Y", "you raised $Z". False for sender-product statements, questions, greetings.
  fact_supported_by_brief: true if the specific assertion is entailed by one of the grounded facts. False if the fact is not in the brief.
  is_hedged_or_generalized: true if the sentence uses hedged/generalized phrasing that does NOT claim to be a specific fact about the target. Examples: "teams in this space often…", "companies at this stage tend to…", "as organizations scale…". False if it directly asserts something specific about the target.

Return ONLY a JSON array. If no sentence makes a claim about the target company, return [].
Do NOT include greetings, sender-product statements, CTAs, or questions."""


def run_llm_judge(variant: dict, facts: list[dict]) -> dict:
    """Structured LLM judgment with deterministic label derivation.

    Returns {fabrication_count, human_review_count, sentences}.
    Each sentence: {sentence, asserts_specific, supported, hedged, fabrication, human_review, reasoning}.
    """
    body = variant.get("body", "")
    if not body.strip():
        return {"fabrication_count": 0, "human_review_count": 0, "sentences": []}

    facts_block = "\n".join(f"  [{f['fact_id']}] {f['text']}" for f in facts)

    user_prompt = (
        f"DRAFT EMAIL:\n{body}\n\n"
        f"GROUNDED FACTS:\n{facts_block}\n\n"
        f"Analyze each sentence that claims something about the target company."
    )

    judge_provider = os.environ.get("JUDGE_PROVIDER", os.environ.get("GTM_PROVIDER", "openai"))
    judge_model = os.environ.get("JUDGE_MODEL", "gpt-5.4-mini")

    try:
        from gtm_triage.agents.llm_client import chat
        result = chat(
            provider=judge_provider,
            model=judge_model,
            system=_JUDGE_SYSTEM,
            user=user_prompt,
            max_tokens=800,
        )

        raw_sentences = _parse_structured_response(result.text)
        labeled = [_derive_label(s) for s in raw_sentences]

        fabrication_count = sum(1 for s in labeled if s["fabrication"])
        human_review_count = sum(1 for s in labeled if s["human_review"])

        return {
            "fabrication_count": fabrication_count,
            "human_review_count": human_review_count,
            "sentences": labeled,
        }
    except Exception as exc:
        logger.warning("LLM judge failed: %s", exc)
        return {
            "fabrication_count": -1,
            "human_review_count": 0,
            "sentences": [{"sentence": "JUDGE_ERROR", "fabrication": False, "human_review": True, "reasoning": str(exc)}],
        }


def _parse_structured_response(raw: str) -> list[dict]:
    """Parse the structured judge response into raw boolean dicts."""
    try:
        text = raw.strip()
        m = re.search(r"\[[\s\S]*\]", text)
        if m:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return [
                    {
                        "sentence": item.get("sentence", ""),
                        "asserts_specific": bool(item.get("asserts_specific_fact_about_target", False)),
                        "supported": bool(item.get("fact_supported_by_brief", True)),
                        "hedged": bool(item.get("is_hedged_or_generalized", False)),
                        "reasoning": item.get("reasoning", ""),
                    }
                    for item in data
                ]
    except (json.JSONDecodeError, ValueError):
        pass
    return []


def _derive_label(s: dict) -> dict:
    """Derive fabrication/human_review labels from structured booleans.

    fabrication = asserts_specific AND NOT supported AND NOT hedged
    human_review = booleans are internally inconsistent (hedged AND asserts_specific
                   both true with NOT supported)
    """
    asserts = s.get("asserts_specific", False)
    supported = s.get("supported", True)
    hedged = s.get("hedged", False)

    # Inconsistency: hedged AND asserts_specific AND NOT supported
    # → the judge can't decide; route to human review
    inconsistent = asserts and hedged and not supported

    # Fabrication: asserts a specific unsupported fact that is NOT hedged
    fabrication = asserts and not supported and not hedged

    return {
        "sentence": s.get("sentence", ""),
        "asserts_specific": asserts,
        "supported": supported,
        "hedged": hedged,
        "fabrication": fabrication,
        "human_review": inconsistent,
        "reasoning": s.get("reasoning", ""),
    }


# ── Report ──────────────────────────────────────────────────────────────────

def print_report(results: list[dict], flagged: list[dict]) -> None:
    """Print the per-company table and fabrication rates."""
    if not results:
        print("\nNo results to report.")
        return

    total = len(results)
    det_fails = sum(1 for r in results if not r["det_pass"])
    soft_fails = sum(1 for r in results if r.get("soft_fabrication_count", r.get("llm_unsupported_count", 0)) > 0)
    human_reviews = sum(1 for r in results if r.get("human_review_count", 0) > 0)
    judge_errors = sum(1 for r in results if r.get("soft_fabrication_count", r.get("llm_unsupported_count", 0)) < 0)

    adv_results = [r for r in results if r["adversarial"]]

    print("\n" + "=" * 80)
    print("  OUTBOUND GROUNDING EVAL (structured judge)")
    print("=" * 80)

    # Per-company table
    print(f"\n  {'Domain':<22} {'Var':>3}  {'Det':>4}  {'Fab':>4}  {'Rev':>4}  {'Grounded':>8}  {'Segment':<15}")
    print("  " + "-" * 74)

    seen_domains: set[str] = set()
    for r in results:
        domain_label = r["domain"] if r["domain"] not in seen_domains else ""
        seen_domains.add(r["domain"])
        det_mark = "OK" if r["det_pass"] else "FAIL"
        fab_count = r.get("soft_fabrication_count", r.get("llm_unsupported_count", 0))
        fab_mark = "OK" if fab_count == 0 else ("ERR" if fab_count < 0 else f"{fab_count}!")
        rev_count = r.get("human_review_count", 0)
        rev_mark = f"{rev_count}?" if rev_count > 0 else "-"
        grounded_str = ",".join(r["grounded_on"][:3]) if r["grounded_on"] else "-"
        adv_tag = " [ADV]" if r["adversarial"] else ""
        print(f"  {domain_label:<22} {r['variant']:>3}  {det_mark:>4}  {fab_mark:>4}  {rev_mark:>4}  {grounded_str:>8}  {r['segment']:<15}{adv_tag}")

    # Rates
    print("\n" + "-" * 80)
    print(f"  HARD fabrication rate (deterministic):   {det_fails}/{total} = {det_fails/total*100:.1f}%")
    print(f"  SOFT fabrication rate (structured judge): {soft_fails}/{total} = {soft_fails/total*100:.1f}%")
    print(f"  Routed to human review:                  {human_reviews}/{total}")
    if judge_errors:
        print(f"  Judge errors:                            {judge_errors}/{total}")

    # Adversarial subset
    if adv_results:
        adv_total = len(adv_results)
        adv_det = sum(1 for r in adv_results if not r["det_pass"])
        adv_fab = sum(1 for r in adv_results if r.get("soft_fabrication_count", 0) > 0)
        print(f"\n  ADVERSARIAL subset ({adv_total} variants, sparse-fact briefs):")
        print(f"    Hard: {adv_det}/{adv_total}   Soft: {adv_fab}/{adv_total}")

    # Small-N caveat
    print(f"\n  NOTE: N={total} variants from {len(seen_domains)} companies.")
    print("  Small-N results — directional signal, not a statistical guarantee.")

    # Flagged sentences (fabrications + human review)
    fabrications = [f for f in flagged if f.get("flag_type") == "fabrication"]
    reviews = [f for f in flagged if f.get("flag_type") == "human_review"]

    if fabrications:
        print(f"\n  --- Fabrications ({len(fabrications)}) ---")
        for f in fabrications[:10]:
            print(f"\n  [{f['domain']}][{f['variant']}] \"{f['sentence']}\"")
            print(f"    Reasoning: {f['reasoning']}")

    if reviews:
        print(f"\n  --- Routed to human review ({len(reviews)}) ---")
        for f in reviews[:10]:
            print(f"\n  [{f['domain']}][{f['variant']}] \"{f['sentence']}\"")
            print(f"    Reasoning: {f['reasoning']}")
            print(f"    (asserts_specific={f.get('asserts_specific')}, supported={f.get('supported')}, hedged={f.get('hedged')})")

    if not fabrications and not reviews:
        print("\n  No fabrications or human-review items.")

    # Headline
    print("\n  " + "=" * 76)
    if det_fails == 0 and soft_fails == 0:
        review_note = f" ({human_reviews} hedged sentences routed to human review)" if human_reviews else ""
        print(f"  HEADLINE: 0% hard, 0% soft fabrication on {len(seen_domains)} held-out companies.{review_note}")
    else:
        print(f"  HEADLINE: {det_fails/total*100:.1f}% hard, {soft_fails/total*100:.1f}% soft on {len(seen_domains)} held-out companies.")
    print("  " + "=" * 76)
