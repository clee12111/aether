# FRONTIER-AUDIT.md — GTM Lead-Triage: Phase K Observability Audit

## Phase K — Observability: Frontier Audit

---

### Approach landscape

**K1 — Readiness vs. Liveness split**
Frontier practice separates /health (pure liveness: no I/O, under 1ms, always
200 while the process lives) from /ready (readiness: synchronous dependency
checks — trace store, CRM, optional enrichment provider). Kubernetes routes
traffic based on readiness, not liveness; conflating them means a broken DB
silently receives traffic. The readiness response body carries per-dependency
status so operators can see which dependency failed without log diving.

**K2 — Structured JSON logging + PII-free correlation**
Frontier: every log line is newline-delimited JSON with flat fields (ts, level,
logger, message, plus all extras as top-level keys). A request_id UUID is minted
per request in middleware, stored in request.state, and injected into every log
record via a logging.Filter or contextvars.ContextVar — so a run_id emitted in
run_triage() carries the same request_id as the middleware entry line. PII
(email, name, company, message) is entirely absent at INFO level; DEBUG only
when LOG_LEVEL=debug. Structured extra={} kwargs, never f-string interpolation.

**K3 — Prometheus-format metrics scrape target**
Frontier: GET /metrics returns text/plain Prometheus exposition format backed by
in-process thread-safe counters/gauges/histograms. Required: gtm_requests_total,
gtm_request_errors_total, gtm_triage_total, gtm_daily_cap_used/limit,
gtm_circuit_breaker_state, gtm_cache_hit_total/miss_total,
gtm_request_duration_seconds, gtm_triage_duration_seconds. No SQL queries on
scrape (except one cheap daily-cap read). No cardinality bombs. Public endpoint.

**K4 — Sentry error tracking (no-op without DSN)**
Frontier: sentry-sdk>=2.0 initialized in _lifespan from SENTRY_DSN. Absent DSN
means Sentry is a true no-op (no network, no import error). FastAPI integration
captures unhandled exceptions automatically. A before_send hook scrubs PII before
events leave the process. Environment tagging via APP_ENV, SENTRY_RELEASE.
Traces sampling configurable; default 0.0 (error-only, no performance overhead).

**K5 — Alerting hooks (circuit-open, error-rate-spike)**
Frontier: an injectable AlertHook protocol (LogAlertHook default, WebhookAlertHook
when ALERT_WEBHOOK_URL is set). CircuitBreaker accepts an alert_hook kwarg and
calls hook.fire("circuit_open", {...}) when tripping to OPEN. A rolling 1-minute
error-rate counter fires error_rate_spike when over 20% of requests error, with
a 300s cooldown. Webhook is fire-and-forget in a background thread. No global
singleton — injectable for testing.

**K6 — OpenTelemetry instrumentation (no-op without OTLP endpoint)**
Frontier: OTel SDK initialized in _lifespan; absent OTLP_ENDPOINT or absent SDK
means NoOpTracerProvider. FastAPI auto-instrumentation for root spans. Manual child
spans for gtm.tool.crm_lookup, gtm.tool.enrich_lead, gtm.tool.score_lead,
gtm.llm_call (with llm.input_tokens, llm.output_tokens), and gtm.cache_check.
OTel trace-id injected into structured logs and run_end trace store payload for
cross-system correlation. No PII in span attributes.

**K7 — Outcome-loop stub**
Frontier: an outcomes table in TraceStore.__init__ (CREATE TABLE IF NOT EXISTS).
POST /outcomes/{run_id} (auth required) records actual_outcome (enum:
converted/no_show/unqualified/unknown), write-once (409 on duplicate, 404 if
run_id unknown). GET /metrics/outcomes (public) returns per-tier precision from
the outcomes table at query time. No PII in the outcomes table (run_id only).

---

### What's WRONG with current state

#### K1 — Readiness vs. Liveness: MISSING ENTIRELY

- api.py:243-245 — /health returns {"status": "ok"} unconditionally. This is
  correct for liveness but there is no /ready endpoint anywhere in the codebase.
- No dependency health checks exist. A broken SQLite path, failed TraceStore init,
  or closed CRM connection passes the current /health check and continues receiving
  traffic from a load balancer.
- middleware.py:36 — _PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}.
  /ready and /metrics are absent. If implemented today they would require an API
  key, breaking monitoring infrastructure.
