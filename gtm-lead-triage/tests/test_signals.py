"""Unit tests for atomic signal extraction — zero network calls."""

from __future__ import annotations

import pytest

from gtm_triage.enrichment.signals import (
    Signal,
    SignalExtraction,
    extract_signals_mock,
    signals_to_extraction_result,
)


# ── Attribution tests ──────────────────────────────────────────────────────────


class TestAttribution:
    def test_first_person_sender(self):
        """'I'm the VP' → subject=sender, relation=self."""
        ext = extract_signals_mock(message="As VP of Engineering, I want a demo.")
        sen = [s for s in ext.signals if s.type == "seniority"]
        assert len(sen) >= 1
        assert sen[0].subject == "sender"
        assert sen[0].relation == "self"
        assert sen[0].value == "vp"

    def test_third_party_mentioned(self):
        """'Our CTO mentioned your platform' → subject=third_party, relation=mentioned."""
        ext = extract_signals_mock(message="Our CTO mentioned your platform in a meeting.")
        sen = [s for s in ext.signals if s.type == "seniority"]
        assert len(sen) >= 1
        assert sen[0].subject == "third_party"
        assert sen[0].relation == "mentioned"
        assert sen[0].value == "c_level"

    def test_third_party_sponsor_delegated(self):
        """'My VP asked me to reach out' → subject=third_party, relation=sponsor_delegated."""
        ext = extract_signals_mock(
            message="My VP asked me to reach out about your data pipeline product. She wants a demo."
        )
        sen = [s for s in ext.signals if s.type == "seniority"]
        assert len(sen) >= 1
        assert sen[0].subject == "third_party"
        assert sen[0].relation == "sponsor_delegated"

    def test_sponsor_delegated_generates_intent(self):
        """Sponsor-delegated VP requesting demo → delegated intent signal too."""
        ext = extract_signals_mock(
            message="My VP asked me to reach out. She wants a demo for Q4 planning."
        )
        intent_sigs = [s for s in ext.signals if s.type == "intent"]
        delegated = [s for s in intent_sigs if s.relation == "sponsor_delegated"]
        assert len(delegated) >= 1
        assert delegated[0].value == "high"
        assert delegated[0].subject == "third_party"

    def test_name_field_is_sender(self):
        """Seniority from name field → subject=sender."""
        ext = extract_signals_mock(name="Ken Tanaka, Director of Engineering", message="hello")
        sen = [s for s in ext.signals if s.type == "seniority"]
        assert len(sen) >= 1
        assert sen[0].subject == "sender"
        assert sen[0].value == "director"


# ── Attribution-aware scoring ──────────────────────────────────────────────────


class TestAttributionAwareScoring:
    def test_dell_cto_mentioned_not_credited(self):
        """Dell 'Our CTO mentioned' → seniority NOT credited to sender."""
        ext = extract_signals_mock(
            message="Our CTO mentioned your platform. I'm on the analytics team and wanted to learn more.",
        )
        result = signals_to_extraction_result(ext)
        # CTO is third_party, so sender seniority should NOT be c_level
        assert result.seniority != "c_level"

    def test_cisco_vp_delegated_intent_credited(self):
        """Cisco 'My VP asked me to reach out' → sponsor-delegated intent IS credited."""
        ext = extract_signals_mock(
            message="My VP asked me to reach out about your product. She wants a demo for Q4.",
        )
        result = signals_to_extraction_result(ext)
        # Sponsor-delegated intent should be credited
        assert result.intent in ("high", "medium")
        assert result.intent_confidence >= 0.60

    def test_first_person_vp_credited(self):
        """'As VP of Digital Transformation' → seniority=vp credited."""
        ext = extract_signals_mock(
            message="As VP of Digital Transformation, I'd like to schedule a review.",
        )
        result = signals_to_extraction_result(ext)
        assert result.seniority == "vp"
        assert result.seniority_confidence >= 0.70


# ── Thin input ─────────────────────────────────────────────────────────────────


class TestThinInput:
    def test_empty_message_no_signals(self):
        ext = extract_signals_mock(message="")
        assert len(ext.signals) == 0

    def test_hi_only_no_signals(self):
        ext = extract_signals_mock(message="Hi there.")
        assert len(ext.signals) == 0

    def test_email_only(self):
        ext = extract_signals_mock(email="a.novak@plaid.com")
        assert len(ext.signals) == 0


# ── Hard disqualifiers ────────────────────────────────────────────────────────


class TestDisqualifiers:
    def test_opt_out(self):
        ext = extract_signals_mock(message="Please unsubscribe me from all emails.")
        assert any(s.type == "opt_out" for s in ext.signals)
        result = signals_to_extraction_result(ext)
        assert result.intent == "opt_out"

    def test_legal(self):
        ext = extract_signals_mock(message="This is a data subject access request under GDPR Article 15.")
        assert any(s.type == "legal" for s in ext.signals)
        result = signals_to_extraction_result(ext)
        assert result.intent == "legal_or_compliance"


# ── Evidence spans ─────────────────────────────────────────────────────────────


class TestEvidence:
    def test_evidence_is_substring(self):
        """Every evidence span must be a real substring of the message."""
        msg = "Budget approved, we need 80 licenses deployed. Can we get a kickoff call tomorrow?"
        ext = extract_signals_mock(message=msg)
        for sig in ext.signals:
            if sig.evidence:
                assert sig.evidence.lower() in msg.lower(), \
                    f"Evidence '{sig.evidence}' not found in message"


# ── SignalExtraction helpers ──────────────────────────────────────────────────


class TestHelpers:
    def test_sender_signals(self):
        ext = SignalExtraction(signals=[
            Signal(type="seniority", value="vp", subject="sender", confidence=0.9),
            Signal(type="seniority", value="c_level", subject="third_party", confidence=0.3),
        ])
        sender = ext.sender_signals("seniority")
        assert len(sender) == 1
        assert sender[0].value == "vp"

    def test_has_sponsor_delegated(self):
        ext = SignalExtraction(signals=[
            Signal(type="intent", value="high", subject="third_party", relation="sponsor_delegated"),
        ])
        assert ext.has_sponsor_delegated() is True

    def test_no_sponsor_delegated(self):
        ext = SignalExtraction(signals=[
            Signal(type="intent", value="high", subject="sender"),
        ])
        assert ext.has_sponsor_delegated() is False
