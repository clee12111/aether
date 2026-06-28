"""ChannelAdapter ABC — normalizes raw channel input into a Lead.

Each adapter takes a channel-specific payload and returns a Lead that
satisfies the Signal protocol, ready for run_triage().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from gtm_triage.models.lead import Lead


class ParsedLead(BaseModel):
    """Lead + metadata about how it was parsed."""
    lead: Lead
    source: str
    extraction_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    field_sources: dict[str, str] = Field(default_factory=dict)


class ChannelAdapter(ABC):
    """Normalizes raw channel input into a Lead."""

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Short identifier for this channel (e.g. 'email', 'chat', 'clay')."""

    @abstractmethod
    def to_lead(self, raw: Any, *, source: str | None = None) -> ParsedLead:
        """Parse raw channel input into a Lead + metadata.

        Raises ValueError for unparseable input.
        """
