"""Unit tests for PDLProvider — all against committed cassettes, zero network calls."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gtm_triage.enrichment.pdl_provider import (
    PDLProvider,
    _likelihood_to_confidence,
    _map_company_size,
    _map_industry,
    _map_seniority,
)

_CASSETTES_PATH = Path(__file__).resolve().parent.parent / "gtm_triage" / "enrichment" / "cache" / "pdl_cassettes.json"


@pytest.fixture
def provider() -> PDLProvider:
    """PDLProvider backed by committed cassettes — no API key, no network."""
    return PDLProvider(api_key="", cache_path=_CASSETTES_PATH)


# ── Confidence varies with likelihood ──────────────────────────────────────────


class TestLikelihoodToConfidence:
    def test_high_likelihood_present(self):
        assert _likelihood_to_confidence(9, True) == 0.95

    def test_medium_likelihood_present(self):
        assert _likelihood_to_confidence(7, True) == 0.85

    def test_moderate_likelihood_present(self):
        assert _likelihood_to_confidence(5, True) == 0.70

    def test_low_likelihood_present(self):
        assert _likelihood_to_confidence(3, True) == 0.50

    def test_very_low_likelihood_present(self):
        assert _likelihood_to_confidence(1, True) == 0.35

    def test_field_absent_any_likelihood(self):
        for lk in (1, 5, 10):
            assert _likelihood_to_confidence(lk, False) == 0.0


# ── Mapping helpers ────────────────────────────────────────────────────────────


class TestMappings:
    def test_industry_financial(self):
        assert _map_industry("financial services") == "financial_services"
        assert _map_industry("banking") == "financial_services"

    def test_industry_healthcare(self):
        assert _map_industry("hospital & health care") == "healthcare"

    def test_industry_tech(self):
        assert _map_industry("computer software") == "technology"
        assert _map_industry("information technology and services") == "technology"

    def test_industry_unknown(self):
        assert _map_industry(None) == ""
        assert _map_industry("") == ""

    def test_industry_unmapped_returns_raw(self):
        assert _map_industry("aerospace") == "aerospace"

    def test_company_size(self):
        assert _map_company_size("1-10") == "smb"
        assert _map_company_size("51-200") == "mid_market"
        assert _map_company_size("501-1000") == "enterprise"
        assert _map_company_size("10001+") == "enterprise"
        assert _map_company_size(None) == ""

    def test_seniority(self):
        assert _map_seniority(["cxo"]) == "c_level"
        assert _map_seniority(["vp"]) == "vp"
        assert _map_seniority(["director"]) == "director"
        assert _map_seniority(["manager"]) == "manager"
        assert _map_seniority(["senior"]) == "ic"
        assert _map_seniority(["entry"]) == "ic"
        assert _map_seniority(None) == ""
        assert _map_seniority([]) == ""


# ── PDLProvider from cassettes ─────────────────────────────────────────────────


class TestPDLProviderCassettes:
    def test_high_likelihood_hit(self, provider: PDLProvider):
        """j.martinez has likelihood=8 → confidence should be 0.85."""
        result = provider.enrich("j.martinez@acmefintech.com", "Julia", "Acme", "demo")
        assert result.email == "j.martinez@acmefintech.com"
        assert result.industry.value == "financial_services"
        assert result.industry.source == "pdl"
        assert result.industry.confidence == 0.85
        assert result.company_size.value == "enterprise"
        assert result.company_size.confidence == 0.85
        assert result.seniority.value == "vp"
        assert result.role.value == "vp of sales"
        assert result.is_business_email is True

    def test_very_high_likelihood(self, provider: PDLProvider):
        """s.chen@medvista has likelihood=9 → confidence should be 0.95."""
        result = provider.enrich("s.chen@medvista.com", "", "", "")
        assert result.industry.confidence == 0.95
        assert result.seniority.value == "c_level"
        assert result.seniority.confidence == 0.95

    def test_moderate_likelihood(self, provider: PDLProvider):
        """sean@peopledatalabs has likelihood=6 → confidence should be 0.70."""
        result = provider.enrich("sean@peopledatalabs.com", "", "", "")
        assert result.industry.confidence == 0.70
        assert result.seniority.value == "c_level"  # cxo → c_level

    def test_low_likelihood_partial_data(self, provider: PDLProvider):
        """r.thompson@globalbank has likelihood=3, missing title → mixed confidence."""
        result = provider.enrich("r.thompson@globalbank.com", "", "", "")
        assert result.industry.value == "financial_services"
        assert result.industry.confidence == 0.50
        assert result.seniority.value == ""  # no title_levels
        assert result.seniority.confidence == 0.0
        assert result.company_size.value == "enterprise"
        assert result.company_size.confidence == 0.50

    def test_pdl_miss_404(self, provider: PDLProvider):
        """randomuser123@gmail.com → 404, all fields empty."""
        result = provider.enrich("randomuser123@gmail.com", "", "", "")
        assert result.industry.source == "none"
        assert result.overall_confidence == 0.0

    def test_confidence_varies_across_leads(self, provider: PDLProvider):
        """Verify confidence is NOT a flat constant — it varies with likelihood."""
        high = provider.enrich("s.chen@medvista.com", "", "", "")  # likelihood=9
        med = provider.enrich("mark.chen@cloudtechgroup.com", "", "", "")  # likelihood=7
        low = provider.enrich("alex.kumar@smallstartup.io", "", "", "")  # likelihood=4

        assert high.industry.confidence > med.industry.confidence
        assert med.industry.confidence > low.overall_confidence

    def test_session_cache_dedup(self, provider: PDLProvider):
        """Same email twice returns the same object (session cache)."""
        r1 = provider.enrich("j.martinez@acmefintech.com", "", "", "")
        r2 = provider.enrich("j.martinez@acmefintech.com", "", "", "")
        assert r1 is r2

    def test_case_insensitive(self, provider: PDLProvider):
        result = provider.enrich("J.Martinez@AcmeFintech.com", "", "", "")
        assert result.industry.value == "financial_services"

    def test_no_api_key_no_cache_returns_empty(self):
        """No key + no cache → empty result, no crash."""
        p = PDLProvider(api_key="", cache_path=None)
        result = p.enrich("someone@example.com", "", "", "")
        assert result.overall_confidence == 0.0

    def test_flat_dict_backward_compat(self, provider: PDLProvider):
        result = provider.enrich("j.martinez@acmefintech.com", "", "", "")
        flat = result.to_flat_dict()
        assert flat["industry"] == "financial_services"
        assert flat["source"] == "pdl"
        assert flat["confidence"] == pytest.approx(0.85)
        assert flat["field_sources"]["industry"] == "pdl"


class TestPDLProviderDiskCache:
    def test_writes_to_cache_on_miss(self, tmp_path: Path):
        """When a live call returns data, it's written to the cache file."""
        cache_file = tmp_path / "cache.json"
        cache_file.write_text("{}")

        # Mock the httpx client to simulate a PDL response
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": 200,
            "likelihood": 8,
            "data": {
                "job_title": "CEO",
                "job_title_levels": ["cxo"],
                "job_company_name": "TestCo",
                "job_company_size": "51-200",
                "job_company_industry": "computer software",
                "industry": "computer software",
                "work_email": True,
            },
        }
        mock_client.get.return_value = mock_resp

        p = PDLProvider(api_key="fake-key", cache_path=cache_file, client=mock_client)
        result = p.enrich("new@testco.com", "", "", "")

        assert result.industry.value == "technology"
        assert result.industry.confidence == 0.85

        # Verify cache file was written
        cached = json.loads(cache_file.read_text())
        assert "new@testco.com" in cached
        assert cached["new@testco.com"]["status_code"] == 200

    def test_reads_from_cache_no_api_call(self, tmp_path: Path):
        """Cache hit → no API call made."""
        cache_file = tmp_path / "cache.json"
        cache_data = {
            "cached@test.com": {
                "status_code": 200,
                "body": {
                    "status": 200,
                    "likelihood": 7,
                    "data": {
                        "job_title": "Manager",
                        "job_title_levels": ["manager"],
                        "job_company_name": "CacheCo",
                        "job_company_size": "201-500",
                        "job_company_industry": "retail",
                        "industry": "retail",
                        "work_email": True,
                    },
                },
            }
        }
        cache_file.write_text(json.dumps(cache_data))

        mock_client = MagicMock()
        p = PDLProvider(api_key="fake-key", cache_path=cache_file, client=mock_client)
        result = p.enrich("cached@test.com", "", "", "")

        assert result.seniority.value == "manager"
        mock_client.get.assert_not_called()  # No API call!
