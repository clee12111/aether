"""Tests for the Apollo.io connector — Phase 3 (fixture-first, offline).

Covers model parsing from real API fixtures, all three client variants,
to_research_signals(), LiveApolloClient request construction (mocked transport),
and the factory.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from gtm_triage.apollo.client import get_apollo_client
from gtm_triage.apollo.fixture_client import FixtureApolloClient
from gtm_triage.apollo.live_client import LiveApolloClient
from gtm_triage.apollo.models import (
    ApolloEnrichResult,
    ApolloOrg,
    ApolloOrgSearchResult,
)
from gtm_triage.apollo.null_client import NullApolloClient

_FIXTURES = Path(__file__).resolve().parent.parent / "gtm_triage" / "apollo" / "fixtures"


# ── Model parsing ──────────────────────────────────────────────────────────

class TestModelParsing:
    def test_org_search_parses(self):
        raw = json.loads((_FIXTURES / "org_search.json").read_text())
        result = ApolloOrgSearchResult.model_validate(raw)
        assert result.pagination["total_entries"] == 33614
        assert len(result.organizations) == 3
        org = result.organizations[0]
        assert org.name == "GenAI Works"
        assert org.primary_domain == "genai.works"
        assert org.estimated_num_employees == 850
        assert org.industry == "information technology & services"

    def test_org_enrich_parses(self):
        raw = json.loads((_FIXTURES / "org_enrich.json").read_text())
        result = ApolloEnrichResult.model_validate(raw)
        org = result.organization
        assert org.name == "Notion Labs, Inc."
        assert org.primary_domain == "notion.so"
        assert org.organization_revenue == 600000000.0

    def test_enrich_has_extra_fields(self):
        """Enrichment shape has funding, tech, description that search lacks."""
        raw = json.loads((_FIXTURES / "org_enrich.json").read_text())
        org = ApolloEnrichResult.model_validate(raw).organization
        assert org.short_description is not None
        assert "Notion" in org.short_description
        assert org.total_funding == 613199697
        assert org.total_funding_printed == "613.2M"
        assert len(org.funding_events) == 4
        assert len(org.technology_names) >= 5
        assert "Zendesk" in org.technology_names

    def test_search_lacks_enrich_fields(self):
        """Search shape omits enrich-only fields — they default to None/[]."""
        raw = json.loads((_FIXTURES / "org_search.json").read_text())
        org = ApolloOrgSearchResult.model_validate(raw).organizations[0]
        assert org.short_description is None
        assert org.total_funding is None
        assert org.funding_events == []
        assert org.technology_names == []

    def test_extra_fields_ignored(self):
        """Unknown fields in the API response don't cause parse errors."""
        raw = json.loads((_FIXTURES / "org_search.json").read_text())
        # org_search has fields like intent_strength, sic_codes that aren't in the model
        result = ApolloOrgSearchResult.model_validate(raw)
        assert len(result.organizations) == 3


# ── to_research_signals ────────────────────────────────────────────────────

class TestResearchSignals:
    def test_notion_signals(self):
        raw = json.loads((_FIXTURES / "org_enrich.json").read_text())
        org = ApolloEnrichResult.model_validate(raw).organization
        signals = org.to_research_signals()

        assert signals["what_they_do"] is not None
        assert "Notion" in signals["what_they_do"]
        assert signals["source"] == "apollo"
        assert signals["revenue"] == "600M"
        assert len(signals["tech_stack"]) >= 5

        # Funding signal
        assert len(signals["recent_signals"]) >= 1
        funding = signals["recent_signals"][0]
        assert funding["kind"] == "funding"
        assert funding["source"] == "apollo"
        assert "270M" in funding["text"]
        assert "GIC" in funding["text"] or "Sequoia" in funding["text"]

    def test_search_org_signals(self):
        """Search orgs have no funding/description — signals are sparser."""
        raw = json.loads((_FIXTURES / "org_search.json").read_text())
        org = ApolloOrgSearchResult.model_validate(raw).organizations[0]
        signals = org.to_research_signals()
        assert signals["what_they_do"] is None  # no short_description in search
        assert signals["recent_signals"] == []  # no funding_events
        assert signals["industry"] == "information technology & services"
        assert signals["source"] == "apollo"

    def test_employee_bucket(self):
        assert ApolloOrg(id="a", name="x", estimated_num_employees=10)._employee_bucket() == "smb"
        assert ApolloOrg(id="a", name="x", estimated_num_employees=200)._employee_bucket() == "mid_market"
        assert ApolloOrg(id="a", name="x", estimated_num_employees=1000)._employee_bucket() == "enterprise"
        assert ApolloOrg(id="a", name="x", estimated_num_employees=0)._employee_bucket() is None
        assert ApolloOrg(id="a", name="x")._employee_bucket() is None


# ── FixtureApolloClient ────────────────────────────────────────────────────

