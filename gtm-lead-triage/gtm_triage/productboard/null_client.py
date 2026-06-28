"""NullProductboardClient — no-op client that returns empty results.

Used when PRODUCTBOARD_SOURCE=off. Never raises, never hits the network.
Same discipline as the Sentry/OTel no-op-without-config pattern.
"""

from __future__ import annotations

from gtm_triage.productboard.client import ProductboardClient
from gtm_triage.productboard.models import (
    PBCreateFeedbackResult,
    PBFeedbackList,
    PBField,
    PBIdentity,
    PBMembership,
    PBQueryResult,
    PBWorkspace,
)


class NullProductboardClient(ProductboardClient):
    """No-op client — returns empty typed results, never raises."""

    def get_identity(self) -> PBIdentity:
        return PBIdentity(
            membership=PBMembership(id="", role="", email=""),
            workspace=PBWorkspace(id=0, domain=""),
        )

    def list_feature_fields(self) -> list[PBField]:
        return []

    def query_features(
        self,
        *,
        filter: dict | None = None,
        fields: list[str] | None = None,
        limit: int = 50,
    ) -> PBQueryResult:
        return PBQueryResult(entities=[], total_count=0, unmatched_fields=[])

    def list_feedback(
        self,
        *,
        entity_ids: list[str],
        processed: bool | None = None,
        archived: bool = False,
        cursor: str | None = None,
    ) -> PBFeedbackList:
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
        return PBCreateFeedbackResult(
            id="null",
            name=title,
            display_url="",
            created_at="",
            company=None,
        )
