"""Resilience primitives: retry with backoff, circuit breaker.

No external dependencies — pure stdlib. Used by all outbound calls
(PDL, HubSpot, OpenAI, website fetch).
"""

from __future__ import annotations

import logging
import random
import time
from functools import wraps
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ── Retry with exponential backoff + jitter ──────────────────────────────────

_TRANSIENT_EXCEPTIONS = (
    ConnectionError, TimeoutError, OSError,
)


def retry_with_backoff(
    fn: Callable[..., T],
    *args,
    max_retries: int = 2,
    base_delay: float = 0.5,
    max_delay: float = 5.0,
    transient: tuple = _TRANSIENT_EXCEPTIONS,
    **kwargs,
) -> T:
    """Call fn with retries on transient failures.

    Uses exponential backoff with jitter. Non-transient exceptions
    propagate immediately.
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except transient as exc:
            last_exc = exc
            if attempt == max_retries:
                break
            delay = min(base_delay * (2 ** attempt), max_delay)
            jitter = random.uniform(0, delay * 0.3)
            logger.warning(
                "Retry %d/%d for %s after %s (delay %.2fs)",
                attempt + 1, max_retries, fn.__name__ if hasattr(fn, '__name__') else fn,
                exc, delay + jitter,
            )
            time.sleep(delay + jitter)
    raise last_exc  # type: ignore[misc]


# ── Circuit breaker ──────────────────────────────────────────────────────────

class CircuitBreaker:
    """Simple circuit breaker. Trips after N consecutive failures, resets
    after a cooldown period.

    States:
      CLOSED  — requests flow through normally
      OPEN    — requests fail fast (raise CircuitOpenError)
      HALF    — one test request allowed; success → CLOSED, failure → OPEN
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self.name = name
        self._failure_threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._consecutive_failures = 0
        self._last_failure_time = 0.0
        self._state = "closed"

    @property
    def state(self) -> str:
        if self._state == "open":
            if time.monotonic() - self._last_failure_time >= self._cooldown:
                self._state = "half_open"
        return self._state

    def call(self, fn: Callable[..., T], *args, **kwargs) -> T:
        """Execute fn through the circuit breaker."""
        current_state = self.state

        if current_state == "open":
            raise CircuitOpenError(
                f"Circuit '{self.name}' is open — dependency down, failing fast"
            )

        try:
            result = fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure()
            raise

    def _on_success(self) -> None:
        self._consecutive_failures = 0
        if self._state in ("half_open",):
            logger.info("Circuit '%s' closed after successful test request", self.name)
        self._state = "closed"

    def _on_failure(self) -> None:
        self._consecutive_failures += 1
        self._last_failure_time = time.monotonic()
        if self._consecutive_failures >= self._failure_threshold:
            self._state = "open"
            logger.warning(
                "Circuit '%s' OPEN after %d consecutive failures",
                self.name, self._consecutive_failures,
            )

    def reset(self) -> None:
        """Manually reset the breaker (for testing)."""
        self._consecutive_failures = 0
        self._state = "closed"


class CircuitOpenError(Exception):
    """Raised when a circuit breaker is open."""
    pass
