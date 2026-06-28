"""NullApolloClient — no-op client that returns empty results.

Used when APOLLO_SOURCE=off. Never raises, never hits the network.
"""

from __future__ import annotations

from gtm_triage.apollo.client import ApolloClient
from gtm_triage.apollo.models import ApolloOrg, ApolloOrgSearchResult


class NullApolloClient(ApolloClient):
    """No-op client — returns empty typed results, never raises."""

    def search_organizations(
        self,
        *,
        keyword_tags: list[str] | None = None,
        employee_ranges: list[str] | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> ApolloOrgSearchResult:
        return ApolloOrgSearchResult(
            pagination={"page": page, "per_page": per_page, "total_entries": 0, "total_pages": 0},
            organizations=[],
        )

    def enrich_organization(self, *, domain: str) -> ApolloOrg | None:
        return None
