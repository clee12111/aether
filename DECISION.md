# DECISION.md — Aether GTM Extension Decision Log

## Phase 6a.4: Micro-fix + README + architecture diagram — 2026-06-26

### What was built
- Unique preset emails: each preset chip generates a unique email per click
  (e.g. julia.martinez+m1abc@...) so every preset submission is a fresh run
  showing real tokens/cost, not an idempotency-cached result.
- README rewritten as the project's front door: honest framing ("the RAO
  architecture validated on FinQA at 75.5% on n=200; here applied to lead
  triage and validated at 90% on its own held-out eval"), Mermaid architecture
  diagram, four tools table, eval section, two-view app, run/deploy, scope
  guardrails.
- Mermaid flowchart: Sources -> n8n -> Aether Agent (built) -> HubSpot/Langfuse
  (integrated) -> Routing (AE/SDR/Marketing/Drop). Renders on GitHub.

### Acceptance results
- Mock eval: 5/5, exit 0.
- `npm run build`: 0 errors.
- README reads cleanly with honest FinQA citation.

---

## Phase 6a.3: Daily LLM cap + detail-panel fix — 2026-06-26

### What was built

**Daily query cap:**
- `DAILY_QUERY_CAP` env (default 200). `daily_usage` table in SQLite trace store
  (date PK, count). Per POST /triage: if under cap and OpenAI configured, runs
  with openai and increments; otherwise falls back to mock silently. The app never
  errors or blocks.
- Response tagged with `provider_used` ("openai" or "mock") so the frontend knows.
- GET /config extended: returns daily_cap, used_today, remaining.
- Eval/CI unaffected (they call run_triage directly with mock).

**Detail-panel card fix:**
- Root cause: frontend parsed triage data from trace event payloads (which store
  `str(result)` not JSON). Fix: GET /runs/{id} now includes `triage_result` from
  the idempotency cache, giving the frontend typed access to score, enrichment,
  and outreach.
- `get_result_by_run_id()` added to TraceStore (looks up by run_id in
  idempotency_keys table).
- Frontend uses `triage_result` directly with proper TypeScript interfaces.

**Metric hiding:**
- Stats row hides tokens/cost/latency when all zero (mock runs). Shows provider
  used (green "openai" or gray "mock"). Shows "N real runs left today" from config.

### Acceptance results
- Mock eval: 5/5, exit 0 (unchanged).
- Unit tests: 22/22 (5 daily cap + 10 HubSpot + 7 Postgres).
- `npm run build`: 0 errors.
- GET /config returns cap, used_today, remaining correctly.

### Browser check for the user
1. http://localhost:3000 - click a preset chip, submit. See result with tier.
2. http://localhost:3000/ops - click the lead. Draft card shows subject+body
   with Copy button. Score card shows points + rule split. Enrichment card
   shows industry/seniority/confidence. Phased trace below.
3. For real LLM run: restart API with `GTM_PROVIDER=openai OPENAI_API_KEY=...`,
   submit a lead. Stats row shows real tokens/cost/latency in green. Draft
   card shows a model-written email. Score card shows LLM adjustment.

---

## Phase 6a.2: Production-feel revamp — 2026-06-26

### What was built

**Backend:**
- Default GTM_PROVIDER changed to `openai` (app runtime). Eval/CI stays on mock.
- run_id threaded through executor -> tools: BaseTool.run() now accepts
  `run_id`, executor passes it, enrich_lead and score_lead pass it to their
  LLM calls. Every LLM call (agent decide + tool-internal) now records in
  both SQLite trace and Langfuse.
- GET /config endpoint: returns provider, model, crm_backend, langfuse_enabled,
  langfuse_host so the frontend can build correct deep links and hide irrelevant UI.

**Frontend - Lead form (/):**
- 5 one-click preset chips: hot enterprise buyer, warm mid-market evaluator,
  cold IC browser, obvious spam, opt-out. Clicking fills the form. Manual entry
  still works.

**Frontend - Ops dashboard (/ops):**
- Draft outreach card: subject + body + "draft" badge + Copy button.
- Score card: large points display, rule_points vs llm_adjustment split, reason.
- Enrichment card: industry, size, seniority, business email, confidence.
- Phased trace: flat events grouped into CRM Lookup / Enrichment / Scoring /
  Draft Outreach phases, each collapsible with step count and duration.
- Tier filters: clickable chips in the metric strip filter the lead list.
- Search box: filters by name or email.
- Score shown on every lead row consistently.
- Smart metrics: when provider=mock (0ms/0tok), hides tokens/cost/latency.
- Working deep links:
  - Langfuse: real trace URL from config.langfuse_host + trace name. Hidden
    when Langfuse is not configured.
  - HubSpot: real contact search URL. Grayed out with tooltip when CRM
    backend is SQLite.

### Acceptance results
- `python -m evals.run_eval` (mock): 5/5, exit 0.
- `python -m pytest tests/`: 17/17.
- `npm run build`: compiled in 2.0s, 0 errors, / and /ops static.
- GET /config returns correct runtime info.

