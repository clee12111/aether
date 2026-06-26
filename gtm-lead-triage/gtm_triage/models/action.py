from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentAction(BaseModel):
    reasoning: str = Field(..., description="Why this action now")
    tool: str = Field(default="", description="Tool to call (empty string when is_final)")
    tool_args: dict[str, Any] = Field(default_factory=dict)
    is_final: bool = Field(default=False)


class Observation(BaseModel):
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class LoopStep(BaseModel):
    step_index: int
    action: AgentAction
    observation: Observation


class TriageResult(BaseModel):
    run_id: str
    lead_email: str
    enrichment: dict[str, Any] | None = None
    score: dict[str, Any] | None = None
    outreach: dict[str, Any] | None = None
    steps: list[LoopStep] = Field(default_factory=list)
    final_tier: str | None = None
    final_route: str | None = None
