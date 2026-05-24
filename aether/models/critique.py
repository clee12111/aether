"""
Pydantic models for the Critic agent.

The Critic receives (goal, ExecutionPlan, executor_outputs) and produces a
CritiqueResult — a structured verdict on whether the run satisfied the goal,
with zero or more flagged issues.

Design constraints (from CLAUDE.md):
- Every agent output is a Pydantic model — no freeform dicts.
- CritiqueResult is the final artefact of a run and drives the Streamlit report.
- Flags carry evidence strings so a human auditor can verify the finding.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enums as Literals ─────────────────────────────────────────────────────────

Severity = Literal["critical", "warning", "info"]
Verdict = Literal["pass", "partial", "fail"]

# Predefined flag categories. The Critic may emit any string; these are
# the expected values used in evals and the Streamlit UI.
# Domain-neutral: works for legal, medical, technical, and finance documents.
# Finance-specific categories (allocation_mismatch, reconciliation_gap) are
# in the finance fewshots (aether/prompts/finance/) — not hardcoded here.
FlagCategory = Literal[
    "result_mismatch",        # computed or found result deviates from expected
    "incomplete_coverage",    # answer does not address all parts of the goal
    "calculation_error",      # arithmetic or logical inconsistency in the output
    "missing_data",           # required field, document, or evidence is absent
    "policy_violation",       # item violates a stated rule or requirement
    "data_quality",           # input data appears malformed, suspect, or unreliable
    "unsupported_claim",      # conclusion not grounded in retrieved evidence
    "other",                  # catch-all for Critic discretion
]


# ── CritiqueFlag ──────────────────────────────────────────────────────────────

class CritiqueFlag(BaseModel):
    """A single issue identified by the Critic.

    Flags are the atomic output of the critique process. Each flag must
    include concrete ``evidence`` so a human auditor can verify the finding
    without re-running the pipeline.

    Attributes:
        flag_id:    UUID4 uniquely identifying this flag within the critique.
        severity:   Impact level — critical halts downstream use; warning
                    requires human review; info is informational only.
        category:   Domain-specific issue category (see FlagCategory).
        description: Human-readable explanation of the issue. Must be specific
                     enough to act on without reading the raw data.
        step_ref:   step_id of the PlanStep whose output triggered this flag.
                    None if the flag relates to the overall goal rather than a
                    specific step.
        evidence:   Verbatim excerpt, row ID, or quantitative discrepancy that
                    supports the flag. Must be concrete — not "the data looks wrong".
        suggested_fix: Optional Critic suggestion for how to resolve this flag.
    """

    flag_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUID4 identifier for this flag",
    )
    severity: Severity = Field(..., description="Impact level of this issue")
    category: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Issue category (use FlagCategory values; domain pack in prompts_dir)",
    )
    description: str = Field(
        ...,
        min_length=10,
        description="Human-readable explanation of the issue; specific and actionable",
    )
    step_ref: str | None = Field(
        default=None,
        description="step_id of the PlanStep whose output triggered this flag",
    )
    evidence: str = Field(
        ...,
        min_length=5,
        description="Verbatim excerpt or quantitative discrepancy supporting this flag",
    )
    suggested_fix: str | None = Field(
        default=None,
        description="Optional Critic suggestion for resolving this flag",
    )


# ── CritiqueResult ────────────────────────────────────────────────────────────

class CritiqueResult(BaseModel):
    """The full structured output of the Critic agent for a single run.

    The Critic emits exactly one CritiqueResult per run, after all Executor
    steps have completed. The result is stored in the trace, returned to the
    caller, and rendered in the Streamlit UI.

    Attributes:
        critique_id:       UUID4 uniquely identifying this critique.
        run_id:            The parent run this critique belongs to.
        goal:              Verbatim goal string — copied from the ExecutionPlan.
        overall_verdict:   High-level verdict: pass / partial / fail.
                             pass    → goal fully satisfied, no critical flags
                             partial → goal partially satisfied or warnings exist
                             fail    → goal not satisfied or critical flags exist
        confidence:        Critic's self-reported confidence in the verdict [0, 1].
                           Values below 0.5 should prompt human review.
        flags:             List of CritiqueFlag objects. Empty list = clean run.
        summary:           Concise paragraph summarising the critique outcome.
                           This is the primary text shown in the Streamlit report.
        recommendations:   Ordered list of suggested next actions for the user.
        steps_reviewed:    step_ids that the Critic examined. Used to detect if
                           the Critic skipped any steps.
        created_at:        UTC timestamp when the critique was produced.
    """

    critique_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUID4 identifier for this critique",
    )
    run_id: str = Field(..., description="Parent run identifier")
    goal: str = Field(..., min_length=5, description="Verbatim user goal being evaluated")
    overall_verdict: Verdict = Field(
        ...,
        description="High-level verdict: pass / partial / fail",
    )
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = Field(
        ...,
        description="Critic's self-reported confidence in the verdict [0.0, 1.0]",
    )
    flags: list[CritiqueFlag] = Field(
        default_factory=list,
        description="Identified issues; empty means a clean run",
    )
    summary: str = Field(
        ...,
        min_length=20,
        description="Concise paragraph summarising the critique outcome",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Ordered list of suggested next actions for the user",
    )
    steps_reviewed: list[str] = Field(
        default_factory=list,
        description="step_ids examined by the Critic",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the critique was produced",
    )

    @model_validator(mode="after")
    def verdict_consistent_with_flags(self) -> CritiqueResult:
        """Enforce consistency between verdict and flag severity.

        Rules:
        - A "pass" verdict must have zero critical flags.
        - A "fail" verdict should have at least one critical flag OR be
          explicit that the goal was not met (confidence < 0.5 is allowed).
        """
        critical_count = sum(1 for f in self.flags if f.severity == "critical")
        if self.overall_verdict == "pass" and critical_count > 0:
            raise ValueError(
                f"verdict='pass' is inconsistent with {critical_count} critical flag(s). "
                "Use 'partial' or 'fail'."
            )
        return self

    @field_validator("recommendations")
    @classmethod
    def recommendations_not_empty_strings(cls, v: list[str]) -> list[str]:
        """Strip and reject blank recommendation strings."""
        cleaned = [r.strip() for r in v if r.strip()]
        return cleaned

    # ── Convenience properties ─────────────────────────────────────────────────

    @property
    def critical_flags(self) -> list[CritiqueFlag]:
        """Return only critical-severity flags."""
        return [f for f in self.flags if f.severity == "critical"]

    @property
    def warning_flags(self) -> list[CritiqueFlag]:
        """Return only warning-severity flags."""
        return [f for f in self.flags if f.severity == "warning"]

    @property
    def is_clean(self) -> bool:
        """True if there are zero flags of any severity."""
        return len(self.flags) == 0

    @property
    def has_blocking_issues(self) -> bool:
        """True if the run produced any critical flags."""
        return len(self.critical_flags) > 0
