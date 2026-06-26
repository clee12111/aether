from pydantic import BaseModel, Field


class Lead(BaseModel):
    email: str = Field(..., description="Contact email address")
    name: str = Field(default="", description="Contact name")
    company: str = Field(default="", description="Company name")
    message: str = Field(default="", description="Inbound message or form submission text")
    source: str = Field(default="inbound_form", description="Lead source channel")
