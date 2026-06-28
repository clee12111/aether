# FRONTIER.md — GTM Lead-Triage: Frontier Bar

## Purpose
This file defines the explicit, falsifiable bar for "frontier-grade" work on
three identified honesty gaps. Each gap has: what frontier means, what the check
is, and what "median" looks like (so we can detect it).

---

## Gap 1: Input Extraction — Structured intake from unstructured inbound

### The problem
The current system requires `{email, name, company, message, source}` as
pre-parsed fields (see `Lead` model in `gtm_triage/models/lead.py:4-9` and
`TriageRequest` in `gtm_triage/api.py:54-63`). Real inbound is messy: a raw
email body, a free-text message box, sometimes just an email address with no
other fields.

### Frontier bar
1. **An extraction step exists** — an LLM call (or regex cascade + LLM fallback)
   that accepts raw, unstructured text and emits a strict Pydantic `Lead` model.
2. **Minimal-input acceptance** — the system produces a valid `Lead` from:
   - Just an email address (all other fields empty/inferred)
   - A raw email body (From/Subject/Body — fields extracted, not pre-parsed)
   - A free-text message box with no structured fields
3. **Strict schema validation** — extraction output is validated against the
   Pydantic model before entering the triage loop. Malformed extraction fails
   loudly, never silently passes garbage downstream.
4. **Extraction confidence** — the extraction step returns a confidence score
   and flags which fields were inferred vs. explicitly stated.

### Falsifiable check
- [ ] `POST /triage` accepts a `raw_text` field (alternative to structured fields).
      When `raw_text` is provided, structured fields are ignored and the
      extraction step runs.
- [ ] Unit tests: 5+ cases of raw email bodies → correct `Lead` extraction.
- [ ] Unit tests: email-only input → valid `Lead` with empty/inferred fields.
- [ ] Extraction output includes `extraction_confidence` and `field_sources` dict.
- [ ] A malformed/unparseable input returns an explicit extraction error, not a
      silent default.

### Median fallback (what to avoid)
- Requiring all fields pre-parsed (current state).
- A regex-only extractor that handles 2-3 formats but breaks on real email bodies.
- An LLM call with no schema validation (freeform dict, not Pydantic).

---

## Gap 2: Real Enrichment — Actual firmographic data, not guessing

### The problem
`enrich_lead.py` does not enrich — it *guesses* industry/size/seniority from
keyword matching on domain + message text (lines 24-58, 66-89). The confidence
score (lines 146-154) is fabricated: it's a sum of booleans, not a measure of
data quality. There is no external data source, no email validation, no real
firmographics.

### Frontier bar
1. **Real external provider** — at least one external enrichment API is called.
   Default: People Data Labs (PDL) Person Enrichment API (free dev tier, 100
   calls/month, raw REST via `httpx`). Provider is swappable behind an interface
   (like `CRMStore` is for CRM backends).
2. **Zero-cost waterfall** — before hitting PDL (rate-limited), run free checks:
   - **Email validity**: MX/DNS lookup + disposable-domain blocklist. Invalid
     email → short-circuit to disqualified, no enrichment needed.
   - **PDL call**: on valid business email, call PDL Person Enrichment.
   - **Fallback on miss**: if PDL returns no match, fetch company website
     (domain → homepage) + LLM read for basic firmographics.
3. **Source + confidence tagging** — every enrichment field carries its source
   (`pdl`, `dns`, `llm_fallback`, `regex`, `crm`) and a per-field confidence.
   The overall confidence is derived from source quality, not boolean sums.
4. **Swappable provider interface** — enrichment provider is behind an ABC
   (`EnrichmentProvider`) with `enrich(email, name, company) -> EnrichmentResult`.
   PDL is the default implementation; others (Clearbit, Apollo, etc.) slot in
   without changing the tool.
5. **Free-tier only** — PDL free dev tier (100/month). No paid plan required.
   Rate limiting and caching (by email) to stay within quota.

### Falsifiable check
- [ ] `EnrichmentProvider` ABC exists with at least two implementations:
      `PDLProvider` and `MockProvider`.
- [ ] PDL call uses raw `httpx`, not a vendor SDK.
- [ ] Email validity check (MX lookup + disposable-domain list) runs before PDL.
- [ ] Invalid email → short-circuit, no PDL call made (unit test).
- [ ] PDL response is parsed into the `Enrichment` Pydantic model with
      `source="pdl"` per field.
- [ ] On PDL miss, company-website fetch + LLM read runs as fallback (integration
      test with mock HTTP).
- [ ] Rate-limit guard: after 100 calls, PDL is skipped and fallback runs.
- [ ] Response cache: same email within a session doesn't re-call PDL.
- [ ] Old regex logic is demoted to `MockProvider` (kept for CI/deterministic
      tests only).

### Median fallback (what to avoid)
- Keeping the regex guesser as the "real" enrichment and calling it done.
- Adding an API call but with no fallback (PDL miss → empty data).
- Hard-coding PDL with no provider interface.
- Using a paid-tier API and shipping a cost surprise.

---

## Gap 3: Genuinely Agentic Loop + De-Gamed Eval

### The problem (two sub-problems)

**3a. The loop is scripted, not agentic.** The system prompt in
`loop_agent.py:27-68` prescribes a fixed sequence: `crm_lookup → enrich_lead →
score_lead → draft_outreach → finalize`. The "SKIP if CRM has complete profile"
and "ONLY for hot/warm" are the only branches, and they're hardcoded in the
prompt. The trace never shows genuinely different shapes for different leads.
This is a state machine wearing an agent costume.

