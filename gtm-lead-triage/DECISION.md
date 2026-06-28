# DECISION.md — GTM Lead-Triage: Decision Log

## 2026-06-26 — Enrichment Provider + Honesty-Gap Scope

### Context
Three honesty gaps identified in the current build:
1. Input requires pre-parsed structured fields; real inbound is unstructured.
2. `enrich_lead` guesses via regex, doesn't call any external data source.
3. The loop follows a fixed script; the eval was written to satisfy the rules.

### Decision: PDL as default enrichment provider

**Provider:** People Data Labs (PDL) Person Enrichment API  
**Tier:** Free dev tier (100 calls/month, no credit card required)  
**Integration:** Raw REST via `httpx` — no vendor SDK dependency  
**Interface:** Swappable `EnrichmentProvider` ABC (same pattern as `CRMStore`)  
**Default implementation:** `PDLProvider`  
**Mock implementation:** Current regex logic demoted to `MockProvider` for CI

### Waterfall (zero-cost-first)
```
email arrives
  → MX/DNS validity check (free, stdlib)
  → disposable-domain blocklist check (free, static list)
  → INVALID? → short-circuit to disqualified, no enrichment
  → PDL Person Enrichment (100/month free tier)
  → PDL MISS? → company-website fetch (httpx) + LLM read for basic firmographics
  → cache result by email (in-memory, per-session)
```

### Why PDL
- Free dev tier with no credit card — matches "free-tier keys only" constraint.
- Person Enrichment API returns industry, company size, seniority, title — the
  exact fields the current mock guesses at.
- Raw REST (single POST endpoint) — no SDK, no dependency.
- Swappable: the `EnrichmentProvider` interface means Clearbit, Apollo, or any
  other provider can slot in without changing the tool.

### What would need a paid plan
- PDL: > 100 calls/month. For demo/eval purposes, 100 is sufficient.
- Clearbit: no free tier at all. Ruled out as default.
- Apollo: free tier exists but rate limits are stricter. Viable as alternate
  implementation.

### Scope for this build phase
All three gaps are in scope. No implementation this pass — bar set in FRONTIER.md,
audit produced, phase plan proposed. Build starts only after green-light.

### Constraints
- No LangChain / LangGraph / CrewAI.
- Enrichment behind swappable interface (EnrichmentProvider ABC).
- Free-tier keys only.
- Deterministic executor boundary preserved: enrichment provider calls are in the
  tool (like answer_from_context in Aether), not in the executor.

---

## 2026-06-27 — Eval methodology: de-gamed held-out + dev/test firewall

### Problem found
The original eval was gamed: test-lead company names contained the industry keyword the regex
enrichment keyed on, so the system "passed" by pattern-matching the answer. The held-out set was
same-author, same-style as the training leads.

### Decision
- **holdout_v2** (35 leads): independently built, blind to the rubric, real company domains with
  NO industry-keyword leak, fictional contacts (no real PII), senior-SDR labels. **Write-once /
  measure-once.**
- **dev_split** (~31 leads): the ONLY set we calibrate/iterate against. Same construction rules.
- Metrics: per-tier precision/recall + confusion matrix + **false-hot** (wasted AE time) and
  **false-cold** (lost deal), reported separately. Blended accuracy de-emphasized.
- Never tune to holdout_v2; calibrate on dev_split. Fixing a genuine bug is not tuning;
  threshold-fiddling is.

## 2026-06-27 — Honest progression + final LOCKED number

### The climb (de-gamed holdout_v2, mechanism-attributed)
25.7% (regex baseline) → 31.4% (real PDL → warm) → 51.4% (message extraction → hot + opt-out/legal)
→ 62.9% (real LLM + agency + calibration).

### Decision: official number LOCKED at 62.9%
holdout_v2 = 22/35 (62.9%), **zero false-hots** (hot precision 1.000), warm recall 0.818,
false-cold 12.5%. Provider gpt-4o-mini, PDL cassettes, flat extractor (A). **Frozen — no further
measurement/tuning.** Lead with per-tier metrics, not the blended number.

### Stated limitations (not hidden)
- Hot recall 0.40 — 3 leads (Pfizer/Siemens/Visa) need seniority the message doesn't state.
- Cold→warm inflation — enterprise email (+15) + size (+25) baseline pulls cold-intent enterprise
  leads toward warm (calibration gap).
- 2 "pipeline-error" leads (mailer-daemon/freelancer) flagged for robustness review (bug-fix, not
  a frozen mis-classification).

