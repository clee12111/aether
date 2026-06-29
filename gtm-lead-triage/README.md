# Aether GTM — Agentic GTM Platform

A **Productboard-grounded** platform for running GTM motions as agents — evidence-grounded, traced, and eval-gated. An AI agent receives a signal (an inbound lead, or an outbound target), reasons about it one step at a time, enriches it against real company data **and live product demand from Productboard**, scores and routes it, and drafts outreach — writing the result to the CRM and feeding new product requests back to Productboard. The agent never sends anything; it triages, scores, drafts, and hands off.

Two motions are built on one engine: **inbound lead triage** and **outbound account campaigns**. Adding a third motion is a new trigger + action + eval, not a new system.

> **Live demo:** [aether-c7bg.vercel.app](https://aether-c7bg.vercel.app/) — `/inbound` (lead form), `/outbound` (account campaigns), `/testing` (per-account journey + trace), `/architecture` (system map + live stats).

## Architecture

The reasoning loop is the same reason-act-observe (RAO) architecture validated on the FinQA financial-reasoning benchmark in the core Aether engine (75.5% lenient, n=200). Here that loop is applied to GTM and validated on each motion's own **de-gamed held-out eval**.

**Build the brain, integrate the body.** The decision brain — the RAO agent, its eval, its trace — is built. The CRM (HubSpot), enrichment (People Data Labs + Apollo), search (Brave), product demand (Productboard), and observability (Langfuse) are integrated through swappable interfaces, not rebuilt.

### The pipeline (ports & adapters)

```
1 INTAKE      2 PARSE          3 ENRICH                4 SCORE        5 DRAFT       6 DELIVER      7 OBSERVE
channels  ─▶  Signal schema ─▶ PDL · Apollo · Brave ─▶ rules + ─▶    grounded ─▶  CRM upsert ─▶  Langfuse +
(form,        (extract +       Website · Productboard  clamped LLM   A/B draft    (HubSpot)      Postgres trace
 email,        validate,       demand                  nudge         (no-send)                   + per-motion eval
 chat, Clay)   dedup)                │                                                            
                                     └────────────── write-back: new requests ──▶ Productboard
```

Every stop swaps behind one interface: CRM (HubSpot / Salesforce), enrichment (PDL / Apollo / Clearbit), model (OpenAI / Anthropic), store (Postgres / SQLite), search (Brave / Tavily). New channel = new adapter, nothing downstream changes.

## The two motions

**Inbound triage** (`/triage`, `/intake/*`). A lead arrives on any channel → normalized to a typed `Signal` → the agent reasons step by step: read role + intent, look the contact up in the CRM, enrich the company, score and route, draft outreach. It branches on real signals — an invalid email short-circuits, low-confidence enrichment digs deeper, ambiguous seniority is gated — so different leads produce different trace shapes.

**Outbound campaigns** (`/outbound/*`). Point the same engine at a target account instead of an inbound message. It researches the company (PDL + Apollo + website + Brave + Productboard demand, every fact carrying its source), scores ICP fit (deterministic rules + a clamped ±10 LLM nudge — the LLM never owns the tier), and drafts grounded A/B outreach. A **campaign** expands an account into lookalike targets via Apollo, then runs research → fit → draft per target. The campaign loop is bounded: one Apollo search per campaign, a `MAX_CAMPAIGN_TARGETS` cap, per-domain dedup, and a non-advancing stop so it can't balloon LLM calls.

The agent's tools are also exposed as an MCP server (`gtm_triage/mcp_server.py`).

## The Productboard loop

Productboard is wired into the pipeline at two points, closing a GTM↔Product loop:

- **Read — at Enrich.** Productboard demand is an enrichment source alongside PDL/Apollo/Brave. When the engine builds a company brief, it pulls that domain's existing feature requests. Demand grounds the brief and boosts fit — a company that already asked for features scores hotter (`is_requester +25`). Product pull informs *who* GTM goes after.
- **Write-back — from Intake.** When an inbound lead's message contains a feature request, the engine writes it back into Productboard as customer feedback, keyed by company domain (skips free-email domains and messages with no clear request). GTM conversations push fresh demand *into* the product team.

This is what makes the platform "Productboard-grounded" rather than a generic lead scorer.

## The four-view web app

- **`/inbound`** — lead form across Form / Email / Chat / Clay channels, with hot/warm/cold/spam/opt-out presets and a progressive loading state.
- **`/outbound`** — account-based view: companies grouped by domain (free-email leads as their own one-person accounts), tier + channel color-coding, hot→cold / recency sort, per-contact drafts, and a launchable account campaign.
- **`/testing`** — per-account journey: inbound and outbound traces side by side, the full RAO reasoning trace, and live per-person end-to-end totals.
- **`/architecture`** — the live system map (the snake-road pipeline above) with the Productboard read + write-back loop and live stats.

## Eval — de-gamed and honest

- **Inbound held-out set (n=35):** 62.9% tier accuracy, **zero false-hots**, 12.5% false-cold — reproducible (temp-0, byte-identical runs). Independently labeled (senior-SDR judgment, not derived from the scoring rules); company names carry no industry-keyword leakage. Rebuilt after catching a gamed test set where company names contained the answer the regex keyed on.
- **Outbound grounding eval:** 0% hard / 0% soft fabrication on 12 held-out companies — a two-layer check (deterministic token verifier + a structured-boolean LLM judge), with the engine refusing to invent values the evidence didn't state.
- **Train/dev/test discipline:** a separate dev split for tuning; held-out sets are write-once.
- **CI:** mock eval gate (deterministic, keyless) + a 590-test pytest suite with a coverage floor, run on every push.

Full progression in [DECISION.md](DECISION.md).

## Swappable stack

Each backend sits behind one interface — a new provider is one adapter + an env var (verified in [docs/audit/TRANSFERABILITY_AUDIT.md](docs/audit/TRANSFERABILITY_AUDIT.md)):

| Layer | Built / integrated | Swap to |
| --- | --- | --- |
| CRM | HubSpot v3 REST | Salesforce / SQLite / Postgres |
| Enrichment | People Data Labs + Apollo | Clearbit |
| Search | Brave | Tavily / off |
| Product demand | Productboard (REST + MCP) | fixture / off |
| Model | OpenAI `gpt-5.4-nano` | Anthropic |
| Trace store | Postgres (Neon) | SQLite |

## Production hardening & performance

Auth (fail-closed in production), per-IP rate limiting, request size limits, an SSRF guard on the website fetch, prompt-injection containment (the lead message can never reach the tier — the deterministic scorer is the backstop), retries + graceful degradation on every external call (a slow or out-of-credits provider falls back, never crashes), right-to-erasure (`DELETE /contacts/{email}`), structured JSON logging with correlation IDs, OpenTelemetry, Sentry, a Prometheus `/metrics` endpoint, `/ready` health checks, and a GitHub Actions pipeline that runs the test suite + eval gate on every push.

Latency is managed end to end: company research, the Productboard write-back, and auto-drafting run as **background tasks** so a submit returns as soon as triage + route are done; `/leads` is cached with immediate invalidation on CRM upsert; and the frontend uses a shared stale-while-revalidate store so tab switches render instantly while revalidating in the background.

## Running locally

```bash
cd gtm-lead-triage

# Terminal 1: API (falls back to mock if no OPENAI_API_KEY)
python -m uvicorn gtm_triage.api:app --host 127.0.0.1 --port 8000

# Terminal 2: frontend
cd web && npm run dev
```

Open http://localhost:3000/inbound (lead form) and http://localhost:3000/outbound (account campaigns).

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `GTM_PROVIDER` | `openai` | `openai`, `anthropic`, or `mock` |
| `GTM_MODEL` | `gpt-5.4-nano` | Model for the chosen provider |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | – | Required for the real provider |
| `CRM_BACKEND` | `sqlite` | `sqlite`, `postgres`, or `hubspot` |
| `HUBSPOT_TOKEN` | – | HubSpot Private App token (when `CRM_BACKEND=hubspot`) |
| `DATABASE_URL` | – | Postgres DSN — when set, both CRM + trace use Postgres |
| `ENRICHMENT_PROVIDER` | `mock` | `pdl` or `mock` |
| `PDL_API_KEY` | – | People Data Labs key (real firmographics) |
| `APOLLO_SOURCE` | `fixture` | `live`, `fixture`, or `off` (campaign lookalike search) |
| `APOLLO_API_KEY` | – | Apollo.io key (when `APOLLO_SOURCE=live`) |
| `COMPANY_RESEARCH` | `off` | `on` enables the cited company-research brief |
| `SEARCH_PROVIDER` | `off` | `brave`, `tavily`, or `off` |
| `BRAVE_API_KEY` | – | Brave Search API key |
| `PRODUCTBOARD_SOURCE` | `fixture` | `live`, `fixture`, or `off` |
| `PRODUCTBOARD_TOKEN` | – | Productboard Public API token (when `live`) |
| `MAX_CAMPAIGN_TARGETS` | `5` | Cap on lookalike targets per campaign |
| `APP_ENV` | – | `production` enables fail-closed auth |
| `GTM_API_KEYS` | – | Comma-separated API keys (required if `APP_ENV=production`) |
| `DAILY_QUERY_CAP` | `200` | Max real LLM runs per UTC day (then falls back to mock) |
| `FRONTEND_ORIGIN` | `http://localhost:3000` | CORS allowed origin(s) |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL` | – | Langfuse (optional) |

## Deploy

See [DEPLOY.md](DEPLOY.md): Neon (Postgres) + Render (API, Docker) + Vercel (frontend). The live demo runs auth-off (`APP_ENV=demo`), protected by the daily cap, per-IP rate limiting, and CORS.

## Scope guardrails

- **Draft-only:** outreach is drafted, never sent.
- **Deterministic decision:** the scorer owns the tier; the LLM only gets a clamped nudge.
- **Grounded, never fabricated:** enriched fields carry `value / source / confidence`; the agent refuses or flags rather than inventing.
- **Bounded loops:** both the triage loop and the campaign expansion have explicit guardrails (no repeated tool calls, per-target caps, non-advancing stops).
- **No LangChain:** direct SDK, every decision is visible code.
- **Daily LLM cap:** falls back to mock (free, deterministic) when the quota is exhausted — the app never errors.
- **Idempotent:** duplicate submissions return the cached result; the CRM dedupes by email.
