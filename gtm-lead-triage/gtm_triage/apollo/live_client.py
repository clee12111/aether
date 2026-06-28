"""LiveApolloClient — real Apollo.io API via httpx.

Free-tier endpoints only:
  - GET  /api/v1/organizations/enrich   (org enrichment by domain)
  - POST /api/v1/organizations/search   (org search with ICP filters)

People endpoints (mixed_people, people/search, people/match) return 403
on the free plan and are NOT called.

Auth: X-Api-Key header with the raw key (no Bearer prefix).
"""

from __future__ import annotations

import logging
import os

import httpx

from gtm_triage.apollo.client import ApolloClient
from gtm_triage.apollo.models import ApolloEnrichResult, ApolloOrg, ApolloOrgSearchResult

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.apollo.io/api/v1"


class LiveApolloClient(ApolloClient):
    """Live Apollo.io client — free-tier org endpoints."""

    def __init__(
        self,
        api_key: str = "",
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = (api_key or os.environ.get("APOLLO_API_KEY", "")).strip()
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=15.0)
        return self._client

    def _headers(self) -> dict[str, str]:
        return {
            "X-Api-Key": self._api_key,
            "Content-Type": "application/json",
        }

    def search_organizations(
        self,
        *,
        keyword_tags: list[str] | None = None,
        employee_ranges: list[str] | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> ApolloOrgSearchResult:
        body: dict = {"page": page, "per_page": per_page}
        if keyword_tags:
            body["q_organization_keyword_tags"] = keyword_tags
        if employee_ranges:
            body["organization_num_employees_ranges"] = employee_ranges

        client = self._get_client()
        resp = client.post(
            f"{_BASE_URL}/organizations/search",
            headers=self._headers(),
            json=body,
        )
        if resp.status_code != 200:
            logger.warning("Apollo search: HTTP %d (returning empty)", resp.status_code)
            return ApolloOrgSearchResult(
                pagination={"page": page, "per_page": per_page, "total_entries": 0, "total_pages": 0},
                organizations=[],
            )
        return ApolloOrgSearchResult.model_validate(resp.json())

    def enrich_organization(self, *, domain: str) -> ApolloOrg | None:
        client = self._get_client()
        resp = client.get(
            f"{_BASE_URL}/organizations/enrich",
            headers=self._headers(),
            params={"domain": domain},
        )
        if resp.status_code == 404:
            logger.debug("Apollo enrich %s: 404 not found", domain)
            return None
        if resp.status_code != 200:
            # 422 = credits exhausted, 403 = plan limit, etc. - degrade gracefully
            logger.warning("Apollo enrich %s: HTTP %d (degrading to None)", domain, resp.status_code)
            return None

        data = resp.json()
        if not data.get("organization"):
            logger.debug("Apollo enrich %s: 200 but no organization in response", domain)
            return None

        enrich = ApolloEnrichResult.model_validate(data)
        org = enrich.organization
        logger.debug(
            "Apollo enrich %s: industry=%s size=%s desc=%s tech=%d funding=%d",
            domain,
            org.industry or "empty",
            org.estimated_num_employees or "empty",
            "yes" if org.short_description else "empty",
            len(org.technology_names),
            len(org.funding_events),
        )
        return org

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None
