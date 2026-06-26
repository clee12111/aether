"""Email validation: syntax, MX/DNS, free-domain, and disposable-domain detection.

Zero external dependencies — uses stdlib socket for DNS resolution.
No paid API calls. Returns a structured EmailSignal.
"""

from __future__ import annotations

import re
import socket
from typing import Literal

from pydantic import BaseModel

# RFC 5322 simplified — catches the vast majority of real addresses
_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$"
)

# Well-known free email providers (lowercase domains)
FREE_DOMAINS: frozenset[str] = frozenset({
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "aol.com", "icloud.com", "mail.com", "protonmail.com",
    "proton.me", "live.com", "msn.com", "ymail.com",
    "gmx.com", "gmx.net", "zoho.com", "fastmail.com",
    "tutanota.com", "tuta.io", "hey.com", "pm.me",
    "me.com", "mac.com",
})

# Disposable/temporary email domains. Maintained subset of the most common ones.
# In production, swap this for a larger maintained list (e.g., disposable-email-domains on GitHub).
DISPOSABLE_DOMAINS: frozenset[str] = frozenset({
    "mailinator.com", "guerrillamail.com", "guerrillamail.de",
    "tempmail.com", "throwaway.email", "temp-mail.org",
    "fakeinbox.com", "sharklasers.com", "guerrillamailblock.com",
    "grr.la", "dispostable.com", "yopmail.com", "trashmail.com",
    "maildrop.cc", "discard.email", "tempail.com",
    "10minutemail.com", "minutemail.com", "emailondeck.com",
    "mohmal.com", "getnada.com", "burnermail.io",
    "mailnesia.com", "tempr.email", "tmail.ws",
    "tmpmail.net", "tmpmail.org", "binkmail.com",
    "getairmail.com", "filzmail.com", "inboxbear.com",
    "mailcatch.com", "meltmail.com", "harakirimail.com",
    "bounce-system.net",
})


class EmailSignal(BaseModel):
    """Structured email validation result."""

    email: str
    domain: str = ""
    verdict: Literal["deliverable", "free", "disposable", "invalid"] = "invalid"
    syntax_valid: bool = False
    mx_valid: bool | None = None  # None = check skipped (e.g., syntax invalid)
    is_free: bool = False
    is_disposable: bool = False


def _check_syntax(email: str) -> bool:
    return bool(_EMAIL_RE.match(email))


def _extract_domain(email: str) -> str:
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[1].lower()


def _check_mx(domain: str, timeout: float = 3.0) -> bool:
    """Check if domain has resolvable DNS (A or MX) via stdlib socket.

    We try getaddrinfo which handles A/AAAA records. For a more precise MX
    check, dnspython would be needed — but this catches invalid/typo domains
    (gmial.com, fakeco.invalid) without any external dependency.
    """
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo(domain, 25, socket.AF_UNSPEC, socket.SOCK_STREAM)
        return True
    except (socket.gaierror, socket.timeout, OSError):
        return False


def check_email(email: str, skip_dns: bool = False) -> EmailSignal:
    """Validate an email address. Returns a structured EmailSignal.

    Args:
        email: The email address to validate.
        skip_dns: If True, skip the MX/DNS lookup (for unit tests / offline CI).
    """
    email = email.strip().lower()
    signal = EmailSignal(email=email)

    if not email:
        return signal

    domain = _extract_domain(email)
    signal.domain = domain

    # Syntax check
    signal.syntax_valid = _check_syntax(email)
    if not signal.syntax_valid:
        return signal

    # Free domain check
    signal.is_free = domain in FREE_DOMAINS
    # Disposable domain check
    signal.is_disposable = domain in DISPOSABLE_DOMAINS

    if signal.is_disposable:
        signal.verdict = "disposable"
        signal.mx_valid = None  # don't bother checking DNS for known disposable
        return signal

    # MX/DNS check
    if skip_dns:
        signal.mx_valid = None
    else:
        signal.mx_valid = _check_mx(domain)
        if not signal.mx_valid:
            signal.verdict = "invalid"
            return signal

    # Classify
    if signal.is_free:
        signal.verdict = "free"
    else:
        signal.verdict = "deliverable"

    return signal
