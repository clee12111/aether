"""Sentry integration — no-op without SENTRY_DSN or sentry-sdk.

sentry-sdk is a soft dependency. If not installed, all functions are no-ops.
If installed but SENTRY_DSN is unset, Sentry is not initialized (true no-op).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PII_KEYS = {"email", "name", "company", "message", "lead_email", "full_name"}


def _scrub_value(value: Any) -> Any:
    """Scrub PII from a single value."""
    if isinstance(value, str):
        # Replace email addresses
        value = _EMAIL_RE.sub("[email]", value)
        return value
    return value


def _scrub_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Recursively scrub PII keys and email patterns from a dict."""
    result = {}
    for k, v in d.items():
        if k in _PII_KEYS:
            if isinstance(v, str) and "@" in v:
                result[k] = "[email]"
            else:
                result[k] = "[scrubbed]"
        elif isinstance(v, dict):
            result[k] = _scrub_dict(v)
        elif isinstance(v, list):
            result[k] = [_scrub_dict(i) if isinstance(i, dict) else _scrub_value(i) for i in v]
        elif isinstance(v, str):
            result[k] = _scrub_value(v)
        else:
            result[k] = v
    return result


def before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    """Sentry before_send hook: scrub PII before events leave the process.

    This is a pure function — unit-testable without Sentry.
    """
    # Scrub request data
    if "request" in event and isinstance(event["request"], dict):
        if "data" in event["request"] and isinstance(event["request"]["data"], dict):
            event["request"]["data"] = _scrub_dict(event["request"]["data"])

    # Scrub breadcrumbs
    if "breadcrumbs" in event:
        crumbs = event["breadcrumbs"]
        if isinstance(crumbs, dict) and "values" in crumbs:
            for crumb in crumbs["values"]:
                if isinstance(crumb, dict):
                    if "message" in crumb:
                        crumb["message"] = _scrub_value(crumb["message"])
                    if "data" in crumb and isinstance(crumb["data"], dict):
                        crumb["data"] = _scrub_dict(crumb["data"])

    # Scrub extra context
    if "extra" in event and isinstance(event["extra"], dict):
        event["extra"] = _scrub_dict(event["extra"])

    # Scrub exception values for email patterns
    if "exception" in event and isinstance(event["exception"], dict):
        for exc_val in event["exception"].get("values", []):
            if isinstance(exc_val, dict) and "value" in exc_val:
                exc_val["value"] = _scrub_value(exc_val["value"])

    return event


def init_sentry() -> None:
    """Initialize Sentry if sentry-sdk is installed and SENTRY_DSN is set.

    No-op (silent) if either condition is false.
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return

    try:
        import sentry_sdk
    except ImportError:
        return

    environment = os.environ.get("APP_ENV", "development")
    release = os.environ.get("SENTRY_RELEASE", "")
    traces_sample_rate = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.0"))

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release or None,
        traces_sample_rate=traces_sample_rate,
        before_send=before_send,
        send_default_pii=False,
    )
