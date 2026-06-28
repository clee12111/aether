"""fit_score tool — deterministic ICP fit scoring + clamped LLM nudge.

Mirrors score_lead.py: rules own the tier, LLM can nudge +/-10 with a reason.

Rules score a company brief against a campaign's ICP:
  - Industry match:     +20
  - Size match:         +20
  - Tech-fit:           +10 (any overlap between tech_stack and icp_keywords)
  - is_requester:       +25 (strong positive — they already asked)
  - Has what_they_do:   +5  (we can personalize)
  - Has recent signals: +5

Tier thresholds (same as inbound):
  hot:           >= 70
  warm:          45-69
  cold:          20-44
  disqualified:  < 20

Route mapping:
  hot  -> ae_immediate
  warm -> sdr_nurture
  cold -> marketing_nurture
  disqualified -> skip
"""

from __future__ import annotations

import logging

from gtm_triage.tools.base import BaseTool

logger = logging.getLogger(__name__)

_TIER_THRESHOLDS = [
    (70, "hot", "ae_immediate"),
    (45, "warm", "sdr_nurture"),
    (20, "cold", "marketing_nurture"),
    (0, "disqualified", "skip"),
]

# Target industries — broad set for outbound ICP matching
_HIGH_FIT_INDUSTRIES = {
    "technology", "information technology & services", "computer software",
    "financial_services", "saas", "internet",
}


def _classify(points: int) -> tuple[str, str]:
    for threshold, tier, route in _TIER_THRESHOLDS:
        if points >= threshold:
            return tier, route
    return "disqualified", "skip"


def _score_fit(brief: dict, campaign: dict) -> tuple[int, list[str]]:
    """Deterministic ICP fit score. Returns (points, reason_codes)."""
    points = 0
    reasons: list[str] = []

    # Industry match
    industry = (brief.get("industry") or "").lower()
    icp_keywords = [k.lower() for k in campaign.get("icp_keywords", [])]
    if industry and (industry in _HIGH_FIT_INDUSTRIES or any(kw in industry for kw in icp_keywords)):
        points += 20
        reasons.append(f"industry_match({industry},+20)")

    # Size match
    size = brief.get("size") or ""
    icp_ranges = campaign.get("icp_employee_ranges", [])
    if size:
        # Map size buckets to rough employee ranges for matching
        size_to_ranges = {
            "smb": ["1,50", "51,200"],
            "mid_market": ["201,500", "201,1000", "501,1000"],
            "enterprise": ["1001,5000", "5001,10000"],
        }
        matching_ranges = size_to_ranges.get(size, [])
        if any(r in icp_ranges for r in matching_ranges) or not icp_ranges:
            points += 20
            reasons.append(f"size_match({size},+20)")

    # Tech-fit (any keyword overlap between tech_stack and ICP keywords)
    tech_stack = [t.lower() for t in brief.get("tech_stack", [])]
    if tech_stack and icp_keywords:
        overlaps = [kw for kw in icp_keywords if any(kw in t for t in tech_stack)]
        if overlaps:
            points += 10
            reasons.append(f"tech_fit({','.join(overlaps[:3])},+10)")

    # Description-fit (ICP keywords appear in what_they_do)
    what_they_do = (brief.get("what_they_do") or "").lower()
    if what_they_do and icp_keywords:
        desc_overlaps = [kw for kw in icp_keywords if kw in what_they_do]
        if desc_overlaps:
            points += 15
            reasons.append(f"description_fit({','.join(desc_overlaps[:3])},+15)")

    # is_requester (strong positive)
    if brief.get("is_requester"):
        points += 25
        reasons.append("is_requester(+25)")

    # Has what_they_do (we can personalize)
    if brief.get("what_they_do"):
        points += 5
        reasons.append("has_description(+5)")

    # Has recent signals (funding/launch = active company)
    signals = brief.get("recent_signals", [])
    if signals:
        points += 5
        reasons.append("has_signals(+5)")
        # Funding signal is an extra positive — well-capitalized company
        has_funding = any(s.get("kind") == "funding" for s in signals)
        if has_funding:
            points += 10
            reasons.append("has_funding_signal(+10)")

    # Tech stack breadth — having a real stack means they're tech-forward
    if len(tech_stack) >= 5:
        points += 5
        reasons.append(f"tech_breadth({len(tech_stack)},+5)")

    points = max(0, min(100, points))
    return points, reasons


class FitScoreTool(BaseTool):
    def __init__(self, provider: str = "mock", model: str = "gpt-4o-mini") -> None:
        self._provider = provider
        self._model = model

    @property
    def name(self) -> str:
        return "fit_score"

    def run(self, args: dict, run_id: str = "") -> dict:
        brief = args.get("brief", {})
        campaign = args.get("campaign", {})

        rule_points, reason_codes = _score_fit(brief, campaign)
        rule_tier, _ = _classify(rule_points)

        # LLM nudge (clamped +/-10, mirrors score_lead pattern)
        llm_adjustment = 0
        llm_reason = ""
        if self._provider != "mock":
            # Future: LLM call to nudge based on nuanced brief signals
            pass
        else:
            adj = args.get("llm_adjustment", 0)
            try:
                llm_adjustment = max(-10, min(10, int(adj)))
            except (ValueError, TypeError):
                llm_adjustment = 0

        total = max(0, min(100, rule_points + llm_adjustment))
        tier, route = _classify(total)

        return {
            "points": total,
            "tier": tier,
            "route": route,
            "reason_codes": reason_codes,
            "rule_points": rule_points,
            "llm_adjustment": llm_adjustment,
            "llm_reason": llm_reason,
        }
