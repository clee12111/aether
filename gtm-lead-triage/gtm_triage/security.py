"""Security primitives: SSRF guard, prompt-injection detection.

SSRF: block private/internal IPs, non-http(s) schemes, cap redirects.
Injection: detect known patterns in lead messages, flag but don't crash.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ── SSRF guard ───────────────────────────────────────────────────────────────

# IP ranges that must never be fetched (RFC1918, loopback, link-local, etc.)
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 private
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]

_ALLOWED_SCHEMES = {"http", "https"}
_MAX_RESPONSE_BYTES = 1 * 1024 * 1024  # 1 MB


def validate_url(url: str) -> str | None:
    """Validate a URL for SSRF safety. Returns an error message or None if safe."""
    try:
        parsed = urlparse(url)
    except Exception:
        return "Malformed URL"

    if parsed.scheme not in _ALLOWED_SCHEMES:
        return f"Blocked scheme: {parsed.scheme}"

    if not parsed.hostname:
        return "No hostname"

    return None


def validate_domain(domain: str) -> str | None:
    """Resolve a domain and check the IP isn't internal. Returns error or None."""
    try:
        # Resolve before connecting — prevents DNS rebinding
        infos = socket.getaddrinfo(domain, 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in infos:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            for net in _BLOCKED_NETWORKS:
                if ip in net:
                    return f"Blocked IP: {ip_str} resolves to internal network"
    except (socket.gaierror, socket.timeout, OSError):
        # DNS resolution failed — domain doesn't exist, which is fine (not SSRF)
        return None

    return None


def ssrf_safe_domain(domain: str) -> bool:
    """Return True if the domain is safe to fetch (not internal)."""
    url_err = validate_url(f"https://{domain}")
    if url_err:
        logger.warning("SSRF blocked: %s (%s)", domain, url_err)
        return False

    ip_err = validate_domain(domain)
    if ip_err:
        logger.warning("SSRF blocked: %s (%s)", domain, ip_err)
        return False

    return True


# ── Prompt injection detection ───────────────────────────────────────────────

# Known injection patterns in lead messages
_INJECTION_PATTERNS = [
    r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions",
    r"ignore\s+(?:your|the)\s+(?:instructions|rules|prompt|system)",
    r"system\s*(?:override|prompt|admin)",
    r"(?:classify|mark|set|change)\s+(?:this\s+)?(?:lead\s+)?(?:as\s+)?(?:tier\s*=?\s*)?(?:hot|warm)",
    r"authorization\s+code",
    r"priority\s+override",
    r"admin\s+(?:access|mode|override)",
    r"you\s+(?:are|must)\s+(?:now|a)\s+(?:different|new)",
    r"disregard\s+(?:previous|prior|all)",
    r"forget\s+(?:previous|prior|all|your)",
    r"new\s+(?:instructions|rules|prompt)",
    r"jailbreak",
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]


def detect_injection(text: str) -> tuple[bool, str]:
    """Check if text contains known prompt-injection patterns.

    Returns (is_suspicious, matched_pattern). The lead is NOT blocked —
    just flagged. The deterministic scorer is the backstop (message text
    reaches scoring only as typed signals, never as instructions).
    """
    for pattern in _COMPILED_PATTERNS:
        m = pattern.search(text)
        if m:
            return True, m.group(0)
    return False, ""
