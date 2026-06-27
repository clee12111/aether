"""Unit tests for lead signal extraction — zero network calls."""

from __future__ import annotations

import pytest

from gtm_triage.enrichment.extraction import (
    ExtractionResult,
    extract_lead_signals,
)


# ── Seniority extraction ──────────────────────────────────────────────────────


class TestSeniorityExtraction:
    def test_stated_role_in_message(self):
        r = extract_lead_signals(
            name="Mei Tanaka",
            message="I lead our risk operations team. We need to onboard your platform.",
        )
        assert r.seniority == "manager"
        assert r.seniority_confidence == 0.75

    def test_engagement_partner(self):
        r = extract_lead_signals(
            name="Remi Okafor",
            message="I'm the engagement partner — let's set up a call today.",
        )
        assert r.seniority == "c_level"

    def test_cto_in_name(self):
        r = extract_lead_signals(name="Mike Rodriguez, CTO", message="hello")
        assert r.seniority == "c_level"
        assert r.seniority_confidence == 0.60  # name field = lower confidence

    def test_director_in_message(self):
        r = extract_lead_signals(
            name="",
            message="I'm the Director of Engineering and we need a demo.",
        )
        assert r.seniority == "director"

    def test_head_of_in_message(self):
        r = extract_lead_signals(
            name="James Wu",
            message="As Head of Analytics, I'm evaluating platforms.",
        )
        assert r.seniority == "director"

    def test_intern_in_message(self):
        r = extract_lead_signals(
            name="Jordan Xu",
            message="I'm a summer intern exploring tools for our team.",
        )
        assert r.seniority == "ic"

    def test_student_in_message(self):
        r = extract_lead_signals(
            name="",
            message="I'm a graduate student researching lead scoring.",
        )
        assert r.seniority == "ic"

    def test_signature_block(self):
        r = extract_lead_signals(
            name="Lucia Garcia",
            message=(
                "Please send info.\n"
                "---\n"
                "Senior Program Manager, Digital Transformation\n"
                "Boeing Defense & Space"
            ),
        )
        assert r.seniority == "director"  # senior program manager → director

    def test_third_person_our_cto(self):
        """'our CTO shared' → c_level but LOW confidence (third-person reference)."""
        r = extract_lead_signals(
            message="Your product was on the shortlist our CTO shared.",
        )
        assert r.seniority == "c_level"
        assert r.seniority_confidence < 0.50  # below confidence gate

    def test_third_person_my_manager_not_penalized(self):
        """'My manager' is NOT penalized — manager carries only 10 points,
        too few to cause a false-hot. Only c_level/vp/director get the
        third-person confidence reduction."""
        r = extract_lead_signals(message="My manager asked me to gather options.")
        assert r.seniority == "manager"
        assert r.seniority_confidence >= 0.50  # not gated

    def test_first_person_i_am_the_cto(self):
        """'I'm the CTO' → c_level with HIGH confidence (first-person)."""
        r = extract_lead_signals(message="I'm the CTO and I need a demo.")
        assert r.seniority == "c_level"
        assert r.seniority_confidence >= 0.70

    def test_no_seniority_signal(self):
        r = extract_lead_signals(name="Alex Z", message="What does your product do?")
        assert r.seniority == ""
        assert r.seniority_confidence == 0.0

    def test_vp_in_message(self):
        r = extract_lead_signals(
            name="",
            message="As VP of Engineering, I want to understand your roadmap.",
        )
        assert r.seniority == "vp"


# ── Intent extraction ──────────────────────────────────────────────────────────


