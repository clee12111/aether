# RESTRUCTURE.md — Aether GTM Extension

From a local document-Q&A engine to a deployed GTM lead-triage service. June 2026.
This supersedes the earlier Forward-Deployed-Engineer restructure. The engine and
its discipline are unchanged; the body and the target role change.

## 1. The reframe

Aether began as a local agentic reasoning engine for documents: hybrid retrieval,
a reason-act-observe loop, deterministic tools, a grounding guard that refuses to
fabricate, and a full audit trail. That proved the brain works. This plan grows it
a body and points it at a go-to-market job.

**Who this is for.** The target role is GTM Engineer, with the immediate goal of
the Associate GTM Engineer role at Productboard. A GTM engineer builds the systems
and AI agents that make a revenue team faster: lead capture, enrichment, scoring,
routing, outreach, and the data hygiene that keeps model inputs clean. The role
grew over 200% in postings from 2024 to 2025. What it screens for is a builder who
can stand up reliable AI agents and prove they work with evals.

**The simple idea.** Aether already has the hard, valuable part: an agent that
reasons one step at a time, refuses to invent facts, and records every step. The
modern GTM stack buys almost everything else off the shelf. The piece teams still
build by hand is the AI decision brain. So this plan positions Aether as that
brain and integrates it with the standard stack.

## 2. Where Aether is today

An honest snapshot from the code. A working reason-act-observe loop with a
deterministic tool runner and a step-by-step trace. A grounding guard that returns
INSUFFICIENT_CONTEXT instead of inventing an answer. Hybrid retrieval validated on
FinQA. Everything else is local and single-user: documents on local disk, vectors
in local Chroma, tabular data in in-memory DuckDB, the trace in local SQLite, and
the only interface a Streamlit app run by hand. Nothing external can call it, it
takes no live input, and it forgets results on restart. The loop, the grounding
guard, the retrieval, the trace store, and the eval harness are real and they stay.

## 3. The target architecture (warehouse-native GTM: build the brain, buy the body)

The 2026 GTM stack is warehouse-native. Raw data lands in a warehouse (Snowflake
or BigQuery), dbt models it into a clean golden record, reverse ETL (Hightouch or
Census) pushes decisions back into the CRM (HubSpot or Salesforce) and the
engagement tool (Apollo, Outreach, or Salesloft), Clay enriches along the way, and
n8n orchestrates the moving parts. The newest and least-commoditized layer is the
AI decision brain. That is where Aether sits.

**The build/buy line (the load-bearing decision).**

- Buy or use, because these are solved and a GTM engineer wires them rather than
  rebuilding them: orchestration (n8n), CRM (HubSpot), enrichment (Clay), reverse
  ETL (Hightouch), engagement (Apollo).
- Build, because off-the-shelf cannot be trusted here and this is the part that
  needs evaluation: the decision brain (Aether). Score, qualify, route, and draft,
  with traces, a grounding guard, and an eval gate.

**How a request flows.** A lead hits a form. n8n triggers and handles the
plumbing. n8n calls Aether (over a webhook, or as an MCP tool) for the judgment.
Aether looks up the CRM, enriches the lead, scores and routes it, drafts outreach,
and traces every step. n8n takes Aether's decision and activates it: writes the
enriched record and score to HubSpot, and queues the drafted email in the
engagement tool for a human to approve. n8n orchestrates; Aether decides.

| Layer | Today (local) | Target (real tool) | Buildable stand-in |
|---|---|---|---|
| Source of truth | in-memory DuckDB | Snowflake / BigQuery | local DuckDB or Postgres |
| Transform | none | dbt | plain SQL |
| Enrichment | none | Clay | DIY LLM enrich step |
| Decision brain | Aether (local) | Aether (deployed) | Aether RAO agent |
| CRM | none | HubSpot / Salesforce | SQLite behind an interface |
| Activation / sync | none | Hightouch / Census | a sync function to the CRM |
| Engagement | none | Apollo / Outreach | draft-only stub (never sends) |
| Orchestration | run by hand | n8n | the agent loop itself |
| Tracing + eval | local SQLite | Langfuse + CI eval gate | Aether trace store + eval |

## 4. The phased plan

Each phase ends in something that works and is on its own a resume-worthy skill.
Go in order. Do not start the next phase until the current one is done and logged
in DECISION.md.

