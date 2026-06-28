"""SearchProvider ABC + factory for web-search enrichment.

get_search_provider() reads SEARCH_PROVIDER from environment:
  "fixture" (default) -> FixtureSearchProvider  (replays fixtures/search/*.json)
  "brave"             -> BraveSearchProvider    (Brave Search API, free tier)
  "tavily"            -> TavilySearchProvider   (TODO — scaffold only)
  "off"               -> NullSearchProvider     (returns [], never raises)
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "search"


# ── Result model ────────────────────────────────────────────────────────────

class SearchResult(BaseModel):
    """A single web-search result."""
    title: str
    url: str
    description: str
    age: str = ""  # ISO date or relative age string if available


# ── ABC ─────────────────────────────────────────────────────────────────────

class SearchProvider(ABC):
    """Interface for web-search providers."""

    @abstractmethod
    def search(self, query: str, *, num_results: int = 5) -> list[SearchResult]:
        """Run a web search. Must not raise — return [] on failure."""
        ...


# ── Fixture ─────────────────────────────────────────────────────────────────

class FixtureSearchProvider(SearchProvider):
    """Replays pre-recorded search results from fixtures/search/<domain>.json.

    Looks up the fixture by extracting the first token of the query (assumed to
    be the domain or company name). Falls back to empty results if no fixture.
    """

    def __init__(self, fixtures_dir: Path | None = None) -> None:
        self._dir = fixtures_dir or _FIXTURES_DIR

    def search(self, query: str, *, num_results: int = 5) -> list[SearchResult]:
        # Try to match a fixture file by first query token (domain)
        first_token = query.split()[0].strip().lower() if query.strip() else ""
        fixture_path = self._dir / f"{first_token}.json"
        if not fixture_path.exists():
            return []

        try:
            data = json.loads(fixture_path.read_text(encoding="utf-8"))
            results = data.get("results", [])
            return [SearchResult.model_validate(r) for r in results[:num_results]]
        except Exception as exc:
            logger.debug("Fixture search load failed for %s: %s", first_token, exc)
            return []


# ── Brave ───────────────────────────────────────────────────────────────────

class BraveSearchProvider(SearchProvider):
    """Brave Search API (free tier: 2000 queries/month).

    Requires BRAVE_API_KEY in environment.

    TODO: The exact response schema for Brave Search API v1 may differ
    from what's parsed here. This implementation parses defensively —
    unknown fields are ignored, missing fields default to empty strings.
    Validate against real responses once a key is configured and update
    the parsing if needed.
    """

    _BASE_URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str = "") -> None:
        self._api_key = (api_key or os.environ.get("BRAVE_API_KEY", "")).strip()

    def search(self, query: str, *, num_results: int = 5) -> list[SearchResult]:
        if not self._api_key:
            logger.warning("BRAVE_API_KEY not set; returning empty search results")
            return []

        try:
            import httpx

            resp = httpx.get(
                self._BASE_URL,
                params={"q": query, "count": num_results},
                headers={
                    "X-Subscription-Token": self._api_key,
                    "Accept": "application/json",
                },
                timeout=10.0,
            )
            if resp.status_code != 200:
                logger.warning("Brave search returned %d for query: %s", resp.status_code, query)
                return []

            body = resp.json()
            # TODO: Validate this path against real Brave API responses.
            # The web results may be under body["web"]["results"] or a similar key.
            web_results = body.get("web", {}).get("results", [])
            results: list[SearchResult] = []
            for item in web_results[:num_results]:
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    description=item.get("description", ""),
                    age=item.get("age", ""),
                ))
            return results

        except Exception as exc:
            logger.warning("Brave search failed for query %r: %s", query, exc)
            return []


# ── Null ────────────────────────────────────────────────────────────────────

class NullSearchProvider(SearchProvider):
    """No-op provider — returns empty results, never raises."""

    def search(self, query: str, *, num_results: int = 5) -> list[SearchResult]:
        return []


# ── Factory ─────────────────────────────────────────────────────────────────

def get_search_provider() -> SearchProvider:
    """Factory — returns the search provider matching SEARCH_PROVIDER env."""
    source = os.environ.get("SEARCH_PROVIDER", "fixture").lower()

    if source == "brave":
        return BraveSearchProvider()
    if source == "tavily":
        # TODO: implement TavilySearchProvider when needed
        logger.warning("SEARCH_PROVIDER=tavily not yet implemented; falling back to null")
        return NullSearchProvider()
    if source == "off":
        return NullSearchProvider()

    # Default: fixture
    return FixtureSearchProvider()
