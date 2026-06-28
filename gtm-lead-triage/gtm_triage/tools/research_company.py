"""research_company tool — wraps CompanyResearcher.research(domain, role).

Returns the brief as to_draft_context() dict. Any tool-internal LLM call
(likely_problems) is traced via the researcher's llm_provider.
"""

from __future__ import annotations

import os

from gtm_triage.enrichment.company_research import CompanyResearcher
from gtm_triage.enrichment.fixture_provider import FixtureProvider
from gtm_triage.enrichment.search import get_search_provider
from gtm_triage.tools.base import BaseTool


class ResearchCompanyTool(BaseTool):
    def __init__(self, provider: str = "mock", model: str = "gpt-4o-mini") -> None:
        self._provider = provider
        self._model = model

    @property
    def name(self) -> str:
        return "research_company"

    def run(self, args: dict, run_id: str = "") -> dict:
        domain = args.get("domain", "")
        role = args.get("role", None)
        email = args.get("email", None)

        # Build a PDL provider (fixture or empty depending on config)
        enrichment_backend = os.environ.get("ENRICHMENT_PROVIDER", "mock")
        if enrichment_backend == "pdl":
            from gtm_triage.enrichment.pdl_provider import PDLProvider
            from pathlib import Path
            cassettes = Path(__file__).parent.parent / "enrichment" / "cache" / "pdl_cassettes.json"
            pdl = PDLProvider(cache_path=cassettes if cassettes.exists() else None)
        else:
            # Use the PDL enrichment fixture for offline determinism
            from pathlib import Path
            fixture_path = Path(__file__).parent.parent / "enrichment" / "fixtures" / "pdl_enrichment.json"
            if fixture_path.exists():
                pdl = FixtureProvider(fixture_path)
            else:
                pdl = FixtureProvider({})

        researcher = CompanyResearcher(
            pdl_provider=pdl,
            search_provider=get_search_provider(),
            llm_provider=self._provider,
            llm_model=self._model,
        )

        brief = researcher.research(domain, role=role, email=email)
        return brief.to_draft_context()
