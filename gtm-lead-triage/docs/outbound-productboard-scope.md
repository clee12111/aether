# Outbound + Productboard — Scope

Source of truth for the build: a **Productboard-grounded GTM engine** — inbound
triage and outbound campaigns as motions on one shared core, grounded in real
company data and real product demand, with a feedback loop back into Productboard.

Inbound triage is motion #1 (built). Outbound is motion #2 (next). The motion
seam that makes this possible is **done** (see Build order, Phase 0).

---

## Objective (one line)

Turn real demand and real company signal into grounded, evaluated outreach — and
feed what the field learns back into Productboard.

---

## The thesis (what makes it novel)

Spark turns feedback into what to **build**. Apollo/Clay turn filters into who to
**spam**. Nobody connects product-demand *evidence* to the actual outreach —
grounded, drafted, and looped back. This system is that missing layer: an
**evidence-grounded agent between product and revenue**. The custom parts (the
reasoning agent, the scoring, the grounding guard, the eval) are the moat;
Productboard, PDL, Apollo, and HubSpot are integrated behind swappable interfaces.

---

## What it is (plain English)

Two motions on one shared core:

- **Inbound** — a lead comes to us (signup, email, chat). We enrich, infer what
  they likely want, score/route, and draft a tailored, grounded reply.
- **Outbound** — we reach people we weren't talking to, grounded in real company
  research and real product demand.

Productboard already collects customer feedback and clusters it into themes
(features / initiatives) — that's what Spark does. We **consume that output** (we
do not rebuild the clustering) as a grounding layer, and we **write field signal
back** so product discovery stays current.

---

## Lead sources vs grounding (keep these straight)

**Who to contact (the source / trigger):**
1. **Inbound signals** (primary demo path) — simulated signups / demo requests /
   sales@ emails from varied companies → enrich → triage, or expand to the
   account's buying committee.
2. **Apollo cold list** — ICP filters (industry / size / title), net-new.
3. **Productboard requesters** — people who asked for a feature (mostly existing
   customers → an *expansion/re-engagement* flow).

**Why them + what to say (the grounding):**
- **Company-research enrichment** (real, specific — see below).
- **Productboard demand** — account-level ("you asked for X") *only if* they're a
  requester; otherwise **segment-level** ("teams like yours keep asking for X").

> Honesty rule: a net-new prospect usually has **no** Productboard feedback, so
> grounding must be segment-level — never claim "you asked" unless a real
> feedback ID backs it. This is the grounding guard applied to outreach.

---

## Population & the record-once rule

- **Apollo** is the population/sourcing source — generous free tier (275M
  contacts / 73M companies, full DB search). Use it to pull realistic target
  lists across segments (big-tech / scale-up / startup).
- **PDL** is enrichment only — 100 lookups/month, emails obfuscated on free — so
  it fills in a known lead; it does not build lists.
- **Record-once → fixtures → replay.** Pull a real Apollo list and PDL-enrich it
  *one time*, snapshot both to fixtures, then run all testing/eval/demo against
  the snapshot. Real data, deterministic runs, zero credit burn, no contamination.
  Every external source (Apollo, PDL, Productboard) gets a fixture twin.
- **`data/seed_leads.json`** is the hand-made **edge-case set** (spam, GDPR
  opt-out, free-email vague lead) that Apollo won't source — the eval needs these.
  Realistic population comes from the recorded Apollo snapshot; edges stay synthetic.

---

## Real-company grounding (the enrichment layer)

Draft quality depends on grounding in *real* company data, not firmographic
fields alone. The waterfall composes, each field source + confidence tagged:

| Source | Gives | Status |
| --- | --- | --- |
| PDL | firmographics: industry, size, seniority | built |
| Company website read (`WebsiteFallback`) | what the company actually does, in its words | built (re-enable) |
| Web-search company brief | recent, specific hooks: launches, funding, hiring, news | new |
| Apollo (free) | **population:** real contacts + company DB across segments | phase 3 |
| Productboard | account/segment demand themes + verbatim requests | phase 1 |

The website-read + web-search compose into one **company-research step** that
emits a **cited brief** — every claim traces to its source, refuses to assert
what it can't ground. (This is what Clay's Claygent does; ours is grounded +
eval-checked.) This is the capability that turns "it works" into "these drafts
are scary good."

**Efficiency:** cache enrichment **by domain** (not per contact), batch for the
simulator, and record **fixtures** so runs are fast and tokenless.

---

## The draft: tailored, not "hi"

From minimal input (an email, maybe a role) the system **infers the person and
looks up their company** (company-research enrichment), then drafts an opener that
**names their likely problem and leads with the solution** — a hook grounded in
real company context and, where available, real product demand. Never a generic
greeting.

This **draft engine is shared across motions**: inbound uses it for the tailored
reply; outbound uses it for the cold hook. Same inputs (inferred role + company
research + demand themes), same anti-fabrication guard — a claim it can't ground
is dropped, not invented.

---

## Productboard connector (grounding source + sink)

A `SignalSource` adapter over the Productboard MCP (`mcp.productboard.com`), with
a recorded **fixture twin** so the demo runs tokenless after the trial expires.

