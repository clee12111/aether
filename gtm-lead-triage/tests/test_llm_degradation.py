"""Tests for graceful LLM degradation (R3+R1 fix).

When the LLM (OpenAI) fails after retries, the triage loop should degrade
to mock-provider scoring and return a valid result — never a raw 500.
"""

from __future__ import annotations

from unittest.mock import patch

from gtm_triage.agents.executor import Executor
from gtm_triage.agents.loop_agent import run_triage
from gtm_triage.crm.sqlite_crm import SQLiteCRM
from gtm_triage.models.lead import Lead
from gtm_triage.tools.crm_lookup import CRMLookupTool
from gtm_triage.tools.draft_outreach import DraftOutreachTool
from gtm_triage.tools.enrich_lead import EnrichLeadTool
from gtm_triage.tools.registry import ToolRegistry
from gtm_triage.tools.score_lead import ScoreLeadTool
from gtm_triage.trace.store import TraceStore


def _make_executor():
    crm = SQLiteCRM(":memory:")
    trace = TraceStore(":memory:")
    registry = ToolRegistry([
        CRMLookupTool(crm),
        EnrichLeadTool(provider="mock"),
        ScoreLeadTool(provider="mock"),
        DraftOutreachTool(),
    ])
    return Executor(registry, trace), trace


class TestLLMDegradation:
    def test_openai_failure_returns_valid_result(self):
        """Simulated OpenAI failure → graceful degradation, not a crash."""
        executor, trace = _make_executor()
        lead = Lead(
            email="cto@stripe.com",
            name="CTO",
            company="Stripe",
            message="I'd like a demo",
        )

        with patch("gtm_triage.agents.loop_agent.chat") as mock_chat:
            mock_chat.side_effect = ConnectionError("OpenAI API unreachable")

            # This should NOT raise — it should degrade gracefully
            result = run_triage(
                lead=lead,
                executor=executor,
                trace=trace,
                provider="openai",
                model="gpt-4o-mini",
            )

        # Result must be valid: has a tier, route, and steps
        assert result.final_tier is not None
        assert result.final_tier in ("hot", "warm", "cold", "disqualified")
        assert result.final_route is not None
        assert result.run_id is not None
        assert len(result.steps) >= 1

        # The last step should indicate degradation
        last_step = result.steps[-1]
        assert last_step.action.is_final is True
        assert "degraded" in last_step.action.reasoning.lower() or "degraded" in str(last_step.action.tool_args)

    def test_timeout_failure_degrades(self):
        """TimeoutError also triggers graceful degradation."""
        executor, trace = _make_executor()
        lead = Lead(email="test@example.com", name="Test", company="Co", message="hi")

        with patch("gtm_triage.agents.loop_agent.chat") as mock_chat:
            mock_chat.side_effect = TimeoutError("Request timed out")

            result = run_triage(
                lead=lead,
                executor=executor,
                trace=trace,
                provider="openai",
            )

        assert result.final_tier is not None
        assert result.final_route is not None

    def test_degraded_trace_records_error(self):
        """The trace should record the LLM failure event."""
        executor, trace = _make_executor()
        lead = Lead(email="test@acme.com", name="Test", company="Acme", message="hello")

        with patch("gtm_triage.agents.loop_agent.chat") as mock_chat:
            mock_chat.side_effect = OSError("Network error")

            result = run_triage(
                lead=lead,
                executor=executor,
                trace=trace,
                provider="openai",
            )

        # Check trace events for the error
        events = trace.get_run_events(result.run_id)
        error_events = [e for e in events if e.get("error")]
        assert len(error_events) >= 1
        assert "Network error" in error_events[0]["error"]

    def test_mock_provider_unaffected(self):
        """Mock provider should still work normally (no LLM call)."""
        executor, trace = _make_executor()
        lead = Lead(
            email="j.martinez@acmefintech.com",
            name="Julia Martinez, VP of Sales",
            company="Acme Fintech International",
            message="We'd like to schedule a demo for our trading desk. Urgent need.",
        )

        result = run_triage(
            lead=lead,
            executor=executor,
            trace=trace,
            provider="mock",
        )

        assert result.final_tier == "hot"
        assert result.final_route == "ae_immediate"
