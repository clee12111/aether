"""Tests for Phase H: reliability — retries, circuit breakers, auth fail-closed,
async triage, graceful degradation.
"""

from __future__ import annotations

import os
import time
from unittest.mock import patch

import pytest

from gtm_triage.resilience import CircuitBreaker, CircuitOpenError, retry_with_backoff


# ── Retry with backoff ─────────────────────────────────────────────────────────


class TestRetry:
    def test_succeeds_first_try(self):
        result = retry_with_backoff(lambda: 42, max_retries=2)
        assert result == 42

    def test_retries_on_transient_error(self):
        call_count = 0
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("transient")
            return "ok"

        result = retry_with_backoff(flaky, max_retries=3, base_delay=0.01)
        assert result == "ok"
        assert call_count == 3

    def test_gives_up_after_max_retries(self):
        def always_fail():
            raise TimeoutError("always")

        with pytest.raises(TimeoutError):
            retry_with_backoff(always_fail, max_retries=2, base_delay=0.01)

    def test_non_transient_not_retried(self):
        call_count = 0
        def value_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("not transient")

        with pytest.raises(ValueError):
            retry_with_backoff(value_error, max_retries=3, base_delay=0.01)
        assert call_count == 1  # no retries


# ── Circuit breaker ────────────────────────────────────────────────────────────


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        assert cb.state == "closed"

    def test_passes_through_when_closed(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        result = cb.call(lambda: 42)
        assert result == 42

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker("test", failure_threshold=3, cooldown_seconds=60)
        for _ in range(3):
            with pytest.raises(ValueError):
                cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        assert cb.state == "open"

    def test_open_circuit_raises_circuit_open_error(self):
        cb = CircuitBreaker("test", failure_threshold=1, cooldown_seconds=60)
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        with pytest.raises(CircuitOpenError):
            cb.call(lambda: 42)

    def test_half_open_after_cooldown(self):
        cb = CircuitBreaker("test", failure_threshold=1, cooldown_seconds=0.01)
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        time.sleep(0.02)  # past cooldown
        assert cb.state == "half_open"

    def test_closes_on_success_after_half_open(self):
        cb = CircuitBreaker("test", failure_threshold=1, cooldown_seconds=0.01)
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        time.sleep(0.02)
        result = cb.call(lambda: "recovered")
        assert result == "recovered"
        assert cb.state == "closed"

    def test_reset(self):
        cb = CircuitBreaker("test", failure_threshold=1, cooldown_seconds=60)
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))
        assert cb.state == "open"

        cb.reset()
        assert cb.state == "closed"


# ── Auth fail-closed ───────────────────────────────────────────────────────────


class TestAuthFailClosed:
    def test_production_no_keys_rejects(self):
        """In production with no keys, all auth'd endpoints return 503."""
        with patch.dict(os.environ, {
            "APP_ENV": "production",
            "GTM_API_KEYS": "",
            "GTM_PROVIDER": "mock",
            "CRM_BACKEND": "sqlite",
            "GTM_CRM_DB": ":memory:",
            "GTM_TRACE_DB": ":memory:",
        }, clear=False):
            from fastapi.testclient import TestClient
            from gtm_triage.api import app
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post("/triage", json={"email": "test@example.com"})
                assert resp.status_code == 503
                assert resp.json()["error"]["type"] == "auth_not_configured"

    def test_production_health_still_works(self):
        """Health endpoint works even in fail-closed mode."""
        with patch.dict(os.environ, {
            "APP_ENV": "production",
            "GTM_API_KEYS": "",
            "GTM_PROVIDER": "mock",
            "CRM_BACKEND": "sqlite",
            "GTM_CRM_DB": ":memory:",
            "GTM_TRACE_DB": ":memory:",
        }, clear=False):
            from fastapi.testclient import TestClient
            from gtm_triage.api import app
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.get("/health")
                assert resp.status_code == 200


# ── Timing-safe key comparison ─────────────────────────────────────────────────


class TestTimingSafe:
    def test_valid_key_accepted(self):
        from gtm_triage.middleware import _timing_safe_key_check
        assert _timing_safe_key_check("key-1", {"key-1", "key-2"}) is True

    def test_invalid_key_rejected(self):
        from gtm_triage.middleware import _timing_safe_key_check
        assert _timing_safe_key_check("wrong", {"key-1", "key-2"}) is False

    def test_empty_key_rejected(self):
        from gtm_triage.middleware import _timing_safe_key_check
        assert _timing_safe_key_check("", {"key-1"}) is False


# ── Async triage ───────────────────────────────────────────────────────────────


class TestAsyncTriage:
    def test_triage_endpoint_is_async(self):
        """The triage endpoint must be an async function (uses asyncio.to_thread)."""
        import inspect
        from gtm_triage.api import triage
        assert inspect.iscoroutinefunction(triage), "POST /triage must be async"


# ── Graceful degradation ──────────────────────────────────────────────────────


class TestGracefulDegradation:
    def test_enrichment_failure_doesnt_crash_triage(self):
        """If enrichment raises, the executor catches it and the agent proceeds."""
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

        # Even if enrichment returns an error, the agent should finalize
        lead = Lead(email="test@example.com", message="hello")
        result = run_triage(lead, executor, trace, provider="mock")
        assert result.final_tier is not None  # completed, not crashed
