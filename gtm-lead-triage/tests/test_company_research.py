"""Tests for company research enrichment — Phase 2 (fixture-first, offline).

Covers model parsing, CompanyResearcher with fixtures (PDL + website + search),
anti-fabrication guard, COMPANY_RESEARCH=off toggle, and search provider factory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_triage.enrichment.base import EnrichmentResult, FieldValue
from gtm_triage.enrichment.company_research import (
    CompanyResearch,
    CompanyResearcher,
    RecentSignal,
    SourcedClaim,
)
from gtm_triage.enrichment.fixture_provider import FixtureProvider
from gtm_triage.enrichment.search import (
    FixtureSearchProvider,
    NullSearchProvider,
    get_search_provider,
)

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "gtm_triage" / "enrichment" / "fixtures"


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_pdl_fixture() -> FixtureProvider:
    """PDL fixture with notion.so and datadoghq.com data."""
    return FixtureProvider(_FIXTURES_DIR / "pdl_enrichment.json")


def _make_website_fetcher() -> callable:
    """Website fetcher that returns fixture text for known domains."""
    def _fetch(domain: str) -> str | None:
        path = _FIXTURES_DIR / f"website_{domain}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("text")
        return None
    return _fetch


def _make_search_provider() -> FixtureSearchProvider:
    return FixtureSearchProvider(_FIXTURES_DIR / "search")


def _make_researcher() -> CompanyResearcher:
    return CompanyResearcher(
        pdl_provider=_make_pdl_fixture(),
        search_provider=_make_search_provider(),
        website_fetcher=_make_website_fetcher(),
        llm_provider="mock",
    )


# ── Model tests ─────────────────────────────────────────────────────────────

class TestCompanyResearchModel:
    def test_basic_construction(self):
        cr = CompanyResearch(
            domain="example.com",
            what_they_do="They sell widgets.",
            industry="technology",
            size="enterprise",
            confidence=0.75,
        )
        assert cr.domain == "example.com"
        assert cr.what_they_do == "They sell widgets."
        assert cr.recent_signals == []
        assert cr.likely_problems == []

    def test_to_draft_context_keys(self):
        cr = CompanyResearch(
            domain="example.com",
            what_they_do="They sell widgets.",
            industry="technology",
            size="enterprise",
            recent_signals=[
                RecentSignal(text="Launched v2", source_url="https://x.com/1", kind="launch"),
            ],
            likely_problems=["scaling infrastructure"],
            inferred_role="VP of Sales",
            confidence=0.75,
        )
        ctx = cr.to_draft_context()
        assert set(ctx.keys()) == {
            "domain", "what_they_do", "industry", "size",
            "recent_signals", "likely_problems", "inferred_role", "confidence",
            "tech_stack", "is_requester",
        }
        assert len(ctx["recent_signals"]) == 1
        assert ctx["recent_signals"][0]["kind"] == "launch"
        assert ctx["likely_problems"] == ["scaling infrastructure"]

    def test_none_fields_when_ungrounded(self):
        cr = CompanyResearch(domain="unknown.com", confidence=0.0)
        assert cr.what_they_do is None
        assert cr.industry is None
        assert cr.size is None
        assert cr.recent_signals == []

    def test_sourced_claim(self):
        sc = SourcedClaim(text="Industry: tech", source="pdl")
        assert sc.source == "pdl"

    def test_recent_signal(self):
        rs = RecentSignal(text="Launched AI", source_url="https://x.com", kind="launch")
        assert rs.kind == "launch"


# ── Researcher with full fixtures (notion.so) ──────────────────────────────

class TestCompanyResearcherFull:
    def setup_method(self):
        self.researcher = _make_researcher()

    def test_notion_has_industry_and_size(self):
        result = self.researcher.research("notion.so", email="user@notion.so")
        assert result.industry == "technology"
        assert result.size == "enterprise"

    def test_notion_has_what_they_do(self):
        result = self.researcher.research("notion.so", email="user@notion.so")
        # what_they_do should be populated from website text (mock LLM fallback)
        assert result.what_they_do is not None
        assert len(result.what_they_do) > 10

    def test_notion_has_recent_signals(self):
        result = self.researcher.research("notion.so", email="user@notion.so")
        assert len(result.recent_signals) >= 1
        # Signals should have classified kinds
        kinds = {s.kind for s in result.recent_signals}
        assert kinds  # at least one kind classified

    def test_every_source_has_nonempty_source_string(self):
        result = self.researcher.research("notion.so", email="user@notion.so")
        for claim in result.sources:
            assert claim.source, f"Empty source for claim: {claim.text}"
            assert len(claim.source) > 0

    def test_confidence_positive(self):
        result = self.researcher.research("notion.so", email="user@notion.so")
        assert result.confidence > 0.0

    def test_datadoghq_has_signals(self):
        result = self.researcher.research("datadoghq.com", email="user@datadoghq.com")
        assert len(result.recent_signals) >= 1
        assert result.industry == "technology"


# ── Anti-fabrication ────────────────────────────────────────────────────────

class TestAntiFabrication:
    def test_unknown_domain_no_fabrication(self, monkeypatch):
        """A domain with NO Apollo/PB/website/search hits → nothing invented."""
        monkeypatch.setenv("APOLLO_SOURCE", "off")
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "off")
        researcher = CompanyResearcher(
            pdl_provider=FixtureProvider({}),  # empty PDL
            search_provider=FixtureSearchProvider(_FIXTURES_DIR / "search"),
            website_fetcher=lambda domain: None,  # no website
            llm_provider="mock",
        )
        result = researcher.research("unknown-domain-404.com")
        # Anti-fabrication: nothing invented
        assert result.what_they_do is None
        assert result.recent_signals == []
        assert result.likely_problems == []
        assert result.industry is None
        assert result.size is None
        assert result.confidence == 0.0
        assert result.is_requester is False
        assert result.tech_stack == []

    def test_pdl_only_domain_no_website(self, monkeypatch):
        """PDL hit but no website/search/apollo → what_they_do is None, signals empty."""
        monkeypatch.setenv("APOLLO_SOURCE", "off")
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "off")
        researcher = CompanyResearcher(
            pdl_provider=_make_pdl_fixture(),
            search_provider=NullSearchProvider(),
            website_fetcher=lambda domain: None,
            llm_provider="mock",
        )
        result = researcher.research("notion.so", email="user@notion.so")
        assert result.industry == "technology"
        assert result.size == "enterprise"
        # No search results → no recent signals
        assert result.recent_signals == []


# ── COMPANY_RESEARCH=off ────────────────────────────────────────────────────

class TestCompanyResearchOff:
    def test_off_returns_pdl_only_brief(self, monkeypatch):
        monkeypatch.setenv("COMPANY_RESEARCH", "off")

        # Track whether website/search were called
        website_called = []
        search_called = []

        class TrackingSearch:
            def search(self, query, *, num_results=5):
                search_called.append(query)
                return []

        researcher = CompanyResearcher(
            pdl_provider=_make_pdl_fixture(),
            search_provider=TrackingSearch(),
            website_fetcher=lambda d: website_called.append(d) or "text",
            llm_provider="mock",
        )
        result = researcher.research("notion.so", email="user@notion.so")

        # PDL data present
        assert result.industry == "technology"
        assert result.size == "enterprise"
        # No website or search calls
        assert website_called == []
        assert search_called == []
        # No website/search-derived fields
        assert result.what_they_do is None
        assert result.recent_signals == []

    def test_off_never_raises(self, monkeypatch):
        monkeypatch.setenv("COMPANY_RESEARCH", "off")
        researcher = CompanyResearcher(
            pdl_provider=FixtureProvider({}),
            llm_provider="mock",
        )
        # Should not raise even with empty PDL
        result = researcher.research("nonexistent.example")
        assert result.domain == "nonexistent.example"


# ── Search provider factory ────────────────────────────────────────────────

class TestSearchProviderFactory:
    def test_default_is_fixture(self, monkeypatch):
        monkeypatch.delenv("SEARCH_PROVIDER", raising=False)
        provider = get_search_provider()
        assert isinstance(provider, FixtureSearchProvider)

    def test_fixture_explicit(self, monkeypatch):
        monkeypatch.setenv("SEARCH_PROVIDER", "fixture")
        provider = get_search_provider()
        assert isinstance(provider, FixtureSearchProvider)

    def test_off_returns_null(self, monkeypatch):
        monkeypatch.setenv("SEARCH_PROVIDER", "off")
        provider = get_search_provider()
        assert isinstance(provider, NullSearchProvider)

    def test_null_returns_empty(self):
        provider = NullSearchProvider()
        results = provider.search("anything")
        assert results == []


# ── Fixture search provider ────────────────────────────────────────────────

class TestFixtureSearchProvider:
    def test_loads_notion_fixture(self):
        provider = FixtureSearchProvider(_FIXTURES_DIR / "search")
        results = provider.search("notion.so product OR launch OR hiring")
        assert len(results) == 3
        assert "Notion" in results[0].title
        assert results[0].url.startswith("https://")

    def test_loads_datadog_fixture(self):
        provider = FixtureSearchProvider(_FIXTURES_DIR / "search")
        results = provider.search("datadoghq.com product OR launch OR hiring")
        assert len(results) == 3

    def test_missing_domain_returns_empty(self):
        provider = FixtureSearchProvider(_FIXTURES_DIR / "search")
        results = provider.search("nonexistent-xyz.com stuff")
        assert results == []

    def test_num_results_respected(self):
        provider = FixtureSearchProvider(_FIXTURES_DIR / "search")
        results = provider.search("notion.so stuff", num_results=1)
        assert len(results) == 1


# ── Signal kind classification ─────────────────────────────────────────────

class TestSignalClassification:
    def test_funding_before_hiring(self):
        """'Raises Series D... Eyes Enterprise Expansion' → funding, not hiring."""
        from gtm_triage.enrichment.company_research import _classify_signal
        kind = _classify_signal(
            "Notion Raises Series D at $15B Valuation, Eyes Enterprise Expansion",
            "Productivity platform Notion has closed a $350M Series D round...",
        )
        assert kind == "funding"

    def test_pure_hiring(self):
        from gtm_triage.enrichment.company_research import _classify_signal
        kind = _classify_signal("Datadog Hiring 500+ Engineers", "expanding team")
        assert kind == "hiring"

    def test_launch(self):
        from gtm_triage.enrichment.company_research import _classify_signal
        kind = _classify_signal("Notion Launches Notion Mail", "new product")
        assert kind == "launch"

    def test_unknown(self):
        from gtm_triage.enrichment.company_research import _classify_signal
        kind = _classify_signal("Something random", "nothing specific")
        assert kind == "other"


# ── Phase 4a: Apollo integration ───────────────────────────────────────────

class TestApolloIntegration:
    """Apollo fixture enrichment composed into the company brief."""

    def test_notion_has_apollo_what_they_do(self, monkeypatch):
        monkeypatch.setenv("APOLLO_SOURCE", "fixture")
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "off")
        researcher = _make_researcher()
        result = researcher.research("notion.so", email="user@notion.so")
        # Apollo's short_description is the primary what_they_do
        assert result.what_they_do is not None
        assert "Notion" in result.what_they_do
        # The what_they_do claim should carry source="apollo"
        apollo_wtd = [s for s in result.sources if s.source == "apollo" and "What they do" in s.text]
        assert len(apollo_wtd) >= 1

    def test_notion_has_apollo_revenue(self, monkeypatch):
        monkeypatch.setenv("APOLLO_SOURCE", "fixture")
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "off")
        researcher = _make_researcher()
        result = researcher.research("notion.so", email="user@notion.so")
        revenue_claims = [s for s in result.sources if s.source == "apollo" and "Revenue" in s.text]
        assert len(revenue_claims) >= 1
        assert "600M" in revenue_claims[0].text

    def test_notion_has_tech_stack(self, monkeypatch):
        monkeypatch.setenv("APOLLO_SOURCE", "fixture")
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "off")
        researcher = _make_researcher()
        result = researcher.research("notion.so", email="user@notion.so")
        assert len(result.tech_stack) >= 5
        assert "Zendesk" in result.tech_stack

    def test_notion_has_funding_signal(self, monkeypatch):
        monkeypatch.setenv("APOLLO_SOURCE", "fixture")
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "off")
        researcher = _make_researcher()
        result = researcher.research("notion.so", email="user@notion.so")
        funding = [s for s in result.recent_signals if s.kind == "funding"]
        assert len(funding) >= 1
        assert "270M" in funding[0].text
        # Funding claim sourced as apollo
        funding_claims = [s for s in result.sources if s.source == "apollo" and "270M" in s.text]
        assert len(funding_claims) >= 1

    def test_apollo_off_no_apollo_calls(self, monkeypatch):
        monkeypatch.setenv("APOLLO_SOURCE", "off")
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "off")
        researcher = _make_researcher()
        result = researcher.research("notion.so", email="user@notion.so")
        # No apollo-sourced claims
        apollo_claims = [s for s in result.sources if s.source == "apollo"]
        assert apollo_claims == []
        assert result.tech_stack == []


# ── Phase 4a: Productboard demand integration ──────────────────────────────

class TestProductboardDemand:
    """Productboard demand signal — is_requester + demand claim."""

    def test_matching_domain_is_requester(self, monkeypatch):
        """datadoghq.com is in the feedback fixture (real seeded) -> is_requester=True."""
        monkeypatch.setenv("APOLLO_SOURCE", "off")
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "fixture")
        researcher = CompanyResearcher(
            pdl_provider=FixtureProvider({}),
            search_provider=NullSearchProvider(),
            website_fetcher=lambda d: None,
            llm_provider="mock",
        )
        result = researcher.research("datadoghq.com")
        assert result.is_requester is True
        pb_claims = [s for s in result.sources if s.source.startswith("productboard:")]
        assert len(pb_claims) >= 1
        assert "requested" in pb_claims[0].text.lower()
        assert "SSO" in pb_claims[0].text or "Datadog" in pb_claims[0].text

    def test_matching_domain_has_demand_signal(self, monkeypatch):
        """datadoghq.com -> a RecentSignal with kind='demand'."""
        monkeypatch.setenv("APOLLO_SOURCE", "off")
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "fixture")
        researcher = CompanyResearcher(
            pdl_provider=FixtureProvider({}),
            search_provider=NullSearchProvider(),
            website_fetcher=lambda d: None,
            llm_provider="mock",
        )
        result = researcher.research("datadoghq.com")
        demand = [s for s in result.recent_signals if s.kind == "demand"]
        assert len(demand) >= 1
        assert demand[0].source_url.startswith("https://")

    def test_nonmatching_domain_not_requester(self, monkeypatch):
        """A domain NOT in feedback -> is_requester=False, no demand claim."""
        monkeypatch.setenv("APOLLO_SOURCE", "off")
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "fixture")
        researcher = CompanyResearcher(
            pdl_provider=FixtureProvider({}),
            search_provider=NullSearchProvider(),
            website_fetcher=lambda d: None,
            llm_provider="mock",
        )
        result = researcher.research("webflow.com")
        assert result.is_requester is False
        pb_claims = [s for s in result.sources if s.source.startswith("productboard:")]
        assert pb_claims == []
        demand = [s for s in result.recent_signals if s.kind == "demand"]
        assert demand == []

    def test_productboard_off_no_pb_calls(self, monkeypatch):
        monkeypatch.setenv("APOLLO_SOURCE", "off")
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "off")
        researcher = CompanyResearcher(
            pdl_provider=FixtureProvider({}),
            search_provider=NullSearchProvider(),
            website_fetcher=lambda d: None,
            llm_provider="mock",
        )
        result = researcher.research("productboard.com")
        assert result.is_requester is False
        pb_claims = [s for s in result.sources if s.source.startswith("productboard:")]
        assert pb_claims == []


# ── Phase 4a: Both sources off → identical to Phase 2 ─────────────────────

class TestBothSourcesOff:
    def test_both_off_matches_phase2(self, monkeypatch):
        """With APOLLO_SOURCE=off and PRODUCTBOARD_SOURCE=off, output is Phase 2 PDL-only."""
        monkeypatch.setenv("APOLLO_SOURCE", "off")
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "off")
        researcher = _make_researcher()
        result = researcher.research("notion.so", email="user@notion.so")
        # PDL data present
        assert result.industry == "technology"
        assert result.size == "enterprise"
        # No apollo/pb claims
        apollo_claims = [s for s in result.sources if s.source == "apollo"]
        pb_claims = [s for s in result.sources if s.source.startswith("productboard:")]
        assert apollo_claims == []
        assert pb_claims == []
        assert result.tech_stack == []
        assert result.is_requester is False
