"""Productboard write-back: log inbound lead requests by company domain.

Best-effort extracts the request/need from the lead's message and writes it
to Productboard as feedback. Skips free-email domains and messages with no
clear request. No-op when PRODUCTBOARD_SOURCE=off.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# Free email domains that don't map to a company
_FREE_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "mail.com", "protonmail.com", "zoho.com", "yandex.com",
    "live.com", "msn.com", "me.com", "fastmail.com", "tutanota.com",
}

# Intent keywords that indicate a request/need (not just browsing)
_REQUEST_PATTERNS = [
    r"\b(?:need|want|looking for|searching for|require|must have)\b",
    r"\b(?:can you|could you|would you|please|help us)\b",
    r"\b(?:demo|trial|pricing|schedule|onboard|integrate)\b",
    r"\b(?:evaluate|evaluating|comparing|considering|exploring)\b",
    r"\b(?:feedback|roadmap|feature request|product)\b.*\b(?:tool|platform|solution)\b",
    r"\b(?:centralize|consolidate|unify|streamline)\b",
    r"\b(?:budget|approved|quarter|timeline|deadline)\b",
    r"\b(?:scale|scaling|growing|expanding)\b.*\b(?:team|org|product)\b",
]


def has_request(message: str) -> bool:
    """Check if a message contains a clear request/need (not just browsing)."""
    if not message or len(message.strip()) < 20:
        return False
    msg_lower = message.lower()
    # At least 2 request-pattern matches = likely a real request
    matches = sum(1 for pat in _REQUEST_PATTERNS if re.search(pat, msg_lower))
    return matches >= 2


def extract_request(message: str, company: str = "") -> str:
    """Extract the core request from a message. Returns the message if it's a request."""
    # For now, use the full message as the request content.
    # A future LLM step could summarize, but the heuristic is sufficient.
    prefix = f"Request from {company}: " if company else "Request: "
    return f"{prefix}{message.strip()}"


def write_lead_to_productboard(
    *,
    email: str,
    message: str,
    company: str = "",
    name: str = "",
    run_id: str = "",
    trace: Any = None,
) -> dict[str, Any] | None:
    """Write a lead's request to Productboard. Returns the created note or None.

    Skips if: PRODUCTBOARD_SOURCE=off, free-email domain, no clear request.
    """
    pb_source = os.environ.get("PRODUCTBOARD_SOURCE", "fixture").lower()
    if pb_source == "off":
        return None

    # Extract domain
    domain = email.rsplit("@", 1)[1].lower() if "@" in email else ""
    if not domain or domain in _FREE_DOMAINS:
        return None

    # Check for a real request
    if not has_request(message):
        return None

    # Write to Productboard
    try:
        from gtm_triage.productboard import get_productboard_client
        pb = get_productboard_client()

        content = extract_request(message, company)
        title = f"{company or domain} - inbound request"
        tags = ["inbound", "auto-logged"]

        result = pb.create_feedback(
            title=title,
            content=content,
            customer_email=email,
            company_domain=domain,
            tags=tags,
        )

        # Log to trace
        if trace and run_id:
            trace.write(
                run_id=run_id,
                event_type="tool_call",
                agent="productboard_writeback",
                payload={
                    "tool": "create_feedback",
                    "domain": domain,
                    "note_id": result.id,
                    "note_url": result.display_url,
                    "title": title,
                },
            )

        logger.info("Logged lead request to Productboard: %s -> %s", domain, result.id)
        return {
            "note_id": result.id,
            "note_url": result.display_url,
            "title": title,
            "domain": domain,
        }

    except Exception as exc:
        logger.debug("Productboard write-back failed for %s: %s", domain, exc)
        return None
