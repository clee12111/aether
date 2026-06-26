# DECISION.md — GTM Lead-Triage: Decision Log

## 2026-06-26 — Enrichment Provider + Honesty-Gap Scope

### Context
Three honesty gaps identified in the current build:
1. Input requires pre-parsed structured fields; real inbound is unstructured.
2. `enrich_lead` guesses via regex, doesn't call any external data source.
3. The loop follows a fixed script; the eval was written to satisfy the rules.

### Decision: PDL as default enrichment provider

**Provider:** People Data Labs (PDL) Person Enrichment API  
**Tier:** Free dev tier (100 calls/month, no credit card required)  
**Integration:** Raw REST via `httpx` — no vendor SDK dependency  
**Interface:** Swappable `EnrichmentProvider` ABC (same pattern as `CRMStore`)  
**Default implementation:** `PDLProvider`  
**Mock implementation:** Current regex logic demoted to `MockProvider` for CI

### Waterfall (zero-cost-first)
```
email arrives
  → MX/DNS validity check (free, stdlib)
  → disposable-domain blocklist check (free, static list)
  → INVALID? → short-circuit to disqualified, no enrichment
  → PDL Person Enrichment (100/month free tier)
  → PDL MISS? → company-website fetch (httpx) + LLM read for basic firmographics
  → cache result by email (in-memory, per-session)
```

### Why PDL
- Free dev tier with no credit card — matches "free-tier keys only" constraint.
- Person Enrichment API returns industry, company size, seniority, title — the
  exact fields the current mock guesses at.
- Raw REST (single POST endpoint) — no SDK, no dependency.
- Swappable: the `EnrichmentProvider` interface means Clearbit, Apollo, or any
  other provider can slot in without changing the tool.

### What would need a paid plan
- PDL: > 100 calls/month. For demo/eval purposes, 100 is sufficient.
- Clearbit: no free tier at all. Ruled out as default.
- Apollo: free tier exists but rate limits are stricter. Viable as alternate
  implementation.

### Scope for this build phase
All three gaps are in scope. No implementation this pass — bar set in FRONTIER.md,
audit produced, phase plan proposed. Build starts only after green-light.

### Constraints
- No LangChain / LangGraph / CrewAI.
- Enrichment behind swappable interface (EnrichmentProvider ABC).
- Free-tier keys only.
- Deterministic executor boundary preserved: enrichment provider calls are in the
  tool (like answer_from_context in Aether), not in the executor.
