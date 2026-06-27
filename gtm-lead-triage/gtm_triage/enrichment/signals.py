"""Atomic signal schema for lead extraction.

Each signal is a typed, attributed, evidence-grounded fact extracted from
the lead's message. Flat list (not nested) — research shows nested schemas
degrade structured-output reliability.

The `subject` field solves third-party attribution structurally:
  - sender:      the person who wrote the message
  - third_party: someone the sender mentions ("our CTO", "my VP")
  - company:     the sender's organization

The `relation` field adds attribution nuance:
  - self:               sender claims this about themselves ("I'm the VP")
  - sponsor_delegated:  a senior person sent the sender ("My VP asked me to reach out")
  - mentioned:          sender casually references someone ("Our CTO shared this")
  - n/a:                for non-person signals (intent, timeline, deal_size)
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SignalType = Literal[
    "seniority", "intent", "timeline", "deal_size",
    "fit", "objection", "opt_out", "legal", "spam",
]
Subject = Literal["sender", "third_party", "company"]
Relation = Literal["self", "sponsor_delegated", "mentioned", "n_a"]


class Signal(BaseModel):
    """One atomic, attributed, evidence-grounded signal."""

    type: SignalType
    value: str
    subject: Subject = "sender"
    relation: Relation = "n_a"
    evidence: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class SignalExtraction(BaseModel):
    """Flat list of atomic signals extracted from a lead."""

    signals: list[Signal] = Field(default_factory=list)

    def sender_signals(self, signal_type: str) -> list[Signal]:
        """Get signals of a type attributed to the sender."""
        return [s for s in self.signals if s.type == signal_type and s.subject == "sender"]

    def best_sender_signal(self, signal_type: str) -> Signal | None:
        """Highest-confidence sender signal of a type, or None."""
        matches = self.sender_signals(signal_type)
        return max(matches, key=lambda s: s.confidence) if matches else None

    def has_sponsor_delegated(self) -> bool:
        """True if any signal has relation=sponsor_delegated."""
        return any(s.relation == "sponsor_delegated" for s in self.signals)

    def sponsor_intent(self) -> Signal | None:
        """Get the intent signal from a sponsor-delegated relationship."""
        for s in self.signals:
            if s.type == "intent" and s.relation == "sponsor_delegated":
                return s
        return None


# ── Mock (heuristic) extractor ──────────────────────────────────────────────

# Reuse the existing pattern lists for the mock path
from gtm_triage.enrichment.extraction import (
    _SENIORITY_PATTERNS,
    _LEGAL_PATTERNS,
    _OPT_OUT_PATTERNS,
    _HIGH_INTENT_PATTERNS,
    _MEDIUM_INTENT_PATTERNS,
    _LOW_INTENT_PATTERNS,
    _REVERSE_INTENT_PATTERNS,
    _match_any,
)


def extract_signals_mock(name: str = "", message: str = "", email: str = "") -> SignalExtraction:
    """Deterministic heuristic signal extraction — keyless CI double.

    Emits atomic signals with subject/relation attribution where possible.
    Limited: can't handle non-English, nuanced third-person, or multi-signal.
    """
    signals: list[Signal] = []
    text = message.lower()

    # ── Seniority ────────────────────────────────────────────────────────
    for pattern, seniority, role_hint in _SENIORITY_PATTERNS:
        m = re.search(pattern, text)
        if m:
            evidence = m.group(0).strip()
            match_start = m.start()
            preceding = text[max(0, match_start - 15):match_start].strip()

            # Determine subject and relation
            is_third_person = bool(re.search(r"\b(?:our|my|their|his|her)\s*$", preceding))

            if is_third_person and seniority in ("c_level", "vp", "director"):
                # Check for sponsor-delegated pattern: "My VP asked me to"
                # Look in the match + text after it (the verb may be inside the match)
                context = text[m.start():m.end() + 50]
                is_delegated = bool(re.search(r"\b(?:asked|told|sent|delegated|wants)\b", context))

                signals.append(Signal(
                    type="seniority",
                    value=seniority,
                    subject="third_party",
                    relation="sponsor_delegated" if is_delegated else "mentioned",
                    evidence=text[max(0, match_start - 5):m.end() + 10].strip(),
                    confidence=0.75 if is_delegated else 0.35,
                ))

                # If delegated, also extract the delegated intent from the
                # surrounding text. Check both shared patterns and
                # additional conjugated forms ("wants a demo").
                if is_delegated:
                    intent_context = text[m.start():]
                    _delegated_high = _HIGH_INTENT_PATTERNS + [
                        r"\bwants?\s+(?:a )?demo\b",
                        r"\bwants?\s+(?:a )?(?:trial|review|call)\b",
                    ]
                    if _match_any(intent_context, _delegated_high):
                        signals.append(Signal(
                            type="intent",
                            value="high",
                            subject="third_party",
                            relation="sponsor_delegated",
                            evidence=intent_context[:60].strip(),
                            confidence=0.70,
                        ))
            else:
                signals.append(Signal(
                    type="seniority",
                    value=seniority,
                    subject="sender",
                    relation="self",
                    evidence=evidence,
                    confidence=0.75 if m.start() > 0 else 0.60,
                ))
            break  # first match only for seniority

    # Check name field for sender seniority if not found in message
    if not any(s.type == "seniority" and s.subject == "sender" for s in signals):
        name_lower = name.lower()
        for pattern, seniority, role_hint in _SENIORITY_PATTERNS:
            m = re.search(pattern, name_lower)
            if m:
                signals.append(Signal(
                    type="seniority",
                    value=seniority,
                    subject="sender",
                    relation="self",
                    evidence=m.group(0).strip(),
                    confidence=0.60,
                ))
                break

    # ── Intent ───────────────────────────────────────────────────────────
    if text.strip():
        if _match_any(text, _LEGAL_PATTERNS):
            m = _first_match(text, _LEGAL_PATTERNS)
            signals.append(Signal(
                type="legal", value="legal_or_compliance", subject="company",
                evidence=m or "", confidence=0.90,
            ))
        elif _match_any(text, _OPT_OUT_PATTERNS):
            m = _first_match(text, _OPT_OUT_PATTERNS)
            signals.append(Signal(
                type="opt_out", value="opt_out", subject="sender",
                evidence=m or "", confidence=0.90,
            ))
        elif _match_any(text, _REVERSE_INTENT_PATTERNS):
            m = _first_match(text, _REVERSE_INTENT_PATTERNS)
            signals.append(Signal(
                type="intent", value="low", subject="sender",
                relation="n_a", evidence=m or "", confidence=0.70,
            ))
        elif _match_any(text, _HIGH_INTENT_PATTERNS):
            m = _first_match(text, _HIGH_INTENT_PATTERNS)
            signals.append(Signal(
                type="intent", value="high", subject="sender",
                relation="n_a", evidence=m or "", confidence=0.70,
            ))
        elif _match_any(text, _MEDIUM_INTENT_PATTERNS):
            m = _first_match(text, _MEDIUM_INTENT_PATTERNS)
            signals.append(Signal(
                type="intent", value="medium", subject="sender",
                relation="n_a", evidence=m or "", confidence=0.65,
            ))
        elif _match_any(text, _LOW_INTENT_PATTERNS):
            m = _first_match(text, _LOW_INTENT_PATTERNS)
            signals.append(Signal(
                type="intent", value="low", subject="sender",
                relation="n_a", evidence=m or "", confidence=0.60,
            ))

    return SignalExtraction(signals=signals)


def _first_match(text: str, patterns: list[str]) -> str:
    """Return the first matching span from the pattern list."""
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0).strip()
    return ""


# ── LLM extractor (OpenAI Structured Outputs) ───────────────────────────────

_LLM_SYSTEM = """You are a B2B lead signal extractor. Extract ALL relevant signals from the lead's message as a flat JSON list.

