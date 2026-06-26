"""Unit tests for WaterfallProvider — all offline, zero network calls."""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_triage.enrichment.base import EnrichmentResult, FieldValue
from gtm_triage.enrichment.pdl_provider import PDLProvider
from gtm_triage.enrichment.waterfall import WaterfallProvider

_CASSETTES_PATH = Path(__file__).resolve().parent.parent / "gtm_triage" / "enrichment" / "cache" / "pdl_cassettes.json"


@pytest.fixture
def pdl() -> PDLProvider:
    return PDLProvider(api_key="", cache_path=_CASSETTES_PATH)


@pytest.fixture
def waterfall(pdl: PDLProvider) -> WaterfallProvider:
    """Waterfall with DNS and website fallback both skipped (offline)."""
    return WaterfallProvider(pdl, skip_dns=True, skip_website=True)


# ── Short-circuit on invalid/disposable email ──────────────────────────────────


class TestShortCircuit:
    def test_disposable_email_short_circuits(self, waterfall: WaterfallProvider):
        """Disposable email → empty result, no PDL call."""
        result = waterfall.enrich("test@mailinator.com", "", "", "")
        assert result.is_business_email is False
        assert result.industry.source == "none"
        assert result.overall_confidence == 0.0

    def test_invalid_syntax_short_circuits(self, waterfall: WaterfallProvider):
        result = waterfall.enrich("not-an-email", "", "", "")
        assert result.is_business_email is False
        assert result.overall_confidence == 0.0

    def test_empty_email_short_circuits(self, waterfall: WaterfallProvider):
        result = waterfall.enrich("", "", "", "")
        assert result.overall_confidence == 0.0


# ── PDL hit flows through ─────────────────────────────────────────────────────


class TestPDLHit:
    def test_high_confidence_pdl_hit(self, waterfall: WaterfallProvider):
        """PDL hit with good data → result comes through with correct source."""
        result = waterfall.enrich("j.martinez@acmefintech.com", "Julia", "Acme", "demo")
        assert result.industry.value == "financial_services"
        assert result.industry.source == "pdl"
        assert result.industry.confidence == 0.85
        assert result.is_business_email is True

    def test_business_email_detection(self, waterfall: WaterfallProvider):
        """Business email correctly identified by email signal, not PDL."""
        result = waterfall.enrich("s.chen@medvista.com", "", "", "")
        assert result.is_business_email is True

    def test_free_email_marked(self, waterfall: WaterfallProvider):
        """Free email → is_business_email=False, PDL still runs (may have data)."""
        result = waterfall.enrich("randomuser123@gmail.com", "", "", "")
        assert result.is_business_email is False


# ── PDL miss ───────────────────────────────────────────────────────────────────


class TestPDLMiss:
    def test_pdl_miss_returns_empty_when_website_skipped(self, waterfall: WaterfallProvider):
        """PDL 404 + no website fallback → empty result."""
        result = waterfall.enrich("test@gmail.com", "Test", "", "")
        assert result.industry.source == "none"
        assert result.overall_confidence == 0.0


# ── Merge / conflict handling ──────────────────────────────────────────────────


