"""
Pydantic models for the Aether trace layer.

Every LLM call and tool call in the system emits a TraceEvent that is written
to the SQLite trace store. This gives full, auditable replay of every run.

Design constraints (from CLAUDE.md):
- Every agent output is a Pydantic model — no freeform dicts.
- Every LLM call and tool call writes a row to the trace store.
- The model must be serialisable to/from JSON for SQLite storage.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator


# ── Event type literals ────────────────────────────────────────────────────────

EventType = Literal[
    "llm_call",         # outbound call to Anthropic API
    "llm_response",     # parsed response from Anthropic API
    "tool_call",        # Executor dispatching a registered tool
    "tool_response",    # result returned by a tool
    "validation_error", # Pydantic validation failed on an LLM response
    "retry",            # retry attempt after failure
    "plan",             # Planner emitted an ExecutionPlan
    "critique",         # Critic emitted a CritiqueResult
    "run_start",        # run lifecycle event
    "run_end",          # run lifecycle event
]


# ── Core model ─────────────────────────────────────────────────────────────────

class TraceEvent(BaseModel):
    """A single auditable event in an Aether run.

    TraceEvents are immutable once written. The trace store uses event_id as
    the primary key. All timestamps are UTC.

    Attributes:
        event_id:      UUID4 uniquely identifying this event.
        run_id:        Groups all events belonging to a single end-to-end run.
        step_id:       The PlanStep this event belongs to (None for lifecycle events).
        event_type:    Discriminator — what kind of event this is.
        agent:         Name of the agent or component that emitted the event
                       (e.g. "planner", "executor", "critic", "tool.read_csv").
        model:         Claude model ID used (only set for llm_call / llm_response).
        input_tokens:  Prompt tokens consumed (llm_call / llm_response only).
        output_tokens: Completion tokens consumed (llm_response only).
        prompt_hash:   SHA-256 of the raw prompt string (hex, first 16 chars).
                       Useful for spotting duplicate prompts and cache hit analysis.
        payload:       The event's structured content. Shape varies by event_type:
                         llm_call      → {"system": str, "user": str}
                         llm_response  → {"raw_text": str, "parsed": dict | None}
                         tool_call     → {"tool": str, "args": dict}
                         tool_response → {"tool": str, "result": any}
                         validation_error → {"attempt": int, "error": str, "raw": str}
                         retry         → {"attempt": int, "reason": str}
                         plan          → ExecutionPlan.model_dump()
                         critique      → CritiqueResult.model_dump()
                         run_start     → {"goal": str, "document_ids": list[str]}
                         run_end       → {"status": "success"|"failure", "summary": str}
        error:         Error message if this event represents a failure.
        duration_ms:   Wall-clock time for the operation in milliseconds.
        created_at:    UTC timestamp when the event was emitted.
        attempt:       Retry attempt number (1-indexed). 1 = first attempt.
    """

    event_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUID4 primary key for this event",
    )
    run_id: str = Field(..., description="Parent run identifier")
    step_id: str | None = Field(
        default=None,
        description="PlanStep this event belongs to; None for lifecycle events",
    )
    event_type: EventType = Field(..., description="Discriminator for the event shape")
    agent: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Name of the emitting agent or component",
    )
    model: str | None = Field(
        default=None,
        description="Claude model ID (set for llm_call and llm_response events)",
    )
    input_tokens: int | None = Field(
        default=None,
        ge=0,
        description="Prompt token count (LLM events only)",
    )
    output_tokens: int | None = Field(
        default=None,
        ge=0,
        description="Completion token count (LLM events only)",
    )
    cached_tokens: int | None = Field(
        default=None,
        ge=0,
        description="Prompt-cache hit tokens (OpenAI only; 0 for Anthropic/Ollama; LLM events only)",
    )
    prompt_hash: str | None = Field(
        default=None,
        description="First 16 hex chars of SHA-256(prompt) for dedup analysis",
    )
    payload: dict[str, Any] = Field(
        ...,
        description="Structured event content; shape depends on event_type",
    )
    error: str | None = Field(
        default=None,
        description="Error message if this event represents a failure path",
    )
    duration_ms: int | None = Field(
        default=None,
        ge=0,
        description="Wall-clock time for the operation in milliseconds",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the event was emitted",
    )
    attempt: Annotated[int, Field(ge=1)] = Field(
        default=1,
        description="Retry attempt number (1 = first attempt, not a retry)",
    )

    @model_validator(mode="after")
    def token_counts_only_for_llm_events(self) -> TraceEvent:
        """Token counts should only be set on LLM events."""
        llm_event_types = {"llm_call", "llm_response"}
        if self.event_type not in llm_event_types:
            if (self.input_tokens is not None or self.output_tokens is not None
                    or self.cached_tokens is not None):
                raise ValueError(
                    f"input_tokens / output_tokens / cached_tokens are only valid on "
                    f"llm_call / llm_response events, not {self.event_type!r}"
                )
        return self

    # ── Convenience constructors ───────────────────────────────────────────────

    @classmethod
    def for_llm_call(
        cls,
        *,
        run_id: str,
        agent: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        step_id: str | None = None,
        attempt: int = 1,
    ) -> TraceEvent:
        """Build a TraceEvent for an outbound LLM call (before the response arrives)."""
        raw = system_prompt + user_prompt
        prompt_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return cls(
            run_id=run_id,
            step_id=step_id,
            event_type="llm_call",
            agent=agent,
            model=model,
            prompt_hash=prompt_hash,
            payload={"system": system_prompt, "user": user_prompt},
            attempt=attempt,
        )

    @classmethod
    def for_tool_call(
        cls,
        *,
        run_id: str,
        agent: str,
        tool: str,
        args: dict[str, Any],
        step_id: str | None = None,
    ) -> TraceEvent:
        """Build a TraceEvent for a tool dispatch."""
        return cls(
            run_id=run_id,
            step_id=step_id,
            event_type="tool_call",
            agent=agent,
            payload={"tool": tool, "args": args},
        )

    @classmethod
    def for_validation_error(
        cls,
        *,
        run_id: str,
        agent: str,
        attempt: int,
        error: str,
        raw_response: str,
        step_id: str | None = None,
    ) -> TraceEvent:
        """Build a TraceEvent for a Pydantic validation failure."""
        return cls(
            run_id=run_id,
            step_id=step_id,
            event_type="validation_error",
            agent=agent,
            error=error,
            payload={"attempt": attempt, "error": error, "raw": raw_response},
            attempt=attempt,
        )

    @property
    def total_tokens(self) -> int | None:
        """Sum of input and output tokens, or None if either is unset."""
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens
