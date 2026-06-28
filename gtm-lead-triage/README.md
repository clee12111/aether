# Aether GTM — Agentic Lead Triage

Agentic reasoning for inbound lead triage — deployed, with real enrichment, a real CRM write, and every step traced.

An AI agent receives an inbound lead and reasons about it step by step: it reads the message, looks the contact up in the CRM, enriches the company, scores and routes the lead, and drafts outreach — then writes the result to HubSpot. The agent never sends anything; it triages, scores, drafts, and hands off.

> **Live demo:** [aether-c7bg.vercel.app](https://aether-c7bg.vercel.app/) — `/` is the lead form, `/ops` is the operator dashboard.

## Architecture

The reasoning loop is the same reason-act-observe (RAO) architecture validated on the FinQA financial-reasoning benchmark in the core Aether engine (75.5% lenient / 68.5% strict, n=200). Here that loop is applied to inbound lead qualification and validated on its own **de-gamed held-out eval**.

**Build the brain, integrate the body.** The decision brain — the RAO agent, its eval, its trace — is built. The CRM (HubSpot), enrichment (People Data Labs), and observability (Langfuse) are integrated through swappable interfaces, not rebuilt.

![GTM lead-triage pipeline](docs/pipeline.svg)

## The pipeline

| Step | What it does | LLM? |
| --- | --- | --- |
| Extract | Read role + intent from the message (structured output) | Yes |
| CRM lookup | Check for an existing contact | No |
| Enrich | Real firmographics via a PDL waterfall (email validation → PDL → website read), each field tagged with source + confidence | Optional |
| Score + route | Deterministic rules + a clamped ±10 LLM nudge → tier + route | Optional |
| Draft outreach | Template draft, never sends | No |

The agent reasons one step at a time and branches on real signals (invalid email short-circuits, low-confidence enrichment digs deeper, ambiguous seniority is gated) — different leads produce different trace shapes. The tools are also exposed as an MCP server (`gtm_triage/mcp_server.py`).

## Eval — de-gamed and honest

- **Held-out set (n=35):** 62.9% tier accuracy, **zero false-hots**, 12.5% false-cold — reproducible (temp-0, byte-identical runs).
- **Independently labeled:** senior-SDR judgment, not derived from the scoring rules; company names carry no industry-keyword leakage.
- **Train/dev/test discipline:** a separate dev split for tuning; the held-out set is write-once.
- **Mock CI gate:** 5/5 deterministic, keyless, runs on every push.
- **486 tests**, 76% coverage floor enforced in CI.

The eval was rebuilt after catching a gamed test set (company names contained the answer the regex keyed on). The de-gamed number is what's reported, false-hot vs. false-cold separately. Full progression in [DECISION.md](DECISION.md).

## The two-view web app

- **`/`** — lead form; Company is optional (the system derives it from the email domain). Preset examples for hot / warm / cold / spam / opt-out.
- **`/ops`** — operator dashboard: tier filters + score sort, the lead list, and a detail panel with the draft, score breakdown, enrichment, and the full RAO trace — plus Langfuse and HubSpot deep links. Delete a lead (CRM + trace) from the panel.

## Swappable stack

Each backend sits behind one interface — a new provider is one adapter + an env var (verified in [docs/audit/TRANSFERABILITY_AUDIT.md](docs/audit/TRANSFERABILITY_AUDIT.md)):

| Layer | Built / integrated | Swap to |
| --- | --- | --- |
| CRM | HubSpot v3 REST | Salesforce |
| Enrichment | People Data Labs | Apollo / Clearbit |
| Model | OpenAI `gpt-4o-mini` | Anthropic |
| Trace store | Postgres (Neon) | SQLite |

## Production hardening

Auth (fail-closed in production), per-IP rate limiting, request size limits, an SSRF guard on the website fetch, prompt-injection containment (the lead message can never reach the tier — the deterministic scorer is the backstop), retries + circuit breakers + graceful degradation on every external call, right-to-erasure (`DELETE /contacts/{email}`), structured JSON logging with correlation IDs, **OpenTelemetry**, **Sentry**, a Prometheus `/metrics` endpoint, `/ready` health checks, and a GitHub Actions pipeline that runs the test suite + eval gate on every push.

## Running locally

```bash
cd gtm-lead-triage

# Terminal 1: API (falls back to mock if no OPENAI_API_KEY)
python -m uvicorn gtm_triage.api:app --host 127.0.0.1 --port 8000

# Terminal 2: frontend
cd web && npm run dev
```

Open http://localhost:3000 (form) and http://localhost:3000/ops (dashboard).

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `GTM_PROVIDER` | `openai` | `openai`, `anthropic`, or `mock` |
| `GTM_MODEL` | `gpt-4o-mini` | Model for the chosen provider |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | - | Required for the real provider |
| `ENRICHMENT_PROVIDER` | `mock` | `pdl` or `mock` |
| `PDL_API_KEY` | - | People Data Labs key (for real enrichment) |
| `CRM_BACKEND` | `sqlite` | `sqlite` or `hubspot` |
| `HUBSPOT_TOKEN` | - | HubSpot Private App token |
| `DATABASE_URL` | - | Postgres DSN (uses SQLite if absent) |
| `APP_ENV` | - | `production` enables fail-closed auth |
| `GTM_API_KEYS` | - | Comma-separated API keys (required if `APP_ENV=production`) |
| `DAILY_QUERY_CAP` | `200` | Max real LLM runs per UTC day (falls back to mock) |
| `FRONTEND_ORIGIN` | `http://localhost:3000` | CORS allowed origin |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL` | - | Langfuse (optional) |

## Deploy

See [DEPLOY.md](DEPLOY.md): Neon (Postgres) + Render (API, Docker) + Vercel (frontend).

## Scope guardrails

- **Draft-only:** outreach is drafted, never sent.
- **Deterministic decision:** the scorer owns the tier; the LLM only gets a clamped nudge.
- **No LangChain:** direct SDK, every decision is visible code.
- **Daily LLM cap:** falls back to mock (free, deterministic) when the quota is exhausted — the app never errors.
- **Idempotent:** duplicate submissions return the cached result; the CRM dedupes by email.
