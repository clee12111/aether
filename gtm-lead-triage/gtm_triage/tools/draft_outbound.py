"""draft_outbound tool — produce TWO grounded A/B outbound email variants.

LLM path (provider != "mock"): one LLM call synthesizes two variants from
grounded facts, then a verifier strips any claim not backed by a fact.

Mock/fallback path: deterministic template for CI — zero LLM, fully offline.

HARD RULE: any specific claim in the draft must map to a brief source. The
verifier enforces this — ungrounded specifics are replaced with generic openers.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from gtm_triage.tools.base import BaseTool

logger = logging.getLogger(__name__)


# ── Step 1: build grounded facts from brief ─────────────────────────────────

def _build_grounded_facts(brief: dict) -> list[dict[str, str]]:
    """Extract a list of {fact_id, text} from the brief.

    Each fact has a stable id and a natural-language phrasing.
    """
    facts: list[dict[str, str]] = []

    what_they_do = brief.get("what_they_do")
    if what_they_do:
        facts.append({"fact_id": "wtd", "text": what_they_do})

    industry = brief.get("industry")
    if industry:
        facts.append({"fact_id": "industry", "text": f"They operate in the {industry} space."})

    size = brief.get("size")
    if size:
        label = {"smb": "small", "mid_market": "mid-market", "enterprise": "large enterprise"}.get(size, size)
        facts.append({"fact_id": "size", "text": f"They are a {label} company."})

    for i, sig in enumerate(brief.get("recent_signals", [])):
        raw = sig.get("text", "")
        kind = sig.get("kind", "other")
        # Naturalize the raw signal text
        natural = _naturalize_signal(raw, kind)
        facts.append({"fact_id": f"signal_{i}", "text": natural})

    tech_stack = brief.get("tech_stack", [])
    if tech_stack:
        sample = ", ".join(tech_stack[:5])
        facts.append({"fact_id": "tech", "text": f"Their tech stack includes {sample}."})

    if brief.get("is_requester"):
        facts.append({"fact_id": "demand", "text": "They have already been exploring solutions in this space."})

    for i, problem in enumerate(brief.get("likely_problems", [])):
        facts.append({"fact_id": f"problem_{i}", "text": f"Likely challenge: {problem}"})

    return facts


def _naturalize_signal(raw: str, kind: str) -> str:
    """Convert raw signal text into natural phrasing.

    e.g. "$270M Other from GIC, Sequoia Capital, Index Ventures (2026-01-01)"
    → "They recently raised a $270M round from GIC, Sequoia Capital, and Index Ventures."
    """
    if kind == "funding":
        # Parse amount and investors from the raw text
        m = re.match(r"\$?([\d.]+[MBK]?)\s+\w+(?:\s+\(.*?\))?\s+(?:from\s+)?(.+?)(?:\s+\(\d{4}.*\))?$", raw, re.I)
        if m:
            amount, investors = m.group(1), m.group(2).strip()
            if investors:
                return f"They recently raised a ${amount} round from {investors}."
            return f"They recently raised a ${amount} round."
        # Simple fallback
        if "$" in raw:
            return f"They recently raised funding ({raw.split('(')[0].strip()})."
    elif kind == "demand":
        return raw  # already natural from the PB source
    # Default: present as news
    return f"Recent news: {raw}"


# ── Step 3: verifier ────────────────────────────────────────────────────────

# Tokens that indicate a specific claim needing grounding
_SPECIFIC_PATTERNS = [
    re.compile(r"\$[\d,.]+[MBKmk]?"),              # dollar amounts
    re.compile(r"\b\d{1,3}(?:,\d{3})+\b"),          # large numbers with commas
    re.compile(r"\bSeries [A-Z]\b"),                # specific funding rounds
    re.compile(r"\bIPO\b"),                         # IPO mention
]


def _extract_specifics(text: str) -> list[str]:
    """Extract specific tokens (dollar amounts, large numbers) from text."""
    specifics = []
    for pat in _SPECIFIC_PATTERNS:
        specifics.extend(pat.findall(text))
    return specifics


# Patterns that assert unstated specifics about the target in second person
_ASSERTION_PATTERNS = [
    # "your team is/are/has/faces..."
    re.compile(r"\byour (?:team|company|org(?:anization)?)\s+(?:is|are|has|faces?|struggles?)\b", re.I),
    # "you're growing/scaling/hiring/struggling..."
    re.compile(r"\byou(?:'re| are)\s+(?:growing|scaling|hiring|struggling|expanding|facing)\b", re.I),
    # "as your team grows..."
    re.compile(r"\bas your (?:team|company)\s+(?:grows?|scales?|expands?)\b", re.I),
]


def _soften_unstated_assertions(body: str, all_fact_text: str) -> str:
    """Replace second-person assertions of unstated specifics with hedged generalizations.

    Only triggers when the asserted content isn't backed by any fact text.
    """
    for pat in _ASSERTION_PATTERNS:
        for match in pat.finditer(body):
            matched_text = match.group(0).lower()
            # Check if this assertion is backed by a fact
            # Extract key verb/object from the match
            if matched_text not in all_fact_text:
                # Replace with hedged generalization
                original = match.group(0)
                hedged = _hedge_assertion(original)
                body = body.replace(original, hedged, 1)
    return body


def _hedge_assertion(original: str) -> str:
    """Convert a second-person assertion into a hedged generalization."""
    lower = original.lower()
    if "your team" in lower or "your company" in lower:
        return original.replace("your team", "teams like yours").replace("Your team", "Teams like yours").replace("your company", "companies like yours").replace("Your company", "Companies like yours")
    if "you're" in lower or "you are" in lower:
        return original.replace("you're", "teams in this space often are").replace("You're", "Teams in this space often are").replace("you are", "teams in this space often are").replace("You are", "Teams in this space often are")
    if "as your" in lower:
        return original.replace("as your", "as").replace("As your", "As")
    return original


def _verify_draft(
    draft: dict[str, Any],
    facts: list[dict[str, str]],
    brief: dict,
    persona: str,
    company: str,
    value_prop: str,
) -> dict[str, Any]:
    """Verify a draft's claims against grounded facts.

    - Each claimed fact_id must exist in the fact list.
    - Any specific token ($amount, named round) in the body must appear in some fact.
    - Ungrounded specifics → replace body with a generic safe version.
    """
    fact_ids = {f["fact_id"] for f in facts}
    all_fact_text = " ".join(f["text"] for f in facts).lower()

    # Strip any fact_id markers the LLM leaked into the body
    body = draft.get("body", "")
    body = re.sub(r"\s*\(fact_id:\s*\[?\w+\]?\)", "", body)  # (fact_id: [wtd])
    body = re.sub(r"\s*\[(?:wtd|signal_\d+|problem_\d+|tech|industry|size|demand)\]", "", body)  # bare [wtd]
    draft = {**draft, "body": body}

    # Validate claimed fact_ids
    claims = draft.get("claims", [])
    verified_ids: list[str] = []
    for claim in claims:
        fid = claim.get("fact_id", "")
        if fid in fact_ids:
            verified_ids.append(fid)

    # Scan body for ungrounded specifics
    body = draft.get("body", "")
    specifics = _extract_specifics(body)
    ungrounded = []
    for token in specifics:
        token_lower = token.lower()
        if token_lower not in all_fact_text:
            ungrounded.append(token)

    if ungrounded:
        logger.warning("Ungrounded specifics in draft variant %s: %s", draft.get("variant"), ungrounded)
        # Fall back to generic safe body
        body = (
            f"Hi {persona},\n\n"
            f"I wanted to reach out — we help teams {value_prop}. "
            f"Would it make sense to explore whether that fits what {company} is working on?\n\n"
            f"Happy to share a quick overview if useful.\n\n"
            f"Best"
        )
        verified_ids = []

    # Soften second-person assertions of unstated specifics
    body = _soften_unstated_assertions(body, all_fact_text)

    return {
        "subject": draft.get("subject", f"Idea for {company}"),
        "body": body,
        "variant": draft.get("variant", "A"),
        "grounded_on": verified_ids,
        "status": "draft",
    }


# ── Mock/fallback: deterministic template ───────────────────────────────────

def _mock_drafts(
    brief: dict,
    campaign: dict,
    persona: str,
    company: str,
    facts: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Deterministic template drafts for mock/CI — zero LLM."""
    value_prop = campaign.get("value_prop", "help your team work more effectively")

    drafts = []
    for variant_label in ("A", "B"):
        hook, grounded_on = _pick_grounded_hook(brief, variant_label, facts)

        subject = f"{company} + {campaign.get('name', 'outbound')}" if variant_label == "A" else f"Idea for {company}"
        body = (
            f"Hi {persona},\n\n"
            f"{hook}I thought this might resonate.\n\n"
            f"We help teams {value_prop}. "
            f"Would it make sense to explore whether that fits what {company} is working on?\n\n"
            f"Happy to share a quick overview if useful — no pressure.\n\n"
            f"Best"
        )

        drafts.append({
            "subject": subject,
            "body": body,
            "variant": variant_label,
            "grounded_on": grounded_on,
            "status": "draft",
        })

    return drafts


