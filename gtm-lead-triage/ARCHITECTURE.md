# Architecture — GTM Agent Platform

This is not "an inbound lead router." It is a **platform for running GTM motions** as
agents — evidence-grounded, traced, and eval-gated. Inbound lead triage is **motion #1**,
hardened first. Outbound campaigns, lifecycle, surveys, and ABX are the same engine pointed
at a different trigger, action, and eval.

## The pipeline (ports & adapters)

```
INTAKE              NORMALIZE            MOTION                 ACTION            OBSERVE
(channel adapters)  (Signal schema)      (RAO agent)            (effects)         (trust)
─────────────────   ──────────────────   ────────────────────  ───────────────   ─────────────
web form        ─┐  extraction +         perceive → enrich      route             trace (Langfuse
webhook / n8n   ─┤  validation →          → score → DECIDE      draft (no-send)    + SQLite/PG)
CSV / list      ─┼─▶ typed Signal    ──▶  ┌ inbound ✓      ──▶  notify (Slack) ──▶ eval harness
email           ─┤  (deduped, clean       ├ outbound (next)     CRM upsert         (per motion:
chat / Slack    ─┘   LLM inputs)          ├ lifecycle           assign / SLA        precision/recall,
                                          └ surveys / ABX                           false-hot/cold)
```

### Layers

- **Intake (channel adapters).** Every input mode is an adapter that produces one normalized
  `Signal`. New channel = new adapter, nothing downstream changes. *(JD: "organize system
  integrations and data hygiene to optimize for clean LLM inputs.")*
- **Normalize.** Extraction + validation turn messy input into a typed, deduped `Signal`. This
  is the data-hygiene layer — the deterministic perception that feeds clean inputs to the LLM.
- **Motion (the agent).** A reason-act-observe loop: perceive → enrich → score → **decide the
  path**. Motions are pluggable behind the shared core (enrichment, CRM, scoring, trace, eval).
- **Action.** Route, draft (never sends), notify, CRM write-back, assignment. Side effects are
  explicit and traced.
- **Observe.** Every step traced (Langfuse + trace store). Every motion has its own eval.

## Design principles (the defensible core)

1. **LLM for perception and path; deterministic for the decision.** The LLM reads messy text
   and reasons about *what to do next*; a transparent, auditable rule engine (with a clamped
   LLM nudge) owns the tier/score. Auditability + evaluability + stability. *(JD: "agent-native
   design," "evaluation systems that make them trustworthy.")*
2. **Grounded, never fabricated.** Enriched fields carry `value / source / confidence`; the
   agent refuses or flags rather than inventing. Same discipline as Productboard Spark
   (recommendations traceable to evidence).
3. **Eval-driven.** Every motion ships with a de-gamed, held-out eval. Tuning happens on a
   separate dev split; the held-out set is write-once. *(JD: "eval-driven development.")*
4. **Build the brain, buy the body.** Built: the agent, eval, trace. Integrated: n8n, HubSpot,
   Langfuse, PDL. Direct SDK, no LangChain. *(JD: "build or buy GTM technology.")*
5. **Provider-swappable / multi-model.** Reasoning provider is configurable; cheaper models can
   own extraction, stronger models the nuanced calls. *(JD: "multi-model orchestration.")*

## Current state

| Layer | Built | Planned |
| --- | --- | --- |
| Channels | web form, webhook (n8n) | CSV/list upload, email, Slack |
| Normalize | extraction (role/intent), email validation, dedup | LLM extraction on (attribution, non-English) |
| Motion | **inbound triage** (hardening) | outbound campaign (centerpiece), lifecycle, surveys, ABX |
| Action | route, draft (no-send), CRM upsert | Slack notify, AE assignment, SLA timer |
| Observe | Langfuse + SQLite/Postgres trace, eval harness | per-motion evals, CI eval gate |

## Extensibility (how the platform grows)

- **New input mode** → write one channel adapter → normalized `Signal`. Everything downstream is unchanged.
- **New motion** → new pipeline config (trigger, tools, action) + its own held-out eval. Reuses enrichment, CRM, trace, scoring primitives.

Generalization is proven by shipping **one** second motion (outbound), not by scaffolding five.
The backlog describes the rest; we don't speculatively build it.
