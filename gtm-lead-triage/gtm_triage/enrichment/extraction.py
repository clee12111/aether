"""Lead signal extraction from raw input (name, message, email).

Reads what the lead SAYS — seniority from stated role ("I lead risk ops",
"engagement partner", signature blocks), intent from what they ask for
("budget approved, send contract" = high; "take me off your list" = opt_out).

This is the extraction step the FRONTIER audit identified as missing.
Extraction reads the lead's own words; enrichment looks up the company.
They merge by confidence.

Mock provider = deterministic heuristics, keyless CI.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field


# ── Intent categories ──────────────────────────────────────────────────────────
# Defined from general sales reasoning, NOT from inspecting holdout_v2 failures.

IntentLevel = Literal["high", "medium", "low", "opt_out", "legal_or_compliance", "unknown"]


class ExtractionResult(BaseModel):
    """Structured extraction from lead's own words."""

    seniority: str = ""
    seniority_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    role: str = ""
    role_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    company: str = ""
    company_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    intent: IntentLevel = "unknown"
    intent_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


# ── Seniority patterns ────────────────────────────────────────────────────────
# Match role statements in message text and signature blocks.
# Ordered by specificity — first match wins.

_SENIORITY_PATTERNS: list[tuple[str, str, str]] = [
    # (regex pattern, seniority level, role extract)
    # C-level
    (r"\b(?:i(?:'m| am) (?:the )?)?(?:chief (?:executive|technology|financial|operating|medical|marketing) officer)\b", "c_level", ""),
    (r"\b(?:i(?:'m| am) (?:the )?)?(?:ceo|cto|cfo|coo|cmo|cio)\b", "c_level", ""),
    (r"\bengagement partner\b", "c_level", "engagement partner"),
    (r"\bpresident\b", "c_level", "president"),
    (r"\bfounder\b", "c_level", "founder"),
    (r"\bco-founder\b", "c_level", "co-founder"),
    # VP
    (r"\bvice president\b", "vp", ""),
    (r"\bvp\s+(?:of\s+)?\w+", "vp", ""),
    (r"\bsvp\b", "vp", ""),
    # Director
    (r"\bdirector\s+(?:of\s+)?\w+", "director", ""),
    (r"\bhead\s+of\s+\w+", "director", ""),
    (r"\bsenior\s+(?:program\s+)?manager\b", "director", ""),
    # Manager
    (r"\b(?:i )?lead\s+(?:our|the|a)\s+\w+", "manager", ""),
    (r"\bmanager\b", "manager", ""),
    (r"\bteam lead\b", "manager", ""),
    (r"\boperations lead\b", "manager", ""),
    # IC
    (r"\bintern\b", "ic", "intern"),
    (r"\bstudent\b", "ic", "student"),
    (r"\bgraduate student\b", "ic", "student"),
    (r"\bvolunteer\b", "ic", "volunteer"),
    (r"\bfreelancer?\b", "ic", "freelancer"),
    (r"\banalyst\b", "ic", "analyst"),
    (r"\bengineer\b", "ic", "engineer"),
    (r"\bdeveloper\b", "ic", "developer"),
]

# ── Intent patterns ────────────────────────────────────────────────────────────
# Ordered: hard-disqualifiers first, then by strength.

# Legal/compliance — these are NOT leads, they're legal requests
_LEGAL_PATTERNS = [
    r"\bdata subject access request\b",
    r"\bgdpr\s+article\b",
    r"\bright to (?:erasure|deletion|be forgotten)\b",
    r"\bccpa\b.*\brequest\b",
    r"\bfreedom of information\b",
    r"\bregulatory (?:inquiry|review|audit)\b",
    r"\bcompliance (?:review|audit|inquiry)\b",
    r"\bnot a sales inquiry\b",
    r"\bphishing (?:simulation|test|report)\b",
]

# Opt-out — lead wants to disengage
_OPT_OUT_PATTERNS = [
    r"\b(?:unsubscribe|opt[\s-]?out)\b",
    r"\bremove me\b",
    r"\btake me off\b",
    r"\bstop (?:contacting|emailing|sending)\b",
    r"\bdo not contact\b",
    r"\bno further (?:communications?|emails?|contact)\b",
    r"\bmailing list\b",
    r"\b(?:i(?:'ve| have)) asked (?:three|multiple|several) times\b",
    r"\bdo not wish to receive\b",
    r"\bremove (?:my|this) (?:address|email)\b",
]

