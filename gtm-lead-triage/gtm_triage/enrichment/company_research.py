"""Company research: domain → cited brief with firmographics, signals, and hypotheses.

CompanyResearcher.research(domain) orchestrates:
  1. PDL → industry, size (fallback when Apollo absent)
  2. Apollo org-enrich → what_they_do, industry, size, funding, tech (primary)
  3. WebsiteFallback → what_they_do (best-effort, when Apollo has no description)
  4. SearchProvider → recent_signals
  5. Productboard demand → is_requester + demand signal
  6. One LLM call → summarize + infer likely_problems

Anti-fabrication: factual fields with no source are None/omitted; likely_problems
are explicitly labeled as inferences (hypotheses), never presented as stated facts.
Demand only when a real feedback id backs it.

Toggle: COMPANY_RESEARCH=off → PDL-only minimal brief (no website/search/apollo/pb),
never error (no-op-without-config discipline).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from pydantic import BaseModel, Field

from gtm_triage.enrichment.base import EnrichmentProvider, EnrichmentResult
from gtm_triage.enrichment.search import SearchProvider, SearchResult, get_search_provider

logger = logging.getLogger(__name__)


# ── Models ──────────────────────────────────────────────────────────────────

class SourcedClaim(BaseModel):
    """A factual claim with its provenance."""
    text: str
    source: str  # "pdl" | "website" | "search:<url>" | "apollo" | "productboard:<id>"


class RecentSignal(BaseModel):
    """A recent company signal extracted from search results."""
    text: str
    source_url: str
    kind: str  # launch | hiring | funding | news | demand | other


class CompanyResearch(BaseModel):
    """Cited company brief — every factual claim attributed to a source."""
    domain: str
    what_they_do: str | None = None      # 1-2 sentences; None if ungrounded
    industry: str | None = None           # from Apollo (primary) or PDL (fallback)
    size: str | None = None               # from Apollo (primary) or PDL (fallback)
    recent_signals: list[RecentSignal] = Field(default_factory=list)
    likely_problems: list[str] = Field(default_factory=list)  # hypotheses, not facts
    inferred_role: str | None = None
    sources: list[SourcedClaim] = Field(default_factory=list)
    confidence: float = 0.0               # derived from how many sources resolved
    tech_stack: list[str] = Field(default_factory=list)       # from Apollo
    is_requester: bool = False                                # from Productboard

    def to_draft_context(self) -> dict[str, Any]:
        """Compact, drafter-ready shape for injection into outreach drafts."""
        return {
            "domain": self.domain,
            "what_they_do": self.what_they_do,
            "industry": self.industry,
            "size": self.size,
            "recent_signals": [
                {"text": s.text, "kind": s.kind, "url": s.source_url}
                for s in self.recent_signals
            ],
            "likely_problems": self.likely_problems,
            "inferred_role": self.inferred_role,
            "confidence": self.confidence,
            "tech_stack": self.tech_stack,
            "is_requester": self.is_requester,
        }


# ── Signal classification ──────────────────────────────────────────────────

_KIND_KEYWORDS: list[tuple[str, list[str]]] = [
    # Order matters: funding before hiring (both match "Raises Series D... Expansion")
    ("funding", ["raises", "raised", "funding", "valuation", "series", "round", "invest"]),
    ("launch", ["launch", "releases", "released", "announces", "announced", "introduces", "ships", "beta", "ga"]),
    ("hiring", ["hiring", "hires", "hire", "recruit", "expan", "headcount", "engineer"]),
    ("news", ["report", "revenue", "earnings", "quarter", "growth", "award"]),
]


def _classify_signal(title: str, description: str) -> str:
    """Classify a search result into a signal kind.

    Priority order matters: funding is checked before hiring because
    signals like "Raises Series D... Eyes Expansion" match both.
    """
    combined = f"{title} {description}".lower()
    for kind, keywords in _KIND_KEYWORDS:
        if any(kw in combined for kw in keywords):
            return kind
    return "other"


# ── Researcher ──────────────────────────────────────────────────────────────

_domain_cache: dict[str, "CompanyResearch"] = {}


def clear_domain_cache() -> None:
    """Clear the domain enrichment cache (for tests)."""
    _domain_cache.clear()


class CompanyResearcher:
    """Turns a domain into a cited company brief.

    Caches results by domain so contacts of the same account don't re-enrich.
    """

    def __init__(
        self,
        pdl_provider: EnrichmentProvider,
        search_provider: SearchProvider | None = None,
        website_fetcher: Any = None,
        llm_provider: str = "mock",
        llm_model: str = "gpt-4o-mini",
    ) -> None:
        self._pdl = pdl_provider
        self._search = search_provider or get_search_provider()
        self._website_fetcher = website_fetcher
        self._llm_provider = llm_provider
        self._llm_model = llm_model

    def research(
        self,
        domain: str,
        *,
        name: str | None = None,
        email: str | None = None,
        role: str | None = None,
    ) -> CompanyResearch:
        """Build a cited company brief for the given domain."""
        # Domain cache: same domain = same brief (contacts share enrichment)
        if domain in _domain_cache:
            return _domain_cache[domain]

        off = os.environ.get("COMPANY_RESEARCH", "").lower() == "off"

        sources: list[SourcedClaim] = []
        industry: str | None = None
        size: str | None = None
        what_they_do: str | None = None
        recent_signals: list[RecentSignal] = []
        likely_problems: list[str] = []
        resolved_count = 0

        # ── Step 1: PDL firmographics ───────────────────────────────────
        pdl_email = email or f"user@{domain}"
        pdl_result = self._pdl.enrich(pdl_email, name or "", "", "")

        if pdl_result.industry.source != "none" and pdl_result.industry.value:
            industry = pdl_result.industry.value
            sources.append(SourcedClaim(text=f"Industry: {industry}", source="pdl"))
            resolved_count += 1

        if pdl_result.company_size.source != "none" and pdl_result.company_size.value:
            size = pdl_result.company_size.value
            sources.append(SourcedClaim(text=f"Company size: {size}", source="pdl"))
            resolved_count += 1

        inferred_role = role
        if not inferred_role and pdl_result.role.source != "none" and pdl_result.role.value:
            inferred_role = pdl_result.role.value

        if off:
            # PDL-only brief — no website/search/apollo/pb/LLM
            confidence = min(1.0, resolved_count * 0.3)
            return CompanyResearch(
                domain=domain,
                industry=industry,
                size=size,
                inferred_role=inferred_role,
                sources=sources,
                confidence=confidence,
            )

        # ── Step 2: Apollo org enrichment (primary firmographics) ───────
        tech_stack: list[str] = []
        apollo_source = os.environ.get("APOLLO_SOURCE", "fixture").lower()
        if apollo_source != "off":
            try:
                from gtm_triage.apollo import get_apollo_client
                apollo_org = get_apollo_client().enrich_organization(domain=domain)
                if apollo_org is not None:
                    signals = apollo_org.to_research_signals()
                    resolved_count += 1

                    # Apollo is PRIMARY for what_they_do
                    if signals.get("what_they_do"):
                        what_they_do = signals["what_they_do"]
                        sources.append(SourcedClaim(
                            text=f"What they do: {what_they_do}",
                            source="apollo",
                        ))

                    # Apollo overrides PDL for industry/size when present
                    if signals.get("industry"):
                        # Remove any prior PDL industry claim
                        sources = [s for s in sources if not (s.source == "pdl" and s.text.startswith("Industry:"))]
                        industry = signals["industry"]
                        sources.append(SourcedClaim(text=f"Industry: {industry}", source="apollo"))
                    if signals.get("size"):
                        sources = [s for s in sources if not (s.source == "pdl" and s.text.startswith("Company size:"))]
                        size = signals["size"]
                        sources.append(SourcedClaim(text=f"Company size: {size}", source="apollo"))

                    # Revenue
                    if signals.get("revenue"):
                        sources.append(SourcedClaim(
                            text=f"Revenue: {signals['revenue']}",
                            source="apollo",
                        ))

                    # Tech stack
                    tech_stack = signals.get("tech_stack", [])
                    if tech_stack:
                        sources.append(SourcedClaim(
                            text=f"Tech stack: {', '.join(tech_stack[:5])}...",
                            source="apollo",
                        ))

                    # Funding signals
                    for sig in signals.get("recent_signals", []):
                        recent_signals.append(RecentSignal(
                            text=sig["text"],
                            source_url=sig.get("source_url", ""),
                            kind=sig["kind"],
                        ))
                        sources.append(SourcedClaim(
                            text=sig["text"],
                            source="apollo",
                        ))
            except Exception as exc:
                logger.debug("Apollo enrichment failed for %s: %s", domain, exc)

        # ── Step 3: Website text ────────────────────────────────────────
        website_text: str | None = None
        if self._website_fetcher is not None:
            try:
                website_text = self._website_fetcher(domain)
                if website_text:
                    resolved_count += 1
            except Exception as exc:
                logger.debug("Website fetch failed for %s: %s", domain, exc)

        # ── Step 4: Search for recent signals ───────────────────────────
        query = f"{domain} product OR launch OR hiring"
        search_results = self._search.search(query, num_results=5)
        for sr in search_results:
            kind = _classify_signal(sr.title, sr.description)
            recent_signals.append(RecentSignal(
                text=sr.title,
                source_url=sr.url,
                kind=kind,
            ))
            sources.append(SourcedClaim(
                text=sr.title,
                source=f"search:{sr.url}",
            ))
        if search_results:
            resolved_count += 1

        # ── Step 5: Productboard demand check ──────────────────────────
        is_requester = False
        pb_source = os.environ.get("PRODUCTBOARD_SOURCE", "fixture").lower()
        if pb_source != "off":
            try:
                from gtm_triage.productboard import get_productboard_client
                pb = get_productboard_client()
                features = pb.query_features()
                if features.entities:
                    entity_ids = [e.entity_id for e in features.entities]
                    feedback_list = pb.list_feedback(entity_ids=entity_ids)
                    domain_lower = domain.lower()
                    for fb_item in feedback_list.feedback:
                        parsed = fb_item.parsed_customer
                        if parsed.domain and parsed.domain.lower() == domain_lower:
                            is_requester = True
                            sources.append(SourcedClaim(
                                text=f"{parsed.company or domain} requested: {fb_item.name}",
                                source=f"productboard:{fb_item.id}",
                            ))
                            recent_signals.append(RecentSignal(
                                text=f"{parsed.company or domain} requested: {fb_item.name}",
                                source_url=fb_item.display_url,
                                kind="demand",
                            ))
                            break  # one match is enough to confirm demand
            except Exception as exc:
                logger.debug("Productboard demand check failed for %s: %s", domain, exc)

        # ── Step 6: LLM summary + likely problems ──────────────────────
        # If Apollo already provided what_they_do, skip the LLM summary for it
        if not what_they_do:
            what_they_do, likely_problems = self._llm_summarize(
                domain=domain,
                website_text=website_text,
                search_results=search_results,
                industry=industry,
                size=size,
            )

            if what_they_do:
                sources.append(SourcedClaim(
                    text=f"What they do: {what_they_do}",
                    source="website" if website_text else "search",
                ))
        else:
            # Still run LLM for likely_problems even when Apollo gave what_they_do
            _, likely_problems = self._llm_summarize(
                domain=domain,
                website_text=website_text,
                search_results=search_results,
                industry=industry,
                size=size,
            )

        confidence = min(1.0, resolved_count * 0.20)

        result = CompanyResearch(
            domain=domain,
            what_they_do=what_they_do,
            industry=industry,
            size=size,
            recent_signals=recent_signals,
            likely_problems=likely_problems,
            inferred_role=inferred_role,
            sources=sources,
            confidence=confidence,
            tech_stack=tech_stack,
            is_requester=is_requester,
        )
        # Cache by domain so same-account contacts don't re-enrich
        _domain_cache[domain] = result
        return result

    def _llm_summarize(
        self,
        *,
        domain: str,
        website_text: str | None,
        search_results: list[SearchResult],
        industry: str | None,
        size: str | None,
    ) -> tuple[str | None, list[str]]:
        """One LLM call: summarize what_they_do + infer likely_problems.

        Returns (what_they_do, likely_problems). If no grounded facts exist,
        returns (None, []) — the anti-fabrication guard.
        """
        # Build context from grounded facts only
        context_parts: list[str] = []
        if website_text:
            context_parts.append(f"WEBSITE TEXT:\n{website_text[:2000]}")
        for sr in search_results[:5]:
            context_parts.append(f"SEARCH RESULT: {sr.title} — {sr.description}")
        if industry:
            context_parts.append(f"INDUSTRY (from PDL): {industry}")
        if size:
            context_parts.append(f"COMPANY SIZE (from PDL): {size}")

        # Anti-fabrication: if no grounded context, return empty
        if not context_parts:
            return None, []

        context_block = "\n\n".join(context_parts)

        system = (
            "You are a company research assistant. Given ONLY the grounded facts below, "
            "produce a brief JSON with two fields:\n"
            '  "what_they_do": a 1-2 sentence summary of what the company does '
            "(based on website text or search results; null if insufficient evidence),\n"
            '  "likely_problems": a list of 2-4 hypothetical business problems this company '
            "MIGHT face, inferred from the facts. These are hypotheses, not stated facts.\n\n"
            "Return ONLY valid JSON. Do NOT invent facts beyond what's provided."
        )
        user = f"Company domain: {domain}\n\n{context_block}"

        try:
            from gtm_triage.agents.llm_client import chat
            result = chat(
                provider=self._llm_provider,
                model=self._llm_model,
                system=system,
                user=user,
                max_tokens=300,
            )
            return self._parse_summary(result.text, website_text, search_results)
        except Exception as exc:
            logger.debug("LLM summary failed for %s: %s", domain, exc)
            # Fallback: construct what_they_do from website text if available
            if website_text:
                # Take first sentence
                first_sentence = website_text.split(".")[0].strip() + "."
                return first_sentence[:200], []
            return None, []

    def _parse_summary(
        self,
        raw: str,
        website_text: str | None,
        search_results: list[SearchResult],
    ) -> tuple[str | None, list[str]]:
        """Parse LLM response. Falls back to raw-text extraction on parse failure."""
        try:
            m = re.search(r"\{[\s\S]*\}", raw)
            if m:
                data = json.loads(m.group(0))
                # Only trust the parse if the response has the expected schema
                if "what_they_do" in data or "likely_problems" in data:
                    what = data.get("what_they_do")
                    problems = data.get("likely_problems", [])
                    if isinstance(problems, list):
                        problems = [str(p) for p in problems]
                    else:
                        problems = []
                    return what, problems
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback: use website text first sentence
        if website_text:
            first_sentence = website_text.split(".")[0].strip() + "."
            return first_sentence[:200], []
        return None, []
