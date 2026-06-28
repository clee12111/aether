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
  → PDL MISS? → website fallback BUILT but disabled (skip_website=True); DIG_DEEPER available when enabled
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

## 2026-06-27 — Phase M: Production readiness close-out

### Audit reconciliation
Cold re-audit produced findings report (`docs/audit/PRODUCTION_READINESS_AUDIT.md`).
Reconciliation pass verified every finding against actual file:line, deleted two
false findings from the raw agent output (both claimed `pg_store.py` lacked
`ping()`/`delete_by_email()` — both exist at `:344`/`:324`), and re-rated
severity for the actual deployment scenario (portfolio demo on Render free tier,
not 1000-RPS production).

Original: 3 blockers, 8 major, 6 minor.
Reconciled: 0 blockers, 1 major (R3: LLM failure aborts triage), rest minor/info.

### Severity-calibrated top 5 (for demo)
1. **R3: Wrap `chat()` in try/except** — only Major; OpenAI hiccup during live
   demo → visible 500.
2. **R1 partial: Wire retry on LLM call** — one `retry_with_backoff()` call site
   silently recovers transient failures.
3. **C5: Consume `injection_flagged`** — flag is dead data; either use it or
   remove the detection code.
4. **E3: Scoring-rule unit tests** — `_score_rules()` only tested end-to-end;
   threshold typos slip through CI.
5. **C1: Website fallback or remove claim** — `skip_website=True` contradicts
   ARCHITECTURE.md.

### Dependency/CVE audit
- `pip-audit -r requirements.txt` — **no known vulnerabilities**
- `pip-audit -r requirements-dev.txt` — **no known vulnerabilities**
- GTM service imports zero parent-project dependencies (no aiohttp, chromadb,
  langchain, rank_bm25, sentence_transformers, camelot). Dockerfile copies only
  `gtm_triage/`; `.dockerignore` excludes tests/evals/docs/.env/.git.

### Deployment config fixes
- **Dockerfile:** non-root user (`appuser`), `requirements.txt` instead of inline
  pip install, `HEALTHCHECK` directive calling `/ready`, `.dockerignore` created,
  `psycopg_pool` added.
- **render.yaml:** `healthCheckPath` changed from `/health` (always-200 stub) to
  `/ready` (truthful probe); `APP_ENV=production` added (enables fail-closed
  auth); `GTM_API_KEYS` added as secret env var.

### COMPLIANCE.md corrections
- Removed false claim that LLM prompts are not stored (trace payloads contain
  full tool args including lead PII).
- Documented HubSpot CRM deletion gap honestly (base class no-op, not yet
  implemented).

### Eval reproducibility proven
- Mock gate: 5/5 deterministic, keyless.
- Holdout v2 OpenAI+PDL runs 1-3: tier-for-tier identical (23/35 = 65.7%),
  confirming temp=0 determinism.
- FINAL_LOCK (22/35 = 62.9%, extractor A): committed at `452fe4b`, JSONL
  artifact verified (37 lines: 1 meta + 35 cases + 1 summary).
- `temperature=0` confirmed at `llm_client.py:223`.

### Final state
- **405 tests green** (0 failures)
- **Mock eval gate: 5/5**
- **pip-audit: clean**
- Reconciled audit report: `docs/audit/PRODUCTION_READINESS_AUDIT.md`

## 2026-06-27 — Phase M: Demo-readiness fixes (verified top-5)

Closes the verified top-5 from the reconciled audit before deploy.

### 1. R3+R1: LLM guard + retry (BLOCKER closed)
- Wrapped `chat()` call in `retry_with_backoff()` (2 retries, exponential
  backoff + jitter) at `loop_agent.py:452`.
- On failure after retries: `_degrade_to_mock()` runs the deterministic
  scorer (provider=mock, llm_adjustment=0) with whatever enrichment was
  already gathered. Returns a valid TriageResult — never a raw 500.
- Traces the LLM error and the degraded scoring step.
- 4 new tests in `test_llm_degradation.py`: ConnectionError, TimeoutError,
  OSError all produce valid (degraded) results; mock provider unaffected.

### 2. E3: Scoring-rule unit tests (46 tests)
- `test_score_rules.py`: direct tests for `_score_rules()` covering
  business email (+15), company size tiers, seniority tiers, title-inflation
  discount (vp/c_level at smb), intent signals (extracted + keyword fallback),
  spam suppression, opt-out/legal hard disqualifiers, free-email cap (69),
  industry bonus, existing-customer boost, intent gate (firmographics without
  intent capped at cold), injection flag consumed, and full ScoreLeadTool
  hot/disqualified/hard-override paths.

