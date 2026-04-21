# aether/models/plan.py

from __future__ import annotations

import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, model_validator


class PlanStep(BaseModel):
    step_id: str = Field(..., pattern=r"^[a-z0-9_]{1,64}$")
    name: str
    description: str
    tool: str
    tool_args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    expected_output: str
    is_optional: bool = False


class ExecutionPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    goal: str
    steps: list[PlanStep] = Field(..., min_length=1)
    reasoning: str
    context_used: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def validate_steps(self) -> ExecutionPlan:
        ids = [s.step_id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate step_id in plan")
        known = set(ids)
        for step in self.steps:
            for dep in step.depends_on:
                if dep not in known:
                    raise ValueError(f"Step '{step.step_id}' depends on unknown '{dep}'")
        return self

    def topological_order(self) -> list[PlanStep]:
        """Return steps sorted so each step comes after its dependencies."""
        in_deg = {s.step_id: 0 for s in self.steps}
        children: dict[str, list[str]] = {s.step_id: [] for s in self.steps}
        for s in self.steps:
            for dep in s.depends_on:
                children[dep].append(s.step_id)
                in_deg[s.step_id] += 1

        queue = deque(sid for sid, d in in_deg.items() if d == 0)
        by_id = {s.step_id: s for s in self.steps}
        result: list[PlanStep] = []
        while queue:
            sid = queue.popleft()
            result.append(by_id[sid])
            for child in children[sid]:
                in_deg[child] -= 1
                if in_deg[child] == 0:
                    queue.append(child)

        if len(result) != len(self.steps):
            raise ValueError("Cycle detected in plan dependency graph")
        return result