## 2026-06-27 — E.2: atomic attribution-aware extraction ABLATION → revert to flat

### Decision: keep flat extraction (A); revert atomic signals (B); retain B behind a flag
Built an atomic, attributed, evidence-grounded extractor (subject = sender/third_party, relation =
self/sponsor_delegated/mentioned) and ran a clean A-vs-B ablation on dev_split. **B lost** (48.4% vs
54.8%; couldn't produce hot; OpenAI attribution 72.7% < mock 86.4%).

### Why / what was kept
gpt-4o-mini can't reliably coordinate 5 fields per call — a **model-capacity** result, not a
refutation. B's code kept behind `extractor="B"` for a stronger model (multi-model orchestration).
Enum-constrained values + 100%-accurate evidence grounding kept regardless. Attribution principle
validated on the showcase (Dell "our CTO mentioned" → not credited; Cisco "my VP asked me" →
sponsor-delegated intent counts).

## 2026-06-27 — Frontier research verdict (input extraction strategy)

Per docs/research/FRONTIER_EXTRACTION_RESEARCH.md:
- **Length-adaptive routing (thin→expand / rich→decompose): REJECTED** — internally contradictory
  (50w vs 500w), no ablation support, not GTM practice.
- **FActScore/SAFE "atomic claim decomposition" applies to the OUTPUT (draft verification), NOT the
  input (schema extraction)** — different tasks.
- **HyDE / inference-as-expansion: REJECTED** — ungrounded, hallucination risk. "Expansion" =
  grounded enrichment only.
- **Enrichment-first → single-call structured extraction** confirmed as the real GTM frontier
  (matches current design).

## 2026-06-27 — Grounded-draft guardrail (Meridian-style) — deferred to campaign

Deterministic claim-grounding on the OUTPUT draft: every asserted fact must trace to grounded
signal/enrichment evidence (source + confidence ≥ threshold); ungrounded → generic fallback.
Lightweight now (templates interpolate only grounded fields); full version with the outbound
campaign (generated copy + claim verifier). Correct home for the FActScore/Meridian verification
lineage.

## 2026-06-27 — Platform architecture + scope

Present as a GTM-agent **platform**, inbound = motion #1. Ports-and-adapters: channel adapters →
normalized Signal → pluggable motion → action → trace + eval (see ARCHITECTURE.md, BACKLOG.md).
Inbound hardened first; outbound **campaign** motion is the centerpiece (proves generalization,
maps to the JD's campaign execution). **Do not speculatively build a multi-motion framework** —
prove generalization with one second motion.

## 2026-06-27 — Production hardening (G + H)

### Phase G — API hardening
Bearer/X-API-Key auth (all endpoints except /health), per-key token-bucket rate limit, 64KB body
cap, strict Pydantic input limits, structured errors (no stack leaks), timeouts on every external
call.

### Phase H — reliability
- **Auth fails CLOSED in production** (APP_ENV=production + no keys → 503); hmac.compare_digest.
- **Async-concurrent triage** (asyncio.to_thread frees the event loop — concurrency, NOT
  fire-and-return; a job queue is the high-throughput upgrade).
- Retry + backoff (transient only), thread-safe circuit breaker, graceful degradation (enrichment
  failure → proceed; over-cap → mock fallback).
- Postgres: versioned migrations (schema_migrations) + connection pool.

### Provider/model
Default gpt-4o-mini, provider-swappable (mock for keyless CI; cassettes for reproducible eval).

## 2026-06-27 — Phase I: Security hardening

### Threat model
Lead messages are ATTACKER-CONTROLLED text that flows into: (1) LLM extraction + scoring nudge,
(2) website fetch (domain from lead email). Both paths hardened.

### Decisions
- **Prompt injection**: detected + flagged, NOT blocked — the deterministic scorer is the backstop.
  Message text reaches scoring only as typed signals (intent enum, seniority enum), never as raw
  instructions. Injection text cannot alter the tier. 3 adversarial injection tests confirm.
- **SSRF guard**: validate_domain() resolves DNS BEFORE connecting, blocks RFC1918/loopback/link-local.
  Blocks non-http(s) schemes. Wired into WebsiteFallback._fetch_homepage and DIG_DEEPER.
- **CORS**: locked to FRONTEND_ORIGIN (no wildcard), methods restricted to GET+POST, headers to
  Authorization/X-API-Key/Content-Type.
- **Secrets audit**: no hardcoded keys in any .py file (automated grep test). .env gitignored at both
  root and project level.
- **Dep scan**: pip-audit run. GTM-specific deps (httpx, psycopg, openai, pydantic, fastapi) clean.
  Parent-project deps (aiohttp, chromadb, langchain) have known CVEs — tracked but not GTM-blocking.

## 2026-06-27 — Phase J: Privacy/compliance

### SSRF hardening (closing Phase I gaps)
- Added CGNAT (100.64.0.0/10), IPv4-mapped IPv6 (::ffff:0:0/96) to blocked networks.
- Cloud metadata endpoint 169.254.169.254 explicitly tested as blocked.
- `resolve_and_validate()` returns safe IPs for IP pinning (prevents DNS-rebinding TOCTOU).
- Full IPv6 coverage: ::1, fc00::/7 (ULA), fe80::/10 (link-local), IPv4-mapped.

### PII minimization + right to erasure
- Only mapped enrichment fields persisted, not raw PDL responses.
- `DELETE /contacts/{email}`: removes CRM record + activities + trace events + idempotency records.
- sean@peopledatalabs.com cassette scrubbed (only real person entry).

### COMPLIANCE.md
- Data collected, lawful basis (legitimate interest Art. 6(1)(f)), retention, deletion path,
  sub-processors (OpenAI, PDL, HubSpot, Neon), PII minimization.

## 2026-06-27 — Phase K: Production observability

### K1 — Readiness vs. liveness
- `/health` remains pure liveness (no I/O, always 200).
- `/ready` checks trace store + CRM via `ping()` (SELECT 1). Returns per-dependency status JSON.
  Enrichment unavailability is degraded, not down. Public (no auth).

### K2 — Structured JSON logging
- `JSONFormatter` (default) and `TextFormatter` (`LOG_FORMAT=text`).
- `contextvars.ContextVar` propagates `request_id` (UUID per request, assigned in
  `RequestIdMiddleware`) and `run_id` into every log record.
- `otel_trace_id` injected when OTel is active. All three IDs correlate in log output.
- **No PII at INFO**: email, name, company, message never appear in log fields.
  `run_start` logs source + run_id + request_id. `run_end` logs tier + route + steps.

### K3 — Prometheus metrics
- `GET /metrics` returns `text/plain; version=0.0.4` Prometheus exposition format. Public.
- In-process counters/gauges/histograms (thread-safe, no SQL on scrape except one daily-cap read).
- Counters: `gtm_requests_total`, `gtm_request_errors_total`, `gtm_triage_total`,
  `gtm_cache_hit_total`, `gtm_cache_miss_total`.
- Gauges: `gtm_circuit_breaker_state`, `gtm_daily_cap_used`, `gtm_daily_cap_limit`.
- Histograms: `gtm_request_duration_seconds`, `gtm_triage_duration_seconds`.
- No cardinality bombs (labels are bounded enums, never user strings/IDs).

### K4 — Sentry (soft dep, no-op without DSN)
- `sentry-sdk` is a soft dependency (`try/except ImportError`). `SENTRY_DSN` unset → true no-op.
- `before_send` hook scrubs PII (email → `[email]`, name/company/message → `[scrubbed]`).
- Environment tagging via `APP_ENV`, release via `SENTRY_RELEASE`.
- `traces_sample_rate` defaults to 0.0 (error-only).

### K5 — AlertHook protocol
- `AlertHook` protocol with `LogAlertHook` (default) and `WebhookAlertHook` (fire-and-forget
  background thread, active only when `ALERT_WEBHOOK_URL` is set).
- `CircuitBreaker` accepts `alert_hook` kwarg; fires `"circuit_open"` on trip.
- `ErrorRateMonitor`: rolling 1-min error rate, configurable threshold (default 20%),
  cooldown (default 300s) prevents alert storms.
- Circuit breaker state reflected in `gtm_circuit_breaker_state` gauge.

### K6 — OpenTelemetry (soft dep, no-op without OTLP_ENDPOINT)
- `opentelemetry-sdk` is a soft dependency. No `OTLP_ENDPOINT` → `NoOpTracer`.
- `traced_span()` context manager creates child spans (or no-ops).
- OTel trace-id injected into `otel_trace_id_var` for log correlation.
- FastAPI auto-instrumentation when SDK present.

### K7 — Outcome-loop stub
- `outcomes` table in TraceStore (CREATE TABLE IF NOT EXISTS). No email stored — run_id only.
- `POST /outcomes/{run_id}`: write-once (409 on dup, 404 if run unknown).
  `actual_outcome` enum: converted / no_show / unqualified / unknown.
- `GET /metrics/outcomes`: precision-against-outcome per tier (empty-safe). Auth-protected.

### Cross-cutting
- `_PUBLIC_PATHS` updated: `/health`, `/ready`, `/docs`, etc. `/metrics` and `/metrics/outcomes`
  are auth-protected (business-sensitive data).
- `RequestIdMiddleware` assigns UUID per request, returns `X-Request-Id` response header.
- `MetricsMiddleware` records request count, latency, error type per endpoint.
- 48 new tests covering all K1–K7 subsystems in isolation (no external deps required).

## 2026-06-27 — Phase L: Testing + CI/CD

### L1 — GitHub Actions CI
- `.github/workflows/gtm-ci.yml`: runs on push/PR touching `gtm-lead-triage/**`.
- Two sequential steps: `pytest tests/ --cov=gtm_triage --cov-fail-under=70` then
  `python evals/run_eval.py` (deterministic mock eval gate). Red = can't merge.
- Dependency caching via `actions/cache` keyed on `requirements-dev.txt`.
- Python 3.11 pinned. Zero secrets required.

### L2 — Integration tests
- `tests/test_integration.py`: 13 end-to-end flows through real FastAPI app (TestClient).
- Full triage lifecycle (all 4 tiers), idempotency replay (same/different keys), auth
  enforcement (no-key/wrong-key/valid-key/public/fail-closed), rate-limit unit test,
  `/ready` 503 with mocked broken TraceStore.ping(), `/health` stays 200 when dep broken,
  outcome flow (triage → record → metrics), right-to-erasure (triage → delete → verify gone).

### L3 — Load/concurrency smoke test
- `tests/test_concurrency.py`: 20 rapid sequential `/triage` requests (mock provider).
  All must complete 200, unique run_ids, bounded latency (<30s), mixed tiers correct,
  no trace corruption (each run_id has its own events).
- Store-level: rapid sequential trace + CRM writes, concurrent metric counter increments
  (Lock-protected counters verified at 1000 = 10 threads × 100 increments).

### L4 — Coverage
- `setup.cfg`: pytest config + coverage config. Source: `gtm_triage`, omit mcp_server/tests/evals.
- Floor: 70%. Current: 83.14%. `pytest-cov` in `requirements-dev.txt`.

### L5 — /metrics exposure decision: AUTH-PROTECTED
**Decision: auth-protect `/metrics` and `/metrics/outcomes`.**
- `/metrics` exposes: triage volumes, tier distribution, cache hit rates, daily cap usage,
  circuit-breaker state. All business-sensitive in multi-tenant production.
- `/metrics/outcomes` exposes: prediction precision by tier — competitive intelligence.
- `/ready` stays public — load balancers need it without credentials.
- Removed both from `_PUBLIC_PATHS`. Integration test verifies 401 without key, 200 with key.

### Test summary
- 385 total tests (313 existing + 24 integration + 7 concurrency + 48 observability − 7 updated).
- Coverage: 83.14% (floor 70%).
- Mock CI gate: 5/5.
- `tests/conftest.py`: sets high rate limit default to prevent cross-test bucket exhaustion.

## 2026-06-27 — Hardening track merge consolidation

### Merge to main
All hardening branches (phase-e2 through phase-l) merged to main in dependency
order. Phases e2 through j were already ancestors of main; phases k and l
fast-forwarded cleanly (no conflicts — disjoint files).

### CI fixes applied on main
1. `test_trace_store_parity.py`: guarded `PostgresTraceStore` import behind
   `try/except ImportError` (psycopg not in CI deps).
2. `test_pg_store.py`: added `pytestmark = pytest.mark.skipif(not _HAS_PSYCOPG)`
   to skip entire module when psycopg unavailable.
3. `TraceStoreProtocol` + `PostgresTraceStore`: added missing methods (ping,
   delete_by_email, record_outcome, get_outcome, get_outcome_metrics) + 002_outcomes
   migration. Protocol/impl parity restored.
4. `ErrorRateMonitor._last_alert_time`: initialized to `-(cooldown+1)` instead of
   `0.0` to ensure first alert fires even when `time.monotonic() < cooldown_seconds`
   (CI runners with uptime < 300s).

### Final numbers on main
- **405 tests green** (local + CI)
- **Coverage: 76% CI / 81% local** (floor: 70%)
- **Mock eval gate: 5/5**
- **GitHub Actions CI: green** (run 28307610669)
- Commit: ae970d1 on main, pushed to origin/main
