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
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

from gtm_triage.agents.executor import Executor
from gtm_triage.agents.loop_agent import run_outbound, run_triage
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

    database_url = os.environ.get("DATABASE_URL", "").strip()

    # When DATABASE_URL is set, ALWAYS use Postgres for CRM (deploy-safe).
    # HubSpot only when explicitly chosen AND no DATABASE_URL.
    if database_url:
        from gtm_triage.crm.pg_crm import PostgresCRM
        _crm = PostgresCRM(database_url)
    elif crm_backend == "hubspot":
        token = os.environ.get("HUBSPOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("CRM_BACKEND=hubspot requires HUBSPOT_TOKEN env var")
        _crm = HubSpotCRM(token)
    else:
        _crm = SQLiteCRM(crm_path)

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

    # ── Startup secret diagnostics (masked — never log values) ──────────
    _secrets = {
        "OPENAI_API_KEY": bool(os.environ.get("OPENAI_API_KEY", "")),
        "HUBSPOT_TOKEN": bool(os.environ.get("HUBSPOT_TOKEN", "")),
        "PDL_API_KEY": bool(os.environ.get("PDL_API_KEY", "")),
        "DATABASE_URL": bool(os.environ.get("DATABASE_URL", "")),
        "LANGFUSE_SECRET_KEY": bool(os.environ.get("LANGFUSE_SECRET_KEY", "")),
    }
    present = [k for k, v in _secrets.items() if v]
    absent = [k for k, v in _secrets.items() if not v]
    logger.info(
        "startup_secrets",
        extra={
            "present": present,
            "absent": absent,
            "enrichment_provider": enrichment_backend,
            "crm_backend": crm_backend,
            "gtm_provider": _provider,
        },
    )

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
    can_use_openai = _provider != "mock"
    return {
        "provider": _provider,
        "model": _model,
        "crm_backend": os.environ.get("CRM_BACKEND", "sqlite"),
        "enrichment_provider": os.environ.get("ENRICHMENT_PROVIDER", "mock"),
        "langfuse_enabled": bool(os.environ.get("LANGFUSE_PUBLIC_KEY", "")),
        "langfuse_host": os.environ.get("LANGFUSE_BASE_URL", "") or os.environ.get("LANGFUSE_HOST", ""),
        "daily_cap": _daily_cap,
        "used_today": used,
        "remaining": max(0, _daily_cap - used) if can_use_openai else 0,
        # Masked secret presence (boolean only, never values)
        "openai_key_set": bool(os.environ.get("OPENAI_API_KEY", "")),
        "hubspot_token_set": bool(os.environ.get("HUBSPOT_TOKEN", "")),
        "pdl_key_set": bool(os.environ.get("PDL_API_KEY", "")),
        "database_url_set": bool(os.environ.get("DATABASE_URL", "")),
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
    if not _trace:
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

    # Daily cap: if using a real LLM provider, fall back to mock when over cap
    effective_provider = _provider
    if effective_provider != "mock":
        used = _trace.get_daily_usage()
        if used >= _daily_cap:
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

    # Increment daily usage only for real LLM runs (not mock)
    if effective_provider != "mock":
        _trace.increment_daily_usage()

    crm_data: dict[str, Any] = {
        "email": lead.email,
        "name": lead.name,
        "company": lead.company,
        "tier": result.final_tier,
        "route": result.final_route,
        "run_id": result.run_id,
        "source": lead.source or "web_form",
    }
    if result.score:
        crm_data["score"] = result.score.get("points", "")
    if result.enrichment:
        crm_data["industry"] = result.enrichment.get("industry", "")
        crm_data["seniority"] = result.enrichment.get("seniority", "")
    try:
        _crm.upsert(lead.email, crm_data)
    except Exception as exc:
        logger.warning("CRM upsert failed for run %s: %s", result.run_id[:8], exc)

    # Enrich-once: attach company-research brief to the stored result
    # so downstream outbound can reuse it without re-enriching
    brief: dict[str, Any] | None = None
    domain = lead.email.rsplit("@", 1)[1] if "@" in lead.email else ""
    if domain:
        try:
            from gtm_triage.tools.research_company import ResearchCompanyTool
            research_tool = ResearchCompanyTool(provider="mock")
            brief = research_tool.run({"domain": domain, "role": "", "email": lead.email})
        except Exception as exc:
            logger.debug("Company research for %s failed: %s", domain, exc)

    # Productboard write-back: log the lead's request by domain
    pb_note: dict[str, Any] | None = None
    try:
        from gtm_triage.productboard.writeback import write_lead_to_productboard
        pb_note = write_lead_to_productboard(
            email=lead.email, message=lead.message,
            company=lead.company, name=lead.name,
            run_id=result.run_id, trace=_trace,
        )
    except Exception as exc:
        logger.debug("PB write-back failed: %s", exc)

    # Store idempotency key -> result (with provider tag + brief + PB note)
    result_dict = result.model_dump()
    result_dict["provider_used"] = effective_provider
    result_dict["motion"] = "inbound"
    if brief:
        result_dict["company_brief"] = brief
    if pb_note:
        result_dict["pb_note"] = pb_note
    _trace.store_idempotency_key(idem_key, result.run_id, result_dict)

    # Auto-draft for warm/hot leads (non-blocking, best-effort)
    if result.final_tier in ("hot", "warm"):
        try:
            _auto_draft_campaign = Campaign(
                name="Auto ICP", value_prop="help your team work more effectively",
                icp_keywords=["saas", "product management"], target_persona="Head of Product",
            )
            _auto_target = OutboundTarget(
                company=lead.company, domain=domain,
                persona_role="Head of Product", campaign=_auto_draft_campaign,
                email=lead.email, name=lead.name,
            )
            _auto_result = _run_single_outbound(_auto_target, effective_provider)
            _auto_result["run_type"] = "outbound_email"
            _auto_result["motion"] = "outbound"
            _auto_result["source_run_id"] = result.run_id
            _auto_idem = hashlib.sha256(f"from-lead|{lead.email}|Auto ICP".encode()).hexdigest()
            _trace.store_idempotency_key(_auto_idem, _auto_result.get("run_id", ""), _auto_result)
        except Exception as exc:
            logger.debug("Auto-draft failed for %s: %s", result.run_id[:8], exc)

    return result_dict


# Manual Productboard send
class PBSendRequest(BaseModel):
    email: str = Field(..., max_length=320)
    content: str = Field(..., max_length=5000)
    company: str = Field(default="", max_length=500)


@app.post("/productboard/send")
async def send_to_productboard(req: PBSendRequest) -> dict[str, Any]:
    """Manually send a request to Productboard for a lead."""
    domain = req.email.rsplit("@", 1)[1].lower() if "@" in req.email else ""
    if not domain:
        raise HTTPException(status_code=422, detail="Invalid email")

    pb_source = os.environ.get("PRODUCTBOARD_SOURCE", "fixture").lower()
    if pb_source == "off":
        raise HTTPException(status_code=422, detail="Productboard is disabled")

    try:
        from gtm_triage.productboard import get_productboard_client
        pb = get_productboard_client()
        result = pb.create_feedback(
            title=f"{req.company or domain} - manual request",
            content=req.content,
            customer_email=req.email,
            company_domain=domain,
            tags=["inbound", "manual"],
        )
        return {
            "note_id": result.id,
            "note_url": result.display_url,
            "title": result.name,
            "domain": domain,
        }
    except Exception as exc:
        logger.warning("Productboard send failed: %s", exc)
        raise HTTPException(status_code=500, detail="Productboard write failed")


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
    contacts = _crm.list_contacts(limit)

    # Join CRM contacts with their latest run from the trace store.
    # list_runs returns lead_email + run_id from run_end events.
    runs = _trace.list_runs(limit=200)
    email_to_run: dict[str, str] = {}
    for r in runs:
        em = r.get("lead_email", "")
        if em and em not in email_to_run:
            email_to_run[em] = r["run_id"]

    for contact in contacts:
        if "run_id" not in contact or not contact.get("run_id"):
            contact["run_id"] = email_to_run.get(contact.get("email", ""), "")

    return contacts


@app.get("/runs")
def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    return _trace.list_runs(limit)


@app.delete("/contacts/{email}")
def delete_contact(email: str) -> dict[str, Any]:
    """Right-to-erasure: delete contact record, activities, and trace data."""
    crm_deleted = _crm.delete_contact(email)
    trace_runs_deleted = _trace.delete_by_email(email)
    if not crm_deleted and trace_runs_deleted == 0:
        raise HTTPException(status_code=404, detail=f"No data found for {email}")
    return {
        "email": email,
        "status": "deleted",
        "crm_record_deleted": crm_deleted,
        "trace_runs_deleted": trace_runs_deleted,
    }


# ── Outbound motion endpoints ──────────────────────────────────────────────

from gtm_triage.models.campaign import Campaign, OutboundTarget
from gtm_triage.tools.draft_outbound import DraftOutboundTool
from gtm_triage.tools.fit_score import FitScoreTool
from gtm_triage.tools.research_company import ResearchCompanyTool

_OUTBOUND_BATCH_CAP = 25


class CampaignRequest(BaseModel):
    name: str = Field(..., max_length=200)
    icp_keywords: list[str] = Field(default_factory=list)
    icp_employee_ranges: list[str] = Field(default_factory=list)
    value_prop: str = Field(default="", max_length=_MAX_MESSAGE_LEN)
    target_persona: str = Field(default="", max_length=_MAX_FIELD_LEN)


class OutboundTargetRequest(BaseModel):
    company: str = Field(..., max_length=_MAX_FIELD_LEN)
    domain: str = Field(..., max_length=320)
    persona_role: str = Field(default="Head of Product", max_length=_MAX_FIELD_LEN)
    campaign: CampaignRequest
    email: str | None = Field(default=None, max_length=320)
    idempotency_key: str | None = Field(default=None, max_length=128)

    @field_validator("domain")
    @classmethod
    def domain_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("domain must not be empty")
        return v.strip()


class ApolloSourceConfig(BaseModel):
    keyword_tags: list[str] = Field(default_factory=list)
    employee_ranges: list[str] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1)


class OutboundCampaignRequest(BaseModel):
    campaign: CampaignRequest
    source: dict[str, ApolloSourceConfig] = Field(default_factory=dict)
    persona_role: str = Field(default="Head of Product", max_length=_MAX_FIELD_LEN)


def _build_outbound_executor(provider: str, model: str) -> Executor:
    """Build an executor with outbound tools."""
    registry = ToolRegistry([
        ResearchCompanyTool(provider=provider, model=model),
        FitScoreTool(provider=provider, model=model),
        DraftOutboundTool(provider=provider, model=model),
    ])
    return Executor(registry, _trace)


def _run_single_outbound(
    target: OutboundTarget,
    effective_provider: str,
) -> dict[str, Any]:
    """Run outbound on a single target, with CRM upsert + idempotency. Sync."""
    import time as _time
    from gtm_triage.observability.metrics import metrics as _metrics

    executor = _build_outbound_executor(effective_provider, _model)

    t0 = _time.monotonic()
    result = run_outbound(
        target=target,
        executor=executor,
        trace=_trace,
        provider=effective_provider,
        model=_model,
    )
    duration = _time.monotonic() - t0

    # Metrics
    _metrics.triage_duration_seconds.observe(duration, provider=effective_provider)
    _metrics.triage_total.inc(
        tier=result.final_tier or "unknown",
        route=result.final_route or "unknown",
        provider=effective_provider,
    )

    # Daily usage
    if effective_provider != "mock":
        _trace.increment_daily_usage()

    # CRM: only upsert for standalone outbound targets (not from-lead,
    # which would overwrite the inbound lead's CRM data)
    if target.source != "outbound_campaign" or not _crm.lookup(target.email):
        crm_key = target.email or f"{target.persona_role.lower().replace(' ', '.')}@{target.domain}"
        crm_data: dict[str, Any] = {
            "email": crm_key,
            "name": target.persona_role or target.name,
            "company": target.company,
            "tier": result.final_tier,
            "route": result.final_route,
            "run_id": result.run_id,
            "motion": "outbound",
        }
        if result.enrichment:
            crm_data["industry"] = result.enrichment.get("industry", "")
        if result.score:
            crm_data["score"] = result.score.get("points", "")
        try:
            _crm.upsert(crm_key, crm_data)
        except Exception as exc:
            logger.warning("CRM upsert failed for outbound %s: %s", crm_key, exc)

    return result.model_dump()


@app.post("/outbound/target")
async def outbound_target(req: OutboundTargetRequest) -> dict[str, Any]:
    """Triage a single outbound target: research → fit-score → draft."""
    # Idempotency
    idem_key = req.idempotency_key
    if not idem_key:
        raw = f"outbound|{req.domain}|{req.persona_role}|{req.campaign.name}"
        idem_key = hashlib.sha256(raw.encode()).hexdigest()

    from gtm_triage.observability.metrics import metrics as _metrics
    prior = _trace.get_by_idempotency_key(idem_key)
    if prior is not None:
        _metrics.cache_hit_total.inc()
        return prior["result"]
    _metrics.cache_miss_total.inc()

    # Daily cap
    effective_provider = _provider
    if effective_provider != "mock":
        used = _trace.get_daily_usage()
        if used >= _daily_cap:
            effective_provider = "mock"

    campaign = Campaign(**req.campaign.model_dump())
    target = OutboundTarget(
        company=req.company,
        domain=req.domain,
        persona_role=req.persona_role,
        campaign=campaign,
        email=req.email or f"{req.persona_role.lower().replace(' ', '.')}@{req.domain}",
        name=req.persona_role,
    )

    result_dict = await asyncio.to_thread(
        _run_single_outbound, target, effective_provider,
    )
    result_dict["provider_used"] = effective_provider

    # Store idempotency
    _trace.store_idempotency_key(idem_key, result_dict.get("run_id", ""), result_dict)

    return result_dict


@app.post("/outbound/campaign")
async def outbound_campaign(req: OutboundCampaignRequest) -> dict[str, Any]:
    """Run outbound on a batch of targets from Apollo search."""
    # Daily cap check
    effective_provider = _provider
    if effective_provider != "mock":
        used = _trace.get_daily_usage()
        remaining = max(0, _daily_cap - used)
        if remaining == 0:
            effective_provider = "mock"
    else:
        remaining = _OUTBOUND_BATCH_CAP

    # Build campaign
    campaign = Campaign(**req.campaign.model_dump())

    # Source targets from Apollo
    apollo_config = req.source.get("apollo")
    if not apollo_config:
        raise HTTPException(status_code=422, detail="source.apollo is required")

    from gtm_triage.apollo import get_apollo_client
    apollo = get_apollo_client()
    batch_limit = min(apollo_config.limit, _OUTBOUND_BATCH_CAP, remaining)

    search_result = apollo.search_organizations(
        keyword_tags=apollo_config.keyword_tags or None,
        employee_ranges=apollo_config.employee_ranges or None,
        per_page=batch_limit,
    )

    # Build targets from Apollo orgs
    targets = [
        OutboundTarget.from_apollo_org(org, req.persona_role, campaign)
        for org in search_result.organizations[:batch_limit]
    ]

    # Run each target (sequentially to respect rate limits)
    results: list[dict[str, Any]] = []
    for target in targets:
        # Re-check daily cap per target
        if effective_provider != "mock":
            used = _trace.get_daily_usage()
            if used >= _daily_cap:
                effective_provider = "mock"

        result_dict = await asyncio.to_thread(
            _run_single_outbound, target, effective_provider,
        )
        result_dict["provider_used"] = effective_provider
        result_dict["run_type"] = "outbound_campaign"
        result_dict["motion"] = "outbound"
        results.append(result_dict)

    # Summary
    tier_counts: dict[str, int] = {}
    for r in results:
        tier = r.get("final_tier") or "unknown"
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    return {
        "campaign": campaign.name,
        "targets_searched": search_result.pagination.get("total_entries", 0),
        "targets_processed": len(results),
        "provider_used": effective_provider,
        "tier_summary": tier_counts,
        "results": results,
    }


@app.get("/outbound/runs/{run_id}")
def get_outbound_run(run_id: str) -> dict[str, Any]:
    """Trace for an outbound run — reuses the inbound run reader."""
    return get_run(run_id)


# ── Multi-channel intake endpoints ─────────────────────────────────────────

from gtm_triage.channels.chat import ChatAdapter
from gtm_triage.channels.clay import ClayWebhookAdapter
from gtm_triage.channels.email import EmailAdapter


class EmailIntakeRequest(BaseModel):
    raw_email: str = Field(..., description="Raw email text (headers + body)", max_length=50000)


class ChatIntakeRequest(BaseModel):
    transcript: str = Field(..., description="Chat transcript text", max_length=50000)


class ClayWebhookRequest(BaseModel):
    row: dict[str, Any] = Field(..., description="Clay-enriched row (column->value)")


async def _run_intake(parsed_lead, effective_provider: str) -> dict[str, Any]:
    """Shared intake pipeline: parsed Lead -> run_triage -> result dict."""
    import time as _time
    from gtm_triage.observability.metrics import metrics as _metrics

    # Idempotency
    idem_raw = f"{parsed_lead.source}|{parsed_lead.lead.email}|{parsed_lead.lead.message[:100]}"
    idem_key = hashlib.sha256(idem_raw.encode()).hexdigest()

    prior = _trace.get_by_idempotency_key(idem_key)
    if prior is not None:
        _metrics.cache_hit_total.inc()
        return prior["result"]
    _metrics.cache_miss_total.inc()

    # Build executor with effective provider
    if effective_provider != _provider:
        eff_registry = ToolRegistry([
            CRMLookupTool(_crm),
            EnrichLeadTool(provider=effective_provider, model=_model),
            ScoreLeadTool(provider=effective_provider, model=_model),
            DraftOutreachTool(),
        ])
        eff_executor = Executor(eff_registry, _trace)
    else:
        eff_executor = _executor

    t0 = _time.monotonic()
    result = await asyncio.to_thread(
        run_triage,
        lead=parsed_lead.lead,
        executor=eff_executor,
        trace=_trace,
        provider=effective_provider,
        model=_model,
    )
    duration = _time.monotonic() - t0

    _metrics.triage_duration_seconds.observe(duration, provider=effective_provider)
    _metrics.triage_total.inc(
        tier=result.final_tier or "unknown",
        route=result.final_route or "unknown",
        provider=effective_provider,
    )

    if effective_provider != "mock":
        _trace.increment_daily_usage()

    # CRM upsert
    crm_data: dict[str, Any] = {
        "email": parsed_lead.lead.email,
        "name": parsed_lead.lead.name,
        "company": parsed_lead.lead.company,
        "tier": result.final_tier,
        "route": result.final_route,
        "run_id": result.run_id,
        "motion": "inbound",
        "source": parsed_lead.source,
    }
    if result.score:
        crm_data["score"] = result.score.get("points", "")
    if result.enrichment:
        crm_data["industry"] = result.enrichment.get("industry", "")
    try:
        _crm.upsert(parsed_lead.lead.email, crm_data)
    except Exception as exc:
        logger.warning("CRM upsert failed for %s: %s", parsed_lead.lead.email, exc)

    result_dict = result.model_dump()
    result_dict["provider_used"] = effective_provider
    result_dict["source"] = parsed_lead.source
    result_dict["parsed_lead"] = parsed_lead.lead.model_dump()
    result_dict["extraction_confidence"] = parsed_lead.extraction_confidence
    result_dict["field_sources"] = parsed_lead.field_sources

    _trace.store_idempotency_key(idem_key, result.run_id, result_dict)
    return result_dict


def _effective_provider() -> str:
    """Determine effective provider respecting daily cap."""
    p = _provider
    if p != "mock":
        used = _trace.get_daily_usage()
        if used >= _daily_cap:
            p = "mock"
    return p


@app.post("/intake/email")
async def intake_email(req: EmailIntakeRequest) -> dict[str, Any]:
    """Intake a raw email → parse → triage."""
    try:
        parsed = EmailAdapter().to_lead(req.raw_email)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return await _run_intake(parsed, _effective_provider())


@app.post("/intake/chat")
async def intake_chat(req: ChatIntakeRequest) -> dict[str, Any]:
    """Intake a chat transcript → parse → triage."""
    try:
        parsed = ChatAdapter().to_lead(req.transcript)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return await _run_intake(parsed, _effective_provider())


@app.post("/webhooks/clay")
async def webhook_clay(req: ClayWebhookRequest) -> dict[str, Any]:
    """Intake a Clay webhook row -> parse -> triage."""
    try:
        parsed = ClayWebhookAdapter().to_lead(req.row)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return await _run_intake(parsed, _effective_provider())


# ── Journey + from-lead endpoints ──────────────────────────────────────────


@app.get("/outbound/by-lead/{email}")
def get_outbound_for_lead(email: str) -> dict[str, Any]:
    """Get existing outbound results for a lead WITHOUT re-running.

    Returns outbound_email + outbound_campaign results if they exist.
    """
    all_runs = _trace.list_runs(limit=500)
    lead_runs = [r for r in all_runs if r.get("lead_email", "") == email]

    outbound_results: list[dict[str, Any]] = []
    for run_summary in lead_runs:
        stored = _trace.get_result_by_run_id(run_summary["run_id"])
        if stored and stored.get("motion") == "outbound":
            outbound_results.append(stored)

    return {"email": email, "results": outbound_results}


@app.get("/journey/{email}")
def get_journey(email: str) -> dict[str, Any]:
    """Get the full inbound->outbound journey for a lead by email.

    Returns the inbound run + any outbound runs, each with their full traces,
    as one ordered timeline.
    """
    all_runs = _trace.list_runs(limit=500)
    lead_runs = [r for r in all_runs if r.get("lead_email", "") == email]

    if not lead_runs:
        raise HTTPException(status_code=404, detail=f"No runs found for {email}")

    journey: list[dict[str, Any]] = []
    for run_summary in lead_runs:
        run_id = run_summary["run_id"]
        events = _trace.get_run_events(run_id)
        stats = _trace.get_run_stats(run_id)
        stored_result = _trace.get_result_by_run_id(run_id)
        journey.append({
            "run_id": run_id,
            "motion": stored_result.get("motion", "inbound") if stored_result else "inbound",
            "run_type": stored_result.get("run_type", "inbound") if stored_result else "inbound",
            "final_tier": run_summary.get("final_tier"),
            "final_route": run_summary.get("final_route"),
            "trace_path": stored_result.get("trace_path", "") if stored_result else "",
            "started_at": run_summary.get("started_at"),
            "event_count": len(events),
            "stats": stats,
            "events": events,
            "result": stored_result,
        })

    return {"email": email, "runs": journey}


class FromLeadRequest(BaseModel):
    email: str = Field(..., max_length=320)
    campaign: CampaignRequest


@app.post("/outbound/from-lead")
async def outbound_from_lead(req: FromLeadRequest) -> dict[str, Any]:
    """Build an OutboundTarget from an inbound lead's stored brief, then run outbound."""
    # Idempotency check (same as /triage and /outbound/target)
    from gtm_triage.observability.metrics import metrics as _metrics
    idem_key = hashlib.sha256(
        f"from-lead|{req.email}|{req.campaign.name}".encode()
    ).hexdigest()
    prior = _trace.get_by_idempotency_key(idem_key)
    if prior is not None:
        _metrics.cache_hit_total.inc()
        return prior["result"]
    _metrics.cache_miss_total.inc()

    # Find the inbound lead's stored result (most recent run for this email)
    all_runs = _trace.list_runs(limit=200)
    lead_runs = [r for r in all_runs if r.get("lead_email", "") == req.email]
    if not lead_runs:
        raise HTTPException(status_code=404, detail=f"No inbound run found for {req.email}")

    source_run_id = lead_runs[0]["run_id"]
    stored = _trace.get_result_by_run_id(source_run_id)

    # Look up CRM data for company name
    crm_record = _crm.lookup(req.email)
    company = (crm_record or {}).get("company", "")
    domain = req.email.rsplit("@", 1)[1] if "@" in req.email else ""

    # Build campaign + target
    campaign = Campaign(**req.campaign.model_dump())
    target = OutboundTarget(
        company=company or domain,
        domain=domain,
        persona_role=req.campaign.target_persona or "Head of Product",
        campaign=campaign,
        email=req.email,
        name=(crm_record or {}).get("name", ""),
    )

    effective_provider = _effective_provider()
    result_dict = await asyncio.to_thread(
        _run_single_outbound, target, effective_provider,
    )
    result_dict["provider_used"] = effective_provider
    result_dict["source_run_id"] = source_run_id
    result_dict["motion"] = "outbound"
    result_dict["run_type"] = "outbound_email"

    # Store with idempotency
    idem_key = hashlib.sha256(
        f"from-lead|{req.email}|{req.campaign.name}".encode()
    ).hexdigest()
    _trace.store_idempotency_key(idem_key, result_dict.get("run_id", ""), result_dict)

    return result_dict


class CompanyCampaignRequest(BaseModel):
    domain: str = Field(..., max_length=320)
    company: str = Field(default="", max_length=500)
    campaign: CampaignRequest
    apollo_keyword_tags: list[str] = Field(default_factory=list)
    apollo_employee_ranges: list[str] = Field(default_factory=list)
    apollo_limit: int = Field(default=3, ge=1)


# Keep old endpoint as an alias for backward compat
class CampaignFromLeadRequest(BaseModel):
    email: str = Field(..., max_length=320)
    campaign: CampaignRequest
    apollo_keyword_tags: list[str] = Field(default_factory=list)
    apollo_employee_ranges: list[str] = Field(default_factory=list)
    apollo_limit: int = Field(default=3, ge=1)


def _run_domain_campaign(
    domain: str, company: str, campaign: Campaign,
    keyword_tags: list[str], employee_ranges: list[str], limit: int,
) -> dict[str, Any]:
    """Core campaign logic: Apollo search -> enrich + score + draft per target.

    This does REAL work: finds similar companies, researches each, scores ICP
    fit, and drafts tailored outreach. Respects daily cap.
    """
    from gtm_triage.apollo import get_apollo_client
    from gtm_triage.tools.research_company import ResearchCompanyTool
    from gtm_triage.tools.fit_score import FitScoreTool
    from gtm_triage.tools.draft_outbound import DraftOutboundTool

    run_id = f"campaign-{hashlib.sha256(domain.encode()).hexdigest()[:12]}"
    effective_provider = _effective_provider()

    # Step 1: Apollo search for similar companies
    target_results: list[dict[str, Any]] = []
    _trace.write(
        run_id=run_id, event_type="run_start", agent="campaign",
        payload={"domain": domain, "lead_email": f"campaign@{domain}", "campaign": campaign.name},
    )

    try:
        apollo = get_apollo_client()
        batch_limit = min(limit, _OUTBOUND_BATCH_CAP, 5)
        tags = keyword_tags or ([company.lower().split()[0]] if company else [])

        _trace.write(run_id=run_id, event_type="tool_call", agent="campaign",
            payload={"tool": "apollo_search", "keyword_tags": tags, "limit": batch_limit})

        search_result = apollo.search_organizations(
            keyword_tags=tags or None,
            employee_ranges=employee_ranges or None,
            per_page=batch_limit,
        )

        _trace.write(run_id=run_id, event_type="tool_response", agent="campaign",
            payload={"tool": "apollo_search", "found": len(search_result.organizations)})

        # Step 2: For each target - enrich + score + draft
        research_tool = ResearchCompanyTool(provider=effective_provider, model=_model)
        fit_tool = FitScoreTool(provider=effective_provider, model=_model)
        draft_tool = DraftOutboundTool(provider=effective_provider, model=_model)
        campaign_dict = campaign.model_dump()

        for org in search_result.organizations[:batch_limit]:
            target_domain = org.primary_domain or ""
            target_company = org.name or target_domain

            # Research
            _trace.write(run_id=run_id, event_type="tool_call", agent="campaign",
                payload={"tool": "research_company", "domain": target_domain})
            brief = research_tool.run({"domain": target_domain}, run_id=run_id)

            # Score
            _trace.write(run_id=run_id, event_type="tool_call", agent="campaign",
                payload={"tool": "fit_score", "domain": target_domain})
            fit = fit_tool.run({"brief": brief, "campaign": campaign_dict}, run_id=run_id)
            tier = fit.get("tier", "cold")

            # Draft (only for hot/warm)
            drafts = []
            if tier in ("hot", "warm"):
                _trace.write(run_id=run_id, event_type="tool_call", agent="campaign",
                    payload={"tool": "draft_outbound", "domain": target_domain})
                draft_result = draft_tool.run({
                    "brief": brief, "campaign": campaign_dict,
                    "persona_role": campaign.target_persona or "Head of Product",
                    "company": target_company,
                }, run_id=run_id)
                drafts = draft_result.get("drafts", [])

            target_results.append({
                "company": target_company,
                "domain": target_domain,
                "industry": brief.get("industry"),
                "employees": org.estimated_num_employees,
                "revenue": org.organization_revenue_printed,
                "fit_tier": tier,
                "fit_points": fit.get("points", 0),
                "fit_reasons": fit.get("reason_codes", []),
                "drafts": drafts,
            })

    except Exception as exc:
        logger.warning("Campaign execution failed: %s", exc)

    # Finalize
    tier_counts: dict[str, int] = {}
    for t in target_results:
        tier_counts[t["fit_tier"]] = tier_counts.get(t["fit_tier"], 0) + 1

    total_drafts = sum(len(t.get("drafts", [])) for t in target_results)

    _trace.write(
        run_id=run_id, event_type="run_end", agent="campaign",
        payload={
            "domain": domain, "lead_email": f"campaign@{domain}",
            "final_tier": "campaign", "final_route": "campaign",
            "trace_path": "OUTBOUND_CAMPAIGN",
            "steps_taken": len(target_results),
            "total_drafts": total_drafts,
            "tier_summary": tier_counts,
        },
    )

    campaign_result: dict[str, Any] = {
        "run_id": run_id,
        "domain": domain,
        "motion": "outbound",
        "run_type": "outbound_campaign",
        "campaign_name": campaign.name,
        "source_company": company or domain,
        "targets": target_results,
        "targets_processed": len(target_results),
        "total_drafts": total_drafts,
        "tier_summary": tier_counts,
        "status": "launched",
    }

    # Store in idempotency (domain-keyed, overwrite on re-launch)
    idem_key = hashlib.sha256(f"campaign-domain|{domain}".encode()).hexdigest()
    _trace.store_idempotency_key(idem_key, run_id, campaign_result)

    return campaign_result


@app.post("/outbound/campaign-for-company")
async def campaign_for_company(req: CompanyCampaignRequest) -> dict[str, Any]:
    """Launch/update a campaign for a company DOMAIN (account-level)."""
    campaign = Campaign(**req.campaign.model_dump())
    return _run_domain_campaign(
        domain=req.domain, company=req.company,
        campaign=campaign,
        keyword_tags=req.apollo_keyword_tags,
        employee_ranges=req.apollo_employee_ranges,
        limit=req.apollo_limit,
    )


@app.get("/outbound/campaign/{domain}")
def get_domain_campaign(domain: str) -> dict[str, Any]:
    """Get the existing campaign for a domain, if any."""
    idem_key = hashlib.sha256(f"campaign-domain|{domain}".encode()).hexdigest()
    stored = _trace.get_by_idempotency_key(idem_key)
    if stored is None:
        return {"domain": domain, "campaign": None}
    return {"domain": domain, "campaign": stored["result"]}


@app.post("/outbound/campaign-from-lead")
async def campaign_from_lead(req: CampaignFromLeadRequest) -> dict[str, Any]:
    """Legacy alias: launch campaign from a lead's email (derives domain)."""
    domain = req.email.rsplit("@", 1)[1] if "@" in req.email else ""
    if not domain:
        raise HTTPException(status_code=422, detail="Cannot derive domain from email")
    crm_record = _crm.lookup(req.email)
    company = (crm_record or {}).get("company", "") or domain
    campaign = Campaign(**req.campaign.model_dump())
    return _run_domain_campaign(
        domain=domain, company=company,
        campaign=campaign,
        keyword_tags=req.apollo_keyword_tags,
        employee_ranges=req.apollo_employee_ranges,
        limit=req.apollo_limit,
    )
