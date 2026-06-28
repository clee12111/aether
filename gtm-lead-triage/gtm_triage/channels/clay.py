"""ClayWebhookAdapter — Clay-enriched row (flat dict) -> Lead.

Maps common Clay column names to Lead fields. Tolerates arbitrary/missing
columns gracefully — unmapped columns are ignored, missing fields become
empty strings.
"""

from __future__ import annotations

from typing import Any

from gtm_triage.channels.base import ChannelAdapter, ParsedLead
from gtm_triage.models.lead import Lead

# Common Clay column name variants -> Lead field
_COLUMN_MAP: dict[str, str] = {
    # email
    "email": "email",
    "Email": "email",
    "email_address": "email",
    "Email Address": "email",
    "Work Email": "email",
    "work_email": "email",
    # name
    "name": "name",
    "Name": "name",
    "full_name": "name",
    "Full Name": "name",
    "First Name": "first_name",
    "first_name": "first_name",
    "Last Name": "last_name",
    "last_name": "last_name",
    # company
    "company": "company",
    "Company": "company",
    "company_name": "company",
    "Company Name": "company",
    "Organization": "company",
    # message/notes
    "message": "message",
    "Message": "message",
    "notes": "message",
    "Notes": "message",
    "note": "message",
    # title/role (stored in message as context)
    "title": "title",
    "Title": "title",
    "Job Title": "title",
    "job_title": "title",
    # domain
    "domain": "domain",
    "Domain": "domain",
    "Website": "domain",
    "website": "domain",
}


class ClayWebhookAdapter(ChannelAdapter):
    @property
    def channel_name(self) -> str:
        return "clay"

    def to_lead(self, raw: Any, *, source: str | None = None) -> ParsedLead:
        if not isinstance(raw, dict):
            raise ValueError("ClayWebhookAdapter expects a dict (Clay row)")

        if not raw:
            raise ValueError("Empty Clay row")

        # Map columns to fields
        mapped: dict[str, str] = {}
        field_sources: dict[str, str] = {}

        for col_name, col_value in raw.items():
            target = _COLUMN_MAP.get(col_name)
            if target and col_value:
                val = str(col_value).strip()
                if val:
                    mapped[target] = val
                    field_sources[target] = f"clay:{col_name}"

        # Handle first_name + last_name -> name
        if "name" not in mapped:
            first = mapped.pop("first_name", "")
            last = mapped.pop("last_name", "")
            if first or last:
                mapped["name"] = f"{first} {last}".strip()
                field_sources["name"] = "clay:first+last"

        # Email is required
        email = mapped.get("email", "")
        if not email:
            raise ValueError("Clay row must contain an email column")

        # Build message from title + message + any extra context
        message_parts = []
        if mapped.get("title"):
            message_parts.append(f"Role: {mapped['title']}")
        if mapped.get("message"):
            message_parts.append(mapped["message"])
        message = ". ".join(message_parts) if message_parts else ""

        lead = Lead(
            email=email,
            name=mapped.get("name", ""),
            company=mapped.get("company", ""),
            message=message,
            source=source or "clay",
        )

        conf = 0.9 if mapped.get("name") and mapped.get("company") else 0.6

        return ParsedLead(
            lead=lead,
            source=source or "clay",
            extraction_confidence=conf,
            field_sources=field_sources,
        )
