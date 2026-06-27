"""Tests for API hardening: auth, rate limiting, input validation, error handling.

Uses FastAPI TestClient — no real server needed.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_no_auth():
    """TestClient with auth disabled (no GTM_API_KEYS set)."""
    with patch.dict(os.environ, {"GTM_API_KEYS": ""}, clear=False):
        from gtm_triage.api import app
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


@pytest.fixture
def client_with_auth():
    """TestClient with auth enabled."""
    with patch.dict(os.environ, {
        "GTM_API_KEYS": "test-key-1,test-key-2",
        "GTM_PROVIDER": "mock",
        "CRM_BACKEND": "sqlite",
        "GTM_CRM_DB": ":memory:",
        "GTM_TRACE_DB": ":memory:",
    }, clear=False):
        from gtm_triage.api import app
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


# ── Auth tests ─────────────────────────────────────────────────────────────────


class TestAuth:
    def test_health_no_auth_required(self, client_with_auth: TestClient):
        """Health endpoint is public — no auth needed."""
        resp = client_with_auth.get("/health")
        assert resp.status_code == 200

    def test_triage_requires_auth(self, client_with_auth: TestClient):
        """Triage without a key returns 401."""
        resp = client_with_auth.post("/triage", json={
            "email": "test@example.com",
        })
        assert resp.status_code == 401
        body = resp.json()
        assert body["error"]["type"] == "unauthorized"

    def test_triage_with_bearer_token(self, client_with_auth: TestClient):
        """Valid Bearer token passes auth."""
        resp = client_with_auth.post(
            "/triage",
            json={"email": "test@example.com"},
            headers={"Authorization": "Bearer test-key-1"},
        )
        # Should get past auth (may fail on other grounds, but NOT 401)
        assert resp.status_code != 401

    def test_triage_with_api_key_header(self, client_with_auth: TestClient):
        """Valid X-API-Key header passes auth."""
        resp = client_with_auth.post(
            "/triage",
            json={"email": "test@example.com"},
            headers={"X-API-Key": "test-key-2"},
        )
        assert resp.status_code != 401

    def test_triage_with_wrong_key(self, client_with_auth: TestClient):
        """Wrong key returns 401."""
        resp = client_with_auth.post(
            "/triage",
            json={"email": "test@example.com"},
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 401

    def test_auth_disabled_allows_all(self, client_no_auth: TestClient):
        """When GTM_API_KEYS is empty, auth is disabled."""
        resp = client_no_auth.get("/config")
        assert resp.status_code == 200

    def test_runs_requires_auth(self, client_with_auth: TestClient):
        resp = client_with_auth.get("/runs")
        assert resp.status_code == 401

    def test_leads_requires_auth(self, client_with_auth: TestClient):
        resp = client_with_auth.get("/leads")
        assert resp.status_code == 401


# ── Input validation tests ─────────────────────────────────────────────────────


class TestInputValidation:
    def test_triage_missing_email(self, client_no_auth: TestClient):
        """Missing required email field returns 422."""
        resp = client_no_auth.post("/triage", json={})
        assert resp.status_code == 422

    def test_triage_empty_email(self, client_no_auth: TestClient):
        """Empty email string returns 422."""
        resp = client_no_auth.post("/triage", json={"email": ""})
        assert resp.status_code == 422

    def test_triage_email_too_long(self, client_no_auth: TestClient):
        """Email exceeding max_length returns 422."""
        resp = client_no_auth.post("/triage", json={"email": "a" * 321})
        assert resp.status_code == 422

    def test_triage_message_too_long(self, client_no_auth: TestClient):
        """Message exceeding max_length returns 422."""
        resp = client_no_auth.post("/triage", json={
            "email": "test@example.com",
            "message": "x" * 10001,
        })
        assert resp.status_code == 422

    def test_deliver_invalid_tier(self, client_no_auth: TestClient):
        """Invalid tier value returns 422."""
        resp = client_no_auth.post("/deliver", json={
            "email": "test@example.com",
            "run_id": "run-1",
            "tier": "invalid_tier",
            "route": "ae_immediate",
        })
        assert resp.status_code == 422

    def test_deliver_valid_tier(self, client_no_auth: TestClient):
        """Valid tier passes validation."""
        resp = client_no_auth.post("/deliver", json={
            "email": "test@example.com",
            "run_id": "run-1",
            "tier": "hot",
            "route": "ae_immediate",
        })
        # May fail for other reasons but NOT 422
        assert resp.status_code != 422

    def test_malformed_json(self, client_no_auth: TestClient):
        """Malformed JSON returns 422, not 500."""
        resp = client_no_auth.post(
            "/triage",
            content=b"not json at all",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422


# ── Rate limiting tests ────────────────────────────────────────────────────────


class TestRateLimiting:
    def test_rate_limit_returns_429(self):
        """Exceeding rate limit returns 429."""
        from gtm_triage.middleware import _TokenBucket
        bucket = _TokenBucket(rate=1.0, capacity=2)
        assert bucket.allow("k") is True
        assert bucket.allow("k") is True
        assert bucket.allow("k") is False  # exhausted

    def test_different_keys_independent(self):
        """Different keys have independent limits."""
        from gtm_triage.middleware import _TokenBucket
        bucket = _TokenBucket(rate=1.0, capacity=1)
        assert bucket.allow("key-a") is True
        assert bucket.allow("key-b") is True
        assert bucket.allow("key-a") is False
        assert bucket.allow("key-b") is False


# ── Error handling tests ───────────────────────────────────────────────────────


class TestErrorHandling:
    def test_404_clean_response(self, client_no_auth: TestClient):
        """Non-existent run returns 404 with detail, no stack trace."""
        resp = client_no_auth.get("/runs/nonexistent-run-id")
        assert resp.status_code == 404
        body = resp.json()
        assert "detail" in body
        # No stack trace in response
        assert "Traceback" not in resp.text

    def test_structured_error_format(self):
        """error_response produces the correct shape."""
        from gtm_triage.middleware import error_response
        resp = error_response(400, "bad_request", "Test message")
        assert resp.status_code == 400
        body = resp.body
        import json
        data = json.loads(body)
        assert data["error"]["type"] == "bad_request"
        assert data["error"]["message"] == "Test message"


# ── Timeout coverage ──────────────────────────────────────────────────────────


class TestTimeouts:
    def test_openai_client_has_timeout(self):
        """All OpenAI client instantiations must specify a timeout."""
        import re
        from pathlib import Path
        gtm_dir = Path(__file__).parent.parent / "gtm_triage"
        for py_file in gtm_dir.rglob("*.py"):
            content = py_file.read_text()
            for match in re.finditer(r"OpenAI\(", content):
                # Find the full call up to the closing paren
                start = match.start()
                # Simple: check if "timeout" appears in the next 200 chars
                snippet = content[start:start + 200]
                assert "timeout" in snippet, \
                    f"{py_file.name}: OpenAI() call without timeout at char {start}"

    def test_httpx_client_has_timeout(self):
        """All httpx.Client instantiations must specify a timeout."""
        import re
        from pathlib import Path
        gtm_dir = Path(__file__).parent.parent / "gtm_triage"
        for py_file in gtm_dir.rglob("*.py"):
            content = py_file.read_text()
            for match in re.finditer(r"httpx\.Client\(", content):
                snippet = content[match.start():match.start() + 200]
                assert "timeout" in snippet, \
                    f"{py_file.name}: httpx.Client() call without timeout"
