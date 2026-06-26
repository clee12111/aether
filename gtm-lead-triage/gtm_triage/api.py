"""FastAPI service for the GTM lead-triage agent.

Endpoints:
  POST /triage            — triage a lead; returns TriageResult JSON.
  POST /deliver           — record a routing outcome as a CRM activity.
  GET  /health            — liveness check.
  GET  /leads             — list recently triaged contacts.
  GET  /runs              — list recent run summaries.
  GET  /runs/{run_id}     — trace rows for a given run.
  GET  /contacts/{email}  — CRM record + activity timeline for a contact.

Infrastructure (CRM, trace store, tool registry, executor) is constructed ONCE
at startup and reused across requests.

Config via environment variables:
  GTM_PROVIDER       — "mock" (default) or "openai"
  GTM_MODEL          — model name (default "gpt-4o-mini")
  CRM_BACKEND        — "sqlite" (default) or "hubspot"
  GTM_CRM_DB         — CRM SQLite path (default "gtm_crm.db"); ignored if hubspot
  GTM_TRACE_DB       — trace SQLite path (default "gtm_trace.db"); ignored if DATABASE_URL
  DATABASE_URL       — Postgres DSN; if set, trace store uses Postgres instead of SQLite
  FRONTEND_ORIGIN    — comma-separated allowed CORS origins (default "http://localhost:3000")
  OPENAI_API_KEY     — required when GTM_PROVIDER=openai
  HUBSPOT_TOKEN      — required when CRM_BACKEND=hubspot
"""

from __future__ import annotations

import hashlib
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from gtm_triage.agents.executor import Executor
from gtm_triage.agents.loop_agent import run_triage
from gtm_triage.crm.base import CRMStore
from gtm_triage.crm.hubspot_crm import HubSpotCRM
from gtm_triage.crm.sqlite_crm import SQLiteCRM
from gtm_triage.models.action import TriageResult
from gtm_triage.models.lead import Lead
from gtm_triage.tools.crm_lookup import CRMLookupTool
from gtm_triage.tools.draft_outreach import DraftOutreachTool
from gtm_triage.tools.enrich_lead import EnrichLeadTool
from gtm_triage.tools.registry import ToolRegistry
from gtm_triage.tools.score_lead import ScoreLeadTool
from gtm_triage.trace.store import TraceStore

# ── Request/response models ─────────────────────────────────────────────────

class TriageRequest(BaseModel):
    email: str = Field(..., description="Contact email address")
    name: str = Field(default="", description="Contact name")
    company: str = Field(default="", description="Company name")
    message: str = Field(default="", description="Inbound message or form submission text")
    source: str = Field(default="inbound_form", description="Lead source channel")
    idempotency_key: str | None = Field(
        default=None,
        description="Optional idempotency key. If omitted, derived from hash(email+message+source).",
    )


class DeliverRequest(BaseModel):
    email: str = Field(..., description="Contact email")
    run_id: str = Field(..., description="Run ID from the triage step")
    tier: str = Field(..., description="Tier from triage (hot/warm/cold/disqualified)")
    route: str = Field(..., description="Route from triage")
    action: str = Field(
        default="",
        description="Delivery action taken (e.g. 'notified AE', 'added to nurture')",
    )


class DeliverResponse(BaseModel):
    email: str
    status: str
    activity_recorded: str


# ── Shared state, initialised in lifespan ────────────────────────────────────

_crm: CRMStore | None = None
_trace: TraceStore | None = None
_executor: Executor | None = None
_provider: str = "mock"
_model: str = "gpt-4o-mini"


