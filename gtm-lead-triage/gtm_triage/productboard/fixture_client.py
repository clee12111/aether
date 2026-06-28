"""FixtureProductboardClient — replays recorded MCP payloads from fixtures/.

Read methods parse and return the recorded fixture JSON. Write methods
(create_feedback) synthesize a deterministic result so the sink is testable
without network access.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from gtm_triage.productboard.client import ProductboardClient
from gtm_triage.productboard.models import (
    PBCreateFeedbackResult,
    PBFeedbackList,
    PBField,
    PBIdentity,
    PBQueryResult,
)

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES_DIR / name).read_text(encoding="utf-8"))


class FixtureProductboardClient(ProductboardClient):
    """Offline client that replays recorded MCP response fixtures."""

    def get_identity(self) -> PBIdentity:
        return PBIdentity.model_validate(_load("identity_get_identity.json"))

    def list_feature_fields(self) -> list[PBField]:
        data = _load("entities_list_entity_field_names.json")
        return [PBField.model_validate(f) for f in data["fields"]]

    def query_features(
        self,
        *,
        filter: dict | None = None,
        fields: list[str] | None = None,
        limit: int = 50,
    ) -> PBQueryResult:
        return PBQueryResult.model_validate(
            _load("entities_query_entities.json")
        )

    def list_feedback(
        self,
        *,
        entity_ids: list[str],
        processed: bool | None = None,
        archived: bool = False,
        cursor: str | None = None,
    ) -> PBFeedbackList:
        return PBFeedbackList.model_validate(
            _load("feedback_list_feedback.json")
        )

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
        # Deterministic ID derived from title for reproducibility
        stable_id = hashlib.sha256(title.encode()).hexdigest()[:36]
        return PBCreateFeedbackResult(
            id=stable_id,
            name=title,
            display_url=f"https://fixture.productboard.com/all-notes/notes/{stable_id[:8]}",
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            company=company_domain,
        )