def _pick_grounded_hook(
    brief: dict,
    variant: str,
    facts: list[dict[str, str]],
) -> tuple[str, list[str]]:
    """Pick a personalization hook grounded in the brief (mock path).

    Returns (hook_text, list_of_fact_ids_used).
    """
    fact_map = {f["fact_id"]: f["text"] for f in facts}

    if variant == "A":
        # Lead with first signal
        for fid, text in fact_map.items():
            if fid.startswith("signal_"):
                return f"I noticed that {text.lower()} ", [fid]
        if "wtd" in fact_map:
            return f"Given what your team does — {fact_map['wtd'][:80]} — ", ["wtd"]

    else:  # B
        if "demand" in fact_map:
            return "Your team has already been exploring solutions in this space — ", ["demand"]
        if "tech" in fact_map:
            return f"{fact_map['tech'][:80]} ", ["tech"]
        if "industry" in fact_map:
            return f"{fact_map['industry']} We've seen teams there struggle with — ", ["industry"]
        if "wtd" in fact_map:
            return f"Companies like yours — {fact_map['wtd'][:60]} — often find that ", ["wtd"]

    return "I wanted to reach out because ", []


# ── LLM path ────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are an expert outbound copywriter. You write SHORT, human-sounding sales emails — no hype, no filler, no "I hope this finds you well."

