from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Tier = Literal["hot", "warm", "cold", "disqualified"]
Route = Literal["ae_immediate", "sdr_nurture", "marketing_nurture", "drop"]


class Score(BaseModel):
    email: str
    points: int = Field(..., ge=0, le=100, description="Total score 0-100")
    tier: Tier
    route: Route
    reason: str = ""
    rule_points: int = Field(..., description="Points from deterministic rules alone")
    llm_adjustment: int = Field(
        default=0,
        ge=-10,
        le=10,
        description="Bounded LLM nudge, clamped to [-10, +10]",
    )
