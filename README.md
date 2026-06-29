# Aether GTM — Agentic GTM Platform

A **Productboard-grounded** platform for running GTM motions as agents — evidence-grounded, traced, and eval-gated. An AI agent receives a signal (an inbound lead, or an outbound target), reasons about it one step at a time, enriches it against real company data **and live product demand from Productboard**, scores and routes it, drafts outreach, writes the result to the CRM, and feeds new product requests back to Productboard. The agent never sends anything — it triages, scores, drafts, and hands off.

Two motions run on one engine: **inbound lead triage** and **outbound account campaigns**. The reasoning brain is a reason-act-observe (RAO) loop **independently validated on the FinQA financial-reasoning benchmark** before it was pointed at GTM — so the decision core is proven, not just plausible.

**Build the brain, integrate the body.** The decision brain — the agent, its eval, its trace — is built here. The CRM (HubSpot), enrichment (PDL + Apollo), search (Brave), product demand (Productboard), and observability (Langfuse) are integrated through swappable interfaces, not rebuilt.

> **Live demo:** [aether-c7bg.vercel.app](https://aether-c7bg.vercel.app/) — `/inbound` (lead form), `/outbound` (account campaigns), `/testing` (per-account journey + trace), `/architecture` (system map + live stats).

---

## What it does

**Inbound.** A lead arrives — often just a name, an email, and a sentence (company optional). The agent reads what they said, looks them up in the CRM, enriches the company from the email domain, scores them with deterministic rules plus a bounded LLM nudge, assigns a tier (hot / warm / cold / disqualified) and a route (AE / SDR / marketing / drop), drafts outreach (never sends), writes the result to HubSpot, and produces a full auditable trace. If the message contains a feature request, it's written back to Productboard as demand.

**Outbound.** Point the same engine at a target account. It researches the company (PDL + Apollo + website + Brave + Productboard demand, every fact carrying its source), scores ICP fit, and drafts grounded A/B outreach. A **campaign** expands an account into lookalike targets via Apollo and runs research → fit → draft per target — with a bounded loop so it can't run away.

Three things distinguish it:

- **Grounded.** Every score traces to a signal; enriched fields carry source + confidence. The agent flags or refuses rather than inventing data. A company that already asked for features in Productboard scores hotter (`is_requester +25`).
- **Observable.** Every step, tool call, and observation is written to a trace store and shown in the app, with Langfuse + HubSpot deep links.
- **Honestly evaluated.** Each motion ships a de-gamed, held-out eval — leads independently labeled, no keyword leakage — and the number is reported as-is, false-hot vs. false-cold tracked separately.

---

## The Productboard loop

Productboard is wired into the pipeline at two points, closing a GTM↔Product loop:

- **Read — at Enrich.** Productboard demand is an enrichment source alongside PDL/Apollo/Brave. When the engine builds a company brief, it pulls that domain's existing feature requests; demand grounds the brief and boosts ICP fit. Product pull informs *who* GTM goes after.
- **Write-back — from Intake.** When an inbound lead's message contains a feature request, the engine writes it back into Productboard as customer feedback, keyed by company domain. GTM conversations push fresh demand *into* the product team.

This is what makes the platform "Productboard-grounded" rather than a generic lead scorer.

---

## Agent architecture (the brain)

A reason-act-observe loop with a deterministic execution core. The agent reasons about one action at a time, observes the result, and decides the next — the path is discovered at runtime, not planned upfront. This is the same architecture **validated on FinQA** (financial reasoning, n=200) in the core engine; it's domain-agnostic, and here it's applied to GTM.

![Agent architecture](assets/fig1_architecture.svg)

The **loop agent** picks one tool per step. The **executor** is deterministic for data operations — no LLM in the loop. The deliberate exception is the bounded synthesis step (the **grounding guard**), which refuses rather than fabricating from absent evidence. The **scorer** is a transparent rule engine; the LLM gets only a clamped ±10 nudge, never the decision. Every step is written to a **trace store** — auditability is first-class. Both the triage loop and the campaign expansion have explicit guardrails (no repeated tool calls, per-target caps, non-advancing stops).

---

## GTM architecture (the body)

How the brain connects to the GTM stack — intake, enrichment, CRM, product demand, tracing, routing — all behind swappable interfaces.

```
1 INTAKE      2 PARSE          3 ENRICH                4 SCORE        5 DRAFT       6 DELIVER      7 OBSERVE
channels  ─▶  Signal schema ─▶ PDL · Apollo · Brave ─▶ rules + ─▶    grounded ─▶  CRM upsert ─▶  Langfuse +
(form,        (extract +       Website · Productboard  clamped LLM   A/B draft    (HubSpot)      Postgres trace
 email,        validate,       demand                  nudge         (no-send)                   + per-motion eval
 chat, Clay)   dedup)                │                                                            
                                     └────────────── write-back: new requests ──▶ Productboard
```

The stack is genuinely swappable — each backend is one adapter behind an interface (verified in the [transferability audit](gtm-lead-triage/docs/audit/TRANSFERABILITY_AUDIT.md)):

| Layer | Built / integrated | Swap to |
| --- | --- | --- |
| CRM | HubSpot (v3 REST) | Salesforce / SQLite / Postgres — one adapter + env var |
| Enrichment | People Data Labs + Apollo | Clearbit — one adapter |
| Search | Brave | Tavily / off |
| Product demand | Productboard (REST + MCP) | fixture / off |
| Model | OpenAI `gpt-5.4-nano` | Anthropic — one adapter + env var |
| Trace store | Postgres (Neon) | SQLite (local) |

The four-view app: **`/inbound`** (lead form across Form/Email/Chat/Clay), **`/outbound`** (account-based campaigns), **`/testing`** (per-account journey + full RAO trace), **`/architecture`** (live system map + stats). Full GTM writeup in [gtm-lead-triage/README.md](gtm-lead-triage/README.md).

---

## Results

| Eval | Scope | Result |
| --- | --- | --- |
| Inbound lead qualification | de-gamed held-out (n=35) | **62.9% tier accuracy · zero false-hots · 12.5% false-cold** (reproducible, temp-0) |
| Outbound grounding | held-out (n=12 companies) | **0% hard / 0% soft fabrication** (deterministic verifier + LLM judge) |
| Brain validation (FinQA) | n=200, Number-Match v2 | 75.5% lenient / 68.5% strict — same RAO architecture |
| Retrieval (core engine) | n=200 | R@5 0.85 · MRR@3 0.733 |
| Test suite | full | 590 passing · mock eval gate on every push · coverage floor enforced |

The GTM evals are **de-gamed**: leads/targets are independently labeled (not derived from the rules), company names carry no industry-keyword leakage, and results are reported at the floor. A gamed eval was caught and rebuilt — the de-gamed number is what's reported. Full progression in [gtm-lead-triage/DECISION.md](gtm-lead-triage/DECISION.md).

---

## Key design decisions

- **Deterministic decision, bounded LLM.** The tier comes from a transparent rule engine; the LLM gets a clamped ±10 nudge, never the decision. Auditable, evaluable, stable.
- **Grounded, never fabricated.** Enriched fields carry source + confidence; the agent refuses/flags rather than inventing.
- **One engine, many motions.** Inbound and outbound share enrichment, CRM, scoring, trace, and eval primitives. A new motion is a new trigger + action + eval, not a new system.
- **Swappable backends.** CRM, enrichment, search, model, product-demand, and trace store each sit behind one interface — proven by the transferability audit.
- **Direct SDK, no LangChain.** Every decision is visible code — no framework hiding retry logic or prompt assembly.
- **Eval-driven + honest.** Held-out sets are write-once; tuning happens on a separate dev split; the number is reported as-is.

---

## Stack

| Layer | Choice |
| --- | --- |
| API / brain | FastAPI · Python 3.11 · direct OpenAI/Anthropic SDK (no LangChain) · Pydantic v2 |
| Enrichment | People Data Labs (waterfall) + Apollo (firmographics + campaign lookalikes) |
| Product demand | Productboard (REST API + MCP) — read demand, write-back requests |
| CRM | HubSpot v3 REST (swappable: SQLite / Postgres / Salesforce) |
| Scoring | deterministic rules + clamped LLM nudge |
| Search | Brave (swappable: Tavily) |
| Observability | Langfuse · OpenTelemetry · Sentry · structured JSON logging · `/metrics` · `/ready` |
| Persistence | Postgres (Neon) / SQLite · idempotency keys |
| Frontend | Next.js + Tailwind (Vercel) · four views · stale-while-revalidate store |
| Deploy | Vercel (frontend) · Render (API, Docker) · Neon (Postgres) |

---

## Quickstart

```bash
cd gtm-lead-triage
python -m uvicorn gtm_triage.api:app --port 8000   # API (falls back to mock if no OPENAI_API_KEY)
cd web && npm run dev                              # frontend
```

Open `http://localhost:3000/inbound` (lead form) and `http://localhost:3000/outbound` (account campaigns).

The core financial-reasoning engine — the FinQA-validated brain — lives in `aether/`; run its Streamlit app with `uv run streamlit run ui/app.py`.

---

## Repository layout

```
aether/
├── aether/              core reasoning engine (RAO agent, validated on FinQA)
├── gtm-lead-triage/     the GTM platform
│   ├── gtm_triage/      motions (inbound, outbound), enrichment, scoring,
│   │                    CRM, Apollo, Productboard, trace
│   ├── web/             Next.js: four-view app (inbound / outbound / testing / architecture)
│   ├── evals/           de-gamed held-out + dev split + metrics harness
│   └── docs/            ARCHITECTURE.md, BACKLOG.md, audits
├── ui/app.py            Streamlit (core engine: Run / Trace / Eval)
└── docs/                validation log
```

---

## What this is not

Not a framework wrapper (no LangChain — hand-rolled orchestration is the point). Not a general assistant. Not benchmark-chasing — numbers reported at the floor. The agent decides; the LLM only nudges.

---

Cody Lee · [codylee.tech](https://codylee.tech) · [github.com/clee12111](https://github.com/clee12111)
