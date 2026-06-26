# DEPLOY.md - Aether GTM Lead-Triage Deployment Guide

Three services: Neon (Postgres), Render (API), Vercel (frontend).

## Architecture

```
Browser (Vercel)             API (Render)                   Data
  localhost:3000        ->     POST /triage           ->   Neon Postgres (traces)
  or your-app.vercel.app      GET  /leads, /runs          HubSpot (CRM)
                               GET  /runs/{id}             Langfuse (observability)
```

## 1. Neon (Postgres for trace store)

1. Go to [neon.tech](https://neon.tech) and create a free project.
2. Create a database (default name `neondb` is fine).
3. Copy the **connection string** from the dashboard:
   ```
   postgresql://user:password@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
4. Save this as `DATABASE_URL` for the Render step below.

The API creates its tables (`trace_events`, `idempotency_keys`) on first
startup. No manual migration needed.

## 2. Render (API)

1. Go to [render.com](https://render.com) > New > Web Service.
2. Connect your GitHub repo. Set:
   - **Root directory:** `gtm-lead-triage`
   - **Runtime:** Docker
   - **Dockerfile path:** `./Dockerfile`
   - **Plan:** Free (or Starter for always-on)
3. Set environment variables:

| Variable | Value | Required |
|----------|-------|----------|
| `DATABASE_URL` | Neon connection string from step 1 | Yes |
| `GTM_PROVIDER` | `mock` (default, free) or `openai` (real LLM, costs tokens) | Yes |
| `GTM_MODEL` | `gpt-4o-mini` (only when GTM_PROVIDER=openai) | No |
| `CRM_BACKEND` | `hubspot` (recommended) or `sqlite` | Yes |
| `HUBSPOT_TOKEN` | Your HubSpot Private App token | When CRM_BACKEND=hubspot |
| `OPENAI_API_KEY` | Your OpenAI API key | When GTM_PROVIDER=openai |
| `FRONTEND_ORIGIN` | Your Vercel URL, e.g. `https://your-app.vercel.app` | Yes |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key | No |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key | No |
| `LANGFUSE_BASE_URL` | `https://us.cloud.langfuse.com` (or your host) | No |

4. Deploy. The health check is `GET /health`.
5. Copy the Render URL (e.g. `https://gtm-triage-api.onrender.com`).

## 3. Vercel (Frontend)

1. Go to [vercel.com](https://vercel.com) > Add New > Project.
2. Import your GitHub repo. Set:
   - **Root directory:** `gtm-lead-triage/web`
   - **Framework preset:** Next.js (auto-detected)
   - **Build command:** `npm run build`
   - **Output directory:** (leave default)
3. Set environment variable:

| Variable | Value | Required |
|----------|-------|----------|
| `NEXT_PUBLIC_API_URL` | Your Render URL from step 2, e.g. `https://gtm-triage-api.onrender.com` | Yes |

4. Deploy.

## After deploy: update FRONTEND_ORIGIN on Render

Once you have the Vercel URL, go back to Render and set `FRONTEND_ORIGIN` to
your Vercel URL (e.g. `https://your-app.vercel.app`). This enables CORS from
the deployed frontend.

If you need both local and deployed access:
```
FRONTEND_ORIGIN=https://your-app.vercel.app,http://localhost:3000
```

## Trade-offs for the public form

| GTM_PROVIDER | Behavior | Cost | Speed |
|-------------|----------|------|-------|
| `mock` (default) | Deterministic rules, no LLM | Free | Instant |
| `openai` | Real LLM reasoning, enrichment, score adjustment | ~$0.001/lead | ~11s/lead |

**Recommendation:** Start with `mock`. It scores and routes correctly based on
rules. Flip to `openai` when you want LLM enrichment of unknown fields and
the score nudge. HubSpot writes are real in both modes.

## Local development (unchanged)

```bash
# Terminal 1: API (SQLite, no DATABASE_URL)
cd gtm-lead-triage
python -m uvicorn gtm_triage.api:app --host 127.0.0.1 --port 8000

# Terminal 2: Frontend
cd gtm-lead-triage/web
npm run dev
```

No DATABASE_URL = SQLite trace store. No HUBSPOT_TOKEN = SQLite CRM. The
local path is unchanged and requires no cloud accounts.

## Complete env var reference

| Variable | Where | Default | Purpose |
|----------|-------|---------|---------|
| `DATABASE_URL` | Render | (none, SQLite) | Postgres DSN for trace store |
| `GTM_PROVIDER` | Render | `mock` | LLM provider |
| `GTM_MODEL` | Render | `gpt-4o-mini` | Model name (openai only) |
| `CRM_BACKEND` | Render | `sqlite` | CRM backend |
| `GTM_CRM_DB` | Local | `gtm_crm.db` | SQLite CRM path (local only) |
| `GTM_TRACE_DB` | Local | `gtm_trace.db` | SQLite trace path (local only) |
| `HUBSPOT_TOKEN` | Render | (none) | HubSpot Private App token |
| `OPENAI_API_KEY` | Render | (none) | OpenAI API key |
| `FRONTEND_ORIGIN` | Render | `http://localhost:3000` | Allowed CORS origins |
| `LANGFUSE_PUBLIC_KEY` | Render | (none) | Langfuse public key |
| `LANGFUSE_SECRET_KEY` | Render | (none) | Langfuse secret key |
| `LANGFUSE_BASE_URL` | Render | (none) | Langfuse host URL |
| `NEXT_PUBLIC_API_URL` | Vercel | `http://localhost:8000` | API base URL for frontend |
