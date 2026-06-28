"""In-process metrics with Prometheus text-format exposition.

All counters/gauges/histograms are thread-safe (threading.Lock).
No SQL queries on scrape — metrics are incremented in-line during request
processing. The one exception: daily_cap_used reads one cheap indexed row.

No cardinality bombs: labels are bounded enums, never user-supplied strings.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any


# ── Metric primitives ──────────────────────────────────────────────────────

class Counter:
    """Monotonically increasing counter with optional labels."""

    def __init__(self, name: str, help_text: str, label_names: tuple[str, ...] = ()) -> None:
        self.name = name
        self.help_text = help_text
        self.label_names = label_names
        self._values: dict[tuple[str, ...], float] = {}
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = tuple(labels.get(n, "") for n in self.label_names)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def collect(self) -> list[tuple[dict[str, str], float]]:
        with self._lock:
            return [
                (dict(zip(self.label_names, k)), v)
                for k, v in sorted(self._values.items())
            ]


class Gauge:
    """A value that can go up and down."""

    def __init__(self, name: str, help_text: str, label_names: tuple[str, ...] = ()) -> None:
        self.name = name
        self.help_text = help_text
        self.label_names = label_names
        self._values: dict[tuple[str, ...], float] = {}
        self._lock = threading.Lock()

    def set(self, value: float, **labels: str) -> None:
        key = tuple(labels.get(n, "") for n in self.label_names)
        with self._lock:
            self._values[key] = value

    def get(self, **labels: str) -> float:
        key = tuple(labels.get(n, "") for n in self.label_names)
        with self._lock:
            return self._values.get(key, 0.0)

    def collect(self) -> list[tuple[dict[str, str], float]]:
        with self._lock:
            return [
                (dict(zip(self.label_names, k)), v)
                for k, v in sorted(self._values.items())
            ]


class Histogram:
    """Histogram with configurable buckets."""

    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, float("inf"))

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: tuple[str, ...] = (),
        buckets: tuple[float, ...] | None = None,
    ) -> None:
        self.name = name
        self.help_text = help_text
        self.label_names = label_names
        self.buckets = buckets or self.DEFAULT_BUCKETS
        self._data: dict[tuple[str, ...], dict[str, Any]] = {}
        self._lock = threading.Lock()

    def observe(self, value: float, **labels: str) -> None:
        key = tuple(labels.get(n, "") for n in self.label_names)
        with self._lock:
            if key not in self._data:
                self._data[key] = {
                    "buckets": {b: 0 for b in self.buckets},
                    "sum": 0.0,
                    "count": 0,
                }
            d = self._data[key]
            d["sum"] += value
            d["count"] += 1
            for b in self.buckets:
                if value <= b:
                    d["buckets"][b] += 1

    def collect(self) -> list[tuple[dict[str, str], dict[str, Any]]]:
        with self._lock:
            return [
                (dict(zip(self.label_names, k)), dict(v))
                for k, v in sorted(self._data.items())
            ]


# ── Metric registry (singleton) ───────────────────────────────────────────

class MetricsRegistry:
    """Central registry of all application metrics."""

    def __init__(self) -> None:
        # K3 required counters
        self.requests_total = Counter(
            "gtm_requests_total", "Total HTTP requests",
            ("endpoint", "method", "status_code"),
        )
        self.request_errors_total = Counter(
            "gtm_request_errors_total", "HTTP error responses",
            ("endpoint", "error_type"),
        )
        self.triage_total = Counter(
            "gtm_triage_total", "Completed triage runs",
            ("tier", "route", "provider"),
        )

        # K3 required gauges
        self.circuit_breaker_state = Gauge(
            "gtm_circuit_breaker_state", "Circuit breaker state (0=closed, 1=half_open, 2=open)",
            ("name",),
        )
        self.daily_cap_used = Gauge(
            "gtm_daily_cap_used", "Daily API usage count",
        )
        self.daily_cap_limit = Gauge(
            "gtm_daily_cap_limit", "Daily API usage cap",
        )
        self.cache_hit_total = Counter(
            "gtm_cache_hit_total", "Idempotency cache hits",
        )
        self.cache_miss_total = Counter(
            "gtm_cache_miss_total", "Idempotency cache misses",
        )

        # K3 required histograms
        self.request_duration_seconds = Histogram(
            "gtm_request_duration_seconds", "HTTP request latency",
            ("endpoint",),
        )
        self.triage_duration_seconds = Histogram(
            "gtm_triage_duration_seconds", "End-to-end triage latency",
            ("provider",),
        )

        # Rolling error tracker for K5 alerting
        self._error_window: list[float] = []
        self._request_window: list[float] = []
        self._window_lock = threading.Lock()

    def record_request(self, timestamp: float | None = None) -> None:
        ts = timestamp or time.monotonic()
        with self._window_lock:
            self._request_window.append(ts)

    def record_error(self, timestamp: float | None = None) -> None:
        ts = timestamp or time.monotonic()
        with self._window_lock:
            self._error_window.append(ts)

    def get_error_rate(self, window_seconds: float = 60.0) -> tuple[float, int, int]:
        """Return (error_rate, error_count, request_count) for the rolling window."""
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._window_lock:
            self._error_window = [t for t in self._error_window if t >= cutoff]
            self._request_window = [t for t in self._request_window if t >= cutoff]
            errors = len(self._error_window)
            requests = len(self._request_window)
        rate = errors / requests if requests > 0 else 0.0
        return rate, errors, requests


# Global singleton
metrics = MetricsRegistry()


# ── Prometheus text-format exposition ──────────────────────────────────────

def _format_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    parts = [f'{k}="{v}"' for k, v in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


def _render_counter(c: Counter) -> str:
    lines = [f"# HELP {c.name} {c.help_text}", f"# TYPE {c.name} counter"]
    data = c.collect()
    if not data:
        lines.append(f"{c.name} 0")
    for labels, value in data:
        lines.append(f"{c.name}{_format_labels(labels)} {value}")
    return "\n".join(lines)


def _render_gauge(g: Gauge) -> str:
    lines = [f"# HELP {g.name} {g.help_text}", f"# TYPE {g.name} gauge"]
    data = g.collect()
    if not data:
        lines.append(f"{g.name} 0")
    for labels, value in data:
        lines.append(f"{g.name}{_format_labels(labels)} {value}")
    return "\n".join(lines)


def _render_histogram(h: Histogram) -> str:
    lines = [f"# HELP {h.name} {h.help_text}", f"# TYPE {h.name} histogram"]
    data = h.collect()
    for labels, info in data:
        cumulative = 0
        for bucket_bound in sorted(info["buckets"].keys()):
            if math.isinf(bucket_bound):
                le = "+Inf"
            else:
                le = str(bucket_bound)
            cumulative += info["buckets"][bucket_bound]
            bucket_labels = dict(labels)
            bucket_labels["le"] = le
            lines.append(f"{h.name}_bucket{_format_labels(bucket_labels)} {cumulative}")
        lines.append(f"{h.name}_sum{_format_labels(labels)} {info['sum']}")
        lines.append(f"{h.name}_count{_format_labels(labels)} {info['count']}")
    return "\n".join(lines)


def render_metrics(daily_cap_used: int = 0, daily_cap_limit: int = 0) -> str:
    """Render all metrics in Prometheus text exposition format.

    daily_cap_used/daily_cap_limit are passed in from the caller (one cheap
    SQLite read) rather than querying inside the metrics module.
    """
    metrics.daily_cap_used.set(float(daily_cap_used))
    metrics.daily_cap_limit.set(float(daily_cap_limit))

    sections = [
        _render_counter(metrics.requests_total),
        _render_counter(metrics.request_errors_total),
        _render_counter(metrics.triage_total),
        _render_gauge(metrics.circuit_breaker_state),
        _render_gauge(metrics.daily_cap_used),
        _render_gauge(metrics.daily_cap_limit),
        _render_counter(metrics.cache_hit_total),
        _render_counter(metrics.cache_miss_total),
        _render_histogram(metrics.request_duration_seconds),
        _render_histogram(metrics.triage_duration_seconds),
    ]
    return "\n\n".join(sections) + "\n"
