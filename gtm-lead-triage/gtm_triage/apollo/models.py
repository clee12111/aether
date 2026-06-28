"""Pydantic v2 models for Apollo.io API responses.

ApolloOrg is the unified model for both the lighter search shape (~45 fields)
and the richer enrichment shape (~60 fields). Enrich-only fields default to
None/[] so both parse cleanly.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApolloOrg(BaseModel):
    """Curated subset of an Apollo organization record.

    Fields present in both search and enrichment results are required (with
    Optional where the API returns null). Enrich-only fields have defaults
    so the search shape parses without error.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    # ── Core (both search + enrich) ─────────────────────────────────────
    id: str
    name: str
    primary_domain: str | None = None
    website_url: str | None = None
    industry: str | None = None
    industries: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    estimated_num_employees: int | None = None
    organization_revenue: float | None = None
    organization_revenue_printed: str | None = None
    founded_year: int | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    linkedin_url: str | None = None

    # ── Enrich-only (absent in search results) ──────────────────────────
    short_description: str | None = None
    total_funding: int | None = None
    total_funding_printed: str | None = None
    latest_funding_stage: str | None = None
    latest_funding_round_date: str | None = None
    funding_events: list[dict[str, Any]] = Field(default_factory=list)
    technology_names: list[str] = Field(default_factory=list)
    departmental_head_count: dict[str, Any] = Field(default_factory=dict)

    def to_research_signals(self) -> dict[str, Any]:
        """Drafter-ready grounding dict, each item carrying source='apollo'.

        Mirrors the shape CompanyResearch expects so Apollo can be composed
        into the brief later without changing Phase 2.
        """
        signals: dict[str, Any] = {
            "what_they_do": self.short_description,
            "industry": self.industry if self.industry else (self.industries[0] if self.industries else None),
            "size": self._employee_bucket(),
            "revenue": self.organization_revenue_printed,
            "tech_stack": self.technology_names[:10],
            "recent_signals": [],
            "source": "apollo",
        }

        # Most recent funding event → RecentSignal-compatible dict
        if self.funding_events:
            latest = self.funding_events[0]
            investors = latest.get("investors", "")
            amount = latest.get("amount", "")
            currency = latest.get("currency", "$")
            round_type = latest.get("type", "")
            date = latest.get("date", "")
            text_parts = []
            if amount:
                text_parts.append(f"{currency}{amount} {round_type}".strip())
            if investors:
                text_parts.append(f"from {investors}")
            if date:
                text_parts.append(f"({date[:10]})")
            signals["recent_signals"].append({
                "text": " ".join(text_parts) if text_parts else "Funding round",
                "source_url": latest.get("news_url") or "",
                "kind": "funding",
                "source": "apollo",
            })

        return signals

    def _employee_bucket(self) -> str | None:
        """Map estimated_num_employees to a size bucket."""
        n = self.estimated_num_employees
        if not n:
            return None
        if n <= 50:
            return "smb"
        if n <= 500:
            return "mid_market"
        return "enterprise"


class ApolloOrgSearchResult(BaseModel):
    """Top-level response from POST /organizations/search."""

    model_config = ConfigDict(extra="ignore")

    pagination: dict[str, Any]
    organizations: list[ApolloOrg]


class ApolloEnrichResult(BaseModel):
    """Top-level response from GET /organizations/enrich."""

    model_config = ConfigDict(extra="ignore")

    organization: ApolloOrg
