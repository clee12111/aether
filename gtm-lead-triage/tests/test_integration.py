"""Integration tests: end-to-end flows through the real FastAPI app.

These hit the full stack via TestClient — middleware, auth, rate limiting,
endpoint handlers, agent loop, trace store, CRM — with provider=mock.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def _client(**env_overrides):
    """Build a TestClient with lifespan, merging env overrides."""
    defaults = {
        "GTM_PROVIDER": "mock",
        "GTM_CRM_DB": ":memory:",
        "GTM_TRACE_DB": ":memory:",
        "GTM_RATE_LIMIT_RPM": "10000",  # High limit to avoid cross-test exhaustion
    }
    defaults.update(env_overrides)
    # Remove keys that should be absent (unless overridden)
    for k in ("GTM_API_KEYS", "SENTRY_DSN", "OTLP_ENDPOINT", "APP_ENV"):
        if k not in env_overrides:
            os.environ.pop(k, None)
    for k, v in defaults.items():
        os.environ[k] = v
    from gtm_triage.api import app
    return TestClient(app, raise_server_exceptions=False)


# ── Full triage flow ─────────────────────────────────────────────────────────


class TestTriageFlow:
    def test_full_triage_returns_tier_and_route(self):
        """POST /triage → result with tier, route, run_id, steps."""
        with _client() as c:
            resp = c.post("/triage", json={
                "email": f"flow-{uuid.uuid4().hex[:6]}@acme.com",
                "name": "Test User",
                "message": "We need a demo for our trading desk. Urgent.",
                "idempotency_key": f"flow-{uuid.uuid4().hex}",
            })
            assert resp.status_code == 200
            body = resp.json()
            assert "final_tier" in body
            assert body["final_tier"] in ("hot", "warm", "cold", "disqualified")
            assert "final_route" in body
            assert "run_id" in body
            assert "steps" in body

    def test_disqualified_for_disposable_email(self):
        """Disposable email → disqualified/drop, short-circuited."""
        with _client() as c:
            resp = c.post("/triage", json={
                "email": f"bot-{uuid.uuid4().hex[:6]}@tempmail.com",
                "message": "spam",
                "idempotency_key": f"disposable-{uuid.uuid4().hex}",
            })
            assert resp.status_code == 200
            body = resp.json()
            assert body["final_tier"] == "disqualified"
            assert body["final_route"] == "drop"

    def test_triage_result_persisted_in_crm(self):
        """After triage, GET /contacts/{email} returns the record."""
        email = f"crm-{uuid.uuid4().hex[:6]}@bigcorp.com"
        with _client() as c:
            c.post("/triage", json={
                "email": email,
                "message": "demo please",
                "idempotency_key": f"crm-{uuid.uuid4().hex}",
            })
            resp = c.get(f"/contacts/{email}")
            assert resp.status_code == 200
            body = resp.json()
            assert body["record"]["found"] is True

    def test_run_trace_accessible(self):
        """After triage, GET /runs/{run_id} returns trace events."""
        with _client() as c:
            resp = c.post("/triage", json={
                "email": f"trace-{uuid.uuid4().hex[:6]}@corp.com",
                "message": "demo",
                "idempotency_key": f"trace-{uuid.uuid4().hex}",
            })
            run_id = resp.json()["run_id"]
            resp = c.get(f"/runs/{run_id}")
            assert resp.status_code == 200
            body = resp.json()
            assert body["event_count"] >= 2  # at least run_start + run_end


# ── Idempotency ──────────────────────────────────────────────────────────────


class TestIdempotency:
    def test_same_key_returns_cached_result(self):
        """Same idempotency key returns the exact same result."""
        idem_key = f"idem-{uuid.uuid4().hex}"
        email = f"idem-{uuid.uuid4().hex[:6]}@company.com"
        with _client() as c:
            resp1 = c.post("/triage", json={
                "email": email,
                "message": "demo",
                "idempotency_key": idem_key,
            })
            assert resp1.status_code == 200, f"First triage failed: {resp1.status_code} {resp1.text}"
            resp2 = c.post("/triage", json={
                "email": email,
                "message": "demo",
                "idempotency_key": idem_key,
            })
            assert resp2.status_code == 200, f"Second triage failed: {resp2.status_code} {resp2.text}"
            assert resp1.json()["run_id"] == resp2.json()["run_id"]
            assert resp1.json()["final_tier"] == resp2.json()["final_tier"]

    def test_different_key_produces_different_run(self):
        """Different idempotency keys produce different runs."""
        email = f"diff-{uuid.uuid4().hex[:6]}@company.com"
        with _client() as c:
            resp1 = c.post("/triage", json={
                "email": email,
                "message": "demo",
                "idempotency_key": f"key-a-{uuid.uuid4().hex}",
            })
            assert resp1.status_code == 200
            resp2 = c.post("/triage", json={
                "email": email,
                "message": "demo",
                "idempotency_key": f"key-b-{uuid.uuid4().hex}",
            })
            assert resp2.status_code == 200
            assert resp1.json()["run_id"] != resp2.json()["run_id"]


# ── Auth enforcement ─────────────────────────────────────────────────────────


class TestAuthEnforcement:
    def test_auth_blocks_without_key(self):
        """With GTM_API_KEYS set, requests without a key get 401."""
        with _client(GTM_API_KEYS="secret-key-123") as c:
            resp = c.post("/triage", json={"email": "test@example.com"})
            assert resp.status_code == 401

    def test_auth_passes_with_correct_key(self):
        """Correct key passes auth."""
        with _client(GTM_API_KEYS="secret-key-123") as c:
            resp = c.post(
                "/triage",
                json={
                    "email": f"auth-{uuid.uuid4().hex[:6]}@corp.com",
                    "message": "test",
                    "idempotency_key": f"auth-{uuid.uuid4().hex}",
                },
                headers={"Authorization": "Bearer secret-key-123"},
            )
            assert resp.status_code == 200

    def test_public_endpoints_skip_auth(self):
        """Public endpoints (/health, /ready) skip auth."""
        with _client(GTM_API_KEYS="secret-key-123") as c:
            assert c.get("/health").status_code == 200
            assert c.get("/ready").status_code == 200

    def test_metrics_requires_auth(self):
        """/metrics and /metrics/outcomes require auth (business-sensitive)."""
        with _client(GTM_API_KEYS="secret-key-123") as c:
            assert c.get("/metrics").status_code == 401
            assert c.get("/metrics/outcomes").status_code == 401
            # With auth, they pass
            assert c.get("/metrics", headers={"Authorization": "Bearer secret-key-123"}).status_code == 200
            assert c.get("/metrics/outcomes", headers={"Authorization": "Bearer secret-key-123"}).status_code == 200

    def test_fail_closed_in_production(self):
        """APP_ENV=production + no keys → 503 on authenticated endpoints."""
        with _client(GTM_API_KEYS="", APP_ENV="production") as c:
            resp = c.post("/triage", json={"email": "test@example.com"})
            assert resp.status_code == 503


# ── Rate limiting ────────────────────────────────────────────────────────────


class TestRateLimitIntegration:
    def test_token_bucket_exhaustion(self):
        """Token bucket with low capacity exhausts and denies requests."""
        from gtm_triage.middleware import _TokenBucket
        bucket = _TokenBucket(rate=0.1, capacity=2)
        assert bucket.allow("test-key") is True
        assert bucket.allow("test-key") is True
        assert bucket.allow("test-key") is False  # exhausted


# ── Readiness with broken dependency ─────────────────────────────────────────


class TestReadinessBrokenDep:
    def test_ready_503_when_trace_broken(self):
        """/ready returns 503 when trace store ping fails."""
        with _client() as c:
            from gtm_triage import api
            # Monkey-patch the trace store's ping to return False
            original_ping = api._trace.ping
            api._trace.ping = lambda: False
            try:
                resp = c.get("/ready")
                assert resp.status_code == 503
                body = resp.json()
                assert body["ready"] is False
                assert body["checks"]["trace"] == "fail"
            finally:
                api._trace.ping = original_ping

    def test_health_200_even_when_dep_broken(self):
        """/health stays 200 even when a dependency is broken (liveness ≠ readiness)."""
        with _client() as c:
            from gtm_triage import api
            original_ping = api._trace.ping
            api._trace.ping = lambda: False
            try:
                resp = c.get("/health")
                assert resp.status_code == 200
            finally:
                api._trace.ping = original_ping


# ── Outcome recording flow ───────────────────────────────────────────────────


class TestOutcomeFlow:
    def test_triage_then_outcome_then_metrics(self):
        """Full flow: triage → record outcome → check outcome metrics."""
        with _client() as c:
            # Triage
            resp = c.post("/triage", json={
                "email": f"outcome-flow-{uuid.uuid4().hex[:6]}@corp.com",
                "message": "want a demo",
                "idempotency_key": f"outcome-flow-{uuid.uuid4().hex}",
            })
            run_id = resp.json()["run_id"]
            tier = resp.json()["final_tier"]

            # Record outcome
            resp = c.post(f"/outcomes/{run_id}", json={
                "actual_outcome": "converted",
                "recorded_by": "integration_test",
            })
            assert resp.status_code == 201

            # Check metrics
            resp = c.get("/metrics/outcomes")
            assert resp.status_code == 200
            body = resp.json()
            assert tier in body
            assert body[tier]["with_outcome"] >= 1


# ── Delete (right-to-erasure) flow ───────────────────────────────────────────


class TestDeleteFlow:
    def test_triage_then_delete(self):
        """Triage a lead, then delete it. Verify data is gone."""
        email = f"delete-{uuid.uuid4().hex[:6]}@corp.com"
        with _client() as c:
            c.post("/triage", json={
                "email": email,
                "message": "test",
                "idempotency_key": f"delete-{uuid.uuid4().hex}",
            })

            # Verify exists
            resp = c.get(f"/contacts/{email}")
            assert resp.json()["record"]["found"] is True

            # Delete
            resp = c.delete(f"/contacts/{email}")
            assert resp.status_code == 200
            assert resp.json()["crm_record_deleted"] is True

            # Verify gone
            resp = c.get(f"/contacts/{email}")
            assert resp.json()["record"]["found"] is False
