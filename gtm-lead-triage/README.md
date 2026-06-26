# GTM Lead-Triage Agent — Aether Phase 6b

A reason-act-observe (RAO) loop agent that triages inbound leads, with a
product form + live ops dashboard (Next.js), orchestration (n8n), idempotency,
swappable CRM (SQLite/HubSpot), Postgres trace store, Langfuse observability,
and deploy configs for Neon + Render + Vercel. See [DEPLOY.md](DEPLOY.md).

## The stack (Phase 6a)

```
Product form (localhost:3000)  ─── POST /triage ───>  FastAPI (localhost:8000)
Ops dashboard (localhost:3000/ops)  ── GET /leads, /runs, /runs/{id} ──>  │
                                                                          │
                          Agent (CRM lookup -> enrich -> score -> draft)   │
                          -> CRM (SQLite or HubSpot) + SQLite trace       │
                          -> Langfuse (optional)                          │
```

## Running locally (API + frontend)

```bash
cd gtm-lead-triage

# Terminal 1: start the API
python -m uvicorn gtm_triage.api:app --host 127.0.0.1 --port 8000

# Terminal 2: start the frontend
cd web && npm run dev
# Open http://localhost:3000       (lead form)
# Open http://localhost:3000/ops   (ops dashboard)
```

The frontend reads `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`).
The API allows CORS from `CORS_ORIGINS` (default `http://localhost:3000`).

### Other run options

```bash
# HubSpot CRM
CRM_BACKEND=hubspot HUBSPOT_TOKEN=pat-xxx python -m uvicorn gtm_triage.api:app

# Docker Compose (API + n8n, no frontend)
docker compose up --build
```

Environment variables:
- `GTM_PROVIDER` — `mock` (default) or `openai`
- `GTM_MODEL` — model name (default `gpt-4o-mini`)
- `CRM_BACKEND` — `sqlite` (default) or `hubspot`
- `GTM_CRM_DB` / `GTM_TRACE_DB` — SQLite paths (default `gtm_crm.db` / `gtm_trace.db`)
- `OPENAI_API_KEY` — required when `GTM_PROVIDER=openai`
- `HUBSPOT_TOKEN` — required when `CRM_BACKEND=hubspot`
- `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` — optional; enables Langfuse tracing
- `CORS_ORIGINS` — comma-separated allowed origins (default `http://localhost:3000`)
- `NEXT_PUBLIC_API_URL` — frontend env: API base URL (default `http://localhost:8000`)

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/triage` | Triage a lead. Body: Lead JSON. Returns TriageResult. |
| POST | `/deliver` | Record routing outcome as CRM activity. |
| GET | `/health` | Liveness check. |
| GET | `/runs/{run_id}` | Trace rows for a given run. |
| GET | `/contacts/{email}` | CRM record + activity timeline for a contact. |

## API examples

```bash
# Health check
curl http://localhost:8000/health

# Triage a lead
curl -X POST http://localhost:8000/triage \
  -H "Content-Type: application/json" \
  -d '{
    "email": "vp@acmefintech.com",
    "name": "Julia Martinez, VP of Sales",
    "company": "Acme Fintech International",
    "message": "Schedule a demo. Urgent."
  }'
# -> {"run_id":"...","final_tier":"hot","final_route":"ae_immediate",...}

# Record the delivery (what n8n does after triage)
curl -X POST http://localhost:8000/deliver \
  -H "Content-Type: application/json" \
  -d '{
    "email": "vp@acmefintech.com",
    "run_id": "<run_id from triage>",
    "tier": "hot",
    "route": "ae_immediate"
  }'
# -> {"status":"recorded","activity_recorded":"routed hot -> notified AE..."}

# Read back a contact's CRM record + activity timeline
curl http://localhost:8000/contacts/vp@acmefintech.com