### 3. C1: Website-fallback docs aligned with code
- `skip_website=True` is the intended behavior for demo (avoids latency +
  external fetches). Updated FRONTIER.md and DECISION.md to document the
  fallback as "built but disabled by default" with instructions to re-enable.
  No code change — docs now match code.

### 4. C5: injection_flagged consumed
- `score_lead.py`: when `enrichment["injection_flagged"]` is True, LLM
  adjustment is skipped (`llm_adjustment=0`, `llm_reason="skipped:
  injection_flagged"`). Flag is surfaced in score output for trace visibility.
- Previously: injection was detected, logged, and stored but never consumed
  by scoring. Now: detection → action (LLM adjustment blocked).

### Final state
- **455 tests green** (405 existing + 46 scoring + 4 degradation)
- **Mock eval gate: 5/5**
- All existing tests unaffected (no regressions)

## 2026-06-27 — Phase M: Deploy config (auth + frontend wiring)

### Frontend auth (web/src/lib/api.ts)
- Every request sends `X-API-Key` header from `NEXT_PUBLIC_GTM_API_KEY` env var.
- Friendly error messages for 401 ("check your API key"), 429 ("rate limit"),
  503 ("cold-start, try again in ~30 seconds") — no raw crashes.
- Demo key is intentionally public: rate-limited (60 RPM), API triages
  synthetic leads only, never sends real email.

### render.yaml finalized
- `GTM_PROVIDER=openai` (was `mock`) — live LLM scoring for demo.
- `CRM_BACKEND=sqlite` (was `hubspot`) — no HubSpot dependency for demo.
- Added `ENRICHMENT_PROVIDER=pdl` + `PDL_API_KEY` (secret).
- All secrets via `sync: false` (Render secret env vars), none hardcoded.

### DEPLOY.md rewritten
- Clear env var tables for both Render and Vercel dashboards.
- Cold-start warning + warmup note for Render free tier.
- Verification checklist (8 steps: health, auth, CORS, triage, ops, rate limit, cold-start).

### Env vars needed

**Render dashboard:**
- `APP_ENV=production`
- `GTM_PROVIDER=openai`
- `GTM_API_KEYS=demo-<random>` (secret)
- `OPENAI_API_KEY=sk-proj-...` (secret)
- `ENRICHMENT_PROVIDER=pdl`
- `PDL_API_KEY=...` (secret)
- `FRONTEND_ORIGIN=https://<app>.vercel.app`
- `CRM_BACKEND=sqlite`
- `DATABASE_URL` (optional, Neon Postgres; omit for SQLite)
- `LANGFUSE_*` (optional)

**Vercel dashboard:**
- `NEXT_PUBLIC_API_URL=https://<render-url>`
- `NEXT_PUBLIC_GTM_API_KEY=demo-<same-key-as-Render>`

## 2026-06-27 — Phase M: Postgres migration dict-row fix

### Bug
`pg_store.py:79` used `row[0]` to read applied migration versions, but all
connections use `dict_row` factory. On real Neon Postgres, `row[0]` raises
`KeyError: 0` — the row is a dict, not a tuple. Startup crash on deploy.

Slipped through because existing tests mocked `fetchall.return_value = []`
(empty list → set comprehension never iterates → bug never triggered).

### Fix
- `pg_store.py:79`: `row[0]` → `row["version"]` (the actual column name).
- Audited entire file for other integer-indexed access — none found (all other
  access already uses `row["column_name"]`).
- `psycopg_pool` already in `requirements.txt` (added in Phase M close-out).

### Tests (4 new)
- `test_migration_with_dict_row_cursor`: pre-applied migration in dict format —
  regression test for the exact bug.
- `test_fresh_migration_applies_all`: verifies both 001 + 002 are inserted.
- `test_partial_migration_applies_remaining`: 001 pre-applied → only 002 runs.
- `test_write_then_get_events`: write → read round-trip with dict-row mocks.

### Final state
- **459 tests green**, mock eval gate 5/5

## 2026-06-28 — CORS preflight fix (OPTIONS blocked by AuthMiddleware)

### Bug
Browser sends an OPTIONS preflight before every cross-origin POST. Starlette
middleware runs outside-in (last-added first), so `AuthMiddleware` (added after
`CORSMiddleware`) ran BEFORE it. The OPTIONS request had no API key →
AuthMiddleware returned 401 → CORSMiddleware never ran → browser got no
`Access-Control-Allow-Origin` header → "Failed to fetch."

### Fix
`middleware.py`: skip auth for `request.method == "OPTIONS"` — let it pass
through to CORSMiddleware which handles the preflight response with the
correct CORS headers.

### Final state
- **459 tests green**

## 2026-06-28 — CORS headers missing on error responses ("Failed to fetch")

