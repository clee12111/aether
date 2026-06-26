"""Unit tests for the enrichment provider interface, email signal, and fixture provider.

All tests are offline — zero network calls. DNS checks are skipped via skip_dns=True.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from gtm_triage.enrichment.base import EnrichmentProvider, EnrichmentResult, FieldValue
from gtm_triage.enrichment.email_signal import (
    DISPOSABLE_DOMAINS,
    FREE_DOMAINS,
    EmailSignal,
    check_email,
)
from gtm_triage.enrichment.fixture_provider import FixtureProvider


# ── EmailSignal tests ──────────────────────────────────────────────────────────


class TestEmailSignalSyntax:
    def test_valid_simple(self):
        sig = check_email("user@example.com", skip_dns=True)
        assert sig.syntax_valid is True
        assert sig.domain == "example.com"

    def test_valid_with_dots_and_plus(self):
        sig = check_email("first.last+tag@company.co.uk", skip_dns=True)
        assert sig.syntax_valid is True

    def test_invalid_no_at(self):
        sig = check_email("notanemail", skip_dns=True)
        assert sig.syntax_valid is False
        assert sig.verdict == "invalid"

    def test_invalid_no_domain(self):
        sig = check_email("user@", skip_dns=True)
        assert sig.syntax_valid is False
        assert sig.verdict == "invalid"

    def test_invalid_no_tld(self):
        sig = check_email("user@localhost", skip_dns=True)
        assert sig.syntax_valid is False
        assert sig.verdict == "invalid"

    def test_empty_string(self):
        sig = check_email("", skip_dns=True)
        assert sig.syntax_valid is False
        assert sig.verdict == "invalid"

    def test_whitespace_stripped(self):
        sig = check_email("  user@example.com  ", skip_dns=True)
        assert sig.syntax_valid is True
        assert sig.email == "user@example.com"

    def test_case_normalized(self):
        sig = check_email("User@Example.COM", skip_dns=True)
        assert sig.email == "user@example.com"
        assert sig.domain == "example.com"


class TestEmailSignalFree:
    def test_gmail_is_free(self):
        sig = check_email("test@gmail.com", skip_dns=True)
        assert sig.is_free is True
        assert sig.verdict == "free"

    def test_yahoo_is_free(self):
        sig = check_email("test@yahoo.com", skip_dns=True)
        assert sig.is_free is True

    def test_business_domain_not_free(self):
        sig = check_email("test@acmecorp.com", skip_dns=True)
        assert sig.is_free is False
        assert sig.verdict == "deliverable"

    def test_all_free_domains_recognized(self):
        for domain in FREE_DOMAINS:
            sig = check_email(f"test@{domain}", skip_dns=True)
            assert sig.is_free is True, f"{domain} not recognized as free"


class TestEmailSignalDisposable:
    def test_mailinator_is_disposable(self):
        sig = check_email("test@mailinator.com", skip_dns=True)
        assert sig.is_disposable is True
        assert sig.verdict == "disposable"

    def test_yopmail_is_disposable(self):
        sig = check_email("test@yopmail.com", skip_dns=True)
        assert sig.is_disposable is True

    def test_bounce_system_is_disposable(self):
        sig = check_email("noreply@bounce-system.net", skip_dns=True)
        assert sig.is_disposable is True
        assert sig.verdict == "disposable"

    def test_business_domain_not_disposable(self):
        sig = check_email("test@acmecorp.com", skip_dns=True)
        assert sig.is_disposable is False

    def test_all_disposable_domains_recognized(self):
        for domain in DISPOSABLE_DOMAINS:
            sig = check_email(f"test@{domain}", skip_dns=True)
            assert sig.is_disposable is True, f"{domain} not recognized as disposable"


class TestEmailSignalDNS:
    def test_skip_dns_leaves_mx_none(self):
        sig = check_email("test@example.com", skip_dns=True)
        assert sig.mx_valid is None

    def test_disposable_skips_dns(self):
        sig = check_email("test@mailinator.com", skip_dns=False)
        assert sig.mx_valid is None  # disposable detected before DNS


# ── FieldValue tests ───────────────────────────────────────────────────────────


class TestFieldValue:
    def test_default_is_empty(self):
        fv = FieldValue()
        assert fv.value == ""
        assert fv.source == "none"
        assert fv.confidence == 0.0

    def test_with_real_data(self):
        fv = FieldValue(value="technology", source="pdl", confidence=0.9)
        assert fv.value == "technology"
        assert fv.source == "pdl"
        assert fv.confidence == 0.9


# ── EnrichmentResult tests ─────────────────────────────────────────────────────


class TestEnrichmentResult:
    def test_overall_confidence_empty(self):
        r = EnrichmentResult(email="test@example.com")
        assert r.overall_confidence == 0.0

    def test_overall_confidence_all_resolved(self):
        r = EnrichmentResult(
            email="test@example.com",
            industry=FieldValue(value="tech", source="pdl", confidence=0.9),
            company_size=FieldValue(value="enterprise", source="pdl", confidence=0.9),
            seniority=FieldValue(value="vp", source="pdl", confidence=0.9),
            role=FieldValue(value="VP Eng", source="pdl", confidence=0.9),
            company=FieldValue(value="Acme", source="pdl", confidence=0.9),
        )
        assert r.overall_confidence == pytest.approx(0.9)

    def test_overall_confidence_mixed(self):
        r = EnrichmentResult(
            email="test@example.com",
            industry=FieldValue(value="tech", source="pdl", confidence=0.9),
            company_size=FieldValue(),  # source=none, excluded
            seniority=FieldValue(value="vp", source="regex", confidence=0.3),
        )
        # Only industry and seniority are resolved → (0.9 + 0.3) / 2
        assert r.overall_confidence == pytest.approx(0.6)

    def test_to_flat_dict_format(self):
        r = EnrichmentResult(
            email="test@acme.com",
            industry=FieldValue(value="financial_services", source="fixture", confidence=1.0),
            company_size=FieldValue(value="enterprise", source="fixture", confidence=1.0),
            seniority=FieldValue(value="vp", source="fixture", confidence=1.0),
            role=FieldValue(value="VP Sales", source="fixture", confidence=1.0),
            company=FieldValue(value="Acme Corp", source="fixture", confidence=1.0),
            is_business_email=True,
        )
        flat = r.to_flat_dict()
        assert flat["email"] == "test@acme.com"
        assert flat["industry"] == "financial_services"
        assert flat["company_size"] == "enterprise"
        assert flat["seniority"] == "vp"
        assert flat["is_business_email"] is True
        assert flat["confidence"] == 1.0
        assert flat["source"] == "fixture"
        assert flat["field_sources"]["industry"] == "fixture"

    def test_to_flat_dict_unknown_defaults(self):
        r = EnrichmentResult(email="test@example.com")
        flat = r.to_flat_dict()
        assert flat["industry"] == "unknown"
        assert flat["company_size"] == "unknown"
        assert flat["seniority"] == "unknown"
        assert flat["confidence"] == 0.0
        assert flat["source"] == "none"


# ── EnrichmentProvider ABC tests ───────────────────────────────────────────────


class TestEnrichmentProviderABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            EnrichmentProvider()  # type: ignore[abstract]

    def test_concrete_implementation(self):
        class DummyProvider(EnrichmentProvider):
            def enrich(self, email, name, company, message):
                return EnrichmentResult(email=email)

        provider = DummyProvider()
        result = provider.enrich("a@b.com", "", "", "")
        assert isinstance(result, EnrichmentResult)
        assert result.email == "a@b.com"


# ── FixtureProvider tests ──────────────────────────────────────────────────────


_FIXTURE_DATA = {
    "j.martinez@acmefintech.com": {
        "industry": "financial_services",
        "company_size": "enterprise",
        "seniority": "vp",
        "role": "VP of Sales",
        "company": "Acme Fintech International",
    },
    "randomuser123@gmail.com": {
        "industry": "",
        "company_size": "",
        "seniority": "",
        "role": "",
    },
}


class TestFixtureProviderFromDict:
    def test_known_email_returns_fixture_data(self):
        provider = FixtureProvider(_FIXTURE_DATA)
        result = provider.enrich("j.martinez@acmefintech.com", "Julia Martinez", "Acme", "demo")

        assert result.email == "j.martinez@acmefintech.com"
        assert result.industry.value == "financial_services"
        assert result.industry.source == "fixture"
        assert result.industry.confidence == 1.0
        assert result.company_size.value == "enterprise"
        assert result.seniority.value == "vp"
        assert result.role.value == "VP of Sales"
        assert result.company.value == "Acme Fintech International"
        assert result.is_business_email is True

    def test_unknown_email_returns_empty(self):
        provider = FixtureProvider(_FIXTURE_DATA)
        result = provider.enrich("nobody@unknown.com", "", "", "")

        assert result.email == "nobody@unknown.com"
        assert result.industry.source == "none"
        assert result.industry.confidence == 0.0
        assert result.overall_confidence == 0.0
        assert result.is_business_email is True  # unknown.com is not free/disposable

    def test_free_email_detected(self):
        provider = FixtureProvider(_FIXTURE_DATA)
        result = provider.enrich("randomuser123@gmail.com", "", "", "")

        assert result.is_business_email is False
        # Fixture has empty strings for all fields → FieldValue with source="none"
        assert result.industry.source == "none"

    def test_case_insensitive(self):
        provider = FixtureProvider(_FIXTURE_DATA)
        result = provider.enrich("J.Martinez@AcmeFintech.com", "Julia", "Acme", "hi")
        assert result.industry.value == "financial_services"

    def test_overall_confidence_from_fixture(self):
        provider = FixtureProvider(_FIXTURE_DATA)
        result = provider.enrich("j.martinez@acmefintech.com", "", "", "")
        # All 5 fields present → all confidence=1.0 → overall=1.0
        assert result.overall_confidence == 1.0

    def test_to_flat_dict_backward_compatible(self):
        provider = FixtureProvider(_FIXTURE_DATA)
        result = provider.enrich("j.martinez@acmefintech.com", "", "Acme", "")
        flat = result.to_flat_dict()
        # Must have all keys the existing scoring tool expects
        assert "email" in flat
        assert "industry" in flat
        assert "company_size" in flat
        assert "seniority" in flat
        assert "is_business_email" in flat
        assert "confidence" in flat
        assert "source" in flat
        assert "field_sources" in flat


class TestFixtureProviderFromFile:
    def test_load_from_json_file(self, tmp_path: Path):
        fixture_file = tmp_path / "fixtures.json"
        fixture_file.write_text(json.dumps(_FIXTURE_DATA))

        provider = FixtureProvider(fixture_file)
        result = provider.enrich("j.martinez@acmefintech.com", "", "", "")
        assert result.industry.value == "financial_services"

    def test_load_from_string_path(self, tmp_path: Path):
        fixture_file = tmp_path / "fixtures.json"
        fixture_file.write_text(json.dumps(_FIXTURE_DATA))

        provider = FixtureProvider(str(fixture_file))
        result = provider.enrich("j.martinez@acmefintech.com", "", "", "")
        assert result.industry.value == "financial_services"
