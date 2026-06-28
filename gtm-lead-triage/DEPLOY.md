# DEPLOY.md — GTM Lead-Triage Deployment Guide

## Architecture

```
Browser (Vercel)              API (Render free-tier)          Data
  your-app.vercel.app   ->     POST /triage            ->   SQLite (traces, CRM)
                               GET  /leads, /runs           or Neon Postgres
                               GET  /config                 Langfuse (optional)
                               X-API-Key: demo-xxx          PDL (enrichment)
```

Auth is REQUIRED in production. The frontend sends a rate-limited demo key
on every request. This is acceptable — the API triages fake leads only, never
sends real email, and is rate-limited to 60 RPM.

---

## Render (Backend API)

### Environment variables

| Variable | Value | Type | Required |
|----------|-------|------|----------|
| `APP_ENV` | `production` | Plain | Yes |
| `GTM_PROVIDER` | `openai` | Plain | Yes |
| `GTM_MODEL` | `gpt-4o-mini` | Plain | No (default) |
| `GTM_API_KEYS` | `demo-<random-32-chars>` | **Secret** | Yes |
| `OPENAI_API_KEY` | `sk-proj-...` | **Secret** | Yes |
| `ENRICHMENT_PROVIDER` | `pdl` | Plain | No (default: mock) |
| `PDL_API_KEY` | `(your PDL dev key)` | **Secret** | When ENRICHMENT_PROVIDER=pdl |
| `CRM_BACKEND` | `sqlite` | Plain | No (default) |
| `DATABASE_URL` | _(Neon connection string, or leave unset for SQLite)_ | **Secret** | No |
| `FRONTEND_ORIGIN` | `https://<your-app>.vercel.app` | Plain | Yes |
| `LANGFUSE_PUBLIC_KEY` | _(optional)_ | **Secret** | No |
| `LANGFUSE_SECRET_KEY` | _(optional)_ | **Secret** | No |
| `LANGFUSE_BASE_URL` | _(e.g. https://us.cloud.langfuse.com)_ | Plain | No |

**Notes:**
- `GTM_API_KEYS` is comma-separated. Use the same value for the frontend's
  `NEXT_PUBLIC_GTM_API_KEY`.
- `FRONTEND_ORIGIN` must include `https://` and match the Vercel URL exactly.
  For local + deployed access: `https://your-app.vercel.app,http://localhost:3000`.
- Auth is fail-closed: `APP_ENV=production` + missing `GTM_API_KEYS` → all
  authenticated endpoints return 503.
- No secrets baked into the Docker image. Dockerfile copies only `gtm_triage/`.

### Cold-start warning (free tier)

Render free tier spins down after ~15 minutes of inactivity. First request
after idle takes 30-60 seconds (Docker restart + `/ready` probe). The frontend
handles 503 with a friendly "Service is starting up" message.

**Warmup after deploy:**
```bash
curl https://<your-render-url>/ready
```

### Health check

`render.yaml` uses `/ready` (not `/health`). The `/ready` endpoint calls
`SELECT 1` on the trace store and CRM, returning 503 if either is down.

---

## Vercel (Frontend)

### Environment variables

| Variable | Value | Type | Required |
|----------|-------|------|----------|
| `NEXT_PUBLIC_API_URL` | `https://<your-render-url>` | Plain | Yes |
| `NEXT_PUBLIC_GTM_API_KEY` | `demo-<same-key-as-Render>` | Plain | Yes |

**Notes:**
- `NEXT_PUBLIC_` prefix is required — these are client-side env vars exposed
  to the browser. The demo key is intentionally public: rate-limited, triages
  synthetic leads only.
- Root directory in Vercel: `gtm-lead-triage/web`
- Framework: Next.js (auto-detected)

---

## Provider modes

| GTM_PROVIDER | Behavior | Cost | Speed |
|-------------|----------|------|-------|
| `mock` | Deterministic rules only, no LLM | Free | Instant |
| `openai` | Real LLM reasoning + score adjustment | ~$0.001/lead | ~5-10s |

With `ENRICHMENT_PROVIDER=pdl`, PDL Person Enrichment runs on business emails
(100/month free tier). Without it, regex-based enrichment is used.

---

## Verification checklist

1. **Backend health:** `curl https://<render-url>/ready` → `{"ready": true, ...}`
2. **Auth rejects unauthenticated:** `curl https://<render-url>/config` → `401`
3. **Auth with key:** `curl -H "X-API-Key: demo-..." https://<render-url>/config` → `200`
4. **CORS:** Frontend at Vercel URL can POST to `/triage` without CORS errors
5. **Triage flow:** Submit a preset lead → see tier/route/score in result card
6. **Ops dashboard:** `/ops` shows lead list + trace details
7. **Rate limiting:** Rapid-fire > 60 RPM → `429`
8. **Cold-start UX:** Wait 15+ min, submit → "Service is starting up" → result after warmup

---

## Local development

```bash
# Terminal 1: API (no auth, SQLite)
cd gtm-lead-triage
python -m uvicorn gtm_triage.api:app --host 127.0.0.1 --port 8000

# Terminal 2: Frontend
cd gtm-lead-triage/web
npm run dev
```

No `APP_ENV`, `GTM_API_KEYS`, or `DATABASE_URL` → auth disabled, SQLite for
everything. The local path requires no cloud accounts.