### Bug
The deployed site loaded fine but every form submit showed "Failed to fetch."
The browser completed the OPTIONS preflight (200 + CORS headers) but the
actual POST got a 401 (or 503) **without `access-control-allow-origin`**.
Browsers refuse to let JS read responses without CORS headers, regardless of
status code → "Failed to fetch" instead of the real error.

### Root cause
Starlette middleware runs outside-in (last-added first). `CORSMiddleware` was
added FIRST (innermost), `AuthMiddleware` was added LATER (outermost). Auth
returned 401 before the request ever reached CORS → no CORS headers on the
response.

```
BEFORE (broken):  Auth → RateLimit → ... → CORS → app
                  Auth returns 401 here ↑ — CORS never runs

AFTER (fixed):    CORS → RequestId → Auth → RateLimit → ... → app
                  CORS wraps everything ↑ — adds headers to ALL responses
```

### Fix
`api.py`: moved `app.add_middleware(CORSMiddleware, ...)` to be added LAST
so it's the outermost middleware. Now CORS headers appear on 200s, 401s,
429s, 500s — every response.

### Diagnosis method
```bash
# Preflight: 200 + CORS headers (was already working)
curl -D - -X OPTIONS -H "Origin: https://aether-c7bg.vercel.app" ...

# Real POST: 401 WITHOUT access-control-allow-origin (the bug)
curl -D - -X POST -H "Origin: https://aether-c7bg.vercel.app" ...
# → no access-control-allow-origin header → browser blocks → "Failed to fetch"
```

### Tests added
- `test_cors_headers_on_auth_error`: POST without key → asserts
  `access-control-allow-origin` header is present on the error response.
- `test_cors_preflight_returns_200`: OPTIONS → asserts 200 + correct origin.

### Final state
- **461 tests green**

## 2026-06-28 — Frontend fetch timeout + cold-start UX

### Problem
"Failed to fetch" after ~5s. With CORS fixed, the real issue surfaced: the
default `fetch()` has no timeout and no feedback. Real triage takes ~11s
(OpenAI LLM), and Render free-tier cold-starts add 30-60s. The user stares
at "Triaging..." with no indication of progress.

Additionally, if `NEXT_PUBLIC_API_URL` isn't baked into the Vercel build
(must redeploy after setting it), the frontend defaults to `localhost:8000`
which silently fails with a network error after the browser's TCP timeout.

### Diagnosis
- `curl` to `/triage` with valid key completes in ~11s (200) — backend fine.
- No `AbortController` or timeout in the frontend code — `fetch()` runs
  until browser TCP timeout or success.
- `console.log` added to surface the configured API URL in browser devtools.

### Fixes (web/src/lib/api.ts + page.tsx)
1. **60s explicit timeout** via `AbortController` — no more silent hangs.
   Timeout error says "backend may be waking from cold start, try again."
2. **Network error message** — catches fetch failures and says "check that
   NEXT_PUBLIC_API_URL is set correctly" (the most common misconfiguration).
3. **Console log on load** — `[GTM] API → https://...` so misconfig is
   immediately visible in browser devtools.
4. **Warmup ping on page load** — fires `GET /ready` on mount so Render
   wakes while the user fills out the form.
5. **Progressive loading messages** — "Triaging..." (0-3s) → "Analyzing
   lead — this can take ~15s" (3-15s) → "Still working — backend may be
   waking from cold start" (15s+).

### Final state
- **461 tests green**, frontend build clean

## 2026-06-28 — Disable auth for public demo

### Decision
Auth is DISABLED for the public demo. Protection is via daily cap (200/day,
falls back to mock provider when exceeded) + rate limit (60 RPM per IP).

**Why:** The fail-closed auth (`APP_ENV=production` + `GTM_API_KEYS`) caused
503 on every request from the frontend. The demo key approach required both
Render and Vercel env vars to match AND a Vercel redeploy to bake the key
into the JS bundle — too many moving parts for a portfolio demo that only
triages synthetic leads and never sends real email.

### Changes
- `render.yaml`: `APP_ENV=demo` (was `production`). Removed `GTM_API_KEYS`.
  Auth middleware sees non-production env + no keys → auth disabled.
- `web/src/lib/api.ts`: removed API key logic. No `X-API-Key` header sent.
  Kept: 60s timeout, warmup ping, progressive loading messages, console log.
- `test_cors_on_triage_non_200`: verifies CORS headers on validation errors
  from /triage (the "Failed to fetch" regression guard).

### Abuse protection (still active)
- **Daily cap:** 200 OpenAI calls/day (default `DAILY_QUERY_CAP`). After 200,
  falls back to mock provider (free, instant). Configurable via env var.
