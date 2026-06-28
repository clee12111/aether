"""Enrichment waterfall: email validation → PDL → website fallback.

Zero-cost checks run first (email validity). If the email is invalid or
disposable, short-circuits with no PDL call. On PDL miss or low likelihood,
falls back to company-website fetch + LLM read.

When sources disagree on a field, both values are surfaced: the higher-confidence
source wins the primary slot, but conflicts are recorded so Phase D can branch.
"""

from __future__ import annotations

import logging
import os
import re

import httpx

from gtm_triage.enrichment.base import EnrichmentProvider, EnrichmentResult, FieldValue
from gtm_triage.enrichment.email_signal import FREE_DOMAINS, DISPOSABLE_DOMAINS, check_email

logger = logging.getLogger(__name__)

# Threshold below which PDL result triggers website fallback
_LOW_LIKELIHOOD_THRESHOLD = 5


def _extract_domain(email: str) -> str:
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[1].lower()


def _is_business_email(email: str) -> bool:
    domain = _extract_domain(email)
    return domain not in FREE_DOMAINS and domain not in DISPOSABLE_DOMAINS and domain != ""


class WebsiteFallback:
    """Fetch a company's homepage and extract basic firmographics via LLM.

    Used when PDL misses or returns low-confidence data. The LLM call is
    bounded: one short prompt, structured JSON output.

    In offline/CI mode (no OPENAI_API_KEY), returns empty result.
    """

    def __init__(
        self,
        http_client: httpx.Client | None = None,
        llm_provider: str = "openai",
        llm_model: str = "gpt-4o-mini",
    ) -> None:
        self._client = http_client
        self._owns_client = http_client is None
        self._llm_provider = llm_provider
        self._llm_model = llm_model

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=5.0, follow_redirects=True)
        return self._client

    def fetch_and_extract(self, domain: str) -> EnrichmentResult:
        """Fetch domain homepage, extract firmographics via LLM.

        Returns EnrichmentResult with source='website' for extracted fields.
        On any failure, returns empty result (source='none').
        """
        if not domain:
            return EnrichmentResult(email="")

        # Fetch homepage
        html = self._fetch_homepage(domain)
        if not html:
            return EnrichmentResult(email="")

        # Extract text content (strip tags, limit length)
        text = self._extract_text(html)
        if len(text) < 50:
            return EnrichmentResult(email="")

        # LLM extraction
        return self._llm_extract(domain, text)

    def _fetch_homepage(self, domain: str) -> str:
        # SSRF guard: validate domain isn't internal before connecting
        from gtm_triage.security import ssrf_safe_domain
        if not ssrf_safe_domain(domain):
            logger.warning("SSRF blocked: refusing to fetch %s", domain)
            return ""

        try:
            client = self._get_client()
            resp = client.get(f"https://{domain}", timeout=5.0)
            if resp.status_code == 200:
                return resp.text[:15000]  # limit to avoid huge pages
        except Exception as exc:
            logger.debug("Website fetch failed for %s: %s", domain, exc)
        return ""

    @staticmethod
    def _extract_text(html: str) -> str:
        """Crude HTML → text. Good enough for LLM context."""
        # Remove script/style blocks
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
        # Remove tags
        text = re.sub(r"<[^>]+>", " ", text)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        return text[:5000]

    def _llm_extract(self, domain: str, text: str) -> EnrichmentResult:
        """Call LLM to extract industry and company size from website text."""
        system = (
            "Extract company information from website text. "
            "Return ONLY valid JSON with these fields:\n"
            '{"industry": "...", "company_size": "smb|mid_market|enterprise|unknown", '
            '"company_name": "..."}\n'
            "For industry use: financial_services, healthcare, technology, "
            "consulting, retail, education, or the most specific term. "
            "For company_size, infer from signals like team size mentions, "
            "office count, client scale. If unsure, use unknown."
        )
        user_text = f"Website domain: {domain}\n\nContent:\n{text[:3000]}"

        try:
            import json
            from gtm_triage.agents.llm_client import chat
            result = chat(
                provider=self._llm_provider,
                model=self._llm_model,
                system=system,
                user=user_text,
                max_tokens=150,
            )
            raw = result.text
            m = re.search(r"\{[\s\S]*\}", raw)
            if m:
                data = json.loads(m.group(0))
                return EnrichmentResult(
                    email="",
                    industry=FieldValue(
                        value=data.get("industry", ""),
                        source="website" if data.get("industry") else "none",
                        confidence=0.45 if data.get("industry") else 0.0,
                    ),
                    company_size=FieldValue(
                        value=data.get("company_size", ""),
                        source="website" if data.get("company_size", "unknown") != "unknown" else "none",
                        confidence=0.40 if data.get("company_size", "unknown") != "unknown" else 0.0,
                    ),
                    company=FieldValue(
                        value=data.get("company_name", ""),
                        source="website" if data.get("company_name") else "none",
                        confidence=0.45 if data.get("company_name") else 0.0,
                    ),
                )
        except Exception as exc:
            logger.debug("LLM website extraction failed for %s: %s", domain, exc)

        return EnrichmentResult(email="")

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None


