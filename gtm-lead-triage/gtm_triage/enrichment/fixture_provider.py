"""Fixture-based enrichment provider for deterministic CI tests.

Reads pre-recorded JSON fixtures keyed by email. Returns exact data with
source='fixture' and confidence=1.0 for every present field. Emails not
in the fixture return empty fields with source='none'.

Fixture format (JSON file):
{
  "j.martinez@acmefintech.com": {
    "industry": "financial_services",
    "company_size": "enterprise",
    "seniority": "vp",
    "role": "VP of Sales",
    "company": "Acme Fintech International"
  },
  ...
}
"""

from __future__ import annotations

import json
from pathlib import Path

from gtm_triage.enrichment.base import EnrichmentProvider, EnrichmentResult, FieldValue


class FixtureProvider(EnrichmentProvider):
    """Deterministic enrichment from a JSON fixture file or dict.

    For CI: no network calls, no API keys, fully reproducible.
    """

    def __init__(self, fixture: dict[str, dict] | str | Path) -> None:
        if isinstance(fixture, (str, Path)):
            with open(fixture) as f:
                self._data: dict[str, dict] = json.load(f)
        else:
            self._data = fixture

    def enrich(self, email: str, name: str, company: str, message: str) -> EnrichmentResult:
        email_lower = email.strip().lower()
        record = self._data.get(email_lower, {})

        if not record:
            return EnrichmentResult(
                email=email_lower,
                is_business_email=self._is_business(email_lower),
            )

        def _field(key: str) -> FieldValue:
            val = record.get(key, "")
            if val:
                return FieldValue(value=val, source="fixture", confidence=1.0)
            return FieldValue()

        return EnrichmentResult(
            email=email_lower,
            industry=_field("industry"),
            company_size=_field("company_size"),
            seniority=_field("seniority"),
            role=_field("role"),
            company=_field("company") if record.get("company") else FieldValue(value=company, source="none", confidence=0.0),
            is_business_email=self._is_business(email_lower),
        )

    @staticmethod
    def _is_business(email: str) -> bool:
        from gtm_triage.enrichment.email_signal import FREE_DOMAINS, DISPOSABLE_DOMAINS
        if "@" not in email:
            return False
        domain = email.rsplit("@", 1)[1].lower()
        return domain not in FREE_DOMAINS and domain not in DISPOSABLE_DOMAINS and domain != ""