**Phase 0: Foundation and the bar.** Pin a golden set of about 20 labeled leads.
Write FRONTIER.md with the GTM axes and tiers (done). Reconcile any public claim
to what the repo can reproduce. Done when the eval runs and the bar is written.

**Phase 1: The motion, local and evaluated.** The decision brain end to end: the
agent plus four tools (crm_lookup, enrich_lead, score_lead, draft_outreach) plus a
local SQLite CRM plus the eval. No external services. Done when the eval passes on
the golden set.

**Phase 2: Give it a body.** Wrap the agent in a small API (FastAPI) that accepts
a lead and returns a triage decision. Containerize it. Done when you can POST a
lead to a URL and get a decision back.

**Phase 3: Orchestrate with n8n.** An n8n workflow triggers on a new lead, calls
the Aether API, and routes the result. Done when a form submission drives the whole
flow with no script run by hand.

**Phase 4: Real connectors.** Swap the SQLite CRM for HubSpot's API behind the
same interface. Add a real enrichment step on the Clay pattern. Done when a lead is
enriched and written to a HubSpot sandbox.

**Phase 5: Observability and the eval gate.** Langfuse traces every decision with
latency and cost. The eval runs in CI and blocks a change that drops accuracy below
the FRONTIER.md bar. Done when a quiet regression is rejected automatically.

**Phase 6: The showcase.** One deployed end-to-end thread: form, n8n, Aether,
HubSpot, with the drafted email, the trace, and the eval. Then show the same engine
still does finance Q&A by swapping the prompt directory, proving the brain is
domain-general.

## 5. Stack decisions

Opinionated picks for a solo builder who wants the real tools without heavy setup,
each with a free or buildable local stand-in.

- **Orchestration: n8n.** Self-hostable, developer-friendly, real code nodes, AI
  and LangChain nodes as of 2.0. A GTM engineer is expected to use a tool like
  this; hand-rolling orchestration is reinventing a solved problem.
- **CRM: HubSpot.** Free API, names in the JD, standard for product-led B2B.
  Designed behind a CRM interface so Salesforce is a documented swap.
- **Enrichment: Clay (the pattern).** The control tower of modern GTM. The project
  builds a DIY LLM enrichment step on the same waterfall idea, with a confidence
  score and a recorded source.
- **Decision brain: Aether (built, not bought).** The reason-act-observe loop,
  kept on the direct-SDK, no-LangChain rule for auditability. The model is a
  swappable slot.
- **Agent-to-tools transport: MCP.** Exposes the four GTM tools over the protocol,
  which is the agent-native design the JD asks for and makes the tools portable.
- **Warehouse and reverse ETL: Snowflake/BigQuery plus Hightouch/Census.** Named
  in the target architecture, stood in locally by DuckDB and a sync function.
- **Engagement: Apollo or Outreach.** Named as the activation destination; the
  project stops at a draft and never sends.
- **Tracing and eval: Langfuse plus a CI eval gate.** Start with the existing local
  trace store and eval harness, move to Langfuse in Phase 5.

## 6. What this proves (mapped to the JD)

- **Build or buy GTM technology:** the explicit build/buy line, buy the
  orchestration and the connectors, build the decision brain.
- **Conceive and deploy agents for GTM motions:** the lead-triage agent, deployed
  and callable.
- **Eval-driven development:** the golden set and the CI eval gate.
- **Data hygiene for clean model inputs:** confidence-scored enrichment and
  abstention on thin input.
- **Agent-native design and multi-model orchestration:** the RAO loop, the MCP
  transport, and the swappable model slot.

## 7. Scope guardrails

No real email sending; outreach stops at a draft. No LangChain in the brain. Not a
CRM replacement. Finance is preserved as a proven domain, not deleted. No
multi-turn conversation. Local stand-ins are demonstrated by interface; the
live-vendor swap is documented, and exercised only where a free sandbox allows.

## 8. Honest scope and sequencing

Seven phases plus the connectors, on the order of a few focused weeks, not a
weekend. The discipline that matters most: do not stack phases or widen the
perimeter mid-build. Each phase stands alone and leaves something demonstrable, so
even stopping after Phase 3 is a real step up from where Aether is today. Go in
order, finish each, log each in DECISION.md, and let the eval gate and the ledger
keep you honest.