CRITICAL RULES:
1. ATTRIBUTION: For each signal, identify WHO it's about:
   - subject="sender": the person who wrote the message
   - subject="third_party": someone the sender mentions
   - subject="company": the sender's organization

2. RELATION for seniority signals:
   - "self": sender claims the role ("I'm the VP", "As Director of...")
   - "sponsor_delegated": a senior person sent the sender ("My VP asked me to reach out")
   - "mentioned": sender casually references someone ("Our CTO shared this")
   - "n_a": for non-seniority signals

3. VALUE must be the ENUM value, not freeform text:
   - For type="seniority": value MUST be one of: c_level, vp, director, manager, ic, unknown
   - For type="intent": value MUST be one of: high, medium, low
   - For type="opt_out": value MUST be "opt_out"
   - For type="legal": value MUST be "legal_or_compliance"
   - For type="spam": value MUST be "spam"
   - For type="timeline": value = the timeframe (e.g. "Q3", "this week", "tomorrow")
   - For type="deal_size": value = the quantity (e.g. "200 seats", "80 licenses")
   - For type="objection": value = short label (e.g. "competitor_locked", "compliance_blocker")
   - For type="fit": value = short label (e.g. "scale_question", "free_tier_request")
   Freeform descriptive text goes in "evidence", NOT in "value".

4. EVIDENCE: Copy the exact verbatim span from the message. Must be a real substring.

5. For non-English messages, translate internally but extract normally. Evidence spans from the original text.

6. Do NOT over-extract. If the message is empty or says just "Hi", return an empty signals list.

7. Confidence: 0.9 for explicit first-person claims, 0.7 for clear signals, 0.4 for inferred, 0.2 for third-person.