### Browser check for the user
1. Open http://localhost:3000 - click "Hot buyer" preset chip, form fills,
   submit, see tier badge + route + score.
2. Open http://localhost:3000/ops - see the lead in the list with score and
   tier badge. Click it to see the draft card, score card, enrichment card,
   and phased trace. Click tier chips to filter. Use search box.
3. For real LLM metrics: restart the API with
   `GTM_PROVIDER=openai OPENAI_API_KEY=... python -m uvicorn gtm_triage.api:app`
   and submit a lead. The stats row shows real tokens/cost/latency, and the
   score card shows the LLM adjustment.

### Design skill used
design-taste-frontend (taste skill). Same system as Phase 6a.1: Geist Sans +
Mono, zinc neutrals, indigo accent, rounded-lg shape system, VARIANCE 5 /
MOTION 3 / DENSITY 5.

---

## Phase 6b: Deploy-ready (Postgres trace + deploy configs) — 2026-06-26

### What was built
- `gtm_triage/trace/pg_store.py`: PostgresTraceStore using psycopg (sync). Same
  method signatures as the SQLite TraceStore (write, get_run_events, get_run_stats,
  list_runs, get_by_idempotency_key, store_idempotency_key, close). Uses JSONB for
  payload/result columns. Creates tables on first connect.
- API lifespan updated: if `DATABASE_URL` env is set, uses PostgresTraceStore;
  else SQLite (unchanged default).
- CORS updated: reads `FRONTEND_ORIGIN` env (falls back to `CORS_ORIGINS`, then
  `http://localhost:3000`). Supports comma-separated origins for dev + prod.
- Dockerfile updated: added `psycopg[binary]` to pip install.
- `render.yaml`: Render web service config with all env vars.
- `DEPLOY.md`: click-by-click guide for Neon (Postgres), Render (API), Vercel
  (frontend). Complete env var reference table. Trade-off notes for mock vs openai.

### What was NOT modified
Agent, tools, models, existing endpoints, SQLite TraceStore, CRM backends,
frontend code, Langfuse wrapper - all unchanged.

### Acceptance results (executed)
- Mock eval: 5/5, exit 0 (SQLite default path unchanged).
- Unit tests: 17/17 (10 HubSpot + 7 Postgres store against mocked psycopg).
- `npm run build` in web/: compiled in 2.4s, 0 errors.
- render.yaml and DEPLOY.md exist with complete env var lists.

### Postgres backend verification
**Mocked only** (no DATABASE_URL in env). 7 unit tests verify correct SQL
generation, parameter passing, schema creation, JSONB handling, idempotency
dedup, stats aggregation, and list_runs with run_end payload parsing. The user
verifies against a live Neon database during the deploy step.

### Frontier-audit verdict: AT BAR (Phase 6b scope)

Clean interface swap for the trace store (same pattern as CRM). SQLite default
path untouched. Deploy configs prepared with complete env var documentation.
Actual cloud deploy is the user's step, stated plainly.

