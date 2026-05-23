from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class AgentAction(BaseModel):
    """One decision the model makes at a single loop step."""
    reasoning: str = Field(..., description="why this action, given observations so far")
    tool: str = Field(..., description="tool name to call, or empty if is_final")
    tool_args: dict = Field(default_factory=dict)
    is_final: bool = Field(default=False, description="true when the goal is satisfied and no more tools are needed")


class AgentObservation(BaseModel):
    """Result of executing one AgentAction."""
    success: bool
    output: dict = Field(default_factory=dict)
    error: str | None = None


class LoopStep(BaseModel):
    step_index: int
    action: AgentAction
    observation: AgentObservation
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LoopState(BaseModel):
    run_id: str
    goal: str
    initial_context: list[str] = Field(default_factory=list)
    steps: list[LoopStep] = Field(default_factory=list)
    is_complete: bool = False
    stop_reason: str | None = None  # "is_final" | "max_steps" | "error"
