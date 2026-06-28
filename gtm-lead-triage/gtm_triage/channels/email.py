"""EmailAdapter — raw email (From/Subject/Body) -> Lead via extraction.

Parses a raw email string to extract the sender's email, name, and message
content. Uses the extraction module to infer seniority/intent from the body.
"""

from __future__ import annotations

import re
from typing import Any

from gtm_triage.channels.base import ChannelAdapter, ParsedLead
from gtm_triage.enrichment.extraction import extract_lead_signals
from gtm_triage.models.lead import Lead


def _parse_raw_email(raw: str) -> dict[str, str]:
    """Extract From, Subject, Body from a raw email string.

    Handles both structured header format and plain text.
    """
    from_addr = ""
    from_name = ""
    subject = ""
    body = raw

    # Try to parse headers
    lines = raw.split("\n")
    header_end = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            header_end = i + 1
            break

        m_from = re.match(r"^From:\s*(.+)$", stripped, re.I)
        if m_from:
            from_raw = m_from.group(1).strip()
            # Parse "Name <email>" or just "email"
            m_addr = re.search(r"<([^>]+)>", from_raw)
            if m_addr:
                from_addr = m_addr.group(1).strip()
                from_name = from_raw[:m_addr.start()].strip().strip('"')
            else:
                from_addr = from_raw
            continue

        m_subj = re.match(r"^Subject:\s*(.+)$", stripped, re.I)
        if m_subj:
            subject = m_subj.group(1).strip()
            continue

    # Body is everything after headers
    if header_end > 0:
        body = "\n".join(lines[header_end:]).strip()

    return {
        "from_addr": from_addr,
        "from_name": from_name,
        "subject": subject,
        "body": body,
    }


class EmailAdapter(ChannelAdapter):
    @property
    def channel_name(self) -> str:
        return "email"

    def to_lead(self, raw: Any, *, source: str | None = None) -> ParsedLead:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("EmailAdapter expects a non-empty raw email string")

        parsed = _parse_raw_email(raw)
        email = parsed["from_addr"]
        name = parsed["from_name"]
        message = parsed["body"]

        if not email:
            # Try to find an email anywhere in the text
            m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", raw)
            if m:
                email = m.group(0).rstrip(".")

        if not email:
            raise ValueError("Could not extract an email address from the raw email")

        # Use extraction to infer fields from the message
        signals = extract_lead_signals(name=name, message=message, email=email)
        company = signals.company or ""

        # Combine subject + body for message
        full_message = message
        if parsed["subject"]:
            full_message = f"[Subject: {parsed['subject']}] {message}"

        lead = Lead(
            email=email,
            name=name,
            company=company,
            message=full_message,
            source=source or "email",
        )

        field_sources: dict[str, str] = {"email": "email_header"}
        if name:
            field_sources["name"] = "email_header"
        if company:
            field_sources["company"] = "extraction"

        # Confidence: high if we got email + name from headers, lower if inferred
        conf = 0.8 if name else 0.5

        return ParsedLead(
            lead=lead,
            source=source or "email",
            extraction_confidence=conf,
            field_sources=field_sources,
        )
