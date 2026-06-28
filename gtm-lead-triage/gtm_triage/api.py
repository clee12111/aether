"""FastAPI service for the GTM lead-triage agent.

Endpoints:
  POST /triage            — triage a lead; returns TriageResult JSON.
  POST /deliver           — record a routing outcome as a CRM activity.
  GET  /health            — liveness check (public, no auth).
  GET  /leads             — list recently triaged contacts.
  GET  /runs              — list recent run summaries.
  GET  /runs/{run_id}     — trace rows for a given run.
  GET  /contacts/{email}  — CRM record + activity timeline for a contact.

Infrastructure (CRM, trace store, tool registry, executor) is constructed ONCE
at startup and reused across requests.

Config via environment variables:
  GTM_PROVIDER       — "mock" (default) or "openai"
  GTM_MODEL          — model name (default "gpt-4o-mini")
  ENRICHMENT_PROVIDER — "mock" (regex, default) or "pdl" (waterfall)
  CRM_BACKEND        — "sqlite" (default) or "hubspot"
  GTM_CRM_DB         — CRM SQLite path (default "gtm_crm.db"); ignored if hubspot
  GTM_TRACE_DB       — trace SQLite path (default "gtm_trace.db"); ignored if DATABASE_URL
  DATABASE_URL       — Postgres DSN; if set, trace store uses Postgres instead of SQLite
  FRONTEND_ORIGIN    — comma-separated allowed CORS origins (default "http://localhost:3000")
  OPENAI_API_KEY     — required when GTM_PROVIDER=openai
  HUBSPOT_TOKEN      — required when CRM_BACKEND=hubspot
  GTM_API_KEYS       — comma-separated API keys for auth (if unset, auth disabled)
  GTM_RATE_LIMIT_RPM — requests per minute per key/IP (default 60)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

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

_MAX_FIELD_LEN = 1000
_MAX_MESSAGE_LEN = 10000


class TriageRequest(BaseModel):
    email: str = Field(..., description="Contact email address", max_length=320)
    name: str = Field(default="", description="Contact name", max_length=_MAX_FIELD_LEN)
    company: str = Field(default="", description="Company name", max_length=_MAX_FIELD_LEN)
    message: str = Field(default="", description="Inbound message or form submission text", max_length=_MAX_MESSAGE_LEN)
    source: str = Field(default="inbound_form", description="Lead source channel", max_length=100)
    idempotency_key: str | None = Field(
        default=None,
        description="Optional idempotency key. If omitted, derived from hash(email+message+source).",
        max_length=128,
    )

    @field_validator("email")
    @classmethod
    def email_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("email must not be empty")
        return v.strip()

    @field_validator("tier", check_fields=False)
    @classmethod
    def tier_valid(cls, v: str) -> str:
        allowed = {"hot", "warm", "cold", "disqualified"}
        if v not in allowed:
            raise ValueError(f"tier must be one of {allowed}")
        return v


class DeliverRequest(BaseModel):
    email: str = Field(..., description="Contact email", max_length=320)
    run_id: str = Field(..., description="Run ID from the triage step", max_length=64)
    tier: str = Field(..., description="Tier from triage (hot/warm/cold/disqualified)")
    route: str = Field(..., description="Route from triage", max_length=50)
    action: str = Field(
        default="",
        description="Delivery action taken (e.g. 'notified AE', 'added to nurture')",
        max_length=_MAX_FIELD_LEN,
    )

    @field_validator("tier")
    @classmethod
    def tier_valid(cls, v: str) -> str:
        allowed = {"hot", "warm", "cold", "disqualified"}
        if v not in allowed:
            raise ValueError(f"tier must be one of {allowed}")
        return v


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
_daily_cap: int = 200


# Route → default delivery action descriptions
_ROUTE_ACTIONS = {
    "ae_immediate": "notified AE for immediate follow-up",
    "sdr_nurture": "added to SDR nurture sequence",
    "marketing_nurture": "added to marketing nurture campaign",
    "drop": "dropped — no action taken",
}


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _crm, _trace, _executor, _provider, _model, _daily_cap

    # Initialize observability (K2, K4, K6 — all no-op without config)
    from gtm_triage.observability.logging import setup_logging
    from gtm_triage.observability.sentry import init_sentry
    from gtm_triage.observability.tracing import init_tracing

    setup_logging()
    init_sentry()
    init_tracing()

    _provider = os.environ.get("GTM_PROVIDER", "openai")
    _model = os.environ.get("GTM_MODEL", "gpt-4o-mini")
    _daily_cap = int(os.environ.get("DAILY_QUERY_CAP", "200"))
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

    # Set daily cap gauge
    from gtm_triage.observability.metrics import metrics as _metrics
    _metrics.daily_cap_limit.set(float(_daily_cap))

    # Build enrichment provider based on ENRICHMENT_PROVIDER env
    enrichment_backend = os.environ.get("ENRICHMENT_PROVIDER", "mock")
    enrichment_provider = None
    if enrichment_backend == "pdl":
        from pathlib import Path
        from gtm_triage.enrichment.pdl_provider import PDLProvider
        from gtm_triage.enrichment.waterfall import WaterfallProvider
        cassettes = Path(__file__).parent / "enrichment" / "cache" / "pdl_cassettes.json"
        pdl = PDLProvider(cache_path=cassettes if cassettes.exists() else None)
        enrichment_provider = WaterfallProvider(pdl, skip_dns=False, skip_website=True)

    registry = ToolRegistry([
        CRMLookupTool(_crm),
        EnrichLeadTool(provider=_provider, model=_model, enrichment_provider=enrichment_provider),
        ScoreLeadTool(provider=_provider, model=_model),
        DraftOutreachTool(),
    ])
    _executor = Executor(registry, _trace)

    yield

    _crm.close()
    _trace.close()


app = FastAPI(
    title="GTM Lead-Triage Agent",
    version="0.7.0",
    lifespan=_lifespan,
)

# ── Middleware stack (order matters: outermost first) ─────────────────────────
from gtm_triage.middleware import (
    AuthMiddleware,
    MetricsMiddleware,
    RateLimitMiddleware,
    RequestIdMiddleware,
    RequestSizeLimitMiddleware,
    global_exception_handler,
)

# CORS — locked to known frontend origins (no wildcard in production)
_cors_origins = [
    o.strip() for o in
    os.environ.get("FRONTEND_ORIGIN", os.environ.get("CORS_ORIGINS", "http://localhost:3000")).split(",")
    if o.strip()
]

# Middleware execution order is OUTSIDE-IN (last-added runs first).
# CORS must be outermost so it adds headers to ALL responses — including
# auth rejections (401), rate-limit (429), and exceptions (500).
# Without this, the browser can't read error responses and shows "Failed to fetch".
#
# Execution order: CORS → RequestId → Auth → RateLimit → SizeLimit → Metrics → app
app.add_middleware(MetricsMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "X-API-Key", "Content-Type"],
)

# Global exception handler — no stack leaks
app.add_exception_handler(Exception, global_exception_handler)


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/config")
def get_config() -> dict[str, Any]:
    used = _trace.get_daily_usage() if _trace else 0
    can_use_openai = _provider == "openai" and bool(os.environ.get("OPENAI_API_KEY", ""))
    return {
        "provider": _provider,
        "model": _model,
        "crm_backend": os.environ.get("CRM_BACKEND", "sqlite"),
        "langfuse_enabled": bool(os.environ.get("LANGFUSE_PUBLIC_KEY", "")),
        "langfuse_host": os.environ.get("LANGFUSE_BASE_URL", "") or os.environ.get("LANGFUSE_HOST", ""),
        "daily_cap": _daily_cap,
        "used_today": used,
        "remaining": max(0, _daily_cap - used) if can_use_openai else 0,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> JSONResponse:
    """Readiness probe — checks actual dependency health.

    Returns 200 + {"ready": true} when core deps (trace, CRM) are up.
    Returns 503 + {"ready": false} when a core dep is down.
    Enrichment unavailability is degraded, not down.
    """
    checks: dict[str, str] = {}

    trace_ok = _trace.ping() if _trace else False
    checks["trace"] = "ok" if trace_ok else "fail"

    crm_ok = _crm.ping() if _crm else False
    checks["crm"] = "ok" if crm_ok else "fail"

    # Enrichment is optional — degraded, not down
    enrichment_backend = os.environ.get("ENRICHMENT_PROVIDER", "mock")
    checks["enrichment"] = "ok" if enrichment_backend == "mock" else "degraded"

    core_ready = trace_ok and crm_ok
    body = {"ready": core_ready, "checks": checks}
    status_code = 200 if core_ready else 503
    return JSONResponse(content=body, status_code=status_code)


@app.get("/metrics")
def get_metrics() -> Response:
    """Prometheus-format metrics scrape target (public, no auth)."""
    from gtm_triage.observability.metrics import render_metrics
    daily_used = _trace.get_daily_usage() if _trace else 0
    body = render_metrics(daily_cap_used=daily_used, daily_cap_limit=_daily_cap)
    return Response(content=body, media_type="text/plain; version=0.0.4")


@app.get("/metrics/outcomes")
def get_outcome_metrics() -> dict[str, Any]:
    """Precision-against-outcome per tier (public, no auth).

    Returns empty per-tier objects if no outcomes have been recorded.
    """
    if not _trace or not hasattr(_trace, "get_outcome_metrics"):
        return {}
    return _trace.get_outcome_metrics()


# OUTCOME LOOP STUB — foundation for a future CRM-sync feedback loop.
# Current implementation is a manual POST; a future phase would auto-sync
# from HubSpot deal-close webhooks.

_VALID_OUTCOMES = {"converted", "no_show", "unqualified", "unknown"}


class OutcomeRequest(BaseModel):
    actual_outcome: str = Field(..., description="Outcome of the triage prediction")
    recorded_by: str = Field(default="", description="Who/what recorded this", max_length=100)

    @field_validator("actual_outcome")
    @classmethod
    def outcome_valid(cls, v: str) -> str:
        if v not in _VALID_OUTCOMES:
            raise ValueError(f"actual_outcome must be one of {_VALID_OUTCOMES}")
        return v


@app.post("/outcomes/{run_id}", status_code=201)
def record_outcome(run_id: str, req: OutcomeRequest) -> dict[str, Any]:
    """Record the actual outcome for a triage run (write-once)."""
    # Check run exists
    events = _trace.get_run_events(run_id)
    if not events:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    # Check for existing outcome (write-once guard)
    existing = _trace.get_outcome(run_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Outcome already recorded for {run_id}")

    # Get predicted tier from the run result
    result = _trace.get_result_by_run_id(run_id)
    predicted_tier = result.get("final_tier", "unknown") if result else "unknown"

    outcome_id = _trace.record_outcome(
        run_id=run_id,
        predicted_tier=predicted_tier,
        actual_outcome=req.actual_outcome,
        recorded_by=req.recorded_by,
    )
    return {
        "outcome_id": outcome_id,
        "run_id": run_id,
        "predicted_tier": predicted_tier,
        "actual_outcome": req.actual_outcome,
    }


@app.post("/triage")
async def triage(req: TriageRequest) -> dict[str, Any]:
    # Derive idempotency key if not supplied
    idem_key = req.idempotency_key
    if not idem_key:
        raw = f"{req.email}|{req.message}|{req.source}"
        idem_key = hashlib.sha256(raw.encode()).hexdigest()

    # Check for prior run with this key
    from gtm_triage.observability.metrics import metrics as _metrics
    prior = _trace.get_by_idempotency_key(idem_key)
    if prior is not None:
        _metrics.cache_hit_total.inc()
        return prior["result"]
    _metrics.cache_miss_total.inc()

    # Daily cap: if provider is openai but over the cap, fall back to mock
    effective_provider = _provider
    has_openai_key = bool(os.environ.get("OPENAI_API_KEY", ""))
    if effective_provider == "openai" and has_openai_key:
        used = _trace.get_daily_usage()
        if used >= _daily_cap:
            effective_provider = "mock"
    elif effective_provider == "openai" and not has_openai_key:
        effective_provider = "mock"

    # Build executor with the effective provider (may differ from _provider)
    if effective_provider != _provider:
        eff_registry = ToolRegistry([
            CRMLookupTool(_crm),
            EnrichLeadTool(provider=effective_provider, model=_model),
            ScoreLeadTool(provider=effective_provider, model=_model),
            DraftOutreachTool(),
        ])
        eff_executor = Executor(eff_registry, _trace)
    else:
        eff_executor = _executor  # uses enrichment_provider if configured

    # Run the agent off the request thread (pipeline can take ~10s with LLM)
    import time as _time
    lead = Lead(
        email=req.email, name=req.name, company=req.company,
        message=req.message, source=req.source,
    )
    t0 = _time.monotonic()
    result = await asyncio.to_thread(
        run_triage,
        lead=lead,
        executor=eff_executor,
        trace=_trace,
        provider=effective_provider,
        model=_model,
    )
    triage_duration = _time.monotonic() - t0

    # Record triage metrics
    _metrics.triage_duration_seconds.observe(triage_duration, provider=effective_provider)
    _metrics.triage_total.inc(
        tier=result.final_tier or "unknown",
        route=result.final_route or "unknown",
        provider=effective_provider,
    )

    # Increment daily usage only for real openai runs
    if effective_provider == "openai":
        _trace.increment_daily_usage()

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

    # Store idempotency key → result (with provider tag)
    result_dict = result.model_dump()
    result_dict["provider_used"] = effective_provider
    _trace.store_idempotency_key(idem_key, result.run_id, result_dict)

    return result_dict


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
    # Include cached triage result for detail panel cards
    triage_result = _trace.get_result_by_run_id(run_id)
    resp: dict[str, Any] = {
        "run_id": run_id,
        "event_count": len(events),
        "stats": stats,
        "events": events,
    }
    if triage_result:
        resp["triage_result"] = triage_result
    return resp


@app.get("/leads")
def list_leads(limit: int = 50) -> list[dict[str, Any]]:
    if isinstance(_crm, SQLiteCRM):
        return _crm.list_contacts(limit)
    return []


@app.get("/runs")
def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    return _trace.list_runs(limit)


@app.delete("/contacts/{email}")
def delete_contact(email: str) -> dict[str, Any]:
    """Right-to-erasure: delete contact record, activities, and trace data."""
    crm_deleted = _crm.delete_contact(email)
    trace_runs_deleted = _trace.delete_by_email(email) if hasattr(_trace, "delete_by_email") else 0
    if not crm_deleted and trace_runs_deleted == 0:
        raise HTTPException(status_code=404, detail=f"No data found for {email}")
    return {
        "email": email,
        "status": "deleted",
        "crm_record_deleted": crm_deleted,
        "trace_runs_deleted": trace_runs_deleted,
    }
