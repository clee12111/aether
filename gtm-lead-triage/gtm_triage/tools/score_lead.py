"""Lead scoring tool with deterministic rules + optional bounded LLM nudge.

When provider="mock" (default): rules-only, llm_adjustment=0.
When provider="openai": rules first, then a single LLM call proposes a bounded
adjustment in [-10,+10] with a one-line reason. rule_points and llm_adjustment
are recorded separately so the model's influence is auditable.

Tier thresholds:
  hot:           >= 70
  warm:          45-69
  cold:          20-44
  disqualified:  < 20

Route mapping:
  hot  -> ae_immediate
  warm -> sdr_nurture
  cold -> marketing_nurture
  disqualified -> drop

Phase 1.6 rules (deterministic, auditable):
  1. Opt-out hard disqualifier — compliance stop, overrides score.
  2. Spam intent suppression — outbound-spam phrasing zeroes intent bonus.
  3. Existing-customer boost (+15) — known customer is not a cold stranger.
  4. Title-inflation discount (-10) — vp/c_level at smb gets manager/director pts.
"""

from __future__ import annotations

import logging
import re

from gtm_triage.tools.base import BaseTool

logger = logging.getLogger(__name__)

_TIER_THRESHOLDS = [
    (70, "hot", "ae_immediate"),
    (45, "warm", "sdr_nurture"),
    (20, "cold", "marketing_nurture"),
    (0, "disqualified", "drop"),
]

_FREE_EMAIL_CAP = 69

# ── Phase 1.6: opt-out keywords (hard disqualifier) ─────────────────────────
_OPT_OUT_PHRASES = [
    "unsubscribe", "opt out", "opt-out", "remove me", "stop contacting",
    "do not contact", "mailing list", "stop emailing", "no further",
]

# ── Phase 1.6: outbound-spam indicators ─────────────────────────────────────
# If a message contains 2+ of these, the lead is selling TO us, not buying.
_SPAM_PHRASES = [
    "visit our website", "best prices", "act now", "click here",
    "amazing deals", "guaranteed", "limited time", "special offer",
    "cheap", "free consultation", "our services", "we offer",
]


def _is_opt_out(message: str) -> bool:
    """Check if the message contains opt-out language."""
    msg = message.lower()
    return any(phrase in msg for phrase in _OPT_OUT_PHRASES)


def _spam_phrase_count(message: str) -> int:
    """Count how many outbound-spam indicators appear in the message."""
    msg = message.lower()
    return sum(1 for phrase in _SPAM_PHRASES if phrase in msg)


def _score_rules(email: str, message: str, enrichment: dict) -> tuple[int, str, str | None]:
    """Return (rule_points, reason, hard_override_tier_or_None)."""
    msg_lower = message.lower()
    points = 0
    reasons: list[str] = []

    # ── Extraction-based intent hard disqualifiers ───────────────────────
    # When extraction ran, its intent classification is more reliable than
    # the phrase list because it handles edge cases (DSAR, legal requests,
    # variant opt-out phrasing).
    extracted_intent = enrichment.get("extracted_intent", "")
    if extracted_intent == "opt_out":
        reasons.append("extracted_intent_opt_out_disqualify")
        return 0, "; ".join(reasons), "disqualified"
    if extracted_intent == "legal_or_compliance":
        reasons.append("extracted_intent_legal_disqualify")
        return 0, "; ".join(reasons), "disqualified"

    # ── Phase 1.6 rule 1: opt-out hard disqualifier (phrase-list fallback)
    if _is_opt_out(message):
        reasons.append("opt_out_hard_disqualify")
        return 0, "; ".join(reasons), "disqualified"

    # ── Phase 1.6 rule 2: spam intent suppression ────────────────────────
    spam_count = _spam_phrase_count(message)
    is_spam = spam_count >= 2
    if is_spam:
        reasons.append(f"spam_detected({spam_count}_phrases)")

    # ── Standard rules ───────────────────────────────────────────────────

    # 1. Business email
    if enrichment.get("is_business_email", False):
        points += 15
        reasons.append("business_email(+15)")

    # 2. Company size
    size = enrichment.get("company_size", "unknown")
    size_points = {"enterprise": 25, "mid_market": 20, "smb": 10, "unknown": 0}
    pts = size_points.get(size, 0)
    if pts:
        points += pts
        reasons.append(f"company_size_{size}(+{pts})")

    # 3. Seniority (with Phase 1.6 rule 4: title-inflation discount)
    seniority = enrichment.get("seniority", "unknown")
    seniority_points = {"c_level": 25, "vp": 20, "director": 15, "manager": 10, "ic": 5, "unknown": 0}
    sen_pts = seniority_points.get(seniority, 0)

    # Phase 1.6 rule 4: discount vp/c_level at smb by 10
    title_inflated = False
    if seniority in ("vp", "c_level") and size == "smb":
        sen_pts = max(0, sen_pts - 10)
        title_inflated = True
        reasons.append(f"seniority_{seniority}_inflated(+{sen_pts}, -10 smb discount)")
    elif sen_pts:
        reasons.append(f"seniority_{seniority}(+{sen_pts})")
    if sen_pts:
        points += sen_pts

    # 4. Message-intent signals (suppressed if spam detected)
    if is_spam:
        reasons.append("intent_suppressed(spam)")
    else:
        # Try extraction-based intent first (covers non-English, signature blocks, etc.)
        intent_scored = False
        if extracted_intent == "high":
            points += 15
            reasons.append("extracted_high_intent(+15)")
            intent_scored = True
        elif extracted_intent == "medium":
            points += 8
            reasons.append("extracted_medium_intent(+8)")
            intent_scored = True
        elif extracted_intent == "low":
            points += 3
            reasons.append("extracted_low_intent(+3)")
            intent_scored = True

        # Fallback to keyword matching if extraction didn't fire
        if not intent_scored:
            if any(kw in msg_lower for kw in [
                "demo", "trial", "pricing", "buy", "purchase", "urgent",
                "upgrade", "renew",
            ]):
                points += 15
                reasons.append("high_intent_message(+15)")
            elif any(kw in msg_lower for kw in ["interested", "learn more", "evaluate", "considering"]):
                points += 8
                reasons.append("medium_intent_message(+8)")
            elif any(kw in msg_lower for kw in ["info", "question", "curious"]):
                points += 3
                reasons.append("low_intent_message(+3)")

    # 5. Industry bonus
    industry = enrichment.get("industry", "unknown")
    if industry in ("financial_services", "technology"):
        points += 5
        reasons.append(f"target_industry_{industry}(+5)")

    # ── Phase 1.6 rule 3: existing-customer boost ────────────────────────
    if enrichment.get("is_customer"):
        points += 15
        reasons.append("existing_customer(+15)")

    # 6. Free-email cap
    if not enrichment.get("is_business_email", False):
        if points > _FREE_EMAIL_CAP:
            points = _FREE_EMAIL_CAP
            reasons.append(f"free_email_cap({_FREE_EMAIL_CAP})")

    # Spam + free email + no company → hard disqualify
    if is_spam and not enrichment.get("is_business_email", False):
        reasons.append("spam_free_email_disqualify")
        return 0, "; ".join(reasons), "disqualified"

    # ── Phase E: intent gates firmographics ──────────────────────────────
    # Good company + no buying intent must NOT reach warm. Firmographics
    # alone (business_email + enterprise + industry) can accumulate 45+
    # points even when the lead is an intern, a PR request, or a
    # sponsorship pitch. Cap at cold (44) when intent is low/unknown AND
    # seniority doesn't independently justify warm (manager+ is exempt).
    # Only gate when extraction ran AND found low/no intent. If extraction
    # returned unknown (e.g., non-English message the heuristic can't parse)
    # but the keyword fallback DID fire, don't gate — the keyword signal
    # is real even if extraction missed it.
    _INTENT_GATE_CAP = 44
    intent_conf = enrichment.get("extracted_intent_confidence", 0.0)
    keyword_intent_fired = any(
        r.startswith("high_intent_message") or r.startswith("medium_intent_message")
        for r in reasons
    )
    if extracted_intent in ("low",) or (extracted_intent in ("unknown", "") and intent_conf == 0.0 and not keyword_intent_fired):
        sen_justifies_warm = seniority in ("c_level", "vp", "director", "manager")
        if not sen_justifies_warm and points > _INTENT_GATE_CAP:
            points = _INTENT_GATE_CAP
            reasons.append(f"intent_gate_cap({_INTENT_GATE_CAP})")

    points = max(0, min(100, points))
    return points, "; ".join(reasons), None