You will receive:
1. GROUNDED FACTS — numbered items about the target company. You may ONLY reference these facts. Do NOT invent any company-specific detail.
2. CAMPAIGN — the value proposition and target persona.

Write TWO email variants (A and B) with DIFFERENT angles:
- Each opens with a specific, natural observation about THIS company drawn from ONE grounded fact. Reference the fact_id ONLY in the claims array, NEVER in the email body text. The body must read as a clean email with no fact_ids, no brackets, no parenthetical citations.
- The PROBLEM BRIDGE must be GENERALIZED and HEDGED — "teams scaling in this space often find…", "companies at this stage tend to…". NEVER assert an unstated specific about the target ("you're growing", "your team struggles with X", "your feedback is scattered"). Use third-person generalizations, not second-person assertions of unstated facts.
- If a "Likely challenge" fact is provided, draw the problem bridge from it.
- Connect to the campaign value prop as the solution.
- End with a soft CTA (question, not a demand).
- 4-6 sentences total. No bullet points. No "Best regards" — just "Best".

If you cannot ground a personalized opener on any fact, write an honest generic opener and set claims to [].

Return ONLY valid JSON, no markdown fences:
{"variants": [
  {"variant": "A", "subject": "...", "body": "...", "claims": [{"text": "the specific claim you made", "fact_id": "the fact you based it on"}]},
  {"variant": "B", "subject": "...", "body": "...", "claims": [{"text": "...", "fact_id": "..."}]}
]}"""


def _llm_compose(
    *,
    facts: list[dict[str, str]],
    campaign: dict,
    persona: str,
    company: str,
    provider: str,
    model: str,
    run_id: str,
) -> list[dict[str, Any]] | None:
    """Call the LLM to compose two grounded draft variants.

    Returns the parsed variants list, or None on failure.
    """
    if not facts:
        return None

    facts_block = "\n".join(f"  [{f['fact_id']}] {f['text']}" for f in facts)
    value_prop = campaign.get("value_prop", "help your team work more effectively")
    target_persona = campaign.get("target_persona", persona)

    user_prompt = (
        f"TARGET COMPANY: {company}\n"
        f"RECIPIENT ROLE: {target_persona}\n\n"
        f"GROUNDED FACTS:\n{facts_block}\n\n"
        f"CAMPAIGN VALUE PROP: {value_prop}\n\n"
        f"Write two variants. Address the recipient as \"{persona}\"."
    )

    try:
        from gtm_triage.agents.llm_client import chat
        result = chat(
            provider=provider,
            model=model,
            system=_SYSTEM_PROMPT,
            user=user_prompt,
            max_tokens=800,
            run_id=run_id,
            generation_name="draft-outbound",
        )
        return _parse_llm_response(result.text)
    except Exception as exc:
        logger.warning("LLM draft composition failed: %s", exc)
        return None


def _parse_llm_response(raw: str) -> list[dict[str, Any]] | None:
    """Parse LLM JSON response into variants list."""
    try:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            data = json.loads(m.group(0))
            variants = data.get("variants", [])
            if isinstance(variants, list) and len(variants) >= 2:
                return variants
    except (json.JSONDecodeError, ValueError):
        pass
    return None


# ── Tool ────────────────────────────────────────────────────────────────────

class DraftOutboundTool(BaseTool):
    def __init__(self, provider: str = "mock", model: str = "gpt-4o-mini") -> None:
        self._provider = provider
        self._model = model

    @property
    def name(self) -> str:
        return "draft_outbound"

    def run(self, args: dict, run_id: str = "") -> dict:
        brief = args.get("brief", {})
        campaign = args.get("campaign", {})
        persona = args.get("persona_role", "there")
        company = args.get("company", brief.get("domain", "your company"))
        value_prop = campaign.get("value_prop", "help your team work more effectively")

        # Step 1: build grounded facts
        facts = _build_grounded_facts(brief)

        # Step 2: compose drafts
        if self._provider != "mock":
            # LLM path
            raw_variants = _llm_compose(
                facts=facts,
                campaign=campaign,
                persona=persona,
                company=company,
                provider=self._provider,
                model=self._model,
                run_id=run_id,
            )
            if raw_variants is not None:
                # Step 3: verify each variant
                drafts = []
                for rv in raw_variants[:2]:
                    verified = _verify_draft(rv, facts, brief, persona, company, value_prop)
                    drafts.append(verified)
                return {"drafts": drafts}

            # LLM failed — fall through to mock
            logger.info("LLM draft failed; falling back to template")

        # Mock/fallback path
        drafts = _mock_drafts(brief, campaign, persona, company, facts)
        return {"drafts": drafts}
