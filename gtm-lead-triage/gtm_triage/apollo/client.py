"""ApolloClient ABC and factory.

get_apollo_client() reads APOLLO_SOURCE from environment:
  "fixture" (default) -> FixtureApolloClient   (replays recorded payloads)
  "live"              -> LiveApolloClient       (real API, free-tier endpoints)
  "off"               -> NullApolloClient       (no-op, never raises)
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

from gtm_triage.apollo.models import ApolloOrg, ApolloOrgSearchResult


class ApolloClient(ABC):
    """Abstract interface to the Apollo.io API."""

    @abstractmethod
    def search_organizations(
        self,
        *,
        keyword_tags: list[str] | None = None,
        employee_ranges: list[str] | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> ApolloOrgSearchResult: ...

    @abstractmethod
    def enrich_organization(self, *, domain: str) -> ApolloOrg | None: ...


def get_apollo_client() -> ApolloClient:
    """Factory — returns the client variant matching APOLLO_SOURCE."""
    source = os.environ.get("APOLLO_SOURCE", "fixture").lower()

    if source == "live":
        from gtm_triage.apollo.live_client import LiveApolloClient
        return LiveApolloClient()

    if source == "off":
        from gtm_triage.apollo.null_client import NullApolloClient
        return NullApolloClient()

    # Default: fixture
    from gtm_triage.apollo.fixture_client import FixtureApolloClient
    return FixtureApolloClient()
