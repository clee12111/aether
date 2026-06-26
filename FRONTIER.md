# FRONTIER.md — Aether GTM Lead-Triage Extension

Bar-setter pass (advisor + live research, June 2026). This sets the measured bar
the GTM extension is built against. Re-run `frontier-bar` at each phase
transition; a Phase-0 snapshot goes stale.

**Honest ceiling, stated first.** There is no public benchmark for
lead-qualification accuracy the way FinQA exists for financial QA. The bars below
are best-published-practice for the stack shape and internal-golden-set measures
for the numbers. Where a figure has no external anchor it is flagged
`bar confidence: internal-only`. A measured pass against an internal bar must
never masquerade as a proprietary frontier.

## Approach landscape (the families, and which is frontier)

The decision is how to build the judgment layer of a GTM motion. Four families:

1. **Manual or static rules in the CRM.** Median. A human or a fixed HubSpot
   workflow scores and routes. Cheap, brittle, does not reason.
2. **A no-code AI node in an orchestrator.** Industry-common. n8n or Zapier calls
   an LLM inside a workflow. Fast to ship. No per-step trace, no eval, no
   grounding guard. Fine for low-stakes work.
3. **A custom evaluated agent (this project).** The frontier of what a solo
   builder can build and trust. A reason-act-observe loop with auditable traces,
   a grounding guard, and an eval gate. Trustworthy because it is measured.
4. **Warehouse-native AI decisioning.** The current commercial frontier (Hightouch
   AI Decisioning, Databricks CustomerLake, both June 2026). The brain runs on the
   warehouse golden record and activates through reverse ETL. Mostly proprietary.

This project sits at family 3 and integrates toward family 4 (warehouse plus
reverse ETL as the body). Family 4's core is proprietary, so we demonstrate the
open, buildable decision brain and integrate with the rest of the stack.

## Consequence map (axes ordered by real-units impact)

1. **Qualification accuracy.** A wrong tier sends a hot lead to a nurture drip (a
   lost deal) or a junk lead to an account executive (wasted rep time). Highest
   consequence.
2. **Routing correctness.** Right tier, wrong destination. High.
3. **Data hygiene.** Bad enrichment in means a bad decision out. High, and
   upstream of everything else.
4. **Decision latency per lead.** Slow triage lets inbound leads cool off. Medium.
5. **Cost per lead.** LLM plus enrichment spend per decision. Medium.

## Tiers and measures per axis

**Qualification accuracy**
- median: static rules, roughly 60-70% agreement with a human labeler.
- industry: LLM plus rules, roughly 80%.
- frontier (target): 90%+ on the golden set, with calibrated confidence and
  abstention on low-confidence leads.
- measure: eval-harness percentage over a 20+ lead golden set with human-labeled
  tiers and routes.
- reference number: `internal-only` (no public benchmark exists).

**Routing correctness**
- median: tier-to-route lookup with manual exceptions.
- industry: deterministic routing from tier, with guardrails (no AE handoff to a
  free-email address).
- frontier: routing that also accounts for rep capacity, territory, and SLA.
- measure: percent correct route on the golden set.
- reference number: `internal-only`.

**Data hygiene**
- median: take enrichment at face value.
- industry: confidence scores plus a waterfall fallback (the Clay pattern).
- frontier: source-tracked, confidence-weighted, abstains on thin input.
- measure: percent of leads carrying a confidence score and a recorded source,
  plus explicit bad-input handling tests.
- reference number: Clay-style waterfall lifts email find rate from ~50-60%
  (single source) to 80-95% (multi-source). Anchor, June 2026.

**Decision latency per lead**
- median: minutes (manual review).
- industry: seconds (one LLM call).
- frontier: sub-second on the deterministic path, a few seconds with LLM
  synthesis.
- measure: wall-clock per triage, read from the trace store.
- reference number: speed-to-lead research, inbound response inside ~5 minutes
  strongly lifts conversion. Anchor, directional.

**Cost per lead**
- median: human time.
- industry: a few cents (one small-model call plus enrichment).
- frontier: bounded and traced, a cheap model for routing and an expensive one
  only for drafting.
- measure: token plus enrichment cost per run, from the trace store.
- reference number: `internal-only` (depends on model and enrichment vendor).

## Divergence log (each fork from median, with the reason)

- **Build the decision brain instead of using an n8n AI node.** Reason:
  auditability, evals, and grounding, the trust properties an off-the-shelf node
  lacks. Consequence: more build cost, much higher trust, and a real interview
  differentiator.
- **Local stand-ins (DuckDB, SQLite, draft-only) instead of the live
  warehouse / CRM / engagement vendors.** Reason: buildable and free for a
  portfolio. Consequence: the integration is demonstrated by interface, not
  exercised against the live vendor.

## Flags

- bar confidence: published-practice for the stack shape, `internal-only` for the
  accuracy numbers.
- thin: no public lead-qualification benchmark exists.
- stale risk: the AI-decisioning frontier moved twice in June 2026. Re-run this
  bar at each phase transition.