- **Rate limit:** 60 RPM per IP (default `GTM_RATE_LIMIT_RPM`). Returns 429.
- **Request size:** 64 KB body limit. Field length caps (email 320, message 10K).
- **CORS:** locked to `FRONTEND_ORIGIN` (no wildcard).

### Final state
- **462 tests green**, frontend build clean

## 2026-06-28 — CRM upsert crash guard (R4)

### Bug
Triage completed successfully (OpenAI returned a result) but then crashed at
`api.py:447` on `_crm.upsert()` → HubSpot returned 400 Bad Request →
`raise_for_status()` threw → unhandled exception → 500 to the user. The
triage result was lost even though the pipeline succeeded.

Cause: Render dashboard had `CRM_BACKEND=hubspot` (stale) while `render.yaml`
says `sqlite`. The HubSpot token was invalid or the contact properties weren't
set up.

### Fix
- `api.py:447`: wrapped `_crm.upsert()` in try/except — CRM write failures
  are logged but don't crash the triage response. The user gets their result.
- Render dashboard: `CRM_BACKEND` must be changed to `sqlite` to match
  `render.yaml`.

### Final state
- **462 tests green**

## 2026-06-28 — Phase N: Transferability audit + /leads fix

### Audit
Full read-only audit of abstraction leaks, interface completeness, backend
parity, hardcoded assumptions, motion coupling, and swap tests. Report at
`docs/audit/TRANSFERABILITY_AUDIT.md`.

### Root cause of empty ops board
`api.py:537`: `/leads` was hardcoded to `isinstance(_crm, SQLiteCRM)` and
returned `[]` for any other backend. HubSpot contacts existed but were
invisible to the frontend.

### Fixes
- `CRMStore.list_contacts()` added to ABC (`crm/base.py`) with default `[]`.
- `HubSpotCRM.list_contacts()` implemented — HubSpot v3 search, filter by
  contacts with `gtm_tier` set, sorted by last-modified.
- `/leads` endpoint: removed `isinstance`, calls `_crm.list_contacts()`.
- Removed 2 `hasattr()` guards on protocol methods (`api.py:339, 551`).

### Key audit findings (not yet fixed)
1. **LLM provider has no abstraction** — adding Anthropic requires 8+ file
   changes. No `LLMProvider` interface; enrichment extractors instantiate
   `openai.OpenAI()` directly.
2. **Model defaults scattered** — `"gpt-4o-mini"` hardcoded in 8 files;
   `GTM_MODEL` env var doesn't cascade to enrichment.
3. **Inbound motion baked into prompts/templates** — system prompt, draft
   templates, and pre-signal extraction assume inbound. Outbound needs new
   prompt + templates + trigger, but core (enrichment, CRM, scoring, trace)
   is reusable.

### Final state
- **470 tests green** (462 + 8 new list_contacts/ABC tests)

## 2026-06-28 — Phase N2: LLMProvider abstraction + HubSpot delete_contact

### LLMProvider ABC (`agents/llm_provider.py`)
- `LLMProvider` ABC with `chat(messages, model, temperature, ...) -> ChatResult`.
- Implementations: `OpenAIProvider`, `MockProvider`, `AnthropicProvider` (real
  — uses Anthropic SDK with system param extraction).
- Factory: `create_provider("openai" | "anthropic" | "mock")`.
- All LLM calls route through `chat()` → `LLMProvider.chat()`. Zero direct
  vendor SDK imports outside `llm_provider.py` (enforced by AST scan test).

### Call sites refactored
- `extraction.py`: `from openai import OpenAI` → `from llm_client import chat`
- `signals.py`: same
- `waterfall.py`: same + accepts `llm_provider`/`llm_model` params
- `enrich_lead.py`: `self._provider == "openai"` → `self._provider != "mock"`
- `score_lead.py`: same
- `llm_client.py:infer_enrichment/infer_score_adjustment`: accept `provider`
  param, pass through to `chat()`
- `api.py`: over-cap logic generalized from `== "openai"` to `!= "mock"`

### Model cascade
`GTM_MODEL` now propagates from `api.py` through tools to enrichment
extractors. `waterfall.py` no longer hardcodes `"gpt-4o-mini"`.

### HubSpot delete_contact
`HubSpotCRM.delete_contact()` implemented via HubSpot v3 archive endpoint
(`DELETE /crm/v3/objects/contacts/{id}`). COMPLIANCE.md right-to-erasure
path now works on the real CRM.

### Swap test result
To add Anthropic: `pip install anthropic` + set `GTM_PROVIDER=anthropic`
+ set `ANTHROPIC_API_KEY`. Zero other file changes. Downgraded from
"HIGH friction (8+ files)" to **one adapter + one env var**.

### Final state
- **480 tests green** (470 + 10 LLM provider tests)
- Mock eval gate: 5/5