- TraceStore (trace/store.py) has no ping() or lightweight probe method. SQLiteCRM
  and HubSpotCRM have no equivalent. Both need lightweight probes for /ready.

#### K2 — Structured JSON logging: MISSING ENTIRELY

- api.py:43, middleware.py:21, executor.py:14, loop_agent.py:31, resilience.py:15
  — all use logging.getLogger(__name__) with no structured formatter configured
  anywhere. Default Python logging emits plain text.
- No JSON formatter exists anywhere (no logging.config, no custom Formatter
  subclass, no python-json-logger or equivalent import).
- middleware.py does not mint a request_id or store it in request.state. There is
  no logging.Filter or contextvars.ContextVar to propagate a request-id into
  downstream log records.
- loop_agent.py:387-392 — trace.write() for run_start includes "lead": lead.model_dump()
  which contains email, name, company, and message. In the log layer:
  middleware.py:196 — logger.exception("Unhandled error on %s %s", ...) and
  loop_agent.py:468 — logger.warning("Step %d parse failed: %s", ...) both use
  string interpolation rather than structured extra={} kwargs.
- resilience.py:51-56 — retry logger also uses string interpolation throughout.
- No run_id or request_id appears in any log line. Records from inside
  run_triage() are unattributable to a specific HTTP request.

#### K3 — Prometheus metrics endpoint: MISSING ENTIRELY

- No /metrics endpoint exists in api.py.
- Daily-cap counter is only visible at api.py:229-240 (GET /config), which
  requires auth and returns JSON, not Prometheus format.
- Circuit-breaker state (resilience.py:83-90) is entirely invisible from outside
  the process.
- No in-process counters, gauges, or histograms exist anywhere in the codebase.
- The idempotency cache hit path (api.py:257-259) has no counter.
- /metrics is not in _PUBLIC_PATHS (middleware.py:36).

#### K4 — Sentry error tracking: MISSING ENTIRELY

- No sentry-sdk import or initialization anywhere in the codebase.
- middleware.py:194-197 — global_exception_handler logs via logger.exception()
  but never surfaces to an error-tracking system.
- No before_send PII scrubbing hook.
- No environment/release tagging.
- Cold start is clean by accident (nothing Sentry-related exists to break).

#### K5 — Alerting hooks: MISSING ENTIRELY

- resilience.py:119-123 — CircuitBreaker._on_failure() calls logger.warning(...)
  directly, hard-coded, non-injectable. There is no AlertHook protocol, no
  LogAlertHook, no WebhookAlertHook.
- CircuitBreaker.__init__ (resilience.py:76-83) has no alert_hook kwarg.
- No rolling error-rate counter exists anywhere.
- No ALERT_WEBHOOK_URL, ALERT_ERROR_RATE_THRESHOLD, or ALERT_COOLDOWN_SECONDS
  env vars are consumed.
- The current logger.warning in _on_failure is not testable without patching the
  logger global; it cannot be swapped for a webhook without modifying the class.

#### K6 — OpenTelemetry instrumentation: MISSING ENTIRELY

- No OTel SDK initialization, no TracerProvider, no MeterProvider.
- No manual spans in executor.py, loop_agent.py, or llm_client.py.
- No OTLP_ENDPOINT env var consumed anywhere.
- No OTel trace-id in log output or trace store.
- loop_agent.py:377-382 — Langfuse is used as a lightweight trace wrapper
  (langfuse_wrapper.py), but this is a product-specific LLM observability layer.
  It does not produce W3C-compatible distributed traces or OTLP-exportable spans
  for standard infra tooling (Jaeger, Grafana Tempo, Honeycomb).

#### K7 — Outcome-loop stub: MISSING ENTIRELY

- trace/store.py:19-50 — TraceStore.__init__ creates three tables: trace_events,
  idempotency_keys, daily_usage. No outcomes table.
- No POST /outcomes/{run_id} endpoint in api.py.
- No GET /metrics/outcomes endpoint.
- No actual_outcome column or model field anywhere in the codebase.

#### Cross-cutting constraint violations

1. middleware.py:36 — _PUBLIC_PATHS is missing /ready, /metrics, and
   /metrics/outcomes. All three must be public per the K spec. This is a
   single-line change but must be made before K1/K3/K7 endpoints are wired.
