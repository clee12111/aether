"""MCP server exposing the four GTM triage tools via FastMCP.

Wraps the SAME tool objects the agent uses. Running this server is optional —
the agent works without it. This exists so external clients (n8n, Claude Desktop,
etc.) can call the tools over the MCP protocol.

Usage:
    pip install fastmcp
    cd gtm-lead-triage
    python -m gtm_triage.mcp_server
"""

from __future__ import annotations

try:
    from fastmcp import FastMCP
except ImportError:
    raise ImportError(
        "fastmcp is required for the MCP server. Install with: pip install fastmcp"
    )

from gtm_triage.crm.sqlite_crm import SQLiteCRM
from gtm_triage.tools.crm_lookup import CRMLookupTool
from gtm_triage.tools.draft_outreach import DraftOutreachTool
from gtm_triage.tools.enrich_lead import EnrichLeadTool
from gtm_triage.tools.score_lead import ScoreLeadTool

# Shared state — single CRM instance for the server's lifetime
_crm = SQLiteCRM(":memory:")

_crm_lookup = CRMLookupTool(_crm)
_enrich = EnrichLeadTool()
_score = ScoreLeadTool()
_draft = DraftOutreachTool()

mcp = FastMCP("gtm-triage")


@mcp.tool()
def crm_lookup(email: str) -> dict:
    """Look up an existing CRM record by email."""
    return _crm_lookup.run({"email": email})


@mcp.tool()
def enrich_lead(email: str, company: str = "", name: str = "", message: str = "") -> dict:
    """Enrich a lead with industry, company size, seniority, and business-email status."""
    return _enrich.run({"email": email, "company": company, "name": name, "message": message})


@mcp.tool()
def score_lead(email: str, message: str = "", enrichment: dict | None = None, llm_adjustment: int = 0) -> dict:
    """Score a lead 0-100 using deterministic rules + optional LLM nudge."""
    return _score.run({
        "email": email,
        "message": message,
        "enrichment": enrichment or {},
        "llm_adjustment": llm_adjustment,
    })


@mcp.tool()
def draft_outreach(email: str, name: str = "", company: str = "", enrichment: dict | None = None, tier: str = "cold") -> dict:
    """Draft an outreach email based on tier. Status is always 'draft' — never sends."""
    return _draft.run({
        "email": email,
        "name": name,
        "company": company,
        "enrichment": enrichment or {},
        "tier": tier,
    })


if __name__ == "__main__":
    mcp.run()
