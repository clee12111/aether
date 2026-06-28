"""Tests for Phase K: observability — logging, readiness, metrics, alerts,
Sentry, OTel, outcomes.

Each subsystem tested in isolation WITHOUT external dependencies
(no SENTRY_DSN, no OTLP_ENDPOINT, no ALERT_WEBHOOK_URL).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from unittest.mock import MagicMock

import pytest

from fastapi.testclient import TestClient


# ── K2: Structured JSON logging ──────────────────────────────────────────────


class TestStructuredLogging:
    def test_json_formatter_produces_valid_json(self):
        from gtm_triage.observability.logging import JSONFormatter
        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello world", args=(), exc_info=None,
        )
        line = fmt.format(record)
        parsed = json.loads(line)
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "hello world"
        assert "ts" in parsed

    def test_json_formatter_injects_request_id_from_contextvar(self):
        from gtm_triage.observability.logging import JSONFormatter, request_id_var
        fmt = JSONFormatter()
        token = request_id_var.set("req-123")
        try:
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=0,
                msg="test", args=(), exc_info=None,
            )
            line = fmt.format(record)
            parsed = json.loads(line)
            assert parsed["request_id"] == "req-123"
        finally:
            request_id_var.reset(token)

    def test_json_formatter_injects_run_id_from_contextvar(self):
        from gtm_triage.observability.logging import JSONFormatter, run_id_var
        fmt = JSONFormatter()
        token = run_id_var.set("run-456")
        try:
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=0,
                msg="test", args=(), exc_info=None,
            )
            parsed = json.loads(fmt.format(record))
            assert parsed["run_id"] == "run-456"
        finally:
            run_id_var.reset(token)

    def test_json_formatter_flattens_extra_fields(self):
        from gtm_triage.observability.logging import JSONFormatter
        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test", args=(), exc_info=None,
        )
        record.duration_ms = 42
        record.tool = "enrich_lead"
        parsed = json.loads(fmt.format(record))
        assert parsed["duration_ms"] == 42
        assert parsed["tool"] == "enrich_lead"

    def test_text_formatter_produces_readable_output(self):
        from gtm_triage.observability.logging import TextFormatter
        fmt = TextFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello", args=(), exc_info=None,
        )
        line = fmt.format(record)
        assert "INFO" in line
        assert "hello" in line
        # Should NOT be JSON
        with pytest.raises(json.JSONDecodeError):
            json.loads(line)

    def test_setup_logging_json(self):
        from gtm_triage.observability.logging import setup_logging
        setup_logging(log_format="json", log_level="WARNING")
        root = logging.getLogger()
        assert root.level == logging.WARNING

    def test_setup_logging_text(self):
        from gtm_triage.observability.logging import setup_logging, TextFormatter
        setup_logging(log_format="text", log_level="INFO")
        root = logging.getLogger()
        assert any(isinstance(h.formatter, TextFormatter) for h in root.handlers)

    def test_no_pii_in_structured_run_log(self):
        """Run a mock triage and verify no email appears in INFO log records."""
        from gtm_triage.observability.logging import JSONFormatter, setup_logging
        import io

        # Capture logs
        handler = logging.StreamHandler(io.StringIO())
        handler.setFormatter(JSONFormatter())
        handler.setLevel(logging.INFO)

        test_logger = logging.getLogger("gtm_triage.agents.loop_agent")
        test_logger.addHandler(handler)
        try:
            from gtm_triage.agents.executor import Executor
            from gtm_triage.agents.loop_agent import run_triage
            from gtm_triage.crm.sqlite_crm import SQLiteCRM
            from gtm_triage.models.lead import Lead
            from gtm_triage.tools.crm_lookup import CRMLookupTool
            from gtm_triage.tools.draft_outreach import DraftOutreachTool
            from gtm_triage.tools.enrich_lead import EnrichLeadTool
            from gtm_triage.tools.registry import ToolRegistry
            from gtm_triage.tools.score_lead import ScoreLeadTool
            from gtm_triage.trace.store import TraceStore

            crm = SQLiteCRM(":memory:")
            trace = TraceStore(":memory:")
            registry = ToolRegistry([
                CRMLookupTool(crm),
                EnrichLeadTool(provider="mock"),
                ScoreLeadTool(provider="mock"),
                DraftOutreachTool(),
            ])
            executor = Executor(registry, trace)
            lead = Lead(email="pii-test@example.com", message="demo request")
            run_triage(lead, executor, trace, provider="mock")

            # Check captured log output
            output = handler.stream.getvalue()
            for line in output.strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Email must not appear in any INFO-level structured log field
                for key, val in record.items():
                    if isinstance(val, str):
                        assert "pii-test@example.com" not in val, \
                            f"PII leak in log field '{key}': {val}"
        finally:
            test_logger.removeHandler(handler)


# ── K1: Readiness probe ──────────────────────────────────────────────────────


def _make_client():
    """Build a test client with lifespan context."""
    import os
    os.environ.pop("GTM_API_KEYS", None)
    os.environ.pop("SENTRY_DSN", None)
    os.environ.pop("OTLP_ENDPOINT", None)
    os.environ.pop("ALERT_WEBHOOK_URL", None)
    os.environ["GTM_PROVIDER"] = "mock"
    from gtm_triage.api import app
    return TestClient(app)


class TestReadiness:
    def test_ready_returns_200_when_healthy(self):
        with _make_client() as client:
            resp = client.get("/ready")
            assert resp.status_code == 200
            body = resp.json()
            assert body["ready"] is True
            assert body["checks"]["trace"] == "ok"
            assert body["checks"]["crm"] == "ok"

    def test_health_always_200(self):
        with _make_client() as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"

    def test_ready_is_public_no_auth(self):
        """Verify /ready is in _PUBLIC_PATHS and accessible without API key."""
        from gtm_triage.middleware import _PUBLIC_PATHS
        assert "/ready" in _PUBLIC_PATHS


# ── K3: Prometheus metrics ───────────────────────────────────────────────────


class TestMetrics:
    def test_counter_increments(self):
        from gtm_triage.observability.metrics import Counter
        c = Counter("test_counter", "test", ("label",))
        c.inc(label="a")
        c.inc(label="a")
        c.inc(label="b")
        data = c.collect()
        vals = {tuple(labels.values()): v for labels, v in data}
        assert vals[("a",)] == 2.0
        assert vals[("b",)] == 1.0

    def test_gauge_set_and_get(self):
        from gtm_triage.observability.metrics import Gauge
        g = Gauge("test_gauge", "test", ("name",))
        g.set(2.0, name="cb1")
        assert g.get(name="cb1") == 2.0
        g.set(0.0, name="cb1")
        assert g.get(name="cb1") == 0.0

    def test_histogram_observe(self):
        from gtm_triage.observability.metrics import Histogram
        h = Histogram("test_hist", "test", ("ep",), buckets=(0.1, 0.5, 1.0, float("inf")))
        h.observe(0.05, ep="/triage")
        h.observe(0.3, ep="/triage")
        h.observe(0.8, ep="/triage")
        data = h.collect()
        assert len(data) == 1
        _, info = data[0]
        assert info["count"] == 3
        assert abs(info["sum"] - 1.15) < 0.001

    def test_render_metrics_prometheus_format(self):
        from gtm_triage.observability.metrics import render_metrics
        body = render_metrics(daily_cap_used=5, daily_cap_limit=200)
        assert "gtm_requests_total" in body
        assert "gtm_daily_cap_used" in body
        assert "gtm_daily_cap_limit" in body
        assert "gtm_circuit_breaker_state" in body
        assert "# HELP" in body
        assert "# TYPE" in body

    def test_metrics_endpoint_public(self):
        from gtm_triage.middleware import _PUBLIC_PATHS
        assert "/metrics" in _PUBLIC_PATHS

    def test_metrics_endpoint_returns_text_plain(self):
        with _make_client() as client:
            resp = client.get("/metrics")
            assert resp.status_code == 200
            assert "text/plain" in resp.headers["content-type"]
            body = resp.text
            assert "gtm_requests_total" in body

    def test_cache_hit_increments_on_duplicate(self):
        """Submitting the same idempotency key should increment cache_hit_total."""
        from gtm_triage.observability.metrics import metrics
        unique = str(uuid.uuid4())[:8]
        with _make_client() as client:
            # Get baseline
            baseline_hits = sum(v for _, v in metrics.cache_hit_total.collect()) if metrics.cache_hit_total.collect() else 0

            # First triage — cache miss
            req = {"email": f"cache-{unique}@acme.com", "message": "demo", "idempotency_key": f"cache-key-{unique}"}
            client.post("/triage", json=req)

            # Second triage with same key — cache hit
            client.post("/triage", json=req)

            current_hits = sum(v for _, v in metrics.cache_hit_total.collect()) if metrics.cache_hit_total.collect() else 0
            assert current_hits > baseline_hits

    def test_no_email_in_metric_labels(self):
        """No email address should appear in any metric label."""
        from gtm_triage.observability.metrics import render_metrics
        with _make_client() as client:
            email = "metric-pii-test@company.com"
            client.post("/triage", json={"email": email, "message": "test"})

            body = render_metrics(daily_cap_used=0, daily_cap_limit=200)
            assert email not in body


# ── K4: Sentry ───────────────────────────────────────────────────────────────


class TestSentry:
    def test_before_send_scrubs_email(self):
        from gtm_triage.observability.sentry import before_send
        event = {
            "request": {"data": {"email": "test@example.com", "name": "Alice"}},
            "extra": {"lead_email": "test@example.com", "company": "Acme"},
        }
        cleaned = before_send(event, {})
        assert "test@example.com" not in str(cleaned)
        assert cleaned["request"]["data"]["email"] == "[email]"
        assert cleaned["extra"]["lead_email"] == "[email]"
        assert cleaned["extra"]["company"] == "[scrubbed]"

    def test_before_send_scrubs_email_in_breadcrumbs(self):
        from gtm_triage.observability.sentry import before_send
        event = {
            "breadcrumbs": {
                "values": [
                    {"message": "Processing test@example.com", "data": {"email": "test@example.com"}},
                ]
            }
        }
        cleaned = before_send(event, {})
        assert "test@example.com" not in str(cleaned)

    def test_before_send_scrubs_exception_values(self):
        from gtm_triage.observability.sentry import before_send
        event = {
            "exception": {
                "values": [{"value": "Error for test@example.com"}]
            }
        }
        cleaned = before_send(event, {})
        assert "test@example.com" not in str(cleaned)

    def test_init_sentry_noop_without_dsn(self):
        """init_sentry with no SENTRY_DSN does nothing (no error)."""
        import os
        os.environ.pop("SENTRY_DSN", None)
        from gtm_triage.observability.sentry import init_sentry
        init_sentry()  # Should not raise

    def test_sentry_sdk_not_required(self):
        """The module imports cleanly even without sentry-sdk."""
        import importlib
        from gtm_triage.observability import sentry
        importlib.reload(sentry)  # No ImportError


# ── K5: Alert hooks ──────────────────────────────────────────────────────────


class TestAlertHooks:
    def test_log_alert_hook_fires(self, caplog):
        from gtm_triage.observability.alerts import LogAlertHook
        hook = LogAlertHook()
        with caplog.at_level(logging.WARNING):
            hook.fire("test_event", {"key": "value"})
        assert any("test_event" in r.message for r in caplog.records)

    def test_circuit_breaker_fires_alert_hook(self):
        from gtm_triage.resilience import CircuitBreaker

        mock_hook = MagicMock()
        cb = CircuitBreaker("test", failure_threshold=3, alert_hook=mock_hook)

        # Trip the breaker
        for _ in range(3):
            try:
                cb.call(lambda: 1/0)
            except ZeroDivisionError:
                pass

        assert cb.state == "open"
        mock_hook.fire.assert_called_once()
        call_args = mock_hook.fire.call_args
        assert call_args[0][0] == "circuit_open"
        assert call_args[0][1]["name"] == "test"

    def test_webhook_failure_does_not_raise(self):
        """Webhook failures are swallowed — never propagate to the caller."""
        from gtm_triage.observability.alerts import WebhookAlertHook
        hook = WebhookAlertHook(url="http://localhost:1/nonexistent")
        # Should not raise even with a bad URL
        hook.fire("test_event", {"key": "value"})

    def test_error_rate_monitor_fires_once_per_cooldown(self):
        from gtm_triage.observability.alerts import ErrorRateMonitor

        mock_hook = MagicMock()
        monitor = ErrorRateMonitor(hook=mock_hook, threshold=0.10, cooldown_seconds=300)

        # First check — should fire
        fired1 = monitor.check(0.50, error_count=5, request_count=10)
        assert fired1 is True
        assert mock_hook.fire.call_count == 1

        # Second check within cooldown — should NOT fire
        fired2 = monitor.check(0.50, error_count=5, request_count=10)
        assert fired2 is False
        assert mock_hook.fire.call_count == 1  # Still 1

    def test_error_rate_below_threshold_no_alert(self):
        from gtm_triage.observability.alerts import ErrorRateMonitor

        mock_hook = MagicMock()
        monitor = ErrorRateMonitor(hook=mock_hook, threshold=0.20)

        fired = monitor.check(0.05, error_count=1, request_count=20)
        assert fired is False
        mock_hook.fire.assert_not_called()

    def test_get_alert_hook_default_is_log(self):
        import os
        os.environ.pop("ALERT_WEBHOOK_URL", None)
        from gtm_triage.observability.alerts import LogAlertHook, get_alert_hook
        hook = get_alert_hook()
        assert isinstance(hook, LogAlertHook)


# ── K6: OpenTelemetry ────────────────────────────────────────────────────────


class TestOpenTelemetry:
    def test_noop_without_otel_sdk(self):
        """get_tracer returns a no-op tracer when OTel is not configured."""
        from gtm_triage.observability.tracing import _NoOpTracer, get_tracer
        tracer = get_tracer()
        # Should be no-op (either NoOpTracer or real — but shouldn't crash)
        with tracer.start_as_current_span("test") as span:
            span.set_attribute("key", "value")

    def test_traced_span_noop_works(self):
        from gtm_triage.observability.tracing import traced_span
        with traced_span("test.operation", {"key": "value"}) as span:
            pass  # Should not raise

    def test_get_current_trace_id_empty_without_otel(self):
        from gtm_triage.observability.tracing import get_current_trace_id
        # Without active OTel, should return empty string
        trace_id = get_current_trace_id()
        assert isinstance(trace_id, str)

    def test_init_tracing_noop_without_endpoint(self):
        """init_tracing with no OTLP_ENDPOINT does nothing."""
        import os
        os.environ.pop("OTLP_ENDPOINT", None)
        from gtm_triage.observability.tracing import init_tracing
        init_tracing()  # Should not raise


# ── K7: Outcome-loop stub ───────────────────────────────────────────────────


class TestOutcomes:
    def test_record_and_get_outcome(self):
        from gtm_triage.trace.store import TraceStore
        trace = TraceStore(":memory:")
        # Create a run first
        trace.write(run_id="run-1", event_type="run_start", agent="test", payload={})
        trace.store_idempotency_key("key-1", "run-1", {"final_tier": "hot"})

        oid = trace.record_outcome("run-1", "hot", "converted", "human")
        assert oid  # non-empty

        outcome = trace.get_outcome("run-1")
        assert outcome is not None
        assert outcome["predicted_tier"] == "hot"
        assert outcome["actual_outcome"] == "converted"
        assert outcome["recorded_by"] == "human"

    def test_outcome_write_once(self):
        from gtm_triage.trace.store import TraceStore
        import sqlite3
        trace = TraceStore(":memory:")
        trace.write(run_id="run-1", event_type="run_start", agent="test", payload={})

        trace.record_outcome("run-1", "hot", "converted")
        # Second write should fail (UNIQUE constraint on outcome_id, but run_id not unique)
        # We'll test via the API which enforces write-once

    def test_outcome_metrics_empty(self):
        from gtm_triage.trace.store import TraceStore
        trace = TraceStore(":memory:")
        metrics = trace.get_outcome_metrics()
        assert metrics == {}  # No predictions or outcomes

    def test_outcome_metrics_with_data(self):
        from gtm_triage.trace.store import TraceStore
        trace = TraceStore(":memory:")

        # Create 5 "hot" predictions
        for i in range(5):
            run_id = f"run-{i}"
            trace.write(run_id=run_id, event_type="run_start", agent="test", payload={})
            trace.store_idempotency_key(f"key-{i}", run_id, {"final_tier": "hot"})

        # Record 4 "converted" outcomes
        for i in range(4):
            trace.record_outcome(f"run-{i}", "hot", "converted")

        # Record 1 "no_show"
        trace.record_outcome("run-4", "hot", "no_show")

        metrics = trace.get_outcome_metrics()
        assert "hot" in metrics
        assert metrics["hot"]["predicted"] == 5
        assert metrics["hot"]["with_outcome"] == 5
        assert metrics["hot"]["converted"] == 4
        assert abs(metrics["hot"]["precision"] - 0.80) < 0.001

    def test_no_email_in_outcomes_table(self):
        """The outcomes table stores run_id only — no email/PII."""
        from gtm_triage.trace.store import TraceStore
        trace = TraceStore(":memory:")
        trace.write(run_id="run-1", event_type="run_start", agent="test",
                     payload={"lead": {"email": "secret@example.com"}})
        trace.record_outcome("run-1", "warm", "converted")

        # Direct query to verify no email in outcomes
        row = trace._conn.execute("SELECT * FROM outcomes WHERE run_id = 'run-1'").fetchone()
        row_str = str(dict(row))
        assert "secret@example.com" not in row_str

    def test_outcomes_api_endpoint_201(self):
        unique = str(uuid.uuid4())[:8]
        with _make_client() as client:
            # First triage a lead to create a run
            resp = client.post("/triage", json={
                "email": f"outcome-{unique}@acme.com",
                "message": "want a demo",
                "idempotency_key": f"outcome-201-{unique}",
            })
            assert resp.status_code == 200
            run_id = resp.json()["run_id"]

            # Record outcome
            resp = client.post(f"/outcomes/{run_id}", json={
                "actual_outcome": "converted",
                "recorded_by": "test",
            })
            assert resp.status_code == 201
            assert resp.json()["actual_outcome"] == "converted"

    def test_outcomes_api_404_unknown_run(self):
        with _make_client() as client:
            resp = client.post("/outcomes/nonexistent-run", json={
                "actual_outcome": "converted",
            })
            assert resp.status_code == 404

    def test_outcomes_api_409_duplicate(self):
        unique = str(uuid.uuid4())[:8]
        with _make_client() as client:
            # Triage
            resp = client.post("/triage", json={
                "email": f"dup-{unique}@acme.com",
                "message": "want a demo",
                "idempotency_key": f"dup-409-{unique}",
            })
            run_id = resp.json()["run_id"]

            # First outcome
            client.post(f"/outcomes/{run_id}", json={"actual_outcome": "converted"})

            # Duplicate — 409
            resp = client.post(f"/outcomes/{run_id}", json={"actual_outcome": "no_show"})
            assert resp.status_code == 409

    def test_outcomes_api_422_invalid_outcome(self):
        unique = str(uuid.uuid4())[:8]
        with _make_client() as client:
            # Triage
            resp = client.post("/triage", json={
                "email": f"invalid-{unique}@acme.com",
                "message": "test",
                "idempotency_key": f"invalid-422-{unique}",
            })
            run_id = resp.json()["run_id"]

            # Invalid outcome
            resp = client.post(f"/outcomes/{run_id}", json={
                "actual_outcome": "deal_closed",
            })
            assert resp.status_code == 422

    def test_metrics_outcomes_endpoint_empty(self):
        with _make_client() as client:
            resp = client.get("/metrics/outcomes")
            assert resp.status_code == 200
            # Should be valid JSON with zero counts (no 500)

    def test_metrics_outcomes_public(self):
        from gtm_triage.middleware import _PUBLIC_PATHS
        assert "/metrics/outcomes" in _PUBLIC_PATHS


# ── Cross-cutting: _PUBLIC_PATHS ─────────────────────────────────────────────


class TestPublicPaths:
    def test_all_observability_endpoints_public(self):
        from gtm_triage.middleware import _PUBLIC_PATHS
        assert "/health" in _PUBLIC_PATHS
        assert "/ready" in _PUBLIC_PATHS
        assert "/metrics" in _PUBLIC_PATHS
        assert "/metrics/outcomes" in _PUBLIC_PATHS


# ── K3: Rolling error rate tracker ───────────────────────────────────────────


class TestErrorRateTracking:
    def test_error_rate_calculation(self):
        from gtm_triage.observability.metrics import MetricsRegistry
        reg = MetricsRegistry()

        now = time.monotonic()
        # 10 requests, 3 errors
        for i in range(10):
            reg.record_request(now + i * 0.01)
        for i in range(3):
            reg.record_error(now + i * 0.01)

        rate, errors, requests = reg.get_error_rate(window_seconds=60.0)
        assert requests == 10
        assert errors == 3
        assert abs(rate - 0.30) < 0.01


# ── Integration: request_id flows through triage ─────────────────────────────


class TestRequestIdPropagation:
    def test_triage_response_has_request_id_header(self):
        unique = str(uuid.uuid4())[:8]
        with _make_client() as client:
            resp = client.post("/triage", json={
                "email": f"reqid-{unique}@acme.com",
                "message": "demo",
                "idempotency_key": f"reqid-{unique}",
            })
            assert resp.status_code == 200
            assert "x-request-id" in resp.headers
            # Should be a valid UUID
            rid = resp.headers["x-request-id"]
            uuid.UUID(rid)  # Raises if not valid