2. trace/store.py:156-162 — list_runs() stores lead_email in the result dict
   returned by GET /runs. The run_end payload at loop_agent.py:580 also stores
   lead_email, meaning GET /runs/{run_id} responses expose PII. Under K2 and K7,
   this pattern must not be replicated in log records, metric labels, or the
   outcomes table.
3. Zero observability-specific tests exist. test_api_hardening.py and
   test_reliability.py cover auth and rate-limiting but none of K1-K7. Each
   subsystem requires at least one unit test verifying correct behavior without
   the external dependency present.

---

### Median-fallback confession

A median implementation would:
- Rename /health to /ready and call it done — same no-I/O implementation,
  different route. The frontier bar requires opposite semantics: /ready does
  actual dependency checks; /health must not.
- Add logger.info(f"request_id={uuid4()}") at the top of each endpoint handler
  and call it "request correlation." The frontier requires a middleware Filter
  that injects request_id into every log record emitted during the request,
  including records from nested calls in run_triage() and tool executors.
- Return a JSON dict from /metrics instead of Prometheus exposition format.
  Prometheus cannot scrape JSON; the text format spec is not optional.
- Add sentry_sdk.capture_exception(e) only inside global_exception_handler.
  This misses handled errors, context breadcrumbs, and the PII scrubbing hook
  required before events leave the process.
- Add a logger.warning call directly in the circuit breaker and call it alerting.
  The frontier requires an injectable hook so tests can verify alert firing
  without patching the logger, and so a webhook can be swapped in without code changes.
- Skip OTel and claim Langfuse covers it. Langfuse is a product-specific LLM
  observability layer; it does not produce OTLP-exportable spans or W3C
  traceparent headers for standard infra tooling.
- Skip the outcomes table and note it can be added later. K7 is intentionally a
  stub — the bar is minimal (one table, two endpoints) — but the median skips
  even this foundation, leaving no path to operational precision measurement.

The median path produces something that looks like observability in a demo but
provides no operational value: logs you cannot grep, metrics you cannot scrape,
errors you are not paged on, and no mechanism to measure whether "hot" leads
actually convert.

---

### Verdict

**NO-GO**

Zero of seven K sub-requirements are implemented. The codebase has the structural
prerequisites (circuit breaker, idempotency, trace store, middleware stack, lifespan
hook) but the observability layer does not exist.

| Sub-requirement | Status |
|---|---|
| K1 /ready readiness probe | NOT STARTED |
| K2 Structured JSON logging + request-id propagation | NOT STARTED |
| K3 /metrics Prometheus endpoint | NOT STARTED |
| K4 Sentry error tracking (no-op default) | NOT STARTED |
| K5 AlertHook + circuit-open + error-rate alerts | NOT STARTED |
| K6 OpenTelemetry instrumentation | NOT STARTED |
| K7 Outcomes table + POST /outcomes + GET /metrics/outcomes | NOT STARTED |
| Cross-cut: /ready, /metrics, /metrics/outcomes in _PUBLIC_PATHS | NOT STARTED |

**Recommended build order (minimum viable K):**

1. K2 first — structured logging + request-id is the prerequisite for everything
   else. Once log records carry request_id and run_id, all other subsystems become
   testable in isolation.
2. K1 — add ping() to TraceStore and CRM base classes, implement /ready, add to
   _PUBLIC_PATHS. Low-risk, high-impact.
3. K3 — build in-process counter registry, wire middleware for request counts and
   latency, wire run_triage() for triage counts and duration. Add /metrics to
   _PUBLIC_PATHS.
4. K5 — refactor CircuitBreaker to accept injectable alert_hook kwarg, add rolling
   error-rate tracker in the metrics layer, implement WebhookAlertHook.
5. K4 — add optional sentry-sdk dep with try/except ImportError guard, initialize
   in _lifespan, write and unit-test before_send PII scrubber.
6. K7 — add outcomes table migration to TraceStore.__init__, implement two
   endpoints, add /metrics/outcomes to _PUBLIC_PATHS. Lowest complexity.
7. K6 — add OTel as optional dep with try/except ImportError degradation, implement
   NoOpTracerProvider fallback in _lifespan, add manual spans in executor and
   llm_client, inject trace-id into K2 structured log records.
