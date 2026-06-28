"""WebFormAdapter — structured form fields -> Lead.

Formalizes the existing /triage path: fields are already structured,
confidence is 1.0 for provided fields.
"""

from __future__ import annotations

from typing import Any

from gtm_triage.channels.base import ChannelAdapter, ParsedLead
from gtm_triage.models.lead import Lead


class WebFormAdapter(ChannelAdapter):
    @property
    def channel_name(self) -> str:
        return "web_form"

    def to_lead(self, raw: Any, *, source: str | None = None) -> ParsedLead:
        if not isinstance(raw, dict):
            raise ValueError("WebFormAdapter expects a dict with email/name/company/message fields")

        email = (raw.get("email") or "").strip()
        if not email:
            raise ValueError("email is required")

        lead = Lead(
            email=email,
            name=(raw.get("name") or "").strip(),
            company=(raw.get("company") or "").strip(),
            message=(raw.get("message") or "").strip(),
            source=source or "inbound_form",
        )

        field_sources = {k: "form" for k in ("email", "name", "company", "message") if raw.get(k)}

        return ParsedLead(
            lead=lead,
            source=source or "inbound_form",
            extraction_confidence=1.0,
            field_sources=field_sources,
        )
