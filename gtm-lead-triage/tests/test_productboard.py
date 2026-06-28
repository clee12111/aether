"""Tests for the Productboard connector — Phase 1 (fixture-first, offline).

Covers model parsing from real MCP fixtures, all three client variants,
the parsed_customer best-effort extractor, and the factory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gtm_triage.productboard.client import get_productboard_client
from gtm_triage.productboard.fixture_client import FixtureProductboardClient
from gtm_triage.productboard.live_client import LiveProductboardClient
from gtm_triage.productboard.null_client import NullProductboardClient
from gtm_triage.productboard.models import (
    PBCreateFeedbackResult,
    PBFeedbackItem,
    PBFeedbackList,
    PBFeature,
    PBField,
    PBIdentity,
    PBQueryResult,
)

_FIXTURES = Path(__file__).resolve().parent.parent / "gtm_triage" / "productboard" / "fixtures"


# ── Model parsing from raw fixtures ────────────────────────────────────────

class TestModelParsing:
    def test_identity_parses(self):
        raw = json.loads((_FIXTURES / "identity_get_identity.json").read_text())
        identity = PBIdentity.model_validate(raw)
        assert identity.membership.id == "20771652-b57f-47b8-8007-0631ca7c7fe9"
        assert identity.membership.role == "admin"
        assert identity.membership.email == "cody.lee.cl1@gmail.com"
        assert identity.workspace.id == 376912
        assert identity.workspace.domain == "aether-test"

    def test_query_result_parses(self):
        raw = json.loads((_FIXTURES / "entities_query_entities.json").read_text())
        result = PBQueryResult.model_validate(raw)
        assert result.total_count == 3
        assert len(result.entities) == 3
        assert result.unmatched_fields == []
        feat = result.entities[0]
        assert feat.entity_id.startswith("MTpQbUVudGl0")
        assert feat.entity_type == "Feature"
        assert feat.name == "Sample Feature (e.g. Epic)"

    def test_field_list_parses(self):
        raw = json.loads((_FIXTURES / "entities_list_entity_field_names.json").read_text())
        fields = [PBField.model_validate(f) for f in raw["fields"]]
        assert len(fields) == 32
        status_field = next(f for f in fields if f.name == "Status")
        assert status_field.field_type == "STATUS"
        assert status_field.id == "MTpQbUZpZWxkR2VuZXJpYzpzdGF0dXM="

    def test_feedback_list_parses(self):
        raw = json.loads((_FIXTURES / "feedback_list_feedback.json").read_text())
        result = PBFeedbackList.model_validate(raw)
        assert len(result.feedback) == 7  # 4 real seeded + 3 sample
        assert result.next_cursor is None
        # First item is the most recent seeded feedback (Figma)
        item = result.feedback[0]
        assert item.id == "b47de2bb-6cf9-4019-baea-149d19d18f4c"
        assert "Figma" in item.name
        assert item.display_url.startswith("https://")
        assert item.processed is False
        assert item.archived is False

    def test_create_feedback_result_parses(self):
        raw = json.loads((_FIXTURES / "feedback_create_feedback.json").read_text())
        result = PBCreateFeedbackResult.model_validate(raw)
        assert result.id == "6ab50553-5f75-47a1-8705-56a1ab7a8ce8"
        assert result.name == "MCP shape test"
        assert result.company == "Stripe"
        assert result.created_at == "2026-06-28T09:42:47Z"


# ── parsed_customer best-effort extractor ──────────────────────────────────

class TestParsedCustomer:
    def test_real_customer_string(self):
        """Real seeded feedback: 'leah.brooks@figma.com @ Figma (figma.com)'"""
        raw = json.loads((_FIXTURES / "feedback_list_feedback.json").read_text())
        item = PBFeedbackItem.model_validate(raw["feedback"][0])
        pc = item.parsed_customer
        assert pc.domain == "figma.com"
        assert pc.display_name == "leah.brooks@figma.com"
        assert pc.company == "Figma"

    def test_datadog_customer_string(self):
        """Real seeded: 'marcus.hale@datadoghq.com @ Datadoghq (datadoghq.com)'"""
        raw = json.loads((_FIXTURES / "feedback_list_feedback.json").read_text())
        item = PBFeedbackItem.model_validate(raw["feedback"][3])
        pc = item.parsed_customer
        assert pc.domain == "datadoghq.com"
        assert pc.company == "Datadoghq"

    def test_sample_customer_without_display_name(self):
        raw = json.loads((_FIXTURES / "feedback_list_feedback.json").read_text())
        # Sample Company B is at index 6 now
        item = PBFeedbackItem.model_validate(raw["feedback"][6])
        pc = item.parsed_customer
        assert pc.domain == "productboard.com"
        assert pc.display_name is None
        assert pc.company == "Sample Company B"

    def test_unparseable_customer(self):
        item = PBFeedbackItem(
            id="x", name="x", display_url="", content="",
            tags=[], processed=False, archived=False,
            customer="totally free form text",
            created_at="2026-01-01T00:00:00Z",
        )
        pc = item.parsed_customer
        assert pc.domain is None
        assert pc.display_name is None


# ── FixtureProductboardClient ──────────────────────────────────────────────

class TestFixtureClient:
    def setup_method(self):
        self.client = FixtureProductboardClient()

    def test_get_identity(self):
        identity = self.client.get_identity()
        assert isinstance(identity, PBIdentity)
        assert identity.workspace.domain == "aether-test"

    def test_list_feature_fields(self):
        fields = self.client.list_feature_fields()
        assert len(fields) == 32
        assert all(isinstance(f, PBField) for f in fields)

    def test_query_features(self):
        result = self.client.query_features()
        assert isinstance(result, PBQueryResult)
        assert result.total_count == 3
        assert all(isinstance(e, PBFeature) for e in result.entities)

    def test_list_feedback(self):
        result = self.client.list_feedback(entity_ids=["anything"])
        assert isinstance(result, PBFeedbackList)
        assert len(result.feedback) == 7

    def test_create_feedback(self):
        result = self.client.create_feedback(
            title="Test note",
            content="Body text",
            company_domain="example.com",
        )
        assert isinstance(result, PBCreateFeedbackResult)
        assert result.name == "Test note"
        assert result.company == "example.com"
        assert len(result.id) == 36

    def test_create_feedback_deterministic_id(self):
        r1 = self.client.create_feedback(title="same", content="a")
        r2 = self.client.create_feedback(title="same", content="b")
        assert r1.id == r2.id  # stable hash from title


# ── NullProductboardClient ─────────────────────────────────────────────────

class TestNullClient:
    def setup_method(self):
        self.client = NullProductboardClient()

    def test_get_identity_empty(self):
        identity = self.client.get_identity()
        assert identity.membership.id == ""
        assert identity.workspace.id == 0

    def test_list_feature_fields_empty(self):
        assert self.client.list_feature_fields() == []

    def test_query_features_empty(self):
        result = self.client.query_features()
        assert result.total_count == 0
        assert result.entities == []

    def test_list_feedback_empty(self):
        result = self.client.list_feedback(entity_ids=["x"])
        assert result.feedback == []

    def test_create_feedback_noop(self):
        result = self.client.create_feedback(title="t", content="c")
        assert result.name == "t"
        assert result.id == "null"

    def test_never_raises(self):
        """All methods should complete without exception."""
        self.client.get_identity()
        self.client.list_feature_fields()
        self.client.query_features()
        self.client.list_feedback(entity_ids=[])
        self.client.create_feedback(title="", content="")


# ── LiveProductboardClient ─────────────────────────────────────────────────

class TestLiveClient:
    def test_methods_return_valid_types_without_token(self):
        """Without a token, methods degrade gracefully (empty results, not crashes)."""
        client = LiveProductboardClient(token="")
        identity = client.get_identity()
        assert identity.membership.id == ""
        assert client.list_feature_fields() == []
        # query_features and list_feedback will fail on network but degrade
        result = client.query_features()
        assert result.entities == []  # graceful degradation
        fb = client.list_feedback(entity_ids=["x"])
        assert fb.feedback == []  # graceful degradation
        cr = client.create_feedback(title="t", content="c")
        assert cr.name == "t"  # returns error stub


# ── Factory ────────────────────────────────────────────────────────────────

class TestFactory:
    def test_default_is_fixture(self, monkeypatch):
        monkeypatch.delenv("PRODUCTBOARD_SOURCE", raising=False)
        client = get_productboard_client()
        assert isinstance(client, FixtureProductboardClient)

    def test_fixture_explicit(self, monkeypatch):
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "fixture")
        client = get_productboard_client()
        assert isinstance(client, FixtureProductboardClient)

    def test_off_returns_null(self, monkeypatch):
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "off")
        client = get_productboard_client()
        assert isinstance(client, NullProductboardClient)

    def test_live_returns_live(self, monkeypatch):
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "live")
        client = get_productboard_client()
        assert isinstance(client, LiveProductboardClient)
