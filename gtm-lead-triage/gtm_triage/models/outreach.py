from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class OutreachDraft(BaseModel):
    email: str
    name: str = ""
    subject: str = ""
    body: str = ""
    status: Literal["draft"] = Field(default="draft", description="Always 'draft' — never sends")