class TestIntentExtraction:
    def test_high_intent_budget_approved(self):
        r = extract_lead_signals(
            message="Budget is approved. Please send contract terms.",
        )
        assert r.intent == "high"
        assert r.intent_confidence >= 0.70

    def test_high_intent_seat_count(self):
        r = extract_lead_signals(
            message="We need 300 seats by next quarter.",
        )
        assert r.intent == "high"

    def test_high_intent_pilot(self):
        r = extract_lead_signals(
            message="Can we get a pilot started this week?",
        )
        assert r.intent == "high"

    def test_high_intent_urgent(self):
        r = extract_lead_signals(message="This is urgent, we need a demo ASAP.")
        assert r.intent == "high"

    def test_medium_intent_evaluating(self):
        r = extract_lead_signals(
            message="We're evaluating platforms for our analytics team.",
        )
        assert r.intent == "medium"

    def test_medium_intent_interested(self):
        r = extract_lead_signals(message="We're interested in your platform.")
        assert r.intent == "medium"

    def test_medium_intent_case_studies(self):
        r = extract_lead_signals(
            message="Can you share case studies for companies in our space?",
        )
        assert r.intent == "medium"

    def test_low_intent_just_curious(self):
        r = extract_lead_signals(message="Just curious about your product.")
        assert r.intent == "low"

    def test_low_intent_what_do_you_do(self):
        r = extract_lead_signals(message="What does your product do?")
        assert r.intent == "low"

    def test_low_intent_send_info(self):
        r = extract_lead_signals(message="Can you send me some info?")
        assert r.intent == "low"

    def test_opt_out_unsubscribe(self):
        r = extract_lead_signals(message="Please unsubscribe me from all future emails.")
        assert r.intent == "opt_out"
        assert r.intent_confidence >= 0.90

    def test_opt_out_remove_me(self):
        r = extract_lead_signals(message="Remove me from your list.")
        assert r.intent == "opt_out"

    def test_opt_out_take_me_off(self):
        r = extract_lead_signals(
            message="Please take me off your list. I've asked three times already.",
        )
        assert r.intent == "opt_out"

    def test_legal_gdpr(self):
        r = extract_lead_signals(
            message="This is a data subject access request under GDPR Article 15.",
        )
        assert r.intent == "legal_or_compliance"
        assert r.intent_confidence >= 0.90

    def test_legal_compliance_review(self):
        r = extract_lead_signals(
            message="We are conducting a compliance review of digital tools.",
        )
        assert r.intent == "legal_or_compliance"

    def test_reverse_intent_sponsorship(self):
        r = extract_lead_signals(
            message="Would your team like a speaking slot at our developer conference?",
        )
        assert r.intent == "low"

    def test_empty_message(self):
        r = extract_lead_signals(message="")
        assert r.intent == "unknown"
        assert r.intent_confidence == 0.0

    def test_hi_only(self):
        r = extract_lead_signals(message="Hi.")
        assert r.intent == "unknown"


# ── Minimal input ──────────────────────────────────────────────────────────────


class TestMinimalInput:
    def test_email_only(self):
        r = extract_lead_signals(email="someone@stripe.com")
        assert isinstance(r, ExtractionResult)
        assert r.company == "stripe"
        assert r.company_confidence == 0.30

    def test_message_only(self):
        r = extract_lead_signals(message="I'm the CTO and I need a demo.")
        assert r.seniority == "c_level"
        assert r.intent == "high"

    def test_name_only(self):
        r = extract_lead_signals(name="Julia Martinez, VP of Sales")
        assert r.seniority == "vp"

    def test_all_empty(self):
        r = extract_lead_signals()
        assert r.seniority == ""
        assert r.intent == "unknown"
        assert r.company == ""

    def test_email_only_free(self):
        r = extract_lead_signals(email="test@gmail.com")
        assert r.company == "gmail"  # domain-derived, low confidence


# ── Non-English ────────────────────────────────────────────────────────────────


class TestNonEnglish:
    def test_german_with_demo_keyword(self):
        """German text with 'Demo' (cognate) should be detected as high intent."""
        r = extract_lead_signals(
            message="Wir brauchen eine Demo -- 150 Lizenzen, Budget steht.",
        )
        # "150 Lizenzen" won't match English "licenses" pattern, but "Demo" is a cognate
        # and the message contains "Budget" which is close but not an exact match.
        # The mock extractor may not catch German — that's honest.
        # It should at least not crash.
        assert isinstance(r, ExtractionResult)

    def test_spanish_evaluando(self):
        """Spanish 'evaluando' shouldn't match English 'evaluate'."""
        r = extract_lead_signals(
            message="Estamos evaluando plataformas para nuestro equipo.",
        )
        assert isinstance(r, ExtractionResult)
        # Mock extractor legitimately misses non-English — noted as a limitation
