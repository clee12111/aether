"""Tests for Productboard write-back (auto-log lead requests by domain)."""

from __future__ import annotations

import os
import pytest

from gtm_triage.productboard.writeback import has_request, write_lead_to_productboard


class TestHasRequest:
    def test_clear_request(self):
        assert has_request("We need a demo for our team. Budget approved, want to schedule this quarter.") is True

    def test_evaluation_request(self):
        assert has_request("Evaluating tools to centralize customer feedback. Can you send pricing?") is True

    def test_browsing_no_request(self):
        assert has_request("Just browsing. Saw your site.") is False

    def test_empty(self):
        assert has_request("") is False

    def test_too_short(self):
        assert has_request("Hello") is False

    def test_spam_no_request(self):
        assert has_request("Buy cheap SEO backlinks! Best price guaranteed!") is False


class TestWriteBack:
    def test_writes_for_business_email_with_request(self, monkeypatch):
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "fixture")
        result = write_lead_to_productboard(
            email="marcus@datadoghq.com",
            message="We need a single source of truth for product feedback. Budget approved, want a demo.",
            company="Datadog",
        )
        assert result is not None
        assert result["domain"] == "datadoghq.com"
        assert result["note_id"]
        assert "datadoghq.com" in result["note_url"] or "fixture" in result["note_url"]

    def test_skips_free_email(self, monkeypatch):
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "fixture")
        result = write_lead_to_productboard(
            email="user@gmail.com",
            message="We need a demo for our team. Budget approved, want to schedule.",
        )
        assert result is None

    def test_skips_no_request(self, monkeypatch):
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "fixture")
        result = write_lead_to_productboard(
            email="user@datadoghq.com",
            message="Just browsing.",
        )
        assert result is None

    def test_skips_when_off(self, monkeypatch):
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "off")
        result = write_lead_to_productboard(
            email="marcus@datadoghq.com",
            message="We need a demo for our team. Budget approved, want to schedule.",
            company="Datadog",
        )
        assert result is None

    def test_title_includes_company(self, monkeypatch):
        monkeypatch.setenv("PRODUCTBOARD_SOURCE", "fixture")
        result = write_lead_to_productboard(
            email="user@stripe.com",
            message="Evaluating tools to centralize customer feedback. Can you send pricing info?",
            company="Stripe",
        )
        assert result is not None
        assert "Stripe" in result["title"] or "stripe.com" in result["title"]