class TestFixtureClient:
    def setup_method(self):
        self.client = FixtureApolloClient()

    def test_search_organizations(self):
        result = self.client.search_organizations()
        assert isinstance(result, ApolloOrgSearchResult)
        assert len(result.organizations) == 3
        assert all(isinstance(o, ApolloOrg) for o in result.organizations)

    def test_enrich_notion(self):
        org = self.client.enrich_organization(domain="notion.so")
        assert org is not None
        assert org.name == "Notion Labs, Inc."
        assert org.short_description is not None

    def test_enrich_unknown_domain(self):
        org = self.client.enrich_organization(domain="unknown-xyz.com")
        assert org is not None
        assert org.id == "fixture-unknown-xyz.com"
        assert org.primary_domain == "unknown-xyz.com"


# ── NullApolloClient ──────────────────────────────────────────────────────

class TestNullClient:
    def setup_method(self):
        self.client = NullApolloClient()

    def test_search_empty(self):
        result = self.client.search_organizations()
        assert result.organizations == []
        assert result.pagination["total_entries"] == 0

    def test_enrich_none(self):
        assert self.client.enrich_organization(domain="anything.com") is None

    def test_never_raises(self):
        self.client.search_organizations(keyword_tags=["x"], employee_ranges=["1,10"])
        self.client.enrich_organization(domain="x")


# ── LiveApolloClient (mocked transport — NO real network) ──────────────────

class TestLiveClientRequestConstruction:
    """Verify the LiveApolloClient builds correct requests without hitting the network."""

    def _make_transport(self, response_json: dict, status: int = 200) -> httpx.MockTransport:
        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json=response_json)
        return httpx.MockTransport(_handler)

    def test_search_url_and_headers(self):
        search_fixture = json.loads((_FIXTURES / "org_search.json").read_text())
        captured = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["method"] = request.method
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=search_fixture)

        transport = httpx.MockTransport(_handler)
        http_client = httpx.Client(transport=transport)
        client = LiveApolloClient(api_key="test-key-123", client=http_client)

        result = client.search_organizations(
            keyword_tags=["saas"],
            employee_ranges=["51,200"],
            page=2,
            per_page=10,
        )

        assert captured["method"] == "POST"
        assert "api.apollo.io/api/v1/organizations/search" in captured["url"]
        assert captured["headers"]["x-api-key"] == "test-key-123"
        assert captured["body"]["q_organization_keyword_tags"] == ["saas"]
        assert captured["body"]["organization_num_employees_ranges"] == ["51,200"]
        assert captured["body"]["page"] == 2
        assert captured["body"]["per_page"] == 10
        assert isinstance(result, ApolloOrgSearchResult)
        assert len(result.organizations) == 3

    def test_enrich_url_and_headers(self):
        enrich_fixture = json.loads((_FIXTURES / "org_enrich.json").read_text())
        captured = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["method"] = request.method
            captured["headers"] = dict(request.headers)
            return httpx.Response(200, json=enrich_fixture)

        transport = httpx.MockTransport(_handler)
        http_client = httpx.Client(transport=transport)
        client = LiveApolloClient(api_key="test-key-456", client=http_client)

        org = client.enrich_organization(domain="notion.so")

        assert captured["method"] == "GET"
        assert "api.apollo.io/api/v1/organizations/enrich" in captured["url"]
        assert "domain=notion.so" in captured["url"]
        assert captured["headers"]["x-api-key"] == "test-key-456"
        assert org is not None
        assert org.name == "Notion Labs, Inc."

    def test_enrich_404_returns_none(self):
        transport = self._make_transport({}, status=404)
        http_client = httpx.Client(transport=transport)
        client = LiveApolloClient(api_key="k", client=http_client)
        assert client.enrich_organization(domain="nope.com") is None

    def test_enrich_empty_org_returns_none(self):
        transport = self._make_transport({"organization": None}, status=200)
        http_client = httpx.Client(transport=transport)
        client = LiveApolloClient(api_key="k", client=http_client)
        assert client.enrich_organization(domain="empty.com") is None


# ── Factory ────────────────────────────────────────────────────────────────

class TestFactory:
    def test_default_is_fixture(self, monkeypatch):
        monkeypatch.delenv("APOLLO_SOURCE", raising=False)
        client = get_apollo_client()
        assert isinstance(client, FixtureApolloClient)

    def test_fixture_explicit(self, monkeypatch):
        monkeypatch.setenv("APOLLO_SOURCE", "fixture")
        client = get_apollo_client()
        assert isinstance(client, FixtureApolloClient)

    def test_off_returns_null(self, monkeypatch):
        monkeypatch.setenv("APOLLO_SOURCE", "off")
        client = get_apollo_client()
        assert isinstance(client, NullApolloClient)

    def test_live_returns_live(self, monkeypatch):
        monkeypatch.setenv("APOLLO_SOURCE", "live")
        client = get_apollo_client()
        assert isinstance(client, LiveApolloClient)
