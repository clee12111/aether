"""Security primitives: SSRF guard, prompt-injection detection.

SSRF: block private/internal IPs (incl. cloud metadata 169.254.169.254),
non-http(s) schemes, IPv4-mapped IPv6, pin validated IP to prevent DNS
rebinding TOCTOU.
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

# IP ranges that must never be fetched
_BLOCKED_NETWORKS = [
    # IPv4
    ipaddress.ip_network("0.0.0.0/8"),         # "this" network
    ipaddress.ip_network("10.0.0.0/8"),        # RFC1918 private
    ipaddress.ip_network("100.64.0.0/10"),     # CGNAT (shared address space)
    ipaddress.ip_network("127.0.0.0/8"),       # loopback
    ipaddress.ip_network("169.254.0.0/16"),    # link-local (incl. cloud metadata 169.254.169.254)
    ipaddress.ip_network("172.16.0.0/12"),     # RFC1918 private
    ipaddress.ip_network("192.168.0.0/16"),    # RFC1918 private
    # IPv6
    ipaddress.ip_network("::1/128"),           # loopback
    ipaddress.ip_network("fc00::/7"),          # ULA (unique local)
    ipaddress.ip_network("fe80::/10"),         # link-local
    ipaddress.ip_network("::ffff:0:0/96"),     # IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1)
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


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IP falls in any blocked network."""
    for net in _BLOCKED_NETWORKS:
        if ip in net:
            return True
    return False


def resolve_and_validate(domain: str) -> tuple[str | None, list[str]]:
    """Resolve a domain, validate all IPs, return (error, safe_ips).

    Returns the resolved IPs so the caller can PIN the connection to them
    (prevents DNS-rebinding TOCTOU: resolve → validate → connect to the
    validated IP, never re-resolve).
    """
    safe_ips: list[str] = []
    try:
        infos = socket.getaddrinfo(domain, 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in infos:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            if _is_blocked_ip(ip):
                return f"Blocked IP: {ip_str} resolves to internal network", []
            safe_ips.append(ip_str)
    except (socket.gaierror, socket.timeout, OSError):
        # DNS resolution failed — domain doesn't exist
        return None, []

    return None, safe_ips


def ssrf_safe_domain(domain: str) -> bool:
    """Return True if the domain is safe to fetch (not internal).

    Resolves DNS, validates all IPs against blocked networks, and returns
    True only if every resolved IP is public.
    """
    url_err = validate_url(f"https://{domain}")
    if url_err:
        logger.warning("SSRF blocked: %s (%s)", domain, url_err)
        return False

    ip_err, _ = resolve_and_validate(domain)
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
