"""Enrichment provider interface and result model.

Every enrichment field carries its own source and confidence so downstream
consumers (scoring, audit) can reason about data quality per-field rather
than trusting a single aggregate number.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, Field


class FieldValue(BaseModel):
    """A single enrichment field with provenance."""

    value: str = ""
    source: Literal["pdl", "dns", "llm_fallback", "regex", "crm", "fixture", "website", "none"] = "none"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class EnrichmentResult(BaseModel):
    """Structured enrichment output with per-field provenance.

    Confidence is derived from the actual source quality:
      - External provider (pdl): 0.9 if field present, 0.0 if absent
      - CRM:                     0.85 (trusted internal data)
      - DNS/MX:                  0.95 for email validity (binary signal)
      - LLM fallback:            0.4 (educated guess, not verified)
      - Regex:                   0.3 (keyword heuristic)
      - Fixture:                 1.0 (test fixture, deterministic)
      - None:                    0.0 (field not resolved)
    """

    email: str
    industry: FieldValue = Field(default_factory=FieldValue)
    company_size: FieldValue = Field(default_factory=FieldValue)
    seniority: FieldValue = Field(default_factory=FieldValue)
    role: FieldValue = Field(default_factory=FieldValue)
    company: FieldValue = Field(default_factory=FieldValue)
    is_business_email: bool = False

    @property
    def overall_confidence(self) -> float:
        """Weighted average of resolved fields. Fields with source='none' are excluded."""
        fields = [self.industry, self.company_size, self.seniority, self.role, self.company]
        resolved = [f for f in fields if f.source != "none"]
        if not resolved:
            return 0.0
        return sum(f.confidence for f in resolved) / len(resolved)

    def to_flat_dict(self) -> dict:
        """Flatten to the dict format the existing scoring/loop code expects."""
        return {
            "email": self.email,
            "company": self.company.value,
            "industry": self.industry.value or "unknown",
            "company_size": self.company_size.value or "unknown",
            "role": self.role.value,
            "seniority": self.seniority.value or "unknown",
            "is_business_email": self.is_business_email,
            "confidence": round(self.overall_confidence, 2),
            "source": self._dominant_source(),
            "field_sources": {
                "industry": self.industry.source,
                "company_size": self.company_size.source,
                "seniority": self.seniority.source,
            },
        }

    def _dominant_source(self) -> str:
        sources = [
            self.industry.source,
            self.company_size.source,
            self.seniority.source,
        ]
        real = [s for s in sources if s != "none"]
        if not real:
            return "none"
        # Pick the highest-quality source present
        priority = ["pdl", "crm", "dns", "fixture", "llm_fallback", "regex"]
        for p in priority:
            if p in real:
                return p
        return real[0]


class EnrichmentProvider(ABC):
    """Interface for enrichment data providers.

    Implementations: FixtureProvider (CI), MockProvider (regex, legacy),
    PDLProvider (Phase B). All return EnrichmentResult with per-field provenance.
    """

    @abstractmethod
    def enrich(self, email: str, name: str, company: str, message: str) -> EnrichmentResult:
        """Enrich a lead. Must not raise — return empty fields with source='none' on failure."""
        ...