**MCP tools used (4 of 14):**

| Tool | Role |
| --- | --- |
| `entities_query_entities` | pull a theme/feature (and check segment demand) |
| `feedback_list_feedback` | the requests behind a theme → demand + verbatim quotes; check if a domain is a requester |
| `feedback_create_feedback` | write field signal back (the sink) |
| `identity_get_identity` | auth / readiness check at startup |

All other tools (documents, status, comments) are intentionally unused.
Note: connecting Python → a remote OAuth MCP server is non-trivial; a REST+token
path may be simpler server-side. Decide at Phase 1; the fixture twin is required
either way.

---

## End-to-end flow

**Inbound (primary):** signal arrives (any channel → one `Signal`) → company
research enrichment → check Productboard (requester? else segment demand) →
score + route → draft a tailored, grounded reply → upsert HubSpot.

**Outbound:** pick targets (Apollo list, or Productboard requesters for
expansion) → enrich → fit-score (demand = a grounded positive) → A/B draft,
grounded in the company brief + (account or segment) demand → assign sequence
(Apollo, paused) → upsert HubSpot. Never sends.

**Loop (write-back):** the write-back is a **branch off intake, not the end** — every inbound
lead's message is itself customer feedback. The request in the message is written to Productboard
(`feedback_create_feedback`) **by company domain**, so each account's demand accumulates. The
account campaign then reads the domain's whole Productboard history. GTM feeds product discovery,
and Productboard becomes the **per-company memory** the campaign runs on.

Every step is traced; the whole run is auditable.

---

## UI — four views (account-based: Company → People)

- **Inbound** — the public lead form. Four intake modes (Form / Email / Chat / Clay)
  with one-click presets that FILL (not submit); loading + confirmation; no ops detail here.
- **Outbound** — list of **companies (accounts)**. Select a company → aggregated demand +
  a **dropdown of contacts** + one cohesive, updatable **"Launch/Update campaign against
  {company}."** Drop to a person → their tailored email draft (auto-drafted, persisted).
  Campaign + Delete in the body; sort by tier (hot→cold) or recency.
- **Testing** — same Company → People hierarchy. Select a company → the **account campaign
  trace** (cohesive, updatable, transparent). Dropdown of contacts → a person's **inbound +
  outbound-email journey** (per-person). Live end-to-end totals (cost/latency/tokens). Channel
  color-coding (Form/Email/Chat/Clay) + a **hot→cold tier color marking**; sort by tier/recency.
- **Architecture** (4th tab, next) — the system map: the whole pipeline as a "road" with every
  stack at its stop and the write-back loop. The system *at rest* (vs Testing = one run).

---

## Inbound simulator

A realistic, high-volume inbound generator so the system has signal to work on:
- Seeded varied scenarios (channels: form / email / chat; intents: hot / warm /
  spam / opt-out) on **real company domains** (so enrichment returns real data),
  with synthetic contacts.
- A "simulate inbound" action batch-generates N leads.
- Cached-by-domain + fixtures keep it fast and tokenless.

---

## Account-based model (Company → People)

The unit is the **company (account)**, grouped by domain; contacts nest under it.

- **Company** owns: its **contacts** (inbound leads — different people or the same person), the
  **accumulated Productboard demand** for the domain (filled by the write-back), and ONE cohesive
  **campaign** (updatable, never duplicated — re-running updates the same campaign).
- **Person** owns: their **inbound** arrival (channel/parse/score) and their **outbound email**.
- **Split:** inbound + outbound-email are person-level; the **campaign is account-level**.
- **Free-email leads** (gmail, etc.) have no company domain → they group as **individual
  one-person accounts** (not filtered out).
- **Ranking command:** companies (and contacts) sort by **tier (hot→cold)** or **recency** — a
  control in both Outbound and Testing.
- **Write-back makes accounts rich:** each contact's request → Productboard by domain → the
  account campaign reads the domain's whole history.

Build order: (1) write-back per lead, (2) Outbound company view + contacts dropdown + one
updatable campaign + sort, (3) Testing company/person hierarchy + channel/tier color + sort.

---

## In scope

- Company-research enrichment (PDL + website + web-search brief, cited).
- `ProductboardSource` connector + fixture twin (source + sink).
- Outbound motion on the seam: intake → enrich → fit-score → A/B grounded draft →
  sequence assignment → HubSpot.
- Apollo as free lead source / sequencer; HubSpot + PDL reused.
- Three-view UI + inbound simulator.
- Outbound + **grounding** eval (de-gamed, held-out).

## Out of scope (hold the line)

- Rebuilding theme clustering — Spark does it; we consume it.
- Editing specs (`documents_*`) or mutating roadmap status (`update_entity_status`).
- Full automated inbound-reply parsing (v1 logs replies as an explicit action).
- Sending email (drafts only).
- Clay / Gong / Salesforce Agentforce as live integrations (mock behind interfaces).

---

## Clay — deferred (interop at intake, not orchestrator)

