"""Campaign and outbound target models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Campaign(BaseModel):
    """ICP definition + value prop for an outbound campaign."""
    name: str
    icp_keywords: list[str] = Field(default_factory=list)
    icp_employee_ranges: list[str] = Field(default_factory=list)
    value_prop: str = ""
    target_persona: str = ""


class OutboundTarget(BaseModel):
    """A company + role persona to triage through the outbound motion.

    Satisfies the Signal protocol (email, name, company, message, source)
    so it can be passed directly to run_motion().
    """
    company: str
    domain: str
    persona_role: str = ""
    campaign: Campaign = Field(default_factory=Campaign)
    email: str = ""
    # Signal protocol fields — derived from the target
    name: str = ""
    message: str = ""
    source: str = "outbound_campaign"

    @classmethod
    def from_apollo_org(
        cls,
        org: Any,  # ApolloOrg — Any to avoid circular import
        persona_role: str,
        campaign: Campaign,
    ) -> OutboundTarget:
        return cls(
            company=org.name,
            domain=org.primary_domain or "",
            persona_role=persona_role,
            campaign=campaign,
            email=f"{persona_role.lower().replace(' ', '.')}@{org.primary_domain or 'unknown'}",
            name=persona_role,
        )

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        d = super().model_dump(**kwargs)
        d["campaign"] = self.campaign.model_dump()
        return d


class OutboundDraft(BaseModel):
    """A single outbound email draft variant."""
    subject: str
    body: str
    variant: Literal["A", "B"]
    grounded_on: list[str] = Field(default_factory=list)
    status: Literal["draft"] = "draft"