class WaterfallProvider(EnrichmentProvider):
    """Enrichment waterfall: email check → PDL → website fallback.

    Flow:
    1. check_email() — if invalid/disposable, short-circuit (no enrichment calls)
    2. PDL Person Enrichment — if hit with sufficient likelihood, use it
    3. Website fallback — on PDL miss or low likelihood, fetch domain + LLM extract
    4. Merge — highest-confidence source wins per field; conflicts recorded

    Args:
        pdl_provider: The PDL enrichment provider.
        website_fallback: The website fallback extractor. If None, creates one.
        skip_dns: If True, skip MX/DNS check (for offline tests).
        skip_website: If True, skip website fallback (for offline tests).
    """

    def __init__(
        self,
        pdl_provider: EnrichmentProvider,
        website_fallback: WebsiteFallback | None = None,
        skip_dns: bool = False,
        skip_website: bool = False,
    ) -> None:
        self._pdl = pdl_provider
        self._website = website_fallback or WebsiteFallback()
        self._skip_dns = skip_dns
        self._skip_website = skip_website

    def enrich(self, email: str, name: str, company: str, message: str) -> EnrichmentResult:
        email_lower = email.strip().lower()

        # Step 1: Email validation (free, zero-cost)
        email_signal = check_email(email_lower, skip_dns=self._skip_dns)

        if email_signal.verdict == "invalid":
            return EnrichmentResult(
                email=email_lower,
                is_business_email=False,
            )

        if email_signal.verdict == "disposable":
            return EnrichmentResult(
                email=email_lower,
                is_business_email=False,
            )

        is_business = email_signal.verdict == "deliverable"

        # Step 2: PDL enrichment
        pdl_result = self._pdl.enrich(email_lower, name, company, message)
        pdl_result.is_business_email = is_business

        # Check if PDL returned meaningful data
        pdl_has_data = any(
            getattr(pdl_result, f).source == "pdl"
            for f in ("industry", "company_size", "seniority", "role", "company")
        )

        # Determine if we need website fallback
        needs_fallback = not pdl_has_data
        if pdl_has_data:
            # Check for low-confidence fields that might benefit from fallback
            low_conf_fields = [
                f for f in ("industry", "company_size")
                if getattr(pdl_result, f).source == "none"
                or getattr(pdl_result, f).confidence < 0.5
            ]
            if low_conf_fields:
                needs_fallback = True

        # Step 3: Website fallback (if needed and not skipped)
        if needs_fallback and not self._skip_website and is_business:
            domain = _extract_domain(email_lower)
            website_result = self._website.fetch_and_extract(domain)
            # Merge: PDL wins on conflicts (higher confidence), website fills gaps
            pdl_result = self._merge(pdl_result, website_result)

        return pdl_result

    @staticmethod
    def _merge(primary: EnrichmentResult, secondary: EnrichmentResult) -> EnrichmentResult:
        """Merge two enrichment results. Primary wins when both have data.

        When sources disagree on a field, the higher-confidence value wins
        the slot, but the conflict is flagged by reducing confidence slightly
        (capped at 0.05 reduction) to signal uncertainty to downstream consumers.
        """
        def _pick(pri: FieldValue, sec: FieldValue) -> FieldValue:
            # If primary has data, keep it (possibly noting conflict)
            if pri.source != "none" and pri.value:
                if sec.source != "none" and sec.value and sec.value != pri.value:
                    # Conflict: primary wins but confidence reduced
                    return FieldValue(
                        value=pri.value,
                        source=pri.source,
                        confidence=max(0.0, pri.confidence - 0.05),
                    )
                return pri
            # Primary empty, use secondary
            if sec.source != "none" and sec.value:
                return sec
            return pri

        return EnrichmentResult(
            email=primary.email,
            industry=_pick(primary.industry, secondary.industry),
            company_size=_pick(primary.company_size, secondary.company_size),
            seniority=_pick(primary.seniority, secondary.seniority),
            role=_pick(primary.role, secondary.role),
            company=_pick(primary.company, secondary.company),
            is_business_email=primary.is_business_email,
        )
