"""Alerting hooks: injectable protocol for circuit-breaker-open and error-rate-spike.

Default: LogAlertHook (structured WARNING log).
Optional: WebhookAlertHook (HTTP POST, fire-and-forget in background thread).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class AlertHook(Protocol):
    """Injectable alert hook protocol."""

    def fire(self, event: str, payload: dict[str, Any]) -> None: ...


class LogAlertHook:
    """Default alert hook — emits a structured WARNING log."""

    def fire(self, event: str, payload: dict[str, Any]) -> None:
        logger.warning("ALERT: %s", event, extra={"alert_event": event, **payload})


class WebhookAlertHook:
    """Fire-and-forget HTTP POST to ALERT_WEBHOOK_URL.

    Never blocks the request thread. Webhook failures are logged, not raised.
    """

    def __init__(self, url: str | None = None) -> None:
        self._url = url or os.environ.get("ALERT_WEBHOOK_URL", "")
        self._log_hook = LogAlertHook()

    def fire(self, event: str, payload: dict[str, Any]) -> None:
        # Always log
        self._log_hook.fire(event, payload)
        if not self._url:
            return
        # Fire-and-forget in background thread
        thread = threading.Thread(
            target=self._send, args=(event, payload), daemon=True,
        )
        thread.start()

    def _send(self, event: str, payload: dict[str, Any]) -> None:
        try:
            import httpx
            body = {"event": event, "payload": payload}
            httpx.post(self._url, json=body, timeout=5.0)
        except Exception as exc:
            logger.warning(
                "Webhook alert failed: %s", exc,
                extra={"alert_event": event, "webhook_error": str(exc)},
            )


class ErrorRateMonitor:
    """Fires an alert when the rolling error rate exceeds a threshold.

    Cooldown prevents alert storms — fires at most once per cooldown period.
    """

    def __init__(
        self,
        hook: AlertHook | None = None,
        threshold: float | None = None,
        cooldown_seconds: float | None = None,
        window_seconds: float = 60.0,
    ) -> None:
        self._hook = hook or LogAlertHook()
        self._threshold = threshold or float(
            os.environ.get("ALERT_ERROR_RATE_THRESHOLD", "0.20")
        )
        self._cooldown = cooldown_seconds or float(
            os.environ.get("ALERT_COOLDOWN_SECONDS", "300")
        )
        self._window = window_seconds
        self._last_alert_time = -(self._cooldown + 1)  # Ensure first check can fire

    def check(self, error_rate: float, error_count: int, request_count: int) -> bool:
        """Check error rate and fire alert if above threshold. Returns True if alert fired."""
        if error_rate < self._threshold:
            return False
        now = time.monotonic()
        if now - self._last_alert_time < self._cooldown:
            return False
        self._last_alert_time = now
        self._hook.fire("error_rate_spike", {
            "rate": round(error_rate, 4),
            "window_seconds": self._window,
            "error_count": error_count,
            "request_count": request_count,
            "ts": time.time(),
        })
        return True


def get_alert_hook() -> AlertHook:
    """Return the appropriate alert hook based on env config."""
    webhook_url = os.environ.get("ALERT_WEBHOOK_URL", "")
    if webhook_url:
        return WebhookAlertHook(webhook_url)
    return LogAlertHook()