Clay is the GTM team's enrichment/orchestration tool. Our engine already does its
core (waterfall + Claygent = the company-research brief), so Clay is **not** our
orchestrator. When we want hands-on interop, it plugs in at the **intake layer** as
one more source — a `ClayWebhookSource` (`POST /webhooks/clay` that parses a
Clay-enriched row into a `Signal`) or a `ClayCsvSource` (export → ingest), behind
the existing source interface with a fixture twin.

**Deferred until needed.** Clay's live webhook/HTTP-API automation is Growth-plan
($495/mo); the *receiver* is free to build whenever we want it. It's additive —
it never blocks or alters the motion, so there's no cost to waiting.

---

## Build order (phases)

0. **Motion seam** — ✅ **DONE (2026-06-28).** `run_triage` → generic
   `run_motion(signal, motion, executor, trace, provider, model)`; `InboundMotion`
   is the first `Motion`. New: `models/signal.py`, `motions/base.py` (8-hook
   `Motion` ABC), `motions/inbound.py`. Verified byte-identical: **482 passed /
   6 skipped, held-out 5/5, tier accuracy identical.**
1. **Productboard connector** + fixture twin — ✅ **DONE.** 11 models, ABC + factory,
   fixture twin, `parsed_customer`.
2. **Company-research enrichment + grounding compose** — ✅ **DONE (Phase 2 + 4a).**
   One cited brief from PDL + Apollo + website + search + Productboard demand;
   anti-fabrication preserved.
3. **`ApolloSource`** + fixture twin + live client — ✅ **DONE.** Free-tier org
   search + enrich; live confirmed against the real API.
4. **Outbound motion + LLM-composed grounded A/B drafts** — ✅ **DONE (4b + 4b.1).**
   research → fit-score → tailored A/B draft (LLM-written, grounding-verified).
   Drafts never send.
5. **Grounding eval (held-out, two-layer, hardened judge)** — ✅ **DONE (4c + 5 + 5.1).**
   Headline: **0% hard / 0% soft fabrication on 12 held-out companies, 0 to human
   review** (small-N: 24 variants — directional). Structured-boolean judge
   (gpt-5.4-mini) ≥ drafter (gpt-5.4-nano).
6. **Outbound API** (target / from-lead / campaign / by-lead / journey) — ✅ **DONE.**
7. **Multi-channel intake** (Form / Email / Chat / Clay → one Signal) — ✅ **DONE.**
8. **Three-view UI** (Inbound lead-form / Outbound / Testing journey) — ✅ **DONE**
   (design-skill applied, Playwright interaction tests).
9. **Loop guardrail + persistence** — ✅ **DONE.** No tool repeats per run; drafts +
   campaigns persist (load-existing via `/outbound/by-lead`), survive navigation.
10. **Campaign fix** — ✅ **DONE.** `campaign-from-lead` attaches to the existing lead
    (no duplicate); campaign output shows in Outbound; journey groups it under the lead.
11. **Productionized on Postgres** — ✅ **DONE.** `DATABASE_URL` → CRM + trace on Neon;
    SQLite only local/tests. Hardening on all endpoints; Dockerfile / render.yaml / CI;
    Neon round-trip verified.
12. **Productboard demand — seeded snapshot** — ✅ **DONE.** Real-domain feedback
    (Datadog/Notion/Stripe/Figma) seeded + fixtures refreshed; `is_requester` fires and
    boosts fit (+25).
13. **Account-based redesign (Company → People)** — 📋 NEXT (see Account-based model):
    write-back per lead → Outbound company view → Testing company/person hierarchy.
14. **4th tab — Architecture / system map** (the snake-road) — 📋 next.
15. **Live Productboard client (Phase B)** — 📋 later (deployable real-time PB; LiveClient
    is a NotImplemented scaffold today).

---

## Eval — the moat

- Held-out contacts: some **with** a real Productboard request, some **without**.
- **Grounding check:** any "you asked for X" must map to a real feedback ID; any
  company-brief claim must map to a real source. A fabricated claim is a failure —
  the outbound analogue of a false-hot.
- A no-evidence contact **must** produce a generic draft (asserted), never an
  invented personalization.
- Reported at the floor; fabrication tracked separately.
- **Achieved (Phase 5.1):** 0% hard / 0% soft fabrication on 12 held-out companies,
  0 routed to human review; adversarial sparse-fact set 0%. Small-N (24 variants) —
  directional, not statistical. Judge ≥ drafter; structured-boolean label.

---

## Constraints / honest notes

- **Feedback access is entity-first** — no global dump. `feedback_list_feedback`
  needs entity IDs, so campaigns are theme-anchored (the right GTM framing anyway).
- **Company is a domain attribute** on feedback; PDL + research fill the rest.
  Free-email requesters have no `companyDomain` → target on email domain, skip write-back.
- **Demand-list size = workspace feedback volume** → seed the trial workspace with
  ~12 realistic feedback items so there's signal to ground on.
- **Trial clock:** the connector must work from fixtures with no token.
- **Free-tier credit caps:** Apollo (~10 exports/mo) and PDL (100 lookups/mo,
  emails obfuscated free) → never call them in a loop; record once to fixtures
  and replay.