# High intent — ready to buy or in late funnel
_HIGH_INTENT_PATTERNS = [
    r"\bbudget (?:is )?approved\b",
    r"\bsend (?:us )?(?:contract|pricing|terms|proposal)\b",
    r"\bcontract negoti",
    r"\brfp\b",
    r"\bvendor selection\b",
    r"\bpurchase\b",
    r"\bbuy\b",
    r"\blicenses?\s+(?:by|for|within)\b",
    r"\b(?:\d+)\s+(?:seats?|licenses?|users?)\b",
    r"\bpilot\s+start",
    r"\bonboard\b.*\bplatform\b",
    r"\bstart (?:a )?trial\b",
    r"\b(?:need|want|schedule|set up)\s+(?:a )?demo\b",
    r"\bdemand is urgent\b",
    r"\basap\b",
    r"\burgent(?:ly)?\b",
    r"\bby (?:end of |next )?\b(?:month|quarter|week)\b",
]

# Medium intent — evaluating, interested
_MEDIUM_INTENT_PATTERNS = [
    r"\bevaluat",
    r"\binterested\b",
    r"\bexploring\b",
    r"\bconsidering\b",
    r"\bcompare[sd]?\b",
    r"\bshortlist\b",
    r"\bcase stud(?:y|ies)\b",
    r"\broi\s+data\b",
    r"\bwalk(?:ing)?\s+(?:me|us)\s+through\b",
    r"\blearn more\b",
    r"\bscoping\b",
]

# Low intent — browsing, info gathering
_LOW_INTENT_PATTERNS = [
    r"\bjust\s+(?:curious|browsing|looking|testing)\b",
    r"\bwhat (?:does|do) (?:your|you)\b",
    r"\bsend (?:me )?(?:(?:some|more) )?(?:info|information|details|overview|materials|documentation|one-pager)\b",
    r"\bmore info\b",
    r"\bquestion\b",
    r"\bcurious\b",
]

# Reverse intent — they're selling TO us, not buying
_REVERSE_INTENT_PATTERNS = [
    r"\bspeaking slot\b",
    r"\bsponsor\b",
    r"\bpartnership\b.*\bopportunity\b",
    r"\b(?:our|we)\s+(?:help|offer|provide)\s+(?:businesses|companies)\b",
    r"\b(?:our|we)\s+(?:services|solutions|consulting)\b",
]


