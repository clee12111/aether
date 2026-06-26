"""Lead enrichment tool with regex + optional LLM fallback.

When provider="mock" (default): regex-only, fully deterministic.
When provider="openai": regex first, then LLM fills in any "unknown" fields.
Per-field source is recorded: "regex" or "llm".
"""

from __future__ import annotations

import logging
import re

from gtm_triage.tools.base import BaseTool

logger = logging.getLogger(__name__)

# Free-email domains — leads from these are capped at warm
FREE_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "aol.com", "icloud.com", "mail.com", "protonmail.com",
}

# Industry inference from domain/company keywords.
_INDUSTRY_KEYWORDS: list[tuple[str, str]] = [
    ("fintech", "financial_services"),
    ("financial", "financial_services"),
    ("banking", "financial_services"),
    ("capital", "financial_services"),
    ("healthcare", "healthcare"),
    ("pharma", "healthcare"),
    ("biotech", "healthcare"),
    ("medical", "healthcare"),
    ("software", "technology"),
    ("cloud", "technology"),
    ("saas", "technology"),
    ("tech", "technology"),
    ("consulting", "consulting"),
    ("advisory", "consulting"),
    ("retail", "retail"),
    ("ecommerce", "retail"),
    ("commerce", "retail"),
    ("university", "education"),
    ("school", "education"),
    ("academy", "education"),
]

_SIZE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("enterprise", ["global", "international", "fortune", "corporation", "worldwide"]),
    ("mid_market", ["group", "partners", "solutions", "services"]),
    ("smb", ["studio", "freelance", "solo", "startup", "llc"]),
]

_SENIORITY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("c_level", ["ceo", "cto", "cfo", "coo", "cmo", "chief", "founder", "co-founder"]),
    ("vp", ["vp", "vice president"]),
    ("director", ["director", "head of"]),
    ("manager", ["manager", "team lead"]),
    ("ic", ["engineer", "analyst", "developer", "associate", "intern", "student"]),
]


def _word_match(text: str, keyword: str) -> bool:
    return bool(re.search(r"\b" + re.escape(keyword) + r"\b", text))


def _infer_industry(company: str, domain: str) -> str:
    text = (company + " " + domain).lower()
    for keyword, industry in _INDUSTRY_KEYWORDS:
        if _word_match(text, keyword):
            return industry
    return "unknown"


def _infer_company_size(company: str, message: str) -> str:
    text = (company + " " + message).lower()
    for size, keywords in _SIZE_KEYWORDS:
        for kw in keywords:
            if _word_match(text, kw):
                return size
    return "unknown"


def _infer_seniority(name: str, message: str) -> tuple[str, str]:
    text = (name + " " + message).lower()
    for seniority, keywords in _SENIORITY_KEYWORDS:
        for kw in keywords:
            if _word_match(text, kw):
                return seniority, kw
    return "unknown", ""


class EnrichLeadTool(BaseTool):
    def __init__(self, provider: str = "mock", model: str = "gpt-4o-mini") -> None:
        self._provider = provider
        self._model = model

    @property
    def name(self) -> str:
        return "enrich_lead"

    def run(self, args: dict) -> dict:
        email = args.get("email", "")
        company = args.get("company", "")
        name = args.get("name", "")
        message = args.get("message", "")

        domain = email.split("@")[-1].lower() if "@" in email else ""
        is_business = domain not in FREE_DOMAINS and domain != ""

        industry = _infer_industry(company, domain)
        company_size = _infer_company_size(company, message)
        seniority, role = _infer_seniority(name, message)

        # Track source per field
        sources = {
            "industry": "regex" if industry != "unknown" else "unknown",
            "company_size": "regex" if company_size != "unknown" else "unknown",
            "seniority": "regex" if seniority != "unknown" else "unknown",
        }

        # LLM fallback for unknown fields (openai provider only)
        llm_tokens_in = 0
        llm_tokens_out = 0
        if self._provider == "openai":
            unknown_fields = [f for f in ("industry", "company_size", "seniority") if sources[f] == "unknown"]
            if unknown_fields:
                from gtm_triage.agents.llm_client import infer_enrichment
                inferred, llm_tokens_in, llm_tokens_out = infer_enrichment(
                    email=email, name=name, company=company, message=message,
                    unknown_fields=unknown_fields, model=self._model,
                )
                if inferred:
                    if "industry" in unknown_fields and inferred.get("industry", "unknown") != "unknown":
                        industry = inferred["industry"]
                        sources["industry"] = "llm"
                    if "company_size" in unknown_fields and inferred.get("company_size", "unknown") != "unknown":
                        company_size = inferred["company_size"]
                        sources["company_size"] = "llm"
                    if "seniority" in unknown_fields and inferred.get("seniority", "unknown") != "unknown":
                        seniority = inferred["seniority"]
                        sources["seniority"] = "llm"
                    if "role" in inferred and not role:
                        role = inferred["role"]

        confidence = 0.5
        if is_business:
            confidence += 0.2
        if industry != "unknown":
            confidence += 0.1
        if company_size != "unknown":
            confidence += 0.1
        if seniority != "unknown":
            confidence += 0.1

        return {
            "email": email,
            "company": company,
            "industry": industry,
            "company_size": company_size,
            "role": role,
            "seniority": seniority,
            "is_business_email": is_business,
            "confidence": round(confidence, 2),
            "source": "regex" if self._provider == "mock" else "regex+llm",
            "field_sources": sources,
            "llm_tokens_in": llm_tokens_in,
            "llm_tokens_out": llm_tokens_out,
        }
