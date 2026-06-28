"""FixtureApolloClient — replays recorded API payloads from fixtures/.

search_organizations returns the recorded search results (ignoring filters).
enrich_organization returns the recorded notion.so org for any domain that
matches, otherwise synthesizes a minimal ApolloOrg stub.
"""

from __future__ import annotations

import json
from pathlib import Path

from gtm_triage.apollo.client import ApolloClient
from gtm_triage.apollo.models import ApolloEnrichResult, ApolloOrg, ApolloOrgSearchResult

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES_DIR / name).read_text(encoding="utf-8"))


class FixtureApolloClient(ApolloClient):
    """Offline client that replays recorded Apollo response fixtures."""

    def search_organizations(
        self,
        *,
        keyword_tags: list[str] | None = None,
        employee_ranges: list[str] | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> ApolloOrgSearchResult:
        return ApolloOrgSearchResult.model_validate(_load("org_search.json"))

    def enrich_organization(self, *, domain: str) -> ApolloOrg | None:
        data = _load("org_enrich.json")
        enrich = ApolloEnrichResult.model_validate(data)
        org = enrich.organization

        # If the requested domain matches the fixture, return it
        if org.primary_domain and org.primary_domain == domain:
            return org

        # Otherwise return a minimal stub (no network, deterministic)
        return ApolloOrg(
            id=f"fixture-{domain}",
            name=domain,
            primary_domain=domain,
        )
