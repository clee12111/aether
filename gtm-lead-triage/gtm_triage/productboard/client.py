"""ProductboardClient ABC and factory.

get_productboard_client() reads PRODUCTBOARD_SOURCE from the environment:
  "fixture" (default) -> FixtureProductboardClient  (replays recorded payloads)
  "live"              -> LiveProductboardClient      (NotImplementedError scaffold)
  "off"               -> NullProductboardClient      (no-op, never raises)
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

from gtm_triage.productboard.models import (
    PBCreateFeedbackResult,
    PBFeedbackList,
    PBField,
    PBIdentity,
    PBQueryResult,
)


class ProductboardClient(ABC):
    """Abstract interface to the Productboard workspace."""

    @abstractmethod
    def get_identity(self) -> PBIdentity: ...

    @abstractmethod
    def list_feature_fields(self) -> list[PBField]: ...

    @abstractmethod
    def query_features(
        self,
        *,
        filter: dict | None = None,
        fields: list[str] | None = None,
        limit: int = 50,
    ) -> PBQueryResult: ...

    @abstractmethod
    def list_feedback(
        self,
        *,
        entity_ids: list[str],
        processed: bool | None = None,
        archived: bool = False,
        cursor: str | None = None,
    ) -> PBFeedbackList: ...

    @abstractmethod
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
    ) -> PBCreateFeedbackResult: ...


def get_productboard_client() -> ProductboardClient:
    """Factory — returns the client variant matching PRODUCTBOARD_SOURCE."""
    source = os.environ.get("PRODUCTBOARD_SOURCE", "fixture").lower()

    if source == "live":
        from gtm_triage.productboard.live_client import LiveProductboardClient
        return LiveProductboardClient()

    if source == "off":
        from gtm_triage.productboard.null_client import NullProductboardClient
        return NullProductboardClient()

    # Default: fixture
    from gtm_triage.productboard.fixture_client import FixtureProductboardClient
    return FixtureProductboardClient()
