"""Pydantic v2 models matching real Productboard MCP response shapes.

All models use populate_by_name=True so they parse both camelCase JSON keys
(from the MCP server) and snake_case attribute names (from Python code).
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ── Identity ────────────────────────────────────────────────────────────────

class PBMembership(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    role: str
    email: str


class PBWorkspace(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: int
    domain: str


class PBIdentity(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    membership: PBMembership
    workspace: PBWorkspace


# ── Entity fields ───────────────────────────────────────────────────────────

class PBField(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str
    field_type: str = Field(alias="fieldType")
    id: str


# ── Features / entities ────────────────────────────────────────────────────

class PBFeature(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    entity_id: str = Field(alias="entityId")
    name: str
    entity_type: str = Field(alias="entityType")
    url: str
    fields: list[Any] = Field(default_factory=list)


class PBQueryResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    entities: list[PBFeature]
    total_count: int = Field(alias="totalCount")
    unmatched_fields: list[Any] = Field(default_factory=list, alias="unmatchedFields")


# ── Feedback ────────────────────────────────────────────────────────────────

# Regex for parsing the customer string.  Examples:
#   "Sample User C @ Sample Company C (productboard.com)"
#   "Sample Company B (productboard.com)"
_CUSTOMER_RE = re.compile(
    r"^(?:(?P<display_name>.+?)\s+@\s+)?(?P<company>.+?)\s+\((?P<domain>[^)]+)\)$"
)


class ParsedCustomer(BaseModel):
    """Best-effort parse of the loose customer string.

    May be None/placeholder — do NOT treat as reliable identity.
    """
    display_name: str | None = None
    company: str | None = None
    domain: str | None = None


class PBFeedbackItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    name: str
    display_url: str = Field(alias="displayUrl")
    content: str
    tags: list[str] = Field(default_factory=list)
    processed: bool
    archived: bool
    customer: str
    created_at: str = Field(alias="createdAt")

    @property
    def parsed_customer(self) -> ParsedCustomer:
        """Best-effort parse of the customer string.

        Extracts domain from the last parenthesized group, display_name
        from text before " @ " if present.  May return empty fields —
        this is a convenience, not a contract.
        """
        m = _CUSTOMER_RE.match(self.customer)
        if not m:
            return ParsedCustomer()
        return ParsedCustomer(
            display_name=m.group("display_name"),
            company=m.group("company"),
            domain=m.group("domain"),
        )


class PBFeedbackList(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    feedback: list[PBFeedbackItem]
    next_cursor: str | None = Field(default=None, alias="nextCursor")


class PBCreateFeedbackResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    name: str
    display_url: str = Field(alias="displayUrl")
    created_at: str = Field(alias="createdAt")
    company: str | None = None