Return ONLY valid JSON (no markdown fences):
{"signals": [
  {"type": "seniority|intent|timeline|deal_size|fit|objection|opt_out|legal|spam",
   "value": "ENUM_VALUE (see rule 3)",
   "subject": "sender|third_party|company",
   "relation": "self|sponsor_delegated|mentioned|n_a",
   "evidence": "verbatim span from message",
   "confidence": 0.0}
]}"""


def extract_signals_llm(
    name: str = "",
    message: str = "",
    email: str = "",
    model: str = "gpt-4o-mini",
) -> SignalExtraction:
    """Extract atomic signals via LLM structured output.

    Falls back to mock on failure.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return extract_signals_mock(name=name, message=message, email=email)

    text_block = ""
    if name:
        text_block += f"Name: {name}\n"
    if email:
        text_block += f"Email: {email}\n"
    if message:
        text_block += f"Message: {message}\n"

    if not text_block.strip():
        return SignalExtraction(signals=[])

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _LLM_SYSTEM},
                {"role": "user", "content": text_block},
            ],
            max_completion_tokens=500,
            temperature=0,
        )
        raw = resp.choices[0].message.content or ""
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            data = json.loads(m.group(0))
            parsed_signals = []
            for s in data.get("signals", []):
                try:
                    parsed_signals.append(Signal(
                        type=s.get("type", "intent"),
                        value=s.get("value", ""),
                        subject=s.get("subject", "sender"),
                        relation=s.get("relation", "n_a"),
                        evidence=s.get("evidence", ""),
                        confidence=max(0.0, min(1.0, float(s.get("confidence", 0.5)))),
                    ))
                except Exception:
                    continue  # skip malformed signals
            return SignalExtraction(signals=parsed_signals)
    except Exception as exc:
        logger.warning("LLM signal extraction failed: %s", exc)

    return extract_signals_mock(name=name, message=message, email=email)


# ── Convert SignalExtraction → legacy ExtractionResult for scoring compat ────

def signals_to_extraction_result(
    extraction: SignalExtraction,
    email: str = "",
) -> "ExtractionResult":
    """Convert atomic signals to the flat ExtractionResult scoring expects.

    ATTRIBUTION-AWARE:
    - Seniority: credit ONLY when subject=sender
    - Intent: credit when subject=sender OR relation=sponsor_delegated
    - Third-party mentioned signals grant NO sender points
    """
    from gtm_triage.enrichment.extraction import ExtractionResult

    # Sender's seniority (NOT third-party)
    sen_signal = extraction.best_sender_signal("seniority")
    seniority = sen_signal.value if sen_signal else ""
    sen_conf = sen_signal.confidence if sen_signal else 0.0
    role = sen_signal.evidence if sen_signal else ""

    # Intent: sender's own OR sponsor-delegated
    intent = "unknown"
    intent_conf = 0.0

    # Check for hard-disqualifiers first (these signal types override intent)
    for s in extraction.signals:
        if s.type == "opt_out" or (s.type == "intent" and s.value in ("opt_out",)):
            intent = "opt_out"
            intent_conf = s.confidence
            break
        if s.type == "legal" or (s.type == "intent" and s.value in ("legal_or_compliance", "legal")):
            intent = "legal_or_compliance"
            intent_conf = s.confidence
            break
        if s.type == "spam" or (s.type == "intent" and s.value in ("spam",)):
            intent = "low"
            intent_conf = s.confidence
            break

    if intent == "unknown":
        # Sender's own intent
        sender_intent = extraction.best_sender_signal("intent")
        # Sponsor-delegated intent (counts because a VP is backing this)
        sponsor_intent = extraction.sponsor_intent()

        def _clean_intent(val: str) -> str:
            """Validate LLM intent value is a known enum. With the
            constrained schema prompt, value should already be correct.
            Fallback to 'unknown' if not recognized."""
            v = val.lower().strip()
            if v in ("high", "medium", "low", "opt_out", "legal_or_compliance"):
                return v
            return "unknown"

        if sender_intent and sponsor_intent:
            if sponsor_intent.confidence >= sender_intent.confidence:
                intent = _clean_intent(sponsor_intent.value)
                intent_conf = sponsor_intent.confidence
            else:
                intent = _clean_intent(sender_intent.value)
                intent_conf = sender_intent.confidence
        elif sponsor_intent:
            intent = _clean_intent(sponsor_intent.value)
            intent_conf = sponsor_intent.confidence
        elif sender_intent:
            intent = _clean_intent(sender_intent.value)
            intent_conf = sender_intent.confidence

    # Company from email domain
    company = ""
    company_conf = 0.0
    if email and "@" in email:
        domain = email.rsplit("@", 1)[1].lower()
        parts = domain.split(".")
        if len(parts) >= 2:
            company = parts[0]
            company_conf = 0.30

    return ExtractionResult(
        seniority=seniority,
        seniority_confidence=sen_conf,
        role=role,
        role_confidence=sen_conf,
        company=company,
        company_confidence=company_conf,
        intent=intent,
        intent_confidence=intent_conf,
    )
