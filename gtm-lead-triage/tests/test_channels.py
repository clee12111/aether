"""Tests for multi-channel intake — Phase 7.

Covers: adapter unit tests + API endpoint integration tests.
All mock provider, zero network.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gtm_triage.channels.chat import ChatAdapter
from gtm_triage.channels.clay import ClayWebhookAdapter
from gtm_triage.channels.email import EmailAdapter
from gtm_triage.channels.web_form import WebFormAdapter


# ── Adapter unit tests ─────────────────────────────────────────────────────

class TestEmailAdapter:
    def test_full_email(self):
        raw = (
            "From: Jane Doe <jane.doe@acmecorp.com>\n"
            "Subject: Interested in a demo\n"
            "\n"
            "Hi, I'd like to schedule a demo for our team. We're evaluating tools.\n"
        )
        parsed = EmailAdapter().to_lead(raw)
        assert parsed.lead.email == "jane.doe@acmecorp.com"
        assert parsed.lead.name == "Jane Doe"
        assert "demo" in parsed.lead.message
        assert parsed.source == "email"
        assert parsed.extraction_confidence >= 0.5
        assert parsed.field_sources["email"] == "email_header"

    def test_email_only(self):
        raw = "From: user@example.com\n\nJust curious about your product."
        parsed = EmailAdapter().to_lead(raw)
        assert parsed.lead.email == "user@example.com"
        assert parsed.lead.name == ""
        assert parsed.extraction_confidence <= 0.6

    def test_email_in_body_fallback(self):
        raw = "Hello, please contact me at someone@test.io about pricing."
        parsed = EmailAdapter().to_lead(raw)
        assert parsed.lead.email == "someone@test.io"

    def test_no_email_raises(self):
        with pytest.raises(ValueError, match="email"):
            EmailAdapter().to_lead("Hello, no email here.")

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            EmailAdapter().to_lead("")


class TestChatAdapter:
    def test_chat_with_email(self):
        transcript = (
            "Visitor: Hi, I'm Sarah Chen. My email is sarah@bigcorp.com.\n"
            "Visitor: I'm interested in your enterprise plan.\n"
            "Agent: Happy to help! Let me get some details.\n"
        )
        parsed = ChatAdapter().to_lead(transcript)
        assert parsed.lead.email == "sarah@bigcorp.com"
        assert parsed.lead.name == "Sarah Chen"
        assert parsed.source == "chat"

    def test_no_email_raises(self):
        with pytest.raises(ValueError, match="email"):
            ChatAdapter().to_lead("Just chatting, no email given.")

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            ChatAdapter().to_lead("")


class TestClayWebhookAdapter:
    def test_full_row(self):
        row = {
            "Email": "j.martinez@acmefintech.com",
            "Full Name": "Julia Martinez",
            "Company": "Acme Fintech",
            "Job Title": "VP of Sales",
            "Domain": "acmefintech.com",
        }
        parsed = ClayWebhookAdapter().to_lead(row)
        assert parsed.lead.email == "j.martinez@acmefintech.com"
        assert parsed.lead.name == "Julia Martinez"
        assert parsed.lead.company == "Acme Fintech"
        assert "VP of Sales" in parsed.lead.message
        assert parsed.source == "clay"
        assert parsed.extraction_confidence >= 0.8

    def test_first_last_name(self):
        row = {
            "email": "test@example.com",
            "First Name": "John",
            "Last Name": "Smith",
        }
        parsed = ClayWebhookAdapter().to_lead(row)
        assert parsed.lead.name == "John Smith"

    def test_missing_fields_graceful(self):
        row = {"email": "minimal@example.com"}
        parsed = ClayWebhookAdapter().to_lead(row)
        assert parsed.lead.email == "minimal@example.com"
        assert parsed.lead.name == ""
        assert parsed.lead.company == ""

    def test_no_email_raises(self):
        with pytest.raises(ValueError, match="email"):
            ClayWebhookAdapter().to_lead({"Name": "Bob", "Company": "ACME"})

    def test_empty_row_raises(self):
        with pytest.raises(ValueError):
            ClayWebhookAdapter().to_lead({})

    def test_arbitrary_columns_tolerated(self):
        row = {
            "email": "user@co.com",
            "Random Column": "whatever",
            "Another Thing": 42,
            "Enrichment Score": "0.95",
        }
        parsed = ClayWebhookAdapter().to_lead(row)
        assert parsed.lead.email == "user@co.com"


class TestWebFormAdapter:
    def test_full_form(self):
        raw = {"email": "test@example.com", "name": "Test", "company": "Co", "message": "Hello"}
        parsed = WebFormAdapter().to_lead(raw)
        assert parsed.lead.email == "test@example.com"
        assert parsed.extraction_confidence == 1.0

    def test_no_email_raises(self):
        with pytest.raises(ValueError, match="email"):
            WebFormAdapter().to_lead({"name": "Bob"})


# ── API endpoint tests ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GTM_PROVIDER", "mock")
    monkeypatch.setenv("CRM_BACKEND", "sqlite")
    monkeypatch.setenv("GTM_CRM_DB", ":memory:")
    monkeypatch.setenv("GTM_TRACE_DB", ":memory:")
    monkeypatch.setenv("APOLLO_SOURCE", "off")
    monkeypatch.setenv("PRODUCTBOARD_SOURCE", "off")
    monkeypatch.setenv("SEARCH_PROVIDER", "off")
    monkeypatch.setenv("ENRICHMENT_PROVIDER", "mock")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GTM_API_KEYS", raising=False)
    monkeypatch.delenv("HUBSPOT_TOKEN", raising=False)


@pytest.fixture
def client():
    from gtm_triage.api import app
    with TestClient(app) as c:
        yield c


class TestEmailEndpoint:
    def test_valid_email_200(self, client):
        resp = client.post("/intake/email", json={
            "raw_email": (
                "From: Jane Doe <jane@acmecorp.com>\n"
                "Subject: Demo request\n"
                "\n"
                "Hi, we'd like to schedule a demo.\n"
            ),
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "run_id" in data
        assert data["source"] == "email"
        assert data["parsed_lead"]["email"] == "jane@acmecorp.com"
        assert data["parsed_lead"]["name"] == "Jane Doe"
        assert data["extraction_confidence"] > 0
        assert data["final_tier"] in ("hot", "warm", "cold", "disqualified")

    def test_no_email_422(self, client):
        resp = client.post("/intake/email", json={"raw_email": "Hello, no email here."})
        assert resp.status_code == 422

    def test_empty_422(self, client):
        resp = client.post("/intake/email", json={"raw_email": ""})
        assert resp.status_code == 422


class TestChatEndpoint:
    def test_valid_chat_200(self, client):
        resp = client.post("/intake/chat", json={
            "transcript": (
                "Visitor: Hi, I'm Mark Chen. My email is mark@cloudtech.com.\n"
                "Visitor: I'm interested in learning more about your product.\n"
            ),
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "chat"
        assert data["parsed_lead"]["email"] == "mark@cloudtech.com"
        assert "run_id" in data

    def test_no_email_422(self, client):
        resp = client.post("/intake/chat", json={"transcript": "Just chatting."})
        assert resp.status_code == 422


class TestClayWebhook:
    def test_valid_row_200(self, client):
        resp = client.post("/webhooks/clay", json={
            "row": {
                "Email": "j.martinez@acmefintech.com",
                "Full Name": "Julia Martinez",
                "Company": "Acme Fintech",
                "Job Title": "VP of Sales",
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "clay"
        assert data["parsed_lead"]["email"] == "j.martinez@acmefintech.com"
        assert data["parsed_lead"]["name"] == "Julia Martinez"
        assert data["parsed_lead"]["company"] == "Acme Fintech"
        assert "VP of Sales" in data["parsed_lead"]["message"]
        assert "run_id" in data

    def test_missing_email_422(self, client):
        resp = client.post("/webhooks/clay", json={"row": {"Name": "Bob"}})
        assert resp.status_code == 422

    def test_empty_row_422(self, client):
        resp = client.post("/webhooks/clay", json={"row": {}})
        assert resp.status_code == 422

    def test_trace_recorded(self, client):
        resp = client.post("/webhooks/clay", json={
            "row": {"email": "trace@test.com", "name": "Test"},
        })
        run_id = resp.json()["run_id"]
        trace_resp = client.get(f"/runs/{run_id}")
        assert trace_resp.status_code == 200
        assert trace_resp.json()["event_count"] >= 2


class TestAuthOnIntake:
    def test_auth_enforced(self, client, monkeypatch):
        monkeypatch.setenv("GTM_API_KEYS", "secret123")
        from gtm_triage.api import app
        with TestClient(app) as auth_client:
            resp = auth_client.post("/intake/email", json={
                "raw_email": "From: x@y.com\n\nHello",
            })
            assert resp.status_code == 401

            resp = auth_client.post(
                "/intake/email",
                json={"raw_email": "From: x@y.com\n\nHello"},
                headers={"X-API-Key": "secret123"},
            )
            assert resp.status_code == 200


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