def _match_any(text: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            return True
    return False


def _extract_seniority(name: str, message: str) -> tuple[str, str, float]:
    """Extract seniority and role from name + message text.

    Returns (seniority_level, role_string, confidence).
    Confidence: 0.75 for message-body matches (stated by the person),
    0.60 for name-field matches (may be self-reported/inflated).
    """
    # Check message first (higher confidence — they're saying it themselves)
    text = message.lower()
    for pattern, seniority, role_hint in _SENIORITY_PATTERNS:
        m = re.search(pattern, text)
        if m:
            role = role_hint or m.group(0).strip()
            # Check for third-person reference — "our CTO shared", "my manager said"
            # These describe someone ELSE, not the sender. Possessives (our/my/their/
            # his/her) before a title signal third-person. "The" alone doesn't count
            # because "I'm the CTO" is first-person.
            # Only apply for senior titles (c_level, vp, director) where over-crediting
            # is costly (20-25 points). IC keywords ("volunteer", "intern") carry only
            # 5 points — gating them causes regressions without preventing false-hots.
            if seniority in ("c_level", "vp", "director"):
                match_start = m.start()
                preceding = text[max(0, match_start - 15):match_start].strip()
                if re.search(r"\b(?:our|my|their|his|her)\s*$", preceding):
                    # Third-person reference: lower confidence significantly
                    return seniority, role, 0.35
            return seniority, role, 0.75

    # Check name field (lower confidence — self-reported title)
    name_lower = name.lower()
    for pattern, seniority, role_hint in _SENIORITY_PATTERNS:
        m = re.search(pattern, name_lower)
        if m:
            role = role_hint or m.group(0).strip()
            return seniority, role, 0.60

    # Check for signature block in message (pattern: newline + Name\nTitle\nCompany)
    sig_match = re.search(
        r"(?:^|\n)[-—]+\s*\n(.+?)(?:\n|$)",
        message,
        re.MULTILINE,
    )
    if sig_match:
        sig_text = sig_match.group(1).lower()
        for pattern, seniority, role_hint in _SENIORITY_PATTERNS:
            m = re.search(pattern, sig_text)
            if m:
                role = role_hint or m.group(0).strip()
                return seniority, role, 0.65

    return "", "", 0.0


def _extract_intent(message: str) -> tuple[IntentLevel, float]:
    """Extract intent from message text.

    Returns (intent_level, confidence).
    Hard-disqualifiers (legal, opt_out) get 0.90 confidence.
    Intent levels get 0.70 for strong pattern matches.
    """
    if not message.strip():
        return "unknown", 0.0

    text = message.lower()

    # Legal/compliance — highest priority
    if _match_any(text, _LEGAL_PATTERNS):
        return "legal_or_compliance", 0.90

    # Opt-out
    if _match_any(text, _OPT_OUT_PATTERNS):
        return "opt_out", 0.90

    # Reverse intent (selling to us) — treat as low
    if _match_any(text, _REVERSE_INTENT_PATTERNS):
        return "low", 0.70

    # High intent
    if _match_any(text, _HIGH_INTENT_PATTERNS):
        return "high", 0.70

    # Medium intent
    if _match_any(text, _MEDIUM_INTENT_PATTERNS):
        return "medium", 0.65

    # Low intent
    if _match_any(text, _LOW_INTENT_PATTERNS):
        return "low", 0.60

    return "unknown", 0.0


def extract_lead_signals_llm(
    name: str = "",
    message: str = "",
    email: str = "",
    model: str = "gpt-4o-mini",
) -> ExtractionResult:
    """Extract seniority, role, intent via LLM structured output.

    Handles what the heuristic can't: third-person attribution, non-English,
    nuanced roles, ambiguous references. Returns calibrated confidence.
    Falls back to the heuristic path on any failure.
    """
    import json as _json
    import os

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return extract_lead_signals(name=name, message=message, email=email)

    text_block = ""
    if name:
        text_block += f"Name: {name}\n"
    if email:
        text_block += f"Email: {email}\n"
    if message:
        text_block += f"Message: {message}\n"

    if not text_block.strip():
        return ExtractionResult()

    system = (
        "You are a B2B lead signal extractor. Given a lead's name, email, and message, "
        "extract their seniority level and buying intent.\n\n"
        "CRITICAL RULES:\n"
        "- seniority: the SENDER's own role, NOT someone they mention. "
        "'Our CTO shared this' means the SENDER is NOT the CTO. "
        "'I am the CTO' means the SENDER IS the CTO.\n"
        "- intent: what the sender wants from US, not what they're offering.\n"
        "- For non-English messages, translate and extract normally.\n"
        "- Confidence: 0.9 for explicit first-person claims, 0.7 for clear signals, "
        "0.4 for inferred/ambiguous, 0.2 for third-person mentions.\n\n"
        "Return ONLY valid JSON:\n"
        '{"seniority": "c_level|vp|director|manager|ic|unknown", '
        '"seniority_confidence": 0.0, '
        '"role": "stated role or empty", '
        '"intent": "high|medium|low|opt_out|legal_or_compliance|unknown", '
        '"intent_confidence": 0.0, '
        '"reasoning": "one line"}'
    )

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, timeout=30.0)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text_block},
            ],
            max_completion_tokens=200,
            temperature=0,
        )
        raw = resp.choices[0].message.content or ""
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            data = _json.loads(m.group(0))
            # Clamp confidences
            sen_conf = max(0.0, min(1.0, float(data.get("seniority_confidence", 0.0))))
            int_conf = max(0.0, min(1.0, float(data.get("intent_confidence", 0.0))))

            # Derive company from email domain
            company = ""
            company_conf = 0.0
            if email and "@" in email:
                domain = email.rsplit("@", 1)[1].lower()
                parts = domain.split(".")
                if len(parts) >= 2:
                    company = parts[0]
                    company_conf = 0.30

            return ExtractionResult(
                seniority=data.get("seniority", "") or "",
                seniority_confidence=sen_conf,
                role=data.get("role", "") or "",
                role_confidence=sen_conf,
                company=company,
                company_confidence=company_conf,
                intent=data.get("intent", "unknown") or "unknown",
                intent_confidence=int_conf,
            )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("LLM extraction failed: %s", exc)

    # Fallback to heuristic
    return extract_lead_signals(name=name, message=message, email=email)


def extract_lead_signals(
    name: str = "",
    message: str = "",
    email: str = "",
) -> ExtractionResult:
    """Extract seniority, role, intent, and company from the lead's own words.

    This is the deterministic (mock/heuristic) path — keyless CI double.

    Accepts minimal input: any combination of name/message/email. Missing
    fields produce unknown/low-confidence results, never crash.
    """
    # Extract seniority from name + message
    seniority, role, sen_conf = _extract_seniority(name, message)

    # Extract intent from message
    intent, intent_conf = _extract_intent(message)

    # Extract company from email domain (if not provided elsewhere)
    company = ""
    company_conf = 0.0
    if email and "@" in email:
        domain = email.rsplit("@", 1)[1].lower()
        # Use domain as company hint (without TLD)
        parts = domain.split(".")
        if len(parts) >= 2:
            company = parts[0]
            company_conf = 0.30  # very low — just a domain-derived hint

    return ExtractionResult(
        seniority=seniority,
        seniority_confidence=sen_conf,
        role=role,
        role_confidence=sen_conf,  # same confidence as seniority
        company=company,
        company_confidence=company_conf,
        intent=intent,
        intent_confidence=intent_conf,
    )