**3b. The eval is teaching-to-the-test.** Both `cases.py` (22 leads) and
`holdout.py` (10 leads) were authored by the same person who wrote the scoring
rules. The leads are clean, well-structured, and designed to satisfy the rule
set. The holdout set (`holdout.py:1-7` — "written AFTER the 4 rules were
finalized") is still in the same style. There are no genuinely adversarial,
messy, or ambiguous cases that would stress-test the system.

### Frontier bar

**3a. Genuinely branching loop:**
1. **Path varies observably** — the agent's trace shows at least 4 distinct
   shapes across the test set:
   - CRM hit with complete profile → skip enrichment entirely
   - Invalid email → short-circuit to disqualified (no enrichment, no scoring)
   - Low-confidence or conflicting enrichment → re-enrich or dig deeper
   - Ambiguous intent → different tool sequence than clear intent
2. **Observations drive decisions** — the agent's reasoning at each step
   references the *output* of the prior step, not just the plan. If enrichment
   returns low confidence, the agent should react (retry, fallback, flag).
3. **No hardcoded sequence in prompt** — the system prompt describes available
   tools and decision criteria, not a numbered workflow. The agent discovers
   the path.
4. **Short-circuit on garbage** — invalid email or clear spam terminates in
   ≤ 2 steps, not 4-5.

**3b. De-gamed eval:**
1. **Independent sourcing** — at least 10 test leads are sourced independently
   of the scoring rubric (e.g., from real-world form submissions, anonymized
   CRM data, or generated by a separate person/model with no knowledge of the
   rules).
2. **Messy/adversarial cases** — the test set includes:
   - Typos, misspellings, mixed-language text
   - Missing fields (email only, no name/company)
   - Prompt injection attempts (already have one — need more variety)
   - Conflicting signals (C-level + free email + spam-like message)
   - Edge cases the rules don't cover (government, nonprofit, .edu)
3. **Per-tier precision/recall** — eval reports precision and recall for each
   tier (hot/warm/cold/disqualified), not just overall accuracy. Small-N caveat
   is stated explicitly.
4. **False-hot vs. false-cold separation** — the eval distinguishes between
   "scored too high" (false-hot: wasted AE time) and "scored too low"
   (false-cold: lost deal). These have asymmetric business costs and must be
   reported separately.
5. **Trace-shape assertion** — the eval checks that traces are not all the same
   shape. At least 3 distinct tool-call sequences must appear across the test set.

### Falsifiable check
- [ ] System prompt does NOT contain a numbered workflow (1-2-3-4-5).
- [ ] Trace inspector shows ≥ 4 distinct tool-call sequences across the test set.
- [ ] Invalid-email lead terminates in ≤ 2 steps.
- [ ] Low-confidence enrichment triggers a different next-step than high-confidence.
- [ ] Eval test set has ≥ 30 leads, of which ≥ 10 are independently sourced.
- [ ] Eval output includes per-tier precision/recall table.
- [ ] Eval output separates false-hot from false-cold counts.
- [ ] ≥ 5 adversarial/messy leads in the test set (typos, missing fields, mixed
      language, conflicting signals).
- [ ] Trace-shape diversity assertion in the eval runner.

### Median fallback (what to avoid)
- Adding one more `if` branch and calling the loop "agentic."
- Keeping the numbered prompt and claiming the model "chooses" the sequence.
- Writing more leads in the same clean style and calling them "holdout."
- Reporting only aggregate accuracy without per-tier breakdown.

---

## Gap 3a (Phase D): De-Scripted Loop — Signal-Driven Branching

### The problem
The loop agent follows a fixed sequence (`crm_lookup → enrich_lead → score_lead
→ draft_outreach → finalize`) regardless of what it observes. The mock LLM
client (`llm_client.py:77-149`) is a literal if/elif chain. The system prompt
(`loop_agent.py:27-68`) prescribes a numbered workflow. Signals like email
validity, enrichment confidence, extraction confidence, and intent are available
but the loop never reads them.

### Frontier bar
1. **>=5 distinct, signal-justified trace shapes** — each branch driven by a
   real observation (not padding):
   - **SHORT_CIRCUIT_INVALID**: invalid/disposable email → disqualify in <=2
     steps, skip enrichment (no wasted PDL credit)
   - **SHORT_CIRCUIT_INTENT**: opt_out or legal_or_compliance intent → disqualify
     immediately after extraction, skip enrichment+scoring
   - **CRM_HIT_SKIP_ENRICH**: CRM has a complete profile → skip enrichment,
     route on existing data
   - **LOW_CONFIDENCE_GATE**: extraction or enrichment returned low-confidence
     seniority → downgrade to "unknown" before scoring rather than granting full
     points on shaky data (fixes lemonade false-hot)
   - **CLEAN_FULL_PATH**: high-confidence signals → straight through (crm →
     enrich → score → draft if warm+)

2. **Trace records which path** — `TriageResult` includes a `trace_path` label
   so the Trace Explorer and eval harness can assert shape diversity.

3. **Confidence-gating rule**: seniority/intent with confidence < 0.50 is
   downgraded to "unknown"/0 points before entering scoring. This is the
   mechanism that prevents the lemonade false-hot (third-person "our CTO shared"
   gets 0.75 confidence from extraction but should be gated when the pattern
   matches a third-person reference).

4. **No numbered workflow in system prompt** — tools + decision criteria, not
   a step-by-step recipe.

### Falsifiable check
- [ ] `TriageResult` has a `trace_path` field populated for every run.
- [ ] >= 5 distinct `trace_path` values appear across holdout_v2 + golden sets.
- [ ] `x9z@yopmail.com` (disposable) terminates in <= 2 steps with path
      SHORT_CIRCUIT_INVALID.
- [ ] `hr@nvidia.com` (opt_out intent) terminates in <= 2 steps with path
      SHORT_CIRCUIT_INTENT.
- [ ] `e.brook@lemonade.com` is NOT false-hot (confidence gate downgrades the
      third-person CTO seniority).
- [ ] Unit test: lead with seniority_confidence < 0.50 gets seniority downgraded
      to "unknown" before scoring.
- [ ] Mock CI gate (5 MOCK_LEADS) still passes — the branching logic handles
      the original test set correctly.

### Median fallback (what to avoid)
- Adding `if` branches to the mock LLM client without recording which path ran.
- Keeping the numbered system prompt and calling it "agentic."
- Hard-coding a confidence threshold that only fires on the one known false-hot.

---

## Phase K: Production Observability

### Context
The service already has: SQLite/Postgres trace store, daily-cap counter,
circuit breaker (`resilience.py`), rate-limit middleware, idempotency, and a
`/health` liveness endpoint. Phase K adds the observability layer that makes
all of that visible to ops without requiring log-diving.

The non-negotiable constraint: **every observability addition is a no-op when
its dependency is absent** (no DSN → Sentry is a null client; no OTLP endpoint
→ OTel exporters are `NoOpExporter`; no webhook URL → alerting hook logs only).
A cold start with zero env vars must still serve traffic.

---

### K1 — Readiness vs. Liveness split

**The problem:** `/health` returns `{"status": "ok"}` unconditionally — it
passes even if the SQLite connection is broken or the trace store failed to
init. Kubernetes / load-balancers use this as the readiness probe; a broken
dependency silently receives traffic.

**Frontier bar:**
1. `/health` remains the liveness probe — it MUST return 200 as long as the
   process is alive, regardless of dependency state. It NEVER calls external
   services. Latency: < 1 ms (no I/O).
2. `/ready` is added as the readiness probe — it checks actual dependency
   health synchronously before returning 200:
   - Trace store: runs a lightweight query (e.g. `SELECT 1` or equivalent) and
     confirms ≤ N ms round-trip.
   - CRM store: same lightweight ping.
   - Optional / degraded-OK: enrichment provider (PDL) — failure here does NOT
     fail readiness; it sets a `degraded: true` flag in the response body.
3. `/ready` returns a structured JSON body with per-dependency status and
   overall readiness, not just an HTTP code:
   ```json
   {"ready": true, "checks": {"trace": "ok", "crm": "ok", "enrichment": "degraded"}}
   ```
4. `/ready` is always public (no auth). It is NOT included in API key usage
   accounting.
5. Auth middleware's `_PUBLIC_PATHS` must include `/ready`.

**Falsifiable checks:**
- [ ] `/ready` returns 200 + `{"ready": true, ...}` when all deps are up.
- [ ] `/ready` returns 503 + `{"ready": false, ...}` when trace store is down
      (test: pass a broken DB path, assert 503).
- [ ] `/ready` returns 200 + `{"ready": true, "checks": {..., "enrichment": "degraded"}}`
      when enrichment is unreachable but trace+CRM are up.
- [ ] `/health` returns 200 in the same broken-DB scenario (liveness ≠ readiness).
- [ ] `/ready` is in `_PUBLIC_PATHS` (auth middleware bypasses it).
- [ ] Unit test: `GET /ready` with a deliberately broken `TraceStore` → 503.

**Median fallback (what to avoid):**
- Making `/ready` an alias for `/health` (same implementation, different route).
- Calling PDL or any external paid service from `/ready`.
- Failing readiness on enrichment-provider unavailability (that's degraded, not down).

---

### K2 — Structured JSON logging with request/run-id correlation, no PII

**The problem:** `logging.getLogger(__name__)` is called throughout but there
is no structured format, no request-id propagation, and no guarantee that PII
(email, name, message text) does not appear in log output. Log lines cannot be
correlated across the middleware → endpoint → agent → tool call chain.

**Frontier bar:**
1. **JSON log format** — all log output (INFO and above) emits newline-delimited
   JSON with at minimum: `ts` (ISO-8601), `level`, `logger`, `message`, and any
   extra fields passed as kwargs. Plain text format is acceptable in development
   (`LOG_FORMAT=text`); JSON is the default and the required production format.
2. **Request-id injection** — every inbound request gets a `request_id` (UUID)
   assigned in middleware. It is stored in `request.state.request_id` and attached
   to every log record emitted during that request via a `logging.Filter` or
   `contextvars.ContextVar`. Log lines from deep inside `run_triage()` carry the
   same `request_id` as the middleware entry line.
3. **Run-id / request-id correlation** — the `run_id` generated by `run_triage()`
   is emitted alongside `request_id` in the `run_start` log line. OTel trace-id
   (if enabled — K6) also appears in log records so that a single `run_id` can be
   looked up in both logs and traces without secondary joins.
4. **No PII in logs** — email address, name, company, and message text MUST NOT
   appear in log output at INFO or above. Permitted at DEBUG level ONLY, and only
   when `LOG_LEVEL=debug` is explicitly set. Specifically:
   - `run_start` log: `run_id`, `source`, `request_id` — NO email.
   - `run_end` log: `run_id`, `final_tier`, `final_route`, `steps_taken`,
     `duration_ms` — NO email.
   - Tool call logs: tool name, duration, success/error — NO tool args that
     contain email or message text.
5. **Structured fields, not string interpolation** — log calls use
   `logger.info("msg", extra={"run_id": ..., "duration_ms": ...})` not
   `logger.info(f"run {run_id} done in {ms}ms")`. This ensures every field
   is machine-parseable without regex.

**Falsifiable checks:**
- [ ] `LOG_FORMAT=json` produces newline-delimited JSON; each line parses with
      `json.loads()` without error.
- [ ] `LOG_FORMAT=text` produces human-readable output (for local dev).
- [ ] A `POST /triage` request produces log lines that all share the same
      `request_id` value, from middleware entry through `run_end`.
- [ ] `run_id` appears in the log line that also carries `request_id`.
- [ ] No email address appears in any log line at INFO level (test: search
      `json.loads(line)` for the email string across all emitted lines).
- [ ] `run_start` log line contains `request_id` and `source` but NOT `email`.
- [ ] Unit test: inject a known email, capture log output via `logging.handlers`,
      assert email string is absent from all INFO-level lines.

**Median fallback (what to avoid):**
- Wrapping `logger.info(f"Processing {lead.email}")` calls in a JSON formatter
  (the PII is still there, just JSON-encoded).
- Emitting a `request_id` in middleware but not threading it into agent/tool logs.
- A JSON formatter that stringifies the entire `extra` dict as a single `"extra"`
  key rather than flattening fields to the top level.

---

### K3 — Metrics endpoint (Prometheus-format scrape target)

**The problem:** There is no metrics endpoint. The daily-cap counter exists in
`TraceStore` but is only visible via `GET /config`. Circuit-breaker state exists
in `resilience.py` but is not observable from outside the process. There is no
way for an operator to see error rate, p50/p95 latency, or per-tier counts
without querying SQLite directly.

**Frontier bar:**
1. **`GET /metrics`** returns a Prometheus text-format scrape body
   (`Content-Type: text/plain; version=0.0.4`). No Prometheus client library
   required — the format is simple enough to emit manually, though a lightweight
   library (`prometheus-client`) is acceptable if already in the dependency set.
2. **Required counters (all with `gtm_` prefix):**
   - `gtm_requests_total{endpoint, method, status_code}` — HTTP request count.
   - `gtm_request_errors_total{endpoint, error_type}` — 4xx and 5xx by type.
   - `gtm_triage_total{tier, route, provider}` — completed triage runs by
     outcome tier, route, and which LLM provider was used.
   - `gtm_daily_cap_used` / `gtm_daily_cap_limit` — current day's usage and
     configured cap (readable at a glance vs. querying /config).
3. **Required gauges:**
   - `gtm_circuit_breaker_state{name}` — 0=closed, 1=half_open, 2=open.
     Must reflect actual `CircuitBreaker` instances, not a stub.
   - `gtm_cache_hit_total` / `gtm_cache_miss_total` — idempotency-key cache
     (deduplication) hits and misses, since those directly save LLM cost.
4. **Required histograms:**
   - `gtm_request_duration_seconds{endpoint}` — HTTP latency (measured in
     middleware, not inside the handler, so it includes serialization overhead).
   - `gtm_triage_duration_seconds{provider}` — end-to-end triage latency
     (from `run_triage` entry to result, inclusive of all LLM and tool calls).
5. **`/metrics` is public** (no API key) — same as `/health`. Operators need
   it from monitoring infra without client credentials.
6. **Metric state is in-process** — backed by simple thread-safe Python
   counters/gauges, not SQLite queries. Querying SQLite on every Prometheus
   scrape (default: every 15 seconds) is not acceptable.
7. **No cardinality bombs** — `status_code` is the HTTP status integer (200,
   400, 429, 500, etc.) not a full URL or user-supplied string. Email addresses,
   run_ids, and request_ids must NEVER appear as metric labels.

**Falsifiable checks:**
- [ ] `GET /metrics` returns `Content-Type: text/plain; version=0.0.4`.
- [ ] Response body is valid Prometheus exposition format (parseable by
      `prometheus_client.exposition` or equivalent parser).
- [ ] After `POST /triage` returns 200, `gtm_requests_total{status_code="200"}`
      increments by 1.
- [ ] After a 429 (rate-limited) response, `gtm_request_errors_total{error_type="rate_limited"}`
      increments by 1.
- [ ] `gtm_circuit_breaker_state{name="openai"}` returns 2 when the OpenAI
      breaker is manually tripped (test via `CircuitBreaker.reset()` + forced
      failures).
- [ ] `gtm_cache_hit_total` increments when a duplicate idempotency key is
      submitted.
- [ ] `gtm_daily_cap_used` matches `TraceStore.get_daily_usage()` for the
      current day.
- [ ] `/metrics` is accessible without an API key (auth middleware skips it).
- [ ] No metric label contains an email address or run_id (test: submit a
      recognizable email, scrape /metrics, assert the email string is absent).

**Median fallback (what to avoid):**
- Querying SQLite on every `/metrics` scrape instead of in-process counters.
- A `/metrics` endpoint that returns JSON instead of Prometheus text format.
- Omitting histograms (counters only gives rate, not latency distribution).
- Using the full request path (with query params or IDs) as a metric label.

---

### K4 — Sentry error tracking (no-op without DSN)

**The problem:** Unhandled exceptions are caught by `global_exception_handler`
in `middleware.py` and logged, but never surfaced to an error-tracking system.
In production, a spike of 500s is only visible if someone is watching the logs.

**Frontier bar:**
1. **Sentry SDK integrated** (`sentry-sdk>=2.0`) — initialized in `_lifespan`
   from `SENTRY_DSN` env var. If `SENTRY_DSN` is absent or empty, Sentry is
   explicitly initialized with `dsn=None` (or not initialized at all); the result
   must be a true no-op — no network calls, no errors thrown, no startup warning
   in logs.
2. **Automatic FastAPI instrumentation** — `sentry_sdk.init()` uses
   `SentryAsgiMiddleware` or the FastAPI integration so that unhandled exceptions
   are automatically captured without manual `capture_exception()` calls scattered
   through the codebase.
3. **PII scrubbing before send** — a `before_send` hook strips or hashes PII
   from the event before it leaves the process:
   - Email addresses in breadcrumbs, request data, and extra context → replaced
     with `[email]` or a SHA-256 hash of the first 8 chars.
   - `name`, `company`, `message` fields from request body → scrubbed.
   - The hook is a pure function and is unit-testable in isolation.
4. **Environment tagging** — events carry `environment` (`APP_ENV` env var,
   default `"development"`) and `release` (read from `__version__` or a
   `SENTRY_RELEASE` env var). This prevents dev noise from polluting production
   error counts.
5. **Traces sampling** — `traces_sample_rate` is configurable via
   `SENTRY_TRACES_SAMPLE_RATE` (default `0.0` — error tracking only, no
   performance tracing; operators opt in to performance monitoring explicitly).

**Falsifiable checks:**
- [ ] Cold start with no `SENTRY_DSN` — no import error, no network call, no
      log warning about Sentry configuration. Process starts clean.
- [ ] Cold start with a valid `SENTRY_DSN` — Sentry is initialized; a synthetic
      `1/0` exception in a test route appears in Sentry (integration test, mock
      DSN acceptable with `sentry-sdk` test transport).
- [ ] `before_send` hook: given an event dict containing `"email": "test@example.com"`,
      the returned event does NOT contain that string anywhere (unit test,
      no network required).
- [ ] `environment` field on captured events matches `APP_ENV` env var.
- [ ] `SENTRY_TRACES_SAMPLE_RATE=0.0` (default) does not emit transaction events.
- [ ] `sentry_sdk` is not imported at module level in hot paths (only in `_lifespan`
      init) so a missing optional dep does not break the import chain.

**Median fallback (what to avoid):**
- A `try/except` that calls `sentry_sdk.capture_exception(e)` only in the global
  exception handler (misses handled errors and context breadcrumbs).
- Sending the full request body to Sentry (PII leak).
- Raising an ImportError at startup when `sentry-sdk` is not installed
  (should be a soft dep or `try/except ImportError`).

---

### K5 — Alerting hooks (circuit-breaker-open, error-rate-spike)

**The problem:** `CircuitBreaker._on_failure()` logs a WARNING when a circuit
trips, but that log line is invisible to an on-call operator unless they happen
to be watching. An error-rate spike similarly produces no push signal.

**Frontier bar:**
1. **Alert hook interface** — a thin, injectable `AlertHook` protocol:
   ```python
   class AlertHook(Protocol):
       def fire(self, event: str, payload: dict) -> None: ...
   ```
   Default implementation: `LogAlertHook` (emits a structured WARNING log).
   Optional: `WebhookAlertHook` (HTTP POST to `ALERT_WEBHOOK_URL`) — active only
   when `ALERT_WEBHOOK_URL` is set.
2. **Circuit-breaker-open alert** — when `CircuitBreaker._on_failure()` trips the
   circuit to OPEN state, it calls `alert_hook.fire("circuit_open", {...})` with:
   `name`, `consecutive_failures`, `cooldown_seconds`, `ts`.
   The `CircuitBreaker.__init__` accepts an optional `alert_hook` kwarg (default:
   `LogAlertHook()`). No global state; injectable for testing.
3. **Error-rate-spike alert** — the metrics layer (K3) tracks a rolling 1-minute
   error count. When the error rate exceeds `ALERT_ERROR_RATE_THRESHOLD`
   (default: 0.20 = 20% of requests in the last 60 seconds are errors),
   `alert_hook.fire("error_rate_spike", {...})` is called with: `rate`,
   `window_seconds`, `error_count`, `request_count`, `ts`.
   Alert fires at most once per `ALERT_COOLDOWN_SECONDS` (default: 300) to prevent
   alert storms.
4. **Webhook alert is fire-and-forget** — `WebhookAlertHook.fire()` runs in a
   background thread (or `asyncio.create_task`) and NEVER blocks the request path.
   Webhook failures (network error, non-200 response) are logged but do NOT
   propagate exceptions to the caller.
5. **No-op without config** — if neither `ALERT_WEBHOOK_URL` is set nor an
   explicit hook is injected, alerts are `LogAlertHook` only (no network calls).

**Falsifiable checks:**
- [ ] `CircuitBreaker` with a `LogAlertHook` injected: manually trip the breaker
      (force 5 failures), assert that a log record with `event="circuit_open"` was
      emitted (unit test, no network).
- [ ] `WebhookAlertHook` with a mock HTTP server: trip the breaker, assert the
      mock server received one POST with the correct JSON payload.
- [ ] Webhook failure (mock server returns 500) does not raise an exception in the
      calling thread (assert the breaker call succeeds despite webhook failure).
- [ ] Error-rate-spike alert fires when > 20% of requests in a 60-second window
      return 4xx/5xx (unit test via metric counter injection).
- [ ] Alert cooldown: error-rate-spike alert fires exactly once within a 300-second
      window even if the threshold is continuously exceeded.
- [ ] Cold start with no `ALERT_WEBHOOK_URL` → zero network calls from alerting
      code (assert `LogAlertHook` is the active implementation).

**Median fallback (what to avoid):**
- A `logger.warning()` call directly in `_on_failure()` without an injectable
  hook (untestable; can't swap to webhook without changing the class).
- Blocking the request thread on a webhook POST (latency bomb under alert storms).
- A global singleton `AlertHook` that can't be overridden in tests.

---

### K6 — OpenTelemetry instrumentation (no-op without OTLP endpoint)

**The problem:** There is no distributed tracing. When a `/triage` call is slow,
there is no way to see which step (CRM lookup? enrichment? LLM call?) consumed
the time. The `duration_ms` in the trace store is per-event but not presented as
nested spans.

**Frontier bar:**
1. **OTel SDK initialized in `_lifespan`** — `opentelemetry-sdk` +
   `opentelemetry-exporter-otlp-proto-grpc` (or `-http`) are optional deps.
   If `OTLP_ENDPOINT` is not set, the tracer provider is a `NoOpTracerProvider`
   — zero overhead, zero network calls. If the OTel SDK is not installed,
   `try/except ImportError` degrades silently.
2. **FastAPI auto-instrumentation** — `opentelemetry-instrumentation-fastapi`
   instruments all HTTP routes automatically, creating a root span per request.
   Root span carries: `http.method`, `http.route`, `http.status_code`.
3. **Manual spans for key operations** — each of the following creates a child span:
   - `crm_lookup` tool call — span name `gtm.tool.crm_lookup`
   - `enrich_lead` tool call — span name `gtm.tool.enrich_lead`
   - `score_lead` tool call — span name `gtm.tool.score_lead`
   - LLM call (inside `llm_client.py`) — span name `gtm.llm_call` with
     attributes: `llm.provider`, `llm.model`, `llm.input_tokens`, `llm.output_tokens`
   - Idempotency cache check — span name `gtm.cache_check` with `cache.hit` bool
4. **run_id / request_id ↔ OTel trace-id correlation** — the OTel trace-id
   (W3C `traceparent`) for a request is included in:
   - The structured log output (K2) for that request as `otel_trace_id`.
   - The `run_end` trace store event payload as `otel_trace_id`.
   This allows a `run_id` to be looked up in both the local SQLite trace and a
   remote OTel backend (Jaeger, Tempo, Honeycomb) without a separate join.
5. **No PII in span attributes** — same rule as K2: email, name, message text
   are never set as span attributes. `run_id` is acceptable (not PII).
6. **OTel metrics** (optional tier) — if `OTLP_ENDPOINT` is set, the same
   counters/histograms from K3 are also exported via OTLP `MeterProvider`. This
   is additive; the Prometheus `/metrics` endpoint (K3) remains the primary
   scrape target.

**Falsifiable checks:**
- [ ] Cold start with no `OTLP_ENDPOINT` and no OTel SDK installed: process
      starts, serves traffic, no import error, no log warning about OTel.
- [ ] Cold start with `OTLP_ENDPOINT` set and SDK installed: a `POST /triage`
      produces an exportable span tree with at least 3 child spans
      (CRM lookup, enrich, score) visible in a local Jaeger or OTLP collector
      (integration test acceptable; can use `opentelemetry-sdk` in-memory exporter).
- [ ] The `run_id` appears in the root span's attributes as `gtm.run_id`.
- [ ] The OTel trace-id appears in the structured log output for the same request
      (test: capture log output, parse JSON, assert `otel_trace_id` field present
      and non-empty when OTel is active).
- [ ] No span attribute contains an email address (test: submit a recognizable
      email, collect spans from in-memory exporter, assert email string is absent).
- [ ] `gtm.llm_call` span has `llm.input_tokens` and `llm.output_tokens` attributes
      that match the values recorded in the trace store for the same run.

**Median fallback (what to avoid):**
- Installing OTel as a hard dependency (breaks cold starts without the SDK).
- Auto-instrumentation only, no manual spans (produces one span per request with
  no visibility into which sub-step was slow).
- Propagating the OTel trace-id only via response headers but not into logs or
  the trace store (breaks correlation without a UI that joins HTTP traces to app logs).

---

### K7 — Outcome-loop stub: outcomes table, POST /outcomes/{run_id}, precision-against-outcome metric

**The problem:** The system scores and routes leads but has no feedback loop.
There is currently no way to record whether a "hot" lead actually converted, or
whether a "cold" classification was correct. Without this, precision/recall metrics
in the eval are measured against human labels at triage time, not against actual
business outcomes.

**Frontier bar:**
1. **`outcomes` table in the trace store** — a new table (alongside
   `trace_events`, `idempotency_keys`, `daily_usage`) with schema:
   ```
   outcome_id    TEXT PRIMARY KEY
   run_id        TEXT NOT NULL (FK → trace_events.run_id, not enforced in SQLite)
   predicted_tier TEXT NOT NULL
   actual_outcome TEXT NOT NULL  -- "converted", "no_show", "unqualified", "unknown"
   recorded_by   TEXT            -- who/what recorded this ("crm_sync", "human", "webhook")
   recorded_at   TEXT NOT NULL
   ```
   Migration: `CREATE TABLE IF NOT EXISTS outcomes (...)` runs in `TraceStore.__init__`.
2. **`POST /outcomes/{run_id}`** — authenticated endpoint (API key required)
   that accepts:
   ```json
   {"actual_outcome": "converted", "recorded_by": "crm_sync"}
   ```
   Validation: `actual_outcome` must be one of `{"converted", "no_show",
   "unqualified", "unknown"}`. Returns 404 if `run_id` does not exist in
   `trace_events`. Returns 409 if an outcome already exists for this `run_id`
   (outcomes are write-once; no update path).
3. **`GET /metrics/outcomes`** (or added to `/metrics`) — returns precision-
   against-outcome per tier:
   ```json
   {
     "hot":  {"predicted": 42, "with_outcome": 15, "converted": 12, "precision": 0.80},
     "warm": {"predicted": 71, "with_outcome": 20, "converted": 11, "precision": 0.55},
     ...
   }
   ```
   Computed from the `outcomes` table at query time (not cached; small table).
   Returns empty per-tier objects if no outcomes have been recorded yet (graceful
   zero-data state).
4. **Stub is clearly labeled** — a `# OUTCOME LOOP STUB` comment in the endpoint
   handler documents that this is a foundation for a future real-time feedback
   loop, not a complete implementation. The current implementation is a manual
   POST; a future phase would auto-sync from HubSpot deal-close webhooks.
5. **No PII in outcomes table** — `run_id` only; no email, no name. The email
   can be recovered by joining `run_id → trace_events` if needed, but the
   `outcomes` table itself is clean.

**Falsifiable checks:**
- [ ] `POST /outcomes/{run_id}` with a valid `run_id` and `actual_outcome="converted"`
      returns 201 with the stored record.
- [ ] `POST /outcomes/{run_id}` with an invalid `run_id` returns 404.
- [ ] `POST /outcomes/{run_id}` called twice for the same `run_id` returns 409 on
      the second call (write-once guard).
- [ ] `actual_outcome="deal_closed"` (not in the allowed set) returns 422.
- [ ] `GET /metrics/outcomes` with no outcomes recorded returns valid JSON with
      zero counts (no 500 on empty table).
- [ ] `GET /metrics/outcomes` after recording 5 "hot" predictions with 4
      "converted" outcomes returns `"hot": {"precision": 0.80, ...}` (or
      equivalent within float tolerance).
- [ ] `outcomes` table schema is created in `TraceStore.__init__` via
      `CREATE TABLE IF NOT EXISTS` (no manual migration step required).
- [ ] No email address is stored in the `outcomes` table (inspect via direct
      SQLite query in test).

**Median fallback (what to avoid):**
- Adding `actual_outcome` as a column on `idempotency_keys` (conflates run
  metadata with business outcome; breaks the clean separation).
- Making `/metrics/outcomes` require an API key (operators need it from
  monitoring infra without credentials — same rule as `/metrics`).
- Computing precision from the eval harness labels rather than from actual
  `outcomes` table records (that's eval precision, not operational precision).

---

### Cross-cutting constraints for Phase K

These apply to ALL of K1–K7 and will be checked by frontier-audit:

1. **Zero-dependency no-ops** — Sentry (K4), OTel (K6), and webhook alerting (K5)
   must each be independently absent without breaking cold start or tests.
   CI runs with NONE of these env vars set and all tests must pass.

2. **No PII in any observability channel** — metrics labels (K3), log fields (K2),
   span attributes (K6), Sentry events (K4), alert payloads (K5), and the outcomes
   table (K7) must all be free of email addresses, names, company names, and
   message text. A single grep for a test email address across all of these channels
   (after a `POST /triage`) must find zero matches.

3. **`/metrics`, `/ready`, and `/metrics/outcomes` are public** — auth middleware
   `_PUBLIC_PATHS` must include all three. This is a single-source-of-truth
   change in `middleware.py`.

4. **In-process metric state** — metric counters and gauges live in Python objects
   in the process, not in SQLite. The `/metrics` scrape endpoint does NOT execute
   any SQL queries (except for `gtm_daily_cap_used`, which may read the daily
   usage row once per scrape — one cheap indexed read, not a table scan).

5. **Test coverage for each subsystem in isolation** — each of K1–K7 must have
   at least one unit test that verifies the correct behavior WITHOUT the external
   dependency being present (no SENTRY_DSN, no OTLP_ENDPOINT, no ALERT_WEBHOOK_URL).
   This is the "no-op proves it's truly optional" check.

---

## Phase L: Testing + CI/CD

### Context
The service has a substantial unit-test corpus (`tests/`) and a mock-mode eval
gate (`evals/run_eval.py`). What it lacks is: a GitHub Actions workflow that
enforces the gate on every push/PR; integration tests that exercise FastAPI
end-to-end (not just unit-level); a concurrency smoke test; a coverage floor;
and a settled decision on `/metrics` auth. Phase L closes those gaps.

The non-negotiable constraint: **CI must pass with zero external credentials
set** (`OPENAI_API_KEY`, `SENTRY_DSN`, `OTLP_ENDPOINT`, `ALERT_WEBHOOK_URL`,
`HUBSPOT_TOKEN` all absent). Every test that touches the API or agent pipeline
uses `provider=mock` and in-memory stores. No real network calls in CI.

---

### L1 — GitHub Actions CI: pytest + mock eval gate

**The problem:** There is no `.github/workflows/` directory. Nothing enforces
that a PR cannot merge if `pytest` fails or if the mock eval gate regresses.
A developer who breaks the `SHORT_CIRCUIT_INVALID` branch or a Pydantic model
gets no automated signal until they notice locally.

**Frontier bar:**
1. **Workflow file** — `.github/workflows/ci.yml` triggers on `push` and
   `pull_request` to `main`. It MUST block merges on red (set as a required
   status check; documented in the workflow comments even if branch protection
   is configured separately).
2. **Single job, two steps** — the CI job runs:
   - `pytest tests/ -x --tb=short` (all unit tests, fail-fast on first failure)
   - `python -m evals.run_eval` (mock eval gate — must exit 0, i.e., all 5
     MOCK_LEADS correct on tier AND route)
   Both steps use `provider=mock`; no API key required.
3. **Dependency caching** — Python deps are cached via
   `actions/cache` keyed on the `requirements*.txt` or equivalent lock file.
   A cache hit must skip re-installation (verifiable by "cache hit" log line in
   the Actions run). Cold install is acceptable on cache miss; warm install must
   be < 60 seconds for the full dep set.
4. **Python version pinned** — `python-version: "3.11"` (matching the project
   stack). The workflow does NOT use `python-version: "3.x"` (floating version
   is not pinned).
5. **No secrets required** — the workflow file contains zero references to
   `OPENAI_API_KEY`, `SENTRY_DSN`, `HUBSPOT_TOKEN`, or any external secret. If
   a secret is conditionally used for an optional integration test job, it is a
   SEPARATE job that does not gate the required check.
6. **Eval gate exit code** — `python -m evals.run_eval` must return exit code 0
   on all 5 MOCK_LEADS. Any regression (even one lead changing tier or route)
   returns exit code 1 and fails the CI job. This is already the behavior of
   `evals/run_eval.py:main()` — CI just needs to call it.

**Falsifiable checks:**
- [ ] `.github/workflows/ci.yml` exists and triggers on `push` and
      `pull_request` targeting `main`.
- [ ] The workflow installs deps and runs `pytest tests/ -x --tb=short`.
- [ ] The workflow runs `python -m evals.run_eval` as a separate step after
      pytest passes.
- [ ] Dependency cache is configured: `actions/cache` key includes the hash of
      the lock/requirements file. A second identical push hits the cache.
- [ ] `python-version` is set to the literal string `"3.11"` (not `"3.x"`).
- [ ] No `OPENAI_API_KEY` or other external secret appears in the required CI
      job's `env:` block.
- [ ] Manually breaking one MOCK_LEADS case (e.g., hardcoding the wrong tier
      in the mock) causes `python -m evals.run_eval` to exit 1 and the CI job
      to fail (verified by local dry-run or deliberate regression test).
- [ ] The workflow file has a comment identifying the required status check
      name that should be added to branch protection rules.

**Median fallback (what to avoid):**
- A workflow that only runs `pytest` and skips the eval gate.
- Floating Python version (`3.x`) that silently changes behavior on minor
  releases.
- Caching the entire virtualenv directory instead of keying on the lock file
  (stale cache when deps change).
- Putting `OPENAI_API_KEY` in the workflow env and calling `provider=mock`
  tests "integration tests" to justify needing the key.

---

### L2 — Integration tests: end-to-end FastAPI flows

**The problem:** `tests/test_api_hardening.py` and `tests/test_observability.py`
test individual middleware and unit behaviors via `TestClient`. There are no
tests that exercise complete end-to-end flows: the full triage lifecycle,
idempotency replay behavior, auth enforcement on protected vs. public endpoints,
rate-limit enforcement under sustained load, and the `/ready` 503 path with a
deliberately broken dependency.

**Frontier bar:**
1. **Full triage lifecycle** — a test submits `POST /triage`, verifies `200 +
   TriageResult` JSON shape (`run_id`, `final_tier`, `final_route`, `score`,
   `enrichment`, `trace_path`), then calls `GET /runs/{run_id}` and asserts the
   trace events are present (event count > 0). One test per expected tier
   (hot, warm, cold, disqualified) using deterministic mock leads.
2. **Idempotency replay** — submit `POST /triage` twice with the same
   `idempotency_key`. Assert: (a) both return 200, (b) both return the exact
   same `run_id`, (c) `cache_hit_total` metric increments on the second call.
   A third call with a DIFFERENT `idempotency_key` but same email+message
   (auto-derived key) also hits the cache (verifies the SHA-256 derivation path).
3. **Auth enforcement** — with `GTM_API_KEYS` set:
   - `POST /triage` without a key → 401.
   - `POST /triage` with an invalid key → 401.
   - `POST /triage` with a valid Bearer token → 200.
   - `POST /triage` with a valid `X-API-Key` header → 200.
   - `GET /health`, `GET /ready`, `GET /metrics`, `GET /metrics/outcomes` all
     return 200 with no auth header (public paths, no 401).
4. **Rate-limit enforcement** — set `GTM_RATE_LIMIT_RPM=2`. Submit 3 rapid
   `POST /triage` requests to the same key. Assert at least one returns 429
   with `error.type == "rate_limited"`. Assert that `GET /health` (public path)
   is never rate-limited regardless.
5. **`/ready` with broken dependency** — construct a `TestClient` where the
   `TraceStore` is replaced with a mock whose `ping()` returns `False`. Assert
   `GET /ready` returns 503 with `{"ready": false, "checks": {"trace": "fail",
   ...}}`. Assert `GET /health` in the same scenario returns 200 (liveness is
   independent of readiness).
6. **`POST /outcomes` lifecycle** — triage a lead, record an outcome, assert 201;
   record the same outcome again, assert 409; record an outcome for a nonexistent
   `run_id`, assert 404; record `actual_outcome="deal_closed"`, assert 422.
7. **`DELETE /contacts/{email}` right-to-erasure** — triage a lead, then delete
   by email. Assert 200 with `crm_record_deleted=True`. Assert subsequent
   `GET /contacts/{email}` returns an empty record (not 500).

**Falsifiable checks:**
- [ ] A `TestClient`-based integration test file exists (e.g.,
      `tests/test_integration.py`) distinct from the unit test files.
- [ ] Full triage lifecycle test: `POST /triage` → 200 → `GET /runs/{run_id}`
      → event count > 0. Covers all 4 tiers via 4 distinct mock leads.
- [ ] Idempotency test: same `idempotency_key` submitted twice returns same
      `run_id` both times.
- [ ] Idempotency test: auto-derived key (no explicit `idempotency_key` field)
      deduplicates on identical email+message+source.
- [ ] Auth test: `POST /triage` with no key → 401 when `GTM_API_KEYS` is set.
- [ ] Auth test: `GET /health` → 200 with no key even when auth is enabled.
- [ ] Rate-limit test: 3rd request with `GTM_RATE_LIMIT_RPM=2` returns 429.
- [ ] Rate-limit test: `GET /health` is never 429 regardless of rate.
- [ ] `/ready` broken-dep test: mock `TraceStore.ping()` returns `False` →
      `GET /ready` returns 503. `GET /health` in same state returns 200.
- [ ] Outcome lifecycle: 201 → 409 on duplicate → 404 on unknown run → 422 on
      invalid outcome value.
- [ ] Right-to-erasure test: triage + delete → subsequent contact lookup is empty.

**Median fallback (what to avoid):**
- Adding a few more unit tests to existing files and calling them "integration
  tests" (they must exercise the full HTTP stack via `TestClient`, not call
  internal functions directly).
- Testing auth only in the happy path (missing the no-key, wrong-key cases).
- Skipping the `/ready` 503 path (it's the only falsifiable check for K1's
  broken-dependency behavior).

---

### L3 — Load/concurrency smoke test: N concurrent `/triage` requests

**The problem:** The `POST /triage` handler runs `run_triage` in
`asyncio.to_thread()` (see `api.py:407`), which means the async event loop is
not blocked, but the thread pool may be exhausted under concurrent load and
shared in-memory state (idempotency cache, metric counters, circuit breakers)
may corrupt under race conditions. There is no test that fires concurrent
requests and checks for crashes, state corruption, or unacceptable latency.

**Frontier bar:**
1. **N=20 concurrent requests, distinct leads** — submit 20 `POST /triage`
   requests concurrently (using `asyncio.gather` or `concurrent.futures.
   ThreadPoolExecutor`) against a `TestClient`-backed ASGI app. All 20 use
   distinct emails and distinct idempotency keys. Assertion: all 20 return 200.
2. **No response corruption** — each response is a valid `TriageResult` JSON
   with a unique `run_id`. No two responses share a `run_id`. No response
   contains another lead's email in any field.
3. **No state corruption in metric counters** — after the 20 concurrent
   requests, `metrics.requests_total` counter (summed across all label combos)
   has incremented by exactly 20 (no lost increments, no double-counts).
4. **N=20 concurrent idempotency-key collisions** — submit 20 concurrent
   requests all using the SAME `idempotency_key`. Assertion: all 20 return 200
   and all 20 return the same `run_id`. No 500s. `cache_hit_total` increments
   by 19 (first is a miss, 19 are hits — race tolerance: accept 18-19 due to
   the race window between cache check and cache write, but not less than 18).
5. **Latency bound (mock provider only)** — wall-clock time for the 20
   concurrent mock requests (from first submit to last response) is < 10
   seconds. This is a smoke check, not a performance SLO: the mock provider
   has no I/O latency so any >10s result indicates a serialization bug (e.g.,
   the thread pool is sized 1).
6. **No crashes** — after the concurrent test, the app is still healthy:
   `GET /health` returns 200 and `GET /ready` returns 200.

**Falsifiable checks:**
- [ ] A test or script (e.g., `tests/test_concurrency.py`) submits N=20
      concurrent `POST /triage` requests using distinct leads and asserts all
      200 responses.
- [ ] No two responses in the N=20 run share a `run_id`.
- [ ] After N=20 requests, `metrics.requests_total` has incremented by exactly
      20 (counter integrity check).
- [ ] N=20 concurrent requests with the same `idempotency_key` all return 200
      with the same `run_id`. No 500s.
- [ ] `cache_hit_total` increments by ≥ 18 in the idempotency-collision test.
- [ ] Wall-clock time for N=20 concurrent mock requests is < 10 seconds
      (asserted with `time.monotonic()` in the test).
- [ ] `GET /health` and `GET /ready` return 200 after the concurrency test
      completes (no crash-and-wedge scenario).

**Median fallback (what to avoid):**
- Sequential requests with `time.sleep(0)` between them called "concurrent."
- Testing concurrency only on a unit function, not on the full HTTP stack
  (the `asyncio.to_thread` boundary is where races happen).
- A lax latency bound of 60s that would pass even a fully serialized thread
  pool.
- Skipping the idempotency-collision case (that's the shared-state path most
  likely to corrupt under concurrent access).

---

### L4 — Coverage: report + floor (fail under X%)

**The problem:** There is no coverage measurement and no floor. It is possible
to delete entire test classes, break entire subsystems, and have CI pass as
long as `pytest tests/ -x` exits 0 (which it would if the broken tests are
also deleted). A coverage floor makes deletions of tests visible.

**Frontier bar:**
1. **Coverage report in CI** — CI runs `pytest` with `--cov=gtm_triage
   --cov-report=term-missing --cov-report=xml`. The XML report (`coverage.xml`)
   is uploaded as a CI artifact. The terminal report shows per-module line
   coverage with missing lines.
2. **Coverage floor of 80%** — `pytest --cov=gtm_triage --cov-fail-under=80`
   causes the CI job to fail if total line coverage drops below 80%. The 80%
   floor is calibrated to the current test corpus (K-phase tests are expected
   to hold the line comfortably above this; the floor prevents catastrophic
   regression, not marginal drift).
3. **Excluded from coverage** — the following paths are excluded via
   `.coveragerc` or `pyproject.toml [tool.coverage.run] omit`:
   - `gtm_triage/mcp_server.py` (MCP integration, not tested in unit suite)
   - `tests/` (test files themselves)
   - `evals/` (eval scripts, not the module under test)
   The exclusion list is explicit and reviewed; blanket `omit=*` is not allowed.
4. **Coverage does not count mock/stub code as tested** — `gtm_triage/agents/
   llm_client.py`'s mock branch is tested by the mock-mode tests. The real
   OpenAI branch is NOT counted as covered (it requires `OPENAI_API_KEY` and
   is only exercised in the optional openai-eval job). This is acceptable: the
   floor applies to the lines that CAN be reached in CI (mock mode).
5. **`pytest-cov` is the only coverage tool** — no separate `coverage run` +
   `coverage report` invocation. The `pytest --cov` flag is the single source
   of truth. `pytest-cov` must be listed as a dev/test dependency.

**Falsifiable checks:**
- [ ] `pytest tests/ --cov=gtm_triage --cov-fail-under=80` exits 0 on the
      current test corpus (establishes that the floor is achievable).
- [ ] Deleting `tests/test_observability.py` and re-running causes coverage to
      drop below 80% and the CI job to fail (verifies the floor is meaningful).
- [ ] `coverage.xml` is produced by CI and uploaded as an artifact (verifiable
      in the Actions run's Artifacts section).
- [ ] `.coveragerc` or `pyproject.toml [tool.coverage.run]` has an explicit
      `omit` list that includes `gtm_triage/mcp_server.py` and `tests/`.
- [ ] `pytest-cov` appears in the project's test/dev dependency list (in
      `requirements-dev.txt`, `pyproject.toml [dev]`, or equivalent).
- [ ] The CI workflow passes `--cov-fail-under=80` to `pytest` (not a separate
      post-hoc check).

**Median fallback (what to avoid):**
- A `coverage.xml` artifact with no floor (report-only: coverage can drop to
  0% and CI still passes).
- Setting the floor at 50% (so low it catches only catastrophic total deletion).
- Omitting `gtm_triage/mcp_server.py` implicitly by never calling it, then
  being surprised when it's included in the denominator and drops the total.
- Running `coverage run -m pytest` separately from `pytest --cov` (two tools,
  potentially inconsistent results).

---

### L5 — `/metrics` auth decision: protect or document the public choice

**The problem:** `/metrics` is currently in `_PUBLIC_PATHS` (no auth required).
This is a deliberate choice documented in Phase K (K3, point 5: "`/metrics` is
public — same as `/health`. Operators need it from monitoring infra without
client credentials"). However, the choice is not explicitly surfaced in the
codebase, and there is no test that asserts the public status is intentional
rather than an oversight. Meanwhile, Prometheus metric endpoints can leak
internal topology (circuit-breaker names, endpoint names) to unauthenticated
callers.

**Frontier bar:**
The frontier bar for this item is a DECISION, not an implementation. Two
acceptable outcomes:

**Option A — Keep `/metrics` public, document explicitly:**
1. A `DECISION.md` entry records the choice: "we accept `/metrics` public
   because the metric labels contain no PII and the operational benefit
   (scraping from infra without credentials) outweighs the marginal topology
   leak risk."
2. A comment in `middleware.py` at the `_PUBLIC_PATHS` definition explains
   WHY `/metrics` is public (not just that it is).
3. A test asserts `"/metrics" in _PUBLIC_PATHS` (already present in
   `test_observability.py:TestPublicPaths` — must remain present and not be
   deleted or marked `xfail`).
4. The `GET /metrics` endpoint validates that no metric label contains a
   `run_id`, `request_id`, or any user-supplied string (the no-cardinality-bomb
   rule from K3 is the security backstop for the public choice).

**Option B — Auth-protect `/metrics` behind API key:**
1. `/metrics` is removed from `_PUBLIC_PATHS`.
2. `AuthMiddleware` enforces the API key on `/metrics` the same way it does on
   `/triage`.
3. The Prometheus scrape job in any deployment config (e.g., `render.yaml`,
   `docker-compose.yml`) is updated to pass the API key in the scrape config.
4. A test asserts `GET /metrics` without a key returns 401 when
   `GTM_API_KEYS` is set.
5. The `DECISION.md` entry records why auth was added (e.g., topology concerns,
   compliance requirement, or operator preference).

**The bar is met by implementing EITHER option fully.** A half-measure (auth
on `/metrics` but no scrape config update, or public `/metrics` with no
documentation) does NOT meet the bar.

**Falsifiable checks (Option A — public + documented):**
- [ ] `DECISION.md` has a dated entry for the `/metrics` auth decision stating
      "public" and the rationale.
- [ ] `_PUBLIC_PATHS` in `middleware.py` has an inline comment explaining why
      `/metrics` is public (not just a path string in a set).
- [ ] `TestPublicPaths.test_all_observability_endpoints_public` in
      `test_observability.py` passes and is NOT skipped/xfailed.
- [ ] `test_no_email_in_metric_labels` in `test_observability.py` passes
      (no PII in labels is the security backstop for the public choice).

**Falsifiable checks (Option B — auth-protected):**
- [ ] `"/metrics"` is NOT in `_PUBLIC_PATHS`.
- [ ] `GET /metrics` without a key returns 401 when `GTM_API_KEYS` is set.
- [ ] `GET /metrics` with a valid key returns 200 with Prometheus text body.
- [ ] `render.yaml` or `docker-compose.yml` shows the API key passed to the
      Prometheus scrape config (or documents the scrape auth mechanism).
- [ ] `DECISION.md` has a dated entry recording the auth choice and rationale.

**Median fallback (what to avoid):**
- Leaving the decision implicit (no DECISION.md entry, no comment, just a path
  in a set).
- Implementing auth on `/metrics` but not updating the deployment scrape config
  (breaks monitoring silently on deploy).
- Claiming the decision is "documented" because K3 in this FRONTIER.md says
  it's public (FRONTIER.md is a bar-setter, not operational documentation; the
  decision must live in DECISION.md and the code comment).

---

### Cross-cutting constraints for Phase L

These apply to ALL of L1–L5 and will be checked by frontier-audit:

1. **Zero credentials in CI** — The required CI job (`ci.yml` main job) must
   pass with no secrets set. Optional jobs (e.g., a separate `openai-eval` job
   gated on `secrets.OPENAI_API_KEY != ''`) are allowed but must NOT be required
   status checks.

2. **`provider=mock` in all CI tests** — Every test in `tests/` and every eval
   invocation in CI must use `provider=mock` and `GTM_PROVIDER=mock`. No test
   should silently fall back to OpenAI because the env var is unset.

3. **Test isolation: in-memory stores** — Integration tests must use
   `GTM_CRM_DB=:memory:` and `GTM_TRACE_DB=:memory:` (or equivalent `tmp_path`
   fixtures). Tests must not read or write `gtm_crm.db` or `gtm_trace.db` in
   the repository root (those are runtime files, not test fixtures).

4. **Coverage floor applies to the `gtm_triage` package only** — `evals/`,
   `scripts/`, `tests/`, and `web/` are excluded. The floor is a quality signal
   for the production module, not for test helpers.

5. **No flaky tests** — A test that requires `time.sleep()` > 1 second to
   pass (e.g., waiting for a rate-limit window to reset) must use injectable
   clocks or token-bucket reset methods rather than real `sleep()`. CI must
   complete the full test suite in < 3 minutes (cold dep install excluded).

---

## How to use this file
Build against these checks. When implementation is done, run `frontier-audit`
against each checkbox. A check is either met (with evidence: file, line, test
output) or not. No partial credit.
