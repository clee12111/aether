"""Tests for LiveProductboardClient with mocked httpx transport (no network)."""

from __future__ import annotations

import json

import httpx
import pytest

from gtm_triage.productboard.live_client import LiveProductboardClient
from gtm_triage.productboard.models import PBFeedbackItem, PBFeedbackList, PBQueryResult


def _mock_transport(response_json: dict, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=response_json)
    return httpx.MockTransport(handler)


class TestLiveQueryFeatures:
    def test_returns_features(self):
        data = {"data": [
            {"id": "feat-1", "name": "Feature A", "links": {"html": "https://pb.com/f/1"}},
            {"id": "feat-2", "name": "Feature B", "links": {"html": "https://pb.com/f/2"}},
        ]}
        transport = _mock_transport(data)
        client = LiveProductboardClient(token="test-token", client=httpx.Client(transport=transport))
        result = client.query_features()
        assert isinstance(result, PBQueryResult)
        assert len(result.entities) == 2
        assert result.entities[0].name == "Feature A"

    def test_auth_header(self):
        captured = {}
        def handler(request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"data": []})
        client = LiveProductboardClient(token="my-secret", client=httpx.Client(transport=httpx.MockTransport(handler)))
        client.query_features()
        assert captured["auth"] == "Bearer my-secret"

    def test_graceful_on_error(self):
        transport = _mock_transport({}, status=500)
        client = LiveProductboardClient(token="t", client=httpx.Client(transport=transport))
        result = client.query_features()
        assert result.entities == []


class TestLiveListFeedback:
    def test_maps_notes_to_feedback_items(self):
        data = {"data": [
            {
                "id": "note-1", "title": "Customer request",
                "content": "We need SSO support.",
                "tags": [{"name": "enterprise"}],
                "customer": {"email": "user@co.com", "company": {"name": "Co Inc", "domain": "co.com"}},
                "createdAt": "2026-01-01T00:00:00Z",
                "state": "new",
                "links": {"html": "https://pb.com/n/1"},
            },
        ]}
        transport = _mock_transport(data)
        client = LiveProductboardClient(token="t", client=httpx.Client(transport=transport))
        result = client.list_feedback(entity_ids=["any"])
        assert isinstance(result, PBFeedbackList)
        assert len(result.feedback) == 1
        item = result.feedback[0]
        assert item.id == "note-1"
        assert item.name == "Customer request"
        assert "SSO" in item.content
        assert item.customer == "user@co.com @ Co Inc (co.com)"
        pc = item.parsed_customer
        assert pc.domain == "co.com"
        assert pc.company == "Co Inc"


class TestLiveCreateFeedback:
    def test_creates_note(self):
        captured = {}
        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"data": {
                "id": "new-note-1", "title": "Test",
                "links": {"html": "https://pb.com/n/new"},
                "createdAt": "2026-01-01T00:00:00Z",
            }})
        client = LiveProductboardClient(token="t", client=httpx.Client(transport=httpx.MockTransport(handler)))
        result = client.create_feedback(title="Test", content="Body", customer_email="u@co.com", company_domain="co.com", tags=["inbound"])
        assert result.id == "new-note-1"
        assert result.display_url == "https://pb.com/n/new"
        assert captured["body"]["title"] == "Test"
        assert captured["body"]["customer"]["email"] == "u@co.com"
        assert captured["body"]["customer"]["company"]["domain"] == "co.com"
        assert "notes" in captured["url"]

    def test_graceful_on_error(self):
        transport = _mock_transport({}, status=500)
        client = LiveProductboardClient(token="t", client=httpx.Client(transport=transport))
        result = client.create_feedback(title="Test", content="Body")
        assert result.id == "error"
        assert result.name == "Test"