**Items surfaced (not blockers):**
- Postgres backend mocked-only, not live-tested (user's deploy step).
- No connection pooling (single psycopg connection). Fine for single-worker
  Render free tier; production would use a pool.
- No migration tool (tables created on startup). Fine for this schema; production
  would use Alembic.
- render.yaml uses free plan (sleeps after 15min inactivity; first request cold-starts).

---

## Phase 6a.1: Polish the two-view UI via design skill — 2026-06-26

### Skill used
**design-taste-frontend** (taste skill). Explicitly scoped to landing/form
surfaces; dashboard portions used product-UI judgment since the skill excludes
dashboards (Section 13).

### Design system produced
- **Typography:** Geist Sans (via next/font CSS var) + Geist Mono for data/stats
- **Type scale:** text-[10px] (micro labels), text-xs (meta), text-sm (body),
  text-lg (section heads), text-2xl (page heads)
- **Spacing:** 4/8/12/16/24/32px (Tailwind default scale)
- **Radius:** rounded-lg everywhere (one shape system)
- **Accent:** Indigo-600 (primary actions), tier colors (red/amber/blue/zinc)
- **Neutrals:** Zinc family throughout (not mixed gray/slate)
- **Dials:** VARIANCE 5 / MOTION 3 / DENSITY 5

### What changed (styling + small UX only, no logic changes)
**Product form (/):**
- Indigo-600 primary button with active:scale-[0.98] tactile feedback
- Spinner loading state (animated border ring)
- Error state in a styled red-50 alert box
- Result state with human-readable headings per route ("Routed to your Account
  Executive") and "what happens next" detail line
- Tier badge with score shown inline
- Score breakdown in mono font
- Labels above inputs, consistent spacing, zinc neutrals

**Ops dashboard (/ops):**
- Loading skeletons (shimmer animation) for lead list and detail panel
- Empty states with icons and helpful text for both panels
- Live indicator (pulsing green dot) in header
- Vertical timeline for RAO trace: colored dots (indigo for in-progress,
  emerald checkmark for complete), timeline line, friendly event labels
  ("Agent reasoning", "crm_lookup", "Completed: hot")
- Collapsible payload sections (click to expand JSON)
- Detail loading state (skeleton while fetching run)
- Attribute pills (score, industry, seniority, company)
- Stats row in mono font
- Deep-link buttons to HubSpot contact search and Langfuse trace search

### What was NOT modified
Agent, tools, models, API endpoints, data fetched — all unchanged.

### Acceptance results
- `npm run build`: compiled in 2.4s, 0 errors, / and /ops static
- `python -m evals.run_eval`: 5/5, exit 0

### Browser check for the user
1. Open http://localhost:3000 — submit a lead, see the indigo button with
   spinner, then the result card with tier badge and route explanation
2. Open http://localhost:3000/ops — see skeleton loading, then the lead list
   with tier badges. Click a lead to see the timeline trace with expandable
   events, stats, and deep links to HubSpot/Langfuse.

---

## Phase 6a: Two-view app, local — 2026-06-26

### What was built
**Backend additions (no logic changes to existing endpoints):**
- CORS middleware on the FastAPI app, origin configurable via `CORS_ORIGINS` env
  (default `http://localhost:3000`).
- `GET /leads` — list of recently triaged contacts with email, name, company,
  tier, score, route, last activity. Reads SQLiteCRM.list_contacts().
- `GET /runs` — list of recent run summaries (run_id, lead_email, tier, steps,
  started_at). Reads TraceStore.list_runs().

**Frontend (web/):**
- Next.js 16 App Router, TypeScript, Tailwind. Two routes:
  - `/` — product lead-capture form. Name/email/company/message -> POST /triage ->
    confirmation with tier badge and route label.
  - `/ops` — engineer ops dashboard. Metric strip (total leads, tier counts), lead
    list (left panel), detail panel (right) showing the RAO trace, score, stats,
    and run_id. Polls GET /leads + GET /runs every 2s for live updates.
- API base URL via `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`).

### What was NOT modified
Agent, tools, models, existing endpoint logic — unchanged. The three new items
(CORS, GET /leads, GET /runs) are purely additive.

### Acceptance results (executed)
- Mock eval: 5/5, exit 0.
- API started, two leads triaged, GET /leads and GET /runs returned correct JSON
  (2 leads, 2 runs with tier/route/steps).
- `npm run build` in web/: compiled and generated static pages for / and /ops
  in 2.5s with zero errors.

### NOT executed (user's browser check)
Open http://localhost:3000, submit a lead, then open http://localhost:3000/ops
and confirm:
- The lead appears in the left panel with tier badge and score.
- Clicking it shows the RAO trace in the right panel.
- Submitting another lead from / makes it appear live on /ops within ~2s.

### Frontier-audit verdict: AT BAR (Phase 6a scope)

Two clean routes reading the existing API. Build succeeds. New list endpoints
work. CORS configured. Visual verification is the user's step.

**Items surfaced (not blockers):**
- GET /leads returns [] for HubSpot backend (list_contacts not on ABC).
- No auth, no WebSocket (2s polling), no error boundaries.
- No deep links to HubSpot/Langfuse yet (run_id shown as text).

---

## Phase 5: Langfuse observability — 2026-06-26

### What was built
- `gtm_triage/agents/langfuse_wrapper.py`: lazy-init Langfuse client, active only
  when LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY are set. Manages a trace span per
  run_id, records each chat() call as a generation (model, input, output, tokens,
  latency). Fully no-op when keys absent.
- `chat()` in llm_client.py: accepts optional `run_id` and `generation_name`
  params. When `run_id` is provided and Langfuse is active, records the call as a
  generation under the run's trace.
- `run_triage()` in loop_agent.py: initializes Langfuse trace at run start with
  lead metadata (email, provider, model), passes run_id to each chat() call as
  `decide-step-{N}`, and calls end_trace at run end with final tier/route metadata.
- `infer_enrichment()` and `infer_score_adjustment()` accept `run_id` param
  (ready for when the executor threads it through in a future phase).
- Dockerfile: added `langfuse` to pip install.
- README: Langfuse setup section (free project, 3 env vars, how to read traces).

### What was NOT modified
- SQLite trace store: unchanged, still the primary audit log.
- Agent loop logic, tools, models, API endpoints: unchanged.
- Eval/CI path: no Langfuse keys = complete no-op, zero side effects.

### Acceptance results (executed)
- Mock eval: 5/5, exit 0, with NO Langfuse keys — proving no-op path is clean.
- Unit tests: 10/10 (HubSpot tests unchanged).
- Server started, POST /triage with no Langfuse keys — no errors, clean output.
- docker compose config: not re-validated (Dockerfile change only adds a pip pkg).

### NOT executed (user runs with their Langfuse keys)
- Live Langfuse trace production. User must set LANGFUSE_PUBLIC_KEY,
  LANGFUSE_SECRET_KEY, LANGFUSE_HOST, then run a triage with GTM_PROVIDER=openai
  to see traces in the Langfuse dashboard.

### Scope of the wrap
- **Captured in Langfuse:** All 5 decide-step chat() calls per triage (the
  orchestrator reasoning calls).
- **NOT captured (still in SQLite only):** Tool-internal LLM calls
  (infer_enrichment, infer_score_adjustment) — these tools don't have run_id in
  scope. Threading it would require refactoring the executor→tool interface.

### Frontier-audit verdict: AT BAR (Phase 5 scope)

Single choke-point wrap, no-op when keys absent, SQLite untouched. Live
verification is the user's step. Tool-internal LLM calls deferred to a future
executor refactor.

**Items surfaced (not blockers):**
- Tool-internal LLM calls not in Langfuse (in SQLite only).
- No cost_details (Langfuse may auto-compute from model name).
- No prompt management via Langfuse.

---

## Phase 4 live verification — 2026-06-26

### What was verified (live, against real HubSpot account)
- Custom properties created: gtm_tier, gtm_score, gtm_route, gtm_industry,
  gtm_seniority, gtm_activity_log — all 6 CREATED.
- Smoke test (scripts/hubspot_smoke.py): create, lookup, upsert, activity,
  dedup — all passed. Dedup correctly returned "already_recorded" on second call.
- Full agent triage (CRM_BACKEND=hubspot, GTM_PROVIDER=mock):
  - POST /triage → hot, 85 pts, industry=financial_services, seniority=c_level.
  - POST /deliver → activity recorded.
  - GET /contacts → HubSpot contact confirmed with:
    hubspot_id=509214136020, tier=hot, score=85, route=ae_immediate,
    industry=financial_services, seniority=c_level,
    activity=[ec24f200] notified AE for immediate follow-up.

### Bugs found and fixed during live run
1. **`.example` TLD rejected** — HubSpot validates email domains; `.example` is
   RFC-reserved and rejected as INVALID_EMAIL (400). Fix: changed smoke test
   email to `@aether-gtm-demo.com`.
2. **Silent write failures** — `upsert()` and `add_activity()` didn't check
   response status; API errors were swallowed. Fix: added `resp.raise_for_status()`
   on all write calls in hubspot_crm.py.
3. **Score/industry/seniority not passed to CRM** — API `upsert` call only sent
   email/name/company/tier/route. Fix: added score from `result.score`, industry
   and seniority from `result.enrichment` in api.py.

### Test contacts left in HubSpot (obviously fake, for user to inspect)
- `gtm-smoke-test@aether-gtm-demo.com` (smoke test)
- `test.vp@acme-gtm-demo.com` (first triage run)
- `test.cto@acme-gtm-demo.com` (second triage run — fully enriched)

Delete later if wanted:
```bash
HUBSPOT_TOKEN=pat-xxx python -c "
import httpx,os; c=httpx.Client(base_url='https://api.hubapi.com',headers={'Authorization':f'Bearer {os.environ[\"HUBSPOT_TOKEN\"]}'}); [c.delete(f'/crm/v3/objects/contacts/{cid}') for cid in ['509381249757','509214136020']]
"
```

### Confirmation
- SQLite path: eval 5/5, exit 0. Unit tests: 10/10.
- No changes to agents/, tools/, or models/.

### Frontier-audit verdict: AT BAR (Phase 4 live-verified)

Three bugs found and fixed. All HubSpot fields confirmed populated by the agent.

---

## Phase 4: Swap SQLite CRM for HubSpot (behind same interface) — 2026-06-26

### What was built
- `gtm_triage/crm/hubspot_crm.py`: HubSpotCRM implementing CRMStore against the
  HubSpot v3 REST API via httpx. lookup via /crm/v3/objects/contacts/search,
  upsert via create/PATCH, activities stored as lines in a custom multiline-text
  `gtm_activity_log` property (free accounts lack Notes scope). Dedup on
  (run_id + action) by checking if the line already exists before appending.
- `CRMStore` ABC updated: `add_activity` returns `dict | None` (for dedup),
  `close()` method added with default no-op.
- API lifespan switch: `CRM_BACKEND=hubspot` constructs HubSpotCRM with
  `HUBSPOT_TOKEN`; default `sqlite` unchanged.
- `tests/test_hubspot_crm.py`: 10 unit tests against mocked httpx client.
- `scripts/hubspot_smoke.py`: live smoke test (--setup creates custom properties,
  default run exercises create/lookup/upsert/activity/dedup).
- Dockerfile updated: added httpx to pip install.
- README updated: Phase 4 header, HubSpot setup section, env vars, honesty table.

### What was NOT modified
Agent, tools, models, and API endpoint logic were NOT changed. Only the CRM
construction at startup switches between backends (api.py lines 102-111). This
proves the clean-interface design: the CRMStore ABC is the only contract the
rest of the system depends on.

### Acceptance results (executed)
- Mock eval: 5/5, exit 0 (SQLite default path unchanged).
- Unit tests: 10/10 passed (0.22s) — lookup (found/not-found/error), upsert
  (update/create), add_activity (new/dedup/no-contact), get_activities
  (parse log/empty log).
- docker compose config: valid.
- n8n workflow JSON: valid.

### NOT executed (user runs with their token)
- `scripts/hubspot_smoke.py --setup` (creates custom properties).
- `scripts/hubspot_smoke.py` (live round-trip: create/lookup/upsert/activity).
- API with `CRM_BACKEND=hubspot` against a real HubSpot account.

### HubSpot field mapping
| Our field | HubSpot property | Type |
|-----------|-----------------|------|
| email | email | built-in |
| name | firstname / lastname | built-in |
| company | company | built-in |
| tier | gtm_tier | custom |
| score | gtm_score | custom |
| route | gtm_route | custom |
| industry | gtm_industry | custom |
| seniority | gtm_seniority | custom |
| activities | gtm_activity_log | custom (multiline text) |

### Required HubSpot scopes
- `crm.objects.contacts.read`
- `crm.objects.contacts.write`
- `crm.schemas.contacts.write`

### Frontier-audit verdict: AT BAR (Phase 4 scope)

Clean interface swap with zero changes to agent/tools/API logic. 10 unit tests
pass against mocked httpx. Eval 5/5 on SQLite default path. HubSpot live
verification deferred to user via smoke script, stated honestly.

**Items surfaced (not blockers):**
- Activities stored as text property, not Notes (free-tier scope limitation).
- No retry on HubSpot API errors. No rate-limit handling.
- Eventual-consistency race on search-after-create (graceful: treats as new).
- No live test executed (no token — user runs smoke script).

**Not built (deliberately, scope-locked):** Notes/Engagements API (needs paid
scopes), API retry/rate-limit, multi-CRM abstraction, warehouse sync.

---

## Phase 3.1: Make the pipeline idempotent — 2026-06-26

### What was built
- POST /triage accepts an optional `idempotency_key`. If omitted, one is derived
  from sha256(email + message + source). A repeat key returns the prior TriageResult
  without re-running the agent or writing new trace events.
- POST /deliver deduplicates on (run_id + action): if that exact delivery activity
  already exists, returns `status: "already_recorded"` without creating a duplicate.
- `idempotency_keys` table added to the trace SQLite (key → run_id + cached result).
- n8n workflow updated: triage HTTP node passes `idempotency_key: $execution.id`.

### Acceptance results (executed)
- Mock eval: 5/5, exit 0 (unchanged).
- POST /triage TWICE with same `idempotency_key="test-idem-123"`:
  - Both returned run_id `f30567ce-7e13-48a6-93ae-6f781a3d73f6` (same).
  - GET /runs/{run_id} → 15 events (not doubled).
  - GET /contacts/{email} → 1 contact record.
- POST /triage TWICE with NO key (auto-derived from email+message+source):
  - Both returned run_id `54d200c5-8c78-44ba-b691-fd5412f43d10` (same).
- POST /deliver TWICE with same run_id + action:
  - First: `status: "recorded"`. Second: `status: "already_recorded"`.
  - GET /contacts/{email} → 1 delivery activity (not 2).
- n8n workflow JSON: valid (parses).
- docker compose config: valid.

### Frontier-audit verdict: AT BAR (Phase 3.1 scope)

Application-level idempotency key checked in SQLite before running the agent.
Both explicit and auto-derived keys work. Deliver dedup prevents duplicate CRM
activities. All existing tests pass.

**Items surfaced (not blockers for this scope):**
- No key expiry (table grows forever — irrelevant at demo scale).
- Deliver dedup is a linear scan of activities per contact (fine at low volume).
- No pytest coverage for idempotency (curl-tested only).
- Concurrent race untested (SQLite serializes writes, so safe here).

**Not built (deliberately, scope-locked):** key TTL, concurrent stress test,
pytest for idempotency. Auth, queue, HubSpot remain later phases.

---

## Phase 3: Orchestrate with n8n, close the loop — 2026-06-26

### What was built
- POST /deliver endpoint: records a routing outcome as a CRM activity on the
  contact (e.g. "routed hot -> notified AE for immediate follow-up").
- GET /contacts/{email} endpoint: CRM record + activity timeline for a contact.
- n8n workflow JSON (n8n/lead_triage_workflow.json): Webhook -> HTTP triage ->
  Switch on tier -> per-branch HTTP deliver -> Webhook response.
- docker-compose.yml: triage API + n8n, shared network, one-command bring-up.
- Dockerfile: minimal Python 3.11 image with FastAPI/uvicorn.
- scripts/simulate_inbound.py: POSTs a lead to n8n webhook; falls back to
  direct API calls if n8n is unreachable.

### What was executed vs what the user runs manually
**Executed (actual output verified):**
- Mock eval: 5/5, exit 0.
- Server started, POST /triage → hot/ae_immediate (80 pts).
- POST /deliver → activity recorded in CRM.
- GET /contacts/{email} → CRM record + 1 delivery activity confirmed.
- GET /runs/{run_id} → 15 trace events.
- simulate_inbound.py → full loop closed (direct API fallback).
- Two leads triaged, both persisted to file-backed SQLite.
- n8n workflow JSON: valid (parses).

**NOT executed (user runs manually):**
- docker compose up (Docker not available in build environment).
- Live n8n workflow execution (requires Docker + n8n running).
- Importing workflow JSON into n8n UI.

### Frontier-audit verdict (re-run with Docker validation): AT BAR (Phase 3 scope, with one conditional)

**Executed (actual output verified, this run):**
- Mock eval: 5/5, exit 0.
- Server started, POST /triage → hot/ae_immediate (80 pts), run_id 84a8bc7e.
- POST /deliver → "routed hot -> notified AE for immediate follow-up".
- GET /contacts/vp@acmefintech.com → CRM record + 1 delivery activity confirmed.
- GET /runs/84a8bc7e → 15 trace events.
- simulate_inbound.py → full loop closed (direct API fallback, n8n not running).
- n8n workflow JSON: valid (parses).
- `docker compose config`: valid (via WSL/Ubuntu + Docker Desktop integration).
- Healthcheck uses Python urllib (no curl in image) — confirmed correct.
- Dockerfile includes `openai` — confirmed correct.

**NOT executed (user runs manually):**
- `docker compose up --build` (Docker available via WSL but compose build not run).
- Live n8n workflow execution (requires importing JSON into running n8n).

**Items surfaced:**
- n8n workflow not live-tested (the conditional — cheapest experiment: import and
  fire one lead, 5 minutes).
- No idempotency key (duplicate submissions create duplicate runs — below median,
  a 3-line fix).
- No error branch in n8n flow (null tier from failed triage drops silently).
- No webhook auth on either n8n or triage API.
- Delivery action is a CRM log line, not a real notification.

**Not built (deliberately, scope-locked):** webhook auth, idempotency, retry/DLQ,
real delivery actions (Slack/email), async triage, monitoring. Later phases.

**Bar confidence: thin.** Published-practice for the stack shape, internal-only
for the accuracy numbers. No public lead-qualification benchmark.

---

## Phase 2: Give the agent a callable body (FastAPI) — 2026-06-26

### What was built
FastAPI service (`gtm_triage/api.py`) with three endpoints:
- `POST /triage` — accepts a Lead JSON, runs the full agent loop, returns
  TriageResult with tier/route/score/enrichment/draft/run_id.
- `GET /health` — liveness check.
- `GET /runs/{run_id}` — trace rows for a given run from the trace store.

CRM, trace store, tool registry, and executor constructed ONCE at startup via
FastAPI lifespan. File-backed SQLite for both CRM (`gtm_crm.db`) and trace
(`gtm_trace.db`), configurable via env vars. Provider swappable: default mock
(no API key), openai via `GTM_PROVIDER=openai`.

### Acceptance results (executed)
- Server started: `uvicorn gtm_triage.api:app` on port 8000.
- `curl /health` → `{"status":"ok"}`.
- `POST /triage` with hot lead → tier=hot, route=ae_immediate, 80 pts, 5 steps.
- `POST /triage` with junk lead → tier=disqualified, route=drop, 0 pts, 4 steps
  (draft_outreach correctly skipped).
- `GET /runs/{run_id}` → 15 and 12 trace events respectively.
- File-backed DBs persisted: `gtm_crm.db` (12KB) and `gtm_trace.db` (28KB)
  on disk after two leads. Both runs readable via the API.
- Mock eval: 5/5, exit 0 (unchanged).

### Frontier-audit verdict: AT BAR (for Phase 2 scope) — INDUSTRY overall

Phase 2 delivered the callable HTTP body with persistent memory. The decision
brain is unchanged from Phase 1.6 (90% held-out, 95.5% seen). New axis:
deployability moved from "not callable" to "callable via HTTP with persistence."

**Items surfaced (not blockers):**
- Sync blocking: `/triage` blocks ~11s per lead (openai). One request at a time.
- No error contract: failed triage returns 200 with null tier, not an error.
- SQLite breaks at `--workers 2` (no WAL mode, concurrent writes).
- No `.gitignore` for DB files.

**Not built (deliberately):** auth, rate limiting, async workers, Docker,
managed DB. Those are Phase 3+ concerns.

---

## Phase 1.6: Fix the 4 real rule gaps — 2026-06-26

### What changed from Phase 1.5
Four deterministic rules added to score_lead.py to fix the 4 disagreements from
the Phase 1.5 eval:
1. Opt-out hard disqualifier — "remove me", "unsubscribe", etc. force
   tier=disqualified regardless of profile score.
2. Spam intent suppression — 2+ outbound-spam phrases zero the intent bonus;
   spam + free email = hard disqualify.
3. Existing-customer boost (+15) — CRM-flagged customers get a point bonus;
   "upgrade"/"renew" added to high-intent keywords.
4. Title-inflation discount (-10) — vp/c_level at smb companies get seniority
   points reduced.

Added a 10-lead held-out validation set (evals/holdout.py), written AFTER the
rules were finalized, to prevent overfitting to the original 22.

### Eval results (executed)
- Mock eval: 5/5 (CI gate unchanged).
- OpenAI eval on original 22 (seen data): 95.5% tier accuracy (21/22), up from
  81.8%. All 4 previously-broken leads now correct. 1 remaining disagreement:
  #20 (review=true, warm vs cold judgment call on VP at tiny startup).
- OpenAI eval on 10 held-out leads (unseen data): 90.0% tier accuracy (9/10).
  1 disagreement: spam from a business email scored cold instead of disqualified.
  New gap: spam hard-disqualify only fires for free email, not business email.
- Latency: ~11.6s median per lead (unchanged). Cost: $0.001/lead (unchanged).

### Before/after for the 4 fixed leads
- #13 promo_king: cold(30pts) → disqualified(0pts). Spam + free email.
- #18 carlos.r: warm(65pts) → hot(95pts). "upgrade" as high-intent + customer boost.
- #19 maria.g: warm(50pts) → disqualified(0pts). Opt-out hard disqualifier.
- #20 founder: hot(70pts) → warm(60pts). LLM enrichment shifted score down;
  title discount not triggered (regex didn't match smb for "TinyStartup").

### Frontier-audit verdict: AT BAR (conditionally) — INDUSTRY overall

**FRONTIER.md axis status:**
- Qualification accuracy: AT FRONTIER conditionally (90% on held-out hits the
  target, but n=10 is thin — 95% CI is [56%, 100%]).
- Routing correctness: AT FRONTIER conditionally (same as tier accuracy).
- Data hygiene: INDUSTRY (source-tracked, confidence-scored, no abstention).
- Decision latency: BELOW INDUSTRY (11.6s, target is "few seconds").
- Cost per lead: AT FRONTIER ($0.001/lead).

**Conditions preventing clean AT BAR:**
1. n=10 held-out is too small for statistical confidence. Need n=30+.
2. Labels are builder-assigned, not independently verified.
3. One holdout gap found and deliberately not fixed (biz-email spam).
4. Latency still 4x the target.

**Bar confidence: thin.** No public benchmark. Internal-only numbers.

### Cheapest next experiments
1. Expand held-out to n=30+. (1 hour)
2. Independent labeler for the same 32 leads. (30 min of someone else's time)
3. Fix biz-email spam gap (one if clause). (5 minutes)
4. Parallel tool-internal LLM calls for latency. (30 minutes)

---

## Phase 1.5: De-circularize the eval, make the LLM earn its place — 2026-06-26

### What changed from Phase 1
- Golden set grown from 5 rule-derived leads to 22 human-judgment-labeled leads,
  including hard cases: conflicting signals (CEO + free email), boundary scores,
  vague one-word messages, prompt injection, existing-customer CRM hit, opt-out,
  ambiguous seniority, foreign language. 5 leads marked review=true.
- LLM given two real jobs (openai provider only):
  1. enrich_lead: fills "unknown" fields when regex fails. Source tracked per field.
  2. score_lead: proposes bounded llm_adjustment in [-10,+10] with a one-line reason.
- Conditional flow: skip enrichment if CRM has complete profile, skip draft_outreach
  for cold/disqualified leads.
- Two eval modes: mock (5-lead CI gate) and openai (22-lead agreement measurement).
- Trace store records tokens and duration for latency + cost measurement.

### Eval results (executed)
- Mock eval: 5/5 (deterministic CI gate passes).
- OpenAI eval (gpt-4o-mini, 22 leads):
  - Tier accuracy: 18/22 (81.8%)
  - Route accuracy: 18/22 (81.8%)
  - Median latency: 11,622 ms per lead
  - Cost: $0.001 per lead ($0.021 total)
- LLM influenced 20/22 cases with score adjustments, and filled unknown enrichment
  fields in 10+ cases. French-language lead enriched entirely by LLM.
- 4 disagreements, all revealing real rules gaps:
  1. Spam ("buy cheap SEO") scored cold — "buy" triggered high_intent.
  2. Existing customer upgrade scored warm — "upgrade" not a high-intent keyword.
  3. Opt-out ("remove me from mailing list") scored warm — LLM gave -10 but
     profile scored 60; no hard disqualifier mechanism.
  4. VP at 3-person startup scored hot (review=true) — LLM+5 pushed past threshold.

### Frontier-audit verdict: INDUSTRY (up from MEDIAN)

**What improved from Phase 1:**
- Eval is no longer circular: labels are judgment-first, disagreements documented.
- LLM has a real job: enrichment fallback for unknowns, score adjustments, correctly
  identifying spam/bounce/injection/opt-out signals with negative adjustments.
- 81.8% crosses the FRONTIER.md industry bar (80%) but misses frontier (90%+).
- All five FRONTIER.md axes now measured.

**FRONTIER.md axis status:**
- Qualification accuracy: INDUSTRY (81.8% on 22 builder-labeled leads; industry=80%).
- Routing correctness: INDUSTRY (81.8%; deterministic from tier; free-email cap).
- Data hygiene: INDUSTRY (source-tracked regex+llm, confidence-scored; no waterfall).
- Decision latency: BELOW INDUSTRY (11.6s; target is "few seconds").
- Cost per lead: AT FRONTIER ($0.001/lead; bounded and traced).

**Not yet AT BAR because:**
1. Qualification 81.8% < 90% frontier target. Two high-consequence failures
   (opt-out scored warm, spam scored cold) need hard disqualifiers.
2. Labels are builder-assigned, not independently verified by a GTM practitioner.
3. LLM adjustment is 90% positive (+5 bias) — not calibrated.
4. Latency 11.6s exceeds "few seconds" target (serial LLM calls).
5. No abstention on low-confidence leads.

**Bar confidence: thin.** No public benchmark. Internal-only numbers. The 81.8%
is measured against builder-assigned labels.

### Cheapest next experiments (ordered by information per dollar)
1. Add hard disqualifier rules for opt-out/spam/bounce → likely 90.9%. (30 min)
2. Independent labeling by a GTM practitioner. (30 min of their time)
3. Investigate +5 bias in LLM adjustments; add negative-signal examples to prompt.
4. Parallel tool-internal LLM calls to cut latency below 5s.

---

## Phase 1: Local lead-triage motion — 2026-06-26

### What was built
GTM lead-triage agent in `gtm-lead-triage/` as a sibling to `aether/`. RAO loop
agent with four tools (crm_lookup, enrich_lead, score_lead, draft_outreach),
SQLiteCRM behind a CRMStore ABC, SQLite trace store, mock + openai provider shim,
MCP server wrapper, and a 5-lead eval harness. All outputs are Pydantic v2 models.
No LangChain. No modifications to the existing aether/ package.

### Eval result
5/5 on the golden set (provider=mock). All four tiers covered: hot (80 pts),
warm (58 pts, 55 pts), cold (30 pts), disqualified (0 pts). Verified: with
llm_adjustment=0, all 5 leads still pass. The LLM nudge (+/-10) cannot flip any
tier.

### Frontier-audit verdict: MEDIAN

**Approach landscape:** Four families — static CRM rules (median), no-code AI
node (industry-common), custom evaluated agent with RAO + trace + eval (frontier
for solo builder), warehouse-native AI decisioning (commercial frontier). This
build claims family 3 but the eval substance is family 1: the scoring is pure
deterministic rules, the LLM nudge is vestigial (always 0), and the labels were
derived from the rules.

**What a top-1% practitioner would say is wrong:**
1. The eval is circular — labels designed to fit the rules, not human-labeled.
2. The LLM has zero influence on any outcome (nudge hardcoded to 0).
3. No adversarial or edge-case leads.
4. Enrichment adds no net-new signal (keyword regex on the lead's own fields).
5. No retry / JSON repair in the agent loop.

**FRONTIER.md axis status:**
- Qualification accuracy: NOT MET (5 rule-derived leads, not 20+ human-labeled).
- Routing correctness: MET at industry level (deterministic, free-email cap).
- Data hygiene: PARTIAL (confidence present, no multi-source waterfall).
- Decision latency: NOT MEASURED.
- Cost per lead: NOT MEASURED.

**Bar confidence: thin.** No public benchmark. Internal-only numbers.

### Cheapest next experiments (ordered by information per dollar)
1. Human-label 20 leads before seeing the rules. Run eval against them. (30 min)
2. Run eval with provider=openai on the same 5 leads. (< $0.10)
3. Add 3-5 adversarial leads (conflicting signals, boundary scores, injection). (15 min)
4. Measure latency + cost from trace store on an openai run. (5 lines of code)

### What's stubbed and why
- Mock provider: deterministic CI. Real test = provider=openai.
- Enrichment: keyword inference. Production = Clay API.
- CRM: in-memory SQLite. Phase 4 = HubSpot behind same CRMStore ABC.
- Outreach: template drafts. Never sends.

---

## Earlier decisions this session (2026-06-26)

### FRONTIER.md set for the GTM extension
Axes: qualification accuracy, routing correctness, data hygiene, latency per lead,
cost per lead, each with median/industry/frontier tiers. Honest ceiling:
published-practice for the stack shape, internal-only for the accuracy numbers; no
public lead-qualification benchmark exists. Why: build against a measured bar, not
vibes. Precludes: passing an internal bar off as a proprietary frontier.

### Pivot to the GTM extension; target Associate GTM Engineer
Extend Aether, do not replace it. `RESTRUCTURE.md` remade from the Forward-Deployed-
Engineer framing to GTM. Aether is positioned as the AI decision brain inside the
warehouse-native GTM stack: buy orchestration (n8n), CRM (HubSpot), enrichment
(Clay), reverse ETL (Hightouch), engagement (Apollo); build the brain. No-LangChain
rule kept. Finance preserved as a proven domain. Why: the role screens for
agent-native design and eval-driven development, which Aether already demonstrates,
and the decision brain is the least-commoditized layer to build. Supersedes: the
earlier FDE `RESTRUCTURE.md` (recoverable in git history).
