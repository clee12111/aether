"""Tests for the LLMProvider abstraction.

Verifies: factory creates correct providers, mock routes through the
abstraction, no vendor SDK is imported outside llm_provider.py.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

from gtm_triage.agents.llm_provider import (
    AnthropicProvider,
    ChatResult,
    LLMProvider,
    MockProvider,
    OpenAIProvider,
    create_provider,
    get_default_model,
)


class TestFactory:
    def test_creates_mock(self):
        p = create_provider("mock")
        assert isinstance(p, MockProvider)
        assert p.name == "mock"

    def test_creates_openai(self):
        p = create_provider("openai")
        assert isinstance(p, OpenAIProvider)
        assert p.name == "openai"

    def test_creates_anthropic(self):
        p = create_provider("anthropic")
        assert isinstance(p, AnthropicProvider)
        assert p.name == "anthropic"

    def test_unknown_raises(self):
        import pytest
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            create_provider("llama")


class TestDefaultModels:
    def test_openai_default(self):
        assert get_default_model("openai") == "gpt-4o-mini"

    def test_anthropic_default(self):
        assert "claude" in get_default_model("anthropic")

    def test_mock_default(self):
        assert get_default_model("mock") == "mock"


class TestMockProvider:
    def test_chat_returns_result(self):
        p = MockProvider()
        result = p.chat(
            messages=[
                {"role": "system", "content": "You are a GTM lead-triage agent."},
                {"role": "user", "content": "LEAD:\n  email: test@stripe.com\n\nPRIOR STEPS: (none)\n\nWhat is the SINGLE next action?"},
            ],
            model="mock",
        )
        assert isinstance(result, ChatResult)
        assert result.text  # non-empty
        assert "crm_lookup" in result.text  # first step is CRM lookup

    def test_chat_through_llm_client(self):
        """chat() function in llm_client routes to MockProvider."""
        from gtm_triage.agents.llm_client import chat
        result = chat(
            provider="mock",
            model="mock",
            system="test",
            user="LEAD:\n  email: x@y.com\n\nPRIOR STEPS: (none)\n\nWhat next?",
        )
        assert isinstance(result, ChatResult)
        assert result.text


class TestNoDirectSDKImports:
    """Verify no file outside llm_provider.py imports a vendor SDK directly."""

    def test_no_openai_imports_outside_provider(self):
        src_dir = Path(__file__).parent.parent / "gtm_triage"
        provider_file = src_dir / "agents" / "llm_provider.py"
        violations = []

        for py_file in src_dir.rglob("*.py"):
            if py_file == provider_file:
                continue
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and "openai" in node.module:
                    violations.append(f"{py_file.relative_to(src_dir.parent)}:{node.lineno}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if "openai" in alias.name:
                            violations.append(f"{py_file.relative_to(src_dir.parent)}:{node.lineno}")

        assert violations == [], (
            f"Direct openai SDK imports found outside llm_provider.py:\n"
            + "\n".join(f"  {v}" for v in violations)
        )
