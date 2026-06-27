"""API hardening middleware: auth, rate limiting, error handling.

All middleware is configured via environment variables, never hardcoded.
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# ── Structured error response ────────────────────────────────────────────────

def error_response(status_code: int, error_type: str, message: str) -> JSONResponse:
    """Return a clean, typed error response with no internal details."""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"type": error_type, "message": message}},
    )


# ── Auth middleware ──────────────────────────────────────────────────────────

# Paths that don't require auth
_PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


class AuthMiddleware(BaseHTTPMiddleware):
    """Bearer-token / API-key auth on all endpoints except /health.

    Reads GTM_API_KEYS from env (comma-separated). If not set, auth is
    disabled (development mode). Accepts both:
      Authorization: Bearer <key>
      X-API-Key: <key>
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        allowed_keys = os.environ.get("GTM_API_KEYS", "")
        if not allowed_keys:
            # Auth disabled — dev mode
            return await call_next(request)

        keys = {k.strip() for k in allowed_keys.split(",") if k.strip()}
        path = request.url.path

        # Public paths skip auth
        if path in _PUBLIC_PATHS:
            return await call_next(request)

        # Extract key from header
        auth_header = request.headers.get("authorization", "")
        api_key_header = request.headers.get("x-api-key", "")

        provided_key = ""
        if auth_header.startswith("Bearer "):
            provided_key = auth_header[7:].strip()
        elif api_key_header:
            provided_key = api_key_header.strip()

        if not provided_key or provided_key not in keys:
            return error_response(401, "unauthorized", "Invalid or missing API key")

        # Stash key identity for rate limiting
        request.state.api_key = provided_key
        return await call_next(request)


# ── Rate limiting middleware ─────────────────────────────────────────────────

class _TokenBucket:
    """Simple in-memory token bucket for rate limiting."""

    def __init__(self, rate: float, capacity: int) -> None:
        self._rate = rate          # tokens per second
        self._capacity = capacity
        self._buckets: dict[str, tuple[float, float]] = {}  # key → (tokens, last_refill)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        tokens, last = self._buckets.get(key, (float(self._capacity), now))

        # Refill
        elapsed = now - last
        tokens = min(self._capacity, tokens + elapsed * self._rate)

        if tokens >= 1.0:
            self._buckets[key] = (tokens - 1.0, now)
            return True
        else:
            self._buckets[key] = (tokens, now)
            return False


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-key and per-IP rate limiting. Returns 429 on exceed.

    Config via env:
      GTM_RATE_LIMIT_RPM  — requests per minute per key (default 60)
    """

    def __init__(self, app, rate_per_minute: int | None = None) -> None:
        super().__init__(app)
        rpm = rate_per_minute or int(os.environ.get("GTM_RATE_LIMIT_RPM", "60"))
        self._bucket = _TokenBucket(rate=rpm / 60.0, capacity=rpm)

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip rate limiting on public paths
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        # Use API key if available, otherwise IP
        key = getattr(request.state, "api_key", None) or (request.client.host if request.client else "unknown")

        if not self._bucket.allow(key):
            return error_response(429, "rate_limited", "Too many requests. Try again later.")

        return await call_next(request)


# ── Request size limiting middleware ─────────────────────────────────────────

_MAX_BODY_BYTES = 64 * 1024  # 64 KB


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized request bodies with a clean 413."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in ("POST", "PUT", "PATCH"):
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > _MAX_BODY_BYTES:
                return error_response(
                    413, "payload_too_large",
                    f"Request body exceeds {_MAX_BODY_BYTES} bytes",
                )
        return await call_next(request)


# ── Exception handler (no stack leaks) ───────────────────────────────────────

async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch unhandled exceptions and return a clean 500. Log the real error."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return error_response(500, "internal_error", "An internal error occurred")
