"""ChatAdapter — chat transcript text -> Lead via extraction.

Takes a raw chat transcript and extracts the visitor's email, name, and
intent from the conversation text.
"""

from __future__ import annotations

import re
from typing import Any

from gtm_triage.channels.base import ChannelAdapter, ParsedLead
from gtm_triage.enrichment.extraction import extract_lead_signals
from gtm_triage.models.lead import Lead


class ChatAdapter(ChannelAdapter):
    @property
    def channel_name(self) -> str:
        return "chat"

    def to_lead(self, raw: Any, *, source: str | None = None) -> ParsedLead:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("ChatAdapter expects a non-empty transcript string")

        transcript = raw.strip()

        # Try to extract an email from the transcript
        emails = [e.rstrip(".") for e in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", transcript)]
        email = emails[0] if emails else ""

        if not email:
            raise ValueError("Could not extract an email address from the chat transcript")

        # Try to extract a name (look for "My name is X" or "I'm X" patterns)
        name = ""
        m = re.search(r"(?:my name is|I'?m|i'?m|this is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", transcript)
        if m:
            name = m.group(1).strip()

        # Use extraction for intent/seniority
        signals = extract_lead_signals(name=name, message=transcript, email=email)
        company = signals.company or ""

        lead = Lead(
            email=email,
            name=name,
            company=company,
            message=transcript,
            source=source or "chat",
        )

        field_sources: dict[str, str] = {"email": "transcript"}
        if name:
            field_sources["name"] = "transcript"
        if company:
            field_sources["company"] = "extraction"

        conf = 0.6 if name else 0.4

        return ParsedLead(
            lead=lead,
            source=source or "chat",
            extraction_confidence=conf,
            field_sources=field_sources,
        )
