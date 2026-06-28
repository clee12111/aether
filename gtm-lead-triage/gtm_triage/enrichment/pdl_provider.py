"""PDL (People Data Labs) enrichment provider.

Calls the PDL Person Enrichment API (v5) via raw httpx. Maps the response
to EnrichmentResult with per-field provenance. Confidence is derived from
PDL's own `likelihood` score (1-10), not a flat constant.

API docs: https://docs.peopledatalabs.com/docs/person-enrichment-api

Reads PDL_API_KEY from env. Never hardcodes the key.
Free tier: 100 calls/month — use the response cache to protect credits.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx

from gtm_triage.enrichment.base import EnrichmentProvider, EnrichmentResult, FieldValue

logger = logging.getLogger(__name__)

_PDL_ENRICH_URL = "https://api.peopledatalabs.com/v5/person/enrich"

# PDL company_size ranges → our enum
_SIZE_MAP: dict[str, str] = {
    "1-10": "smb",
    "11-50": "smb",
    "51-200": "mid_market",
    "201-500": "mid_market",
    "501-1000": "enterprise",
    "1001-5000": "enterprise",
    "5001-10000": "enterprise",
    "10001+": "enterprise",
}

# PDL job_title_levels → our seniority enum
_SENIORITY_MAP: dict[str, str] = {
    "cxo": "c_level",
    "owner": "c_level",
    "partner": "c_level",
    "vp": "vp",
    "director": "director",
    "manager": "manager",
    "senior": "ic",
    "entry": "ic",
    "training": "ic",
    "unpaid": "ic",
}

# PDL industry strings → our industry enum (partial map; unmapped → use raw value)
_INDUSTRY_MAP: dict[str, str] = {
    "financial services": "financial_services",
    "banking": "financial_services",
    "insurance": "financial_services",
    "capital markets": "financial_services",
    "investment management": "financial_services",
    "venture capital & private equity": "financial_services",
    "hospital & health care": "healthcare",
    "health, wellness and fitness": "healthcare",
    "medical devices": "healthcare",
    "pharmaceuticals": "healthcare",
    "biotechnology": "healthcare",
    "computer software": "technology",
    "information technology and services": "technology",
    "internet": "technology",
    "computer & network security": "technology",
    "semiconductors": "technology",
    "telecommunications": "technology",
    "management consulting": "consulting",
    "professional training & coaching": "consulting",
    "retail": "retail",
    "consumer goods": "retail",
    "higher education": "education",
    "education management": "education",
    "e-learning": "education",
}


def _likelihood_to_confidence(likelihood: int, field_present: bool) -> float:
    """Convert PDL likelihood (1-10) to a field confidence score.

    PDL likelihood indicates how confident PDL is that the returned record
    matches the queried person. We combine this with whether the specific
    field was actually returned (not null).

    Scale:
      likelihood 9-10 + field present → 0.95
      likelihood 7-8  + field present → 0.85
      likelihood 5-6  + field present → 0.70
      likelihood 3-4  + field present → 0.50
      likelihood 1-2  + field present → 0.35
      any likelihood + field absent   → 0.0
    """
    if not field_present:
        return 0.0
    if likelihood >= 9:
        return 0.95
    if likelihood >= 7:
        return 0.85
    if likelihood >= 5:
        return 0.70
    if likelihood >= 3:
        return 0.50
    return 0.35


def _map_industry(pdl_industry: str | None) -> str:
    if not pdl_industry:
        return ""
    return _INDUSTRY_MAP.get(pdl_industry.lower(), pdl_industry.lower())


def _map_company_size(pdl_size: str | None) -> str:
    if not pdl_size:
        return ""
    return _SIZE_MAP.get(pdl_size, "")


def _map_seniority(title_levels: list[str] | None) -> str:
    if not title_levels:
        return ""
    for level in title_levels:
        mapped = _SENIORITY_MAP.get(level.lower())
        if mapped:
            return mapped
    return ""


class PDLProvider(EnrichmentProvider):
    """People Data Labs Person Enrichment provider.

    Uses the response cache (disk-based JSON) to avoid redundant API calls.
    Cache is checked before every live call. Responses are written back on miss.

    Args:
        api_key: PDL API key. If empty, reads from PDL_API_KEY env var.
        cache_path: Path to the JSON cache file. If None, caching is disabled.
        client: Optional httpx.Client for testing/injection.
    """

    def __init__(
        self,
        api_key: str = "",
        cache_path: str | Path | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = (api_key or os.environ.get("PDL_API_KEY", "")).strip()
        self._client = client
        self._owns_client = client is None

        # Disk cache: email → {status_code, body}
        self._cache_path = Path(cache_path) if cache_path else None
        self._cache: dict[str, dict] = {}
        if self._cache_path and self._cache_path.exists():
            with open(self._cache_path) as f:
                raw = json.load(f)
                # Skip _meta key
                self._cache = {k: v for k, v in raw.items() if not k.startswith("_")}

        # In-memory dedup for this session
        self._session_cache: dict[str, EnrichmentResult] = {}

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=15.0)
        return self._client

    def _save_cache(self) -> None:
        if not self._cache_path:
            return
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._cache_path, "w") as f:
            json.dump(self._cache, f, indent=2)

    def enrich(self, email: str, name: str, company: str, message: str) -> EnrichmentResult:
        email_lower = email.strip().lower()

        # Session dedup
        if email_lower in self._session_cache:
            return self._session_cache[email_lower]

        # Check disk cache
        cached = self._cache.get(email_lower)
        if cached is not None:
            result = self._parse_response(email_lower, cached["status_code"], cached["body"])
            self._session_cache[email_lower] = result
            return result

        # Live API call — requires key
        if not self._api_key:
            logger.warning("PDL_API_KEY not set; returning empty enrichment for %s", email_lower)
            result = EnrichmentResult(email=email_lower)
            self._session_cache[email_lower] = result
            return result

        try:
            client = self._get_client()
            resp = client.get(
                _PDL_ENRICH_URL,
                params={"api_key": self._api_key, "email": email_lower},
            )
            response_data = {
                "status_code": resp.status_code,
                "body": resp.json(),
            }
            # Write to disk cache
            self._cache[email_lower] = response_data
            self._save_cache()

            result = self._parse_response(email_lower, resp.status_code, resp.json())
        except Exception as exc:
            logger.warning("PDL API call failed for %s: %s", email_lower, exc)
            result = EnrichmentResult(email=email_lower)

        self._session_cache[email_lower] = result
        return result

    def _parse_response(self, email: str, status_code: int, body: dict) -> EnrichmentResult:
        if status_code != 200 or "data" not in body:
            return EnrichmentResult(email=email)

        data = body["data"]
        likelihood = body.get("likelihood", 0)

        # Map fields
        raw_industry = data.get("job_company_industry") or data.get("industry")
        industry_val = _map_industry(raw_industry)
        industry_present = bool(industry_val)

        raw_size = data.get("job_company_size")
        size_val = _map_company_size(raw_size)
        size_present = bool(size_val)

        raw_levels = data.get("job_title_levels")
        seniority_val = _map_seniority(raw_levels)
        seniority_present = bool(seniority_val)

        raw_title = data.get("job_title")
        role_present = bool(raw_title)

        raw_company = data.get("job_company_name")
        company_present = bool(raw_company)

        return EnrichmentResult(
            email=email,
            industry=FieldValue(
                value=industry_val,
                source="pdl" if industry_present else "none",
                confidence=_likelihood_to_confidence(likelihood, industry_present),
            ),
            company_size=FieldValue(
                value=size_val,
                source="pdl" if size_present else "none",
                confidence=_likelihood_to_confidence(likelihood, size_present),
            ),
            seniority=FieldValue(
                value=seniority_val,
                source="pdl" if seniority_present else "none",
                confidence=_likelihood_to_confidence(likelihood, seniority_present),
            ),
            role=FieldValue(
                value=raw_title or "",
                source="pdl" if role_present else "none",
                confidence=_likelihood_to_confidence(likelihood, role_present),
            ),
            company=FieldValue(
                value=raw_company or "",
                source="pdl" if company_present else "none",
                confidence=_likelihood_to_confidence(likelihood, company_present),
            ),
            is_business_email=bool(data.get("work_email")),
        )

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None