class TestMerge:
    def test_merge_primary_wins_on_conflict(self):
        """When both sources have a field, higher-confidence (primary) wins."""
        primary = EnrichmentResult(
            email="test@example.com",
            industry=FieldValue(value="technology", source="pdl", confidence=0.85),
        )
        secondary = EnrichmentResult(
            email="test@example.com",
            industry=FieldValue(value="consulting", source="website", confidence=0.45),
        )
        merged = WaterfallProvider._merge(primary, secondary)
        assert merged.industry.value == "technology"
        assert merged.industry.source == "pdl"
        # Conflict → confidence reduced by 0.05
        assert merged.industry.confidence == pytest.approx(0.80)

    def test_merge_secondary_fills_gaps(self):
        """When primary is empty, secondary fills the gap."""
        primary = EnrichmentResult(
            email="test@example.com",
            industry=FieldValue(),  # empty
            company_size=FieldValue(value="enterprise", source="pdl", confidence=0.85),
        )
        secondary = EnrichmentResult(
            email="test@example.com",
            industry=FieldValue(value="healthcare", source="website", confidence=0.45),
            company_size=FieldValue(),  # empty
        )
        merged = WaterfallProvider._merge(primary, secondary)
        assert merged.industry.value == "healthcare"
        assert merged.industry.source == "website"
        assert merged.industry.confidence == 0.45
        assert merged.company_size.value == "enterprise"
        assert merged.company_size.source == "pdl"

    def test_merge_no_conflict_same_value(self):
        """Same value from both sources → no conflict penalty."""
        primary = EnrichmentResult(
            email="test@example.com",
            industry=FieldValue(value="technology", source="pdl", confidence=0.85),
        )
        secondary = EnrichmentResult(
            email="test@example.com",
            industry=FieldValue(value="technology", source="website", confidence=0.45),
        )
        merged = WaterfallProvider._merge(primary, secondary)
        assert merged.industry.value == "technology"
        assert merged.industry.confidence == 0.85  # no penalty

    def test_merge_both_empty(self):
        """Both empty → stays empty."""
        primary = EnrichmentResult(email="test@example.com")
        secondary = EnrichmentResult(email="test@example.com")
        merged = WaterfallProvider._merge(primary, secondary)
        assert merged.industry.source == "none"

    def test_conflict_reduces_confidence(self):
        """Disagreeing values reduce confidence to signal uncertainty."""
        primary = EnrichmentResult(
            email="test@example.com",
            company_size=FieldValue(value="enterprise", source="pdl", confidence=0.70),
        )
        secondary = EnrichmentResult(
            email="test@example.com",
            company_size=FieldValue(value="mid_market", source="website", confidence=0.40),
        )
        merged = WaterfallProvider._merge(primary, secondary)
        assert merged.company_size.value == "enterprise"  # PDL wins
        assert merged.company_size.confidence == pytest.approx(0.65)  # reduced


# ── Confidence variation across the waterfall ──────────────────────────────────


class TestConfidenceVariation:
    def test_confidence_varies_with_pdl_likelihood(self, waterfall: WaterfallProvider):
        """Different leads should have different confidence levels."""
        high = waterfall.enrich("s.chen@medvista.com", "", "", "")       # likelihood=9
        med = waterfall.enrich("mark.chen@cloudtechgroup.com", "", "", "")  # likelihood=7
        low = waterfall.enrich("r.thompson@globalbank.com", "", "", "")  # likelihood=3

        # High likelihood → highest confidence
        assert high.industry.confidence > med.industry.confidence
        assert med.industry.confidence > low.industry.confidence

    def test_missing_fields_have_zero_confidence(self, waterfall: WaterfallProvider):
        """PDL hit with null fields → those fields have confidence=0."""
        # r.thompson has no job_title_levels → seniority missing
        result = waterfall.enrich("r.thompson@globalbank.com", "", "", "")
        assert result.seniority.confidence == 0.0
        assert result.industry.confidence > 0  # industry IS present


# ── Full waterfall flow ────────────────────────────────────────────────────────


class TestFullFlow:
    def test_waterfall_produces_flat_dict(self, waterfall: WaterfallProvider):
        """Verify the waterfall result is backward-compatible with scoring."""
        result = waterfall.enrich("j.martinez@acmefintech.com", "Julia", "Acme", "demo")
        flat = result.to_flat_dict()
        assert flat["industry"] == "financial_services"
        assert flat["company_size"] == "enterprise"
        assert flat["seniority"] == "vp"
        assert flat["is_business_email"] is True
        assert flat["source"] == "pdl"
        assert 0.0 < flat["confidence"] <= 1.0

    def test_waterfall_preserves_email(self, waterfall: WaterfallProvider):
        result = waterfall.enrich("s.chen@medvista.com", "", "", "")
        assert result.email == "s.chen@medvista.com"
