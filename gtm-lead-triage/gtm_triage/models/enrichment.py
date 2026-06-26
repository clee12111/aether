from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Enrichment(BaseModel):
    email: str
    company: str = ""
    industry: str = ""
    company_size: Literal["enterprise", "mid_market", "smb", "unknown"] = "unknown"
    role: str = ""
    seniority: Literal["c_level", "vp", "director", "manager", "ic", "unknown"] = "unknown"
    is_business_email: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: str = "mock"
