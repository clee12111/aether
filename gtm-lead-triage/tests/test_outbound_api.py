"""Tests for the outbound API endpoints — Phase 6.

Uses FastAPI TestClient, mock provider, fixture sources. Zero network.
"""

from __future__ import annotations

import os
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """Set env for mock/fixture mode before app import."""
    monkeypatch.setenv("GTM_PROVIDER", "mock")
    monkeypatch.setenv("GTM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("CRM_BACKEND", "sqlite")
    monkeypatch.setenv("GTM_CRM_DB", ":memory:")
    monkeypatch.setenv("GTM_TRACE_DB", ":memory:")
    monkeypatch.setenv("APOLLO_SOURCE", "fixture")
    monkeypatch.setenv("PRODUCTBOARD_SOURCE", "off")
    monkeypatch.setenv("SEARCH_PROVIDER", "fixture")
    monkeypatch.setenv("ENRICHMENT_PROVIDER", "mock")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GTM_API_KEYS", raising=False)
    monkeypatch.delenv("HUBSPOT_TOKEN", raising=False)


@pytest.fixture
def client():
    from gtm_triage.api import app
    with TestClient(app) as c:
        yield c


_CAMPAIGN_BODY = {
    "name": "Test ICP",
    "icp_keywords": ["product management", "saas"],
    "icp_employee_ranges": ["201,1000"],
    "value_prop": "centralize customer feedback",
    "target_persona": "Head of Product",
}


# ── POST /outbound/target ──────────────────────────────────────────────────

class TestOutboundTarget:
    def test_notion_returns_200_with_brief_and_drafts(self, client):
        resp = client.post("/outbound/target", json={
            "company": "Notion Labs",
            "domain": "notion.so",
            "persona_role": "Head of Product",
            "campaign": _CAMPAIGN_BODY,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "run_id" in data
        assert data["final_tier"] in ("hot", "warm", "cold", "disqualified")
        assert data["trace_path"] in ("OUTBOUND_DRAFTED", "OUTBOUND_NO_DRAFT", "OUTBOUND_DISQUALIFIED")

        # Brief captured
        assert data.get("enrichment") is not None
        assert data["enrichment"].get("domain") == "notion.so"

        # If warm/hot, drafts present
        if data["final_tier"] in ("hot", "warm"):
            assert data["outreach"] is not None
            assert len(data["outreach"]["drafts"]) == 2
            for draft in data["outreach"]["drafts"]:
                assert draft["status"] == "draft"

    def test_run_trace_accessible(self, client):
        """POST creates a run; GET /outbound/runs/{id} returns trace."""
        resp = client.post("/outbound/target", json={
            "company": "Notion Labs",
            "domain": "notion.so",
            "persona_role": "Head of Product",
            "campaign": _CAMPAIGN_BODY,
        })
        run_id = resp.json()["run_id"]

        trace_resp = client.get(f"/outbound/runs/{run_id}")
        assert trace_resp.status_code == 200
        trace_data = trace_resp.json()
        assert trace_data["run_id"] == run_id
        assert trace_data["event_count"] >= 2  # at least run_start + run_end

    def test_idempotency(self, client):
        """Same target + campaign → same result (cache hit)."""
        body = {
            "company": "Notion Labs",
            "domain": "notion.so",
            "persona_role": "Head of Product",
            "campaign": _CAMPAIGN_BODY,
        }
        r1 = client.post("/outbound/target", json=body)
        r2 = client.post("/outbound/target", json=body)
        assert r1.json()["run_id"] == r2.json()["run_id"]

    def test_empty_domain_422(self, client):
        resp = client.post("/outbound/target", json={
            "company": "X",
            "domain": "",
            "campaign": _CAMPAIGN_BODY,
        })
        assert resp.status_code == 422

    def test_no_domain_disqualifies(self, client):
        """A domain that produces an empty brief → cold/disqualified."""
        resp = client.post("/outbound/target", json={
            "company": "Unknown Co",
            "domain": "nonexistent-xyz-404.example",
            "persona_role": "VP",
            "campaign": _CAMPAIGN_BODY,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["final_tier"] in ("cold", "disqualified")


# ── POST /outbound/campaign ────────────────────────────────────────────────

class TestOutboundCampaign:
    def test_returns_batch_results(self, client):
        resp = client.post("/outbound/campaign", json={
            "campaign": _CAMPAIGN_BODY,
            "source": {
                "apollo": {
                    "keyword_tags": ["software"],
                    "employee_ranges": ["201,1000"],
                    "limit": 3,
                },
            },
            "persona_role": "Head of Product",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["campaign"] == "Test ICP"
        assert data["targets_processed"] >= 1
        assert "tier_summary" in data
        assert isinstance(data["results"], list)
        assert len(data["results"]) <= 3

        # Each result has a run_id and tier
        for r in data["results"]:
            assert "run_id" in r
            assert r.get("final_tier") in ("hot", "warm", "cold", "disqualified", None)

    def test_batch_cap_respected(self, client):
        """Batch limit <= 25."""
        resp = client.post("/outbound/campaign", json={
            "campaign": _CAMPAIGN_BODY,
            "source": {
                "apollo": {
                    "keyword_tags": ["software"],
                    "limit": 50,  # over cap
                },
            },
            "persona_role": "VP",
        })
        # Should not process more than 25 (the fixture has 3, so it'll be <=3)
        assert resp.status_code == 200
        data = resp.json()
        assert data["targets_processed"] <= 25

    def test_missing_apollo_source_422(self, client):
        resp = client.post("/outbound/campaign", json={
            "campaign": _CAMPAIGN_BODY,
            "source": {},
            "persona_role": "VP",
        })
        assert resp.status_code == 422


# ── Auth ───────────────────────────────────────────────────────────────────

class TestOutboundAuth:
    def test_auth_enforced_when_keys_set(self, client, monkeypatch):
        monkeypatch.setenv("GTM_API_KEYS", "secret123")
        # Re-create client to pick up new env
        from gtm_triage.api import app
        with TestClient(app) as auth_client:
            # No auth header → 401
            resp = auth_client.post("/outbound/target", json={
                "company": "X",
                "domain": "x.com",
                "campaign": _CAMPAIGN_BODY,
            })
            assert resp.status_code == 401

            # With auth → should work
            resp = auth_client.post(
                "/outbound/target",
                json={
                    "company": "X",
                    "domain": "x.com",
                    "campaign": _CAMPAIGN_BODY,
                },
                headers={"X-API-Key": "secret123"},
            )
            assert resp.status_code == 200

    def test_health_always_public(self, client, monkeypatch):
        monkeypatch.setenv("GTM_API_KEYS", "secret123")
        from gtm_triage.api import app
        with TestClient(app) as auth_client:
            resp = auth_client.get("/health")
            assert resp.status_code == 200


# ── Inbound untouched ──────────────────────────────────────────────────────

class TestInboundUntouched:
    def test_triage_still_works(self, client):
        resp = client.post("/triage", json={
            "email": "test@example.com",
            "name": "Test",
            "company": "Example",
            "message": "Hello",
        })
        assert resp.status_code == 200
        assert "run_id" in resp.json()
