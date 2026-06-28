"""LLMProvider abstraction — one adapter per vendor, one env var to swap.

Providers:
  - MockProvider:     deterministic responses for CI/eval (no API key).
  - OpenAIProvider:   OpenAI API (gpt-4o-mini default).
  - AnthropicProvider: Anthropic API (claude-sonnet-4-6 default).

All LLM calls route through provider.chat(). No call site instantiates a
vendor SDK directly.
"""

from __future__ import annotations

import json
import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ChatResult:
    text: str
    input_tokens: int
    output_tokens: int


class LLMProvider(ABC):
    """Abstract base for LLM providers. One implementation per vendor."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name (e.g. 'openai', 'anthropic', 'mock')."""
        ...

    @abstractmethod
    def chat(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0,
    ) -> ChatResult:
        """Send a chat completion request. Returns text + token counts."""
        ...


# ── OpenAI ──────────────────────────────────────────────────────────────────


class OpenAIProvider(LLMProvider):
    """OpenAI API via the official SDK."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = (api_key or os.environ.get("OPENAI_API_KEY", "")).strip()

    @property
    def name(self) -> str:
        return "openai"

    def chat(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0,
    ) -> ChatResult:
        from openai import OpenAI

        client = OpenAI(api_key=self._api_key, timeout=30.0)
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            max_completion_tokens=max_tokens,
            temperature=temperature,
        )
        usage = resp.usage
        return ChatResult(
            text=resp.choices[0].message.content or "",
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )


# ── Anthropic ───────────────────────────────────────────────────────────────


class AnthropicProvider(LLMProvider):
    """Anthropic API via the official SDK.

    Proves the swap is one file: install `anthropic`, set ANTHROPIC_API_KEY,
    set GTM_PROVIDER=anthropic. No other file changes.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = (api_key or os.environ.get("ANTHROPIC_API_KEY", "")).strip()

    @property
    def name(self) -> str:
        return "anthropic"

    def chat(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0,
    ) -> ChatResult:
        try:
            import anthropic
        except ImportError:
            raise RuntimeError(
                "GTM_PROVIDER=anthropic requires the `anthropic` package. "
                "Install with: pip install anthropic"
            )

        client = anthropic.Anthropic(api_key=self._api_key, timeout=30.0)

        # Anthropic uses a separate system param, not a system message
        system_text = ""
        user_messages = []
        for m in messages:
            if m["role"] == "system":
                system_text = m["content"]
            else:
                user_messages.append(m)

        resp = client.messages.create(
            model=model,
            system=system_text,
            messages=user_messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return ChatResult(
            text=resp.content[0].text if resp.content else "",
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )


# ── Mock ────────────────────────────────────────────────────────────────────


class MockProvider(LLMProvider):
    """Deterministic responses for CI/eval — no API key needed."""

    @property
    def name(self) -> str:
        return "mock"

    def chat(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0,
    ) -> ChatResult:
        # Delegate to the existing mock logic (imported from llm_client)
        system = ""
        user = ""
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            elif m["role"] == "user":
                user = m["content"]
        from gtm_triage.agents.llm_client import _mock_response
        text = _mock_response(system, user)
        return ChatResult(text=text, input_tokens=0, output_tokens=0)


# ── Factory ─────────────────────────────────────────────────────────────────

_DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-6",
    "mock": "mock",
}


def get_default_model(provider_name: str) -> str:
    """Return the default model for a given provider."""
    return _DEFAULT_MODELS.get(provider_name, "gpt-4o-mini")


def create_provider(provider_name: str) -> LLMProvider:
    """Factory: create an LLMProvider by name."""
    if provider_name == "mock":
        return MockProvider()
    elif provider_name == "openai":
        return OpenAIProvider()
    elif provider_name == "anthropic":
        return AnthropicProvider()
    else:
        raise ValueError(f"Unknown LLM provider: {provider_name!r}")
