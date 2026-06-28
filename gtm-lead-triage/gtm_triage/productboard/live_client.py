"""LiveProductboardClient - Productboard REST API v2 via httpx.

Uses a Public API token (PRODUCTBOARD_TOKEN) with Bearer auth.
Falls back gracefully on failure (no crash on triage runs).

REST API v2 docs: https://developer.productboard.com/reference
Note endpoints:
  POST /notes         - create a note (feedback)
  GET  /notes         - list notes
Feature endpoints:
  GET  /features      - list features
  GET  /feature-fields - list feature field definitions

CAVEAT: The Public API requires a Public API token from Productboard
Integrations settings (Team plan+). If the workspace can't mint one,
PRODUCTBOARD_SOURCE stays on 'fixture' and this client is unused.
The REST v2 response shapes differ slightly from the MCP shapes -
a mapper normalizes them to the shared PB models.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from gtm_triage.productboard.client import ProductboardClient
from gtm_triage.productboard.models import (
    PBCreateFeedbackResult,
    PBFeedbackItem,
    PBFeedbackList,
    PBFeature,
    PBField,
    PBIdentity,
    PBMembership,
    PBQueryResult,
    PBWorkspace,
)

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.productboard.com"


class LiveProductboardClient(ProductboardClient):
    """Live Productboard client via REST API v2 + Bearer token."""

    def __init__(self, token: str = "", client: httpx.Client | None = None) -> None:
        self._token = (token or os.environ.get("PRODUCTBOARD_TOKEN", "")).strip()
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=15.0)
        return self._client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "X-Version": "1",
        }

    def get_identity(self) -> PBIdentity:
        """Not available via REST API v2 - return a placeholder."""
        return PBIdentity(
            membership=PBMembership(id="", role="", email=""),
            workspace=PBWorkspace(id=0, domain=""),
        )

    def list_feature_fields(self) -> list[PBField]:
        """GET /feature-fields - not commonly needed; return empty."""
        return []

    def query_features(
        self,
        *,
        filter: dict | None = None,
        fields: list[str] | None = None,
        limit: int = 50,
    ) -> PBQueryResult:
        """GET /features - list features."""
        try:
            client = self._get_client()
            resp = client.get(
                f"{_BASE_URL}/features",
                headers=self._headers(),
                params={"pageLimit": min(limit, 100)},
            )
            resp.raise_for_status()
            data = resp.json()

            features = []
            for item in data.get("data", []):
                features.append(PBFeature(
                    entity_id=item.get("id", ""),
                    name=item.get("name", ""),
                    entity_type="Feature",
                    url=item.get("links", {}).get("html", ""),
                ))

            return PBQueryResult(
                entities=features,
                total_count=len(features),
                unmatched_fields=[],
            )
        except Exception as exc:
            logger.warning("PB query_features failed: %s", exc)
            return PBQueryResult(entities=[], total_count=0, unmatched_fields=[])

    def list_feedback(
        self,
        *,
        entity_ids: list[str],
        processed: bool | None = None,
        archived: bool = False,
        cursor: str | None = None,
    ) -> PBFeedbackList:
        """GET /notes - list notes (feedback).

        The REST v2 Notes API returns a different shape than the MCP.
        Mapper normalizes to PBFeedbackItem.
        """
        try:
            client = self._get_client()
            params: dict[str, Any] = {"pageLimit": 50}
            if cursor:
                params["pageCursor"] = cursor

            resp = client.get(
                f"{_BASE_URL}/notes",
                headers=self._headers(),
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

            items: list[PBFeedbackItem] = []
            for note in data.get("data", []):
                # Map REST v2 note shape -> PBFeedbackItem
                # REST: {id, title, content, tags[], customer:{email, company:{name, domain}}, createdAt}
                customer_str = ""
                customer_data = note.get("customer", {})
                if customer_data:
                    email = customer_data.get("email", "")
                    company = customer_data.get("company", {})
                    company_name = company.get("name", "") if isinstance(company, dict) else ""
                    company_domain = company.get("domain", "") if isinstance(company, dict) else ""
                    if email and company_name and company_domain:
                        customer_str = f"{email} @ {company_name} ({company_domain})"
                    elif company_name and company_domain:
                        customer_str = f"{company_name} ({company_domain})"
                    elif email:
                        customer_str = email

                items.append(PBFeedbackItem(
                    id=note.get("id", ""),
                    name=note.get("title", ""),
                    display_url=note.get("links", {}).get("html", ""),
                    content=note.get("content", ""),
                    tags=[t.get("name", t) if isinstance(t, dict) else str(t) for t in note.get("tags", [])],
                    processed=note.get("state") == "processed",
                    archived=note.get("state") == "archived",
                    customer=customer_str,
                    created_at=note.get("createdAt", ""),
                ))

            next_cursor = data.get("pageCursor")
            return PBFeedbackList(feedback=items, next_cursor=next_cursor)

        except Exception as exc:
            logger.warning("PB list_feedback failed: %s", exc)
            return PBFeedbackList(feedback=[])

    def create_feedback(
        self,
        *,
        title: str,
        content: str,
        customer_email: str | None = None,
        company_domain: str | None = None,
        source_url: str | None = None,
        tags: list[str] | None = None,
        entity_id: str | None = None,
    ) -> PBCreateFeedbackResult:
        """POST /notes - create a note (feedback).

        Maps the internal create_feedback interface to the REST v2 Notes API.
        """
        body: dict[str, Any] = {
            "title": title,
            "content": content,
        }
        if tags:
            body["tags"] = tags
        if source_url:
            body["source"] = {"origin": "api", "record_id": source_url}

        # Customer info
        if customer_email or company_domain:
            customer: dict[str, Any] = {}
            if customer_email:
                customer["email"] = customer_email
            if company_domain:
                customer["company"] = {"domain": company_domain}
            body["customer"] = customer

        try:
            client = self._get_client()
            resp = client.post(
                f"{_BASE_URL}/notes",
                headers=self._headers(),
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

            note = data.get("data", data)
            return PBCreateFeedbackResult(
                id=note.get("id", ""),
                name=note.get("title", title),
                display_url=note.get("links", {}).get("html", ""),
                created_at=note.get("createdAt", ""),
                company=company_domain,
            )
        except Exception as exc:
            logger.warning("PB create_feedback failed: %s", exc)
            # Return a minimal result so the caller doesn't crash
            return PBCreateFeedbackResult(
                id="error",
                name=title,
                display_url="",
                created_at="",
                company=company_domain,
            )

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None