def _classify(points: int) -> tuple[str, str]:
    for threshold, tier, route in _TIER_THRESHOLDS:
        if points >= threshold:
            return tier, route
    return "disqualified", "drop"


class ScoreLeadTool(BaseTool):
    def __init__(self, provider: str = "mock", model: str = "gpt-4o-mini") -> None:
        self._provider = provider
        self._model = model

    @property
    def name(self) -> str:
        return "score_lead"

    def run(self, args: dict, run_id: str = "") -> dict:
        email = args.get("email", "")
        name = args.get("name", "")
        company = args.get("company", "")
        message = args.get("message", "")
        enrichment = args.get("enrichment", {})

        rule_points, reason, hard_override = _score_rules(email, message, enrichment)

        # If a hard override fired, skip LLM adjustment entirely
        if hard_override:
            return {
                "email": email,
                "points": rule_points,
                "tier": hard_override,
                "route": "drop" if hard_override == "disqualified" else _classify(rule_points)[1],
                "reason": reason,
                "rule_points": rule_points,
                "llm_adjustment": 0,
                "llm_reason": "",
                "llm_tokens_in": 0,
                "llm_tokens_out": 0,
            }

        rule_tier, _ = _classify(rule_points)

        # Injection flag: if enrichment flagged prompt injection, skip LLM
        # adjustment entirely — the message text cannot be trusted as input
        # to a scoring LLM.
        injection_flagged = bool(enrichment.get("injection_flagged"))

        # LLM adjustment (openai provider only, skipped if injection flagged)
        llm_adjustment = 0
        llm_reason = ""
        llm_tokens_in = 0
        llm_tokens_out = 0

        if injection_flagged:
            llm_reason = "skipped: injection_flagged"
            logger.info("Skipping LLM adjustment for %s: injection_flagged", email)
        elif self._provider == "openai":
            from gtm_triage.agents.llm_client import infer_score_adjustment
            llm_adjustment, llm_reason, llm_tokens_in, llm_tokens_out = infer_score_adjustment(
                email=email, name=name, company=company, message=message,
                enrichment=enrichment, rule_points=rule_points, rule_tier=rule_tier,
                model=self._model, run_id=run_id,
            )
        else:
            adj = args.get("llm_adjustment", 0)
            try:
                llm_adjustment = max(-10, min(10, int(adj)))
            except (ValueError, TypeError):
                llm_adjustment = 0

        total = max(0, min(100, rule_points + llm_adjustment))
        tier, route = _classify(total)

        result = {
            "email": email,
            "points": total,
            "tier": tier,
            "route": route,
            "reason": reason,
            "rule_points": rule_points,
            "llm_adjustment": llm_adjustment,
            "llm_reason": llm_reason,
            "llm_tokens_in": llm_tokens_in,
            "llm_tokens_out": llm_tokens_out,
        }
        if injection_flagged:
            result["injection_flagged"] = True
        return result