# Route → default delivery action descriptions
_ROUTE_ACTIONS = {
    "ae_immediate": "notified AE for immediate follow-up",
    "sdr_nurture": "added to SDR nurture sequence",
    "marketing_nurture": "added to marketing nurture campaign",
    "drop": "dropped — no action taken",
}


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _crm, _trace, _executor, _provider, _model

    _provider = os.environ.get("GTM_PROVIDER", "mock")
    _model = os.environ.get("GTM_MODEL", "gpt-4o-mini")
    crm_backend = os.environ.get("CRM_BACKEND", "sqlite")
    crm_path = os.environ.get("GTM_CRM_DB", "gtm_crm.db")
    trace_path = os.environ.get("GTM_TRACE_DB", "gtm_trace.db")

    if crm_backend == "hubspot":
        token = os.environ.get("HUBSPOT_TOKEN", "")
        if not token:
            raise RuntimeError("CRM_BACKEND=hubspot requires HUBSPOT_TOKEN env var")
        _crm = HubSpotCRM(token)
    else:
        _crm = SQLiteCRM(crm_path)

    database_url = os.environ.get("DATABASE_URL", "")
    if database_url:
        from gtm_triage.trace.pg_store import PostgresTraceStore
        _trace = PostgresTraceStore(database_url)
    else:
        _trace = TraceStore(trace_path)

    registry = ToolRegistry([
        CRMLookupTool(_crm),
        EnrichLeadTool(provider=_provider, model=_model),
        ScoreLeadTool(provider=_provider, model=_model),
        DraftOutreachTool(),
    ])
    _executor = Executor(registry, _trace)

    yield

    _crm.close()
    _trace.close()


app = FastAPI(
    title="GTM Lead-Triage Agent",
    version="0.6.0",
    lifespan=_lifespan,
)

# CORS — allow the frontend origin (configurable, default localhost:3000)
_cors_origins = os.environ.get(
    "FRONTEND_ORIGIN",
    os.environ.get("CORS_ORIGINS", "http://localhost:3000"),
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/triage")
def triage(req: TriageRequest) -> TriageResult:
    # Derive idempotency key if not supplied
    idem_key = req.idempotency_key
    if not idem_key:
        raw = f"{req.email}|{req.message}|{req.source}"
        idem_key = hashlib.sha256(raw.encode()).hexdigest()

    # Check for prior run with this key
    prior = _trace.get_by_idempotency_key(idem_key)
    if prior is not None:
        return TriageResult(**prior["result"])

    # Run the agent
    lead = Lead(
        email=req.email, name=req.name, company=req.company,
        message=req.message, source=req.source,
    )
    result = run_triage(
        lead=lead,
        executor=_executor,
        trace=_trace,
        provider=_provider,
        model=_model,
    )
    crm_data: dict[str, Any] = {
        "email": lead.email,
        "name": lead.name,
        "company": lead.company,
        "tier": result.final_tier,
        "route": result.final_route,
        "run_id": result.run_id,
    }
    if result.score:
        crm_data["score"] = result.score.get("points", "")
    if result.enrichment:
        crm_data["industry"] = result.enrichment.get("industry", "")
        crm_data["seniority"] = result.enrichment.get("seniority", "")
    _crm.upsert(lead.email, crm_data)

    # Store idempotency key → result
    _trace.store_idempotency_key(idem_key, result.run_id, result.model_dump())

    return result


@app.post("/deliver")
def deliver(req: DeliverRequest) -> DeliverResponse:
    action_desc = req.action or _ROUTE_ACTIONS.get(req.route, f"routed {req.tier} -> {req.route}")
    existing = _crm.add_activity(req.email, {
        "type": "delivery",
        "run_id": req.run_id,
        "tier": req.tier,
        "route": req.route,
        "action": action_desc,
    })
    status = "already_recorded" if existing else "recorded"
    return DeliverResponse(
        email=req.email,
        status=status,
        activity_recorded=f"routed {req.tier} -> {action_desc}",
    )


@app.get("/contacts/{email}")
def get_contact(email: str) -> dict[str, Any]:
    record = _crm.lookup(email)
    activities = _crm.get_activities(email)
    return {
        "email": email,
        "record": record,
        "activities": activities,
    }


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    events = _trace.get_run_events(run_id)
    if not events:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    stats = _trace.get_run_stats(run_id)
    return {
        "run_id": run_id,
        "event_count": len(events),
        "stats": stats,
        "events": events,
    }


@app.get("/leads")
def list_leads(limit: int = 50) -> list[dict[str, Any]]:
    if isinstance(_crm, SQLiteCRM):
        return _crm.list_contacts(limit)
    return []


@app.get("/runs")
def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    return _trace.list_runs(limit)