# Read back a run's trace
curl http://localhost:8000/runs/<run_id>
```

## End-to-end simulation

```bash
# Simulates an inbound lead through the full stack.
# Tries n8n webhook first; falls back to direct API calls if n8n is down.
python -m scripts.simulate_inbound
```

## n8n workflow

The workflow (`n8n/lead_triage_workflow.json`) implements:
1. **Webhook** — receives POST /inbound with lead JSON.
2. **HTTP Request** — calls POST /triage on the triage API.
3. **Switch** — routes by `final_tier` (hot / warm / cold / disqualified).
4. **Deliver** — per-branch POST /deliver with the appropriate action.

Import it into n8n after starting `docker compose up`.

## HubSpot CRM setup (Phase 4)

To use HubSpot as the CRM backend:

1. **Create a Private App** in HubSpot (Settings > Integrations > Private Apps).
   Grant these scopes:
   - `crm.objects.contacts.read`
   - `crm.objects.contacts.write`
   - `crm.schemas.contacts.write`

2. **Create custom contact properties** (one-time):
   ```bash
   HUBSPOT_TOKEN=pat-xxx python -m scripts.hubspot_smoke --setup
   ```
   This creates: `gtm_tier`, `gtm_score`, `gtm_route`, `gtm_industry`,
   `gtm_seniority`, `gtm_activity_log` (multiline text).

3. **Run the smoke test** to verify:
   ```bash
   HUBSPOT_TOKEN=pat-xxx python -m scripts.hubspot_smoke
   ```

4. **Start the API with HubSpot**:
   ```bash
   CRM_BACKEND=hubspot HUBSPOT_TOKEN=pat-xxx python -m uvicorn gtm_triage.api:app
   ```

The agent, tools, and API endpoints are unchanged — only the CRM construction
at startup switches between `SQLiteCRM` and `HubSpotCRM` based on `CRM_BACKEND`.

## Langfuse observability (Phase 5)

Every LLM call flows through a single `chat()` choke point. When Langfuse keys
are set, each call is recorded as a **generation** (model, input, output, tokens,
latency), grouped under one **trace per lead** keyed by `run_id`.

**Setup:**

1. Create a free Langfuse project at [cloud.langfuse.com](https://cloud.langfuse.com).
2. Get your Public Key, Secret Key, and Host URL from Settings > API Keys.
3. Set the env vars:
   ```bash
   export LANGFUSE_PUBLIC_KEY=pk-lf-...
   export LANGFUSE_SECRET_KEY=sk-lf-...
   export LANGFUSE_HOST=https://cloud.langfuse.com   # or your self-hosted URL
   ```
4. Run a triage with `GTM_PROVIDER=openai` — each decide step appears as a
   generation in the Langfuse dashboard, grouped under the run's trace.

**No keys = no-op.** Without the env vars, Langfuse is completely inactive. The
eval/CI path (`provider=mock`, no keys) is unchanged. The SQLite trace store
remains and is not replaced.

**Reading a trace:** In the Langfuse dashboard, filter by the `run_id` (shown in
metadata) or search by `lead_email`. Each trace shows the full triage with nested
generations: decide-step-0 through decide-step-N.

## Eval commands (unchanged)

```bash
python -m evals.run_eval                        # Mock CI gate (5/5)
python -m evals.run_eval_openai                 # 22 golden leads
python -m evals.run_eval_openai --holdout       # 10 held-out leads
python -m scripts.demo_one_lead                 # Single-lead trace demo
```

## Measured results (from Phase 1.6)

- **Original 22 (seen): 95.5% tier accuracy**
- **Held-out 10 (unseen): 90.0% tier accuracy**
- **Latency: ~11.6s/lead (openai), ~0s (mock)**
- **Cost: ~$0.001/lead**

## What's stubbed

- **n8n**: workflow JSON provided, not live-tested in CI (requires Docker).
- **Mock provider**: default, no API key. Real test is provider=openai.
- **Enrichment**: regex + LLM. Production → Clay API.
- **CRM**: SQLite (default) or HubSpot (Phase 4). HubSpot is unit-tested against
  mocked httpx; live verification via `scripts/hubspot_smoke.py` with your token.
- **Outreach**: template drafts. Never sends.
- **No auth, rate limiting, queue, or managed DB** — later phases.

## What is structurally tested vs live-run by user

| Component | Structurally tested | Live-run by user |
|-----------|-------------------|------------------|
| SQLite CRM | Eval + curl tests (executed) | — |
| HubSpot CRM | 10 unit tests against mocked httpx (executed) | `scripts/hubspot_smoke.py` with token |
| n8n workflow | JSON parses, compose config valid | Import + fire via Docker |
| Idempotency | curl double-POST tests (executed) | — |
