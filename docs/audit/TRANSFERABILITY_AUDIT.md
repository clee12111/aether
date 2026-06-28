# Transferability Audit — gtm-lead-triage

**Date:** 2026-06-28
**Scope:** Abstraction leaks, interface completeness, backend parity, hardcoded assumptions, motion coupling, swap tests

---

## 1. Abstraction Leaks

| Severity | File:Line | Leak | Fix |
|----------|-----------|------|-----|
| **Blocker** | `api.py:537` | `isinstance(_crm, SQLiteCRM)` — `/leads` returned `[]` for HubSpot | **FIXED:** removed isinstance, calls `_crm.list_contacts()` via ABC |
| Minor | `api.py:551` | `hasattr(_trace, "delete_by_email")` — method is in Protocol | **FIXED:** removed hasattr, call directly |
| Minor | `api.py:339` | `hasattr(_trace, "get_outcome_metrics")` — method is in Protocol | **FIXED:** removed hasattr |

No other isinstance/type checks against concrete backend classes found. Remaining isinstance usage is legitimate (data type checks on str/datetime/dict, internal no-op tracer).

---

## 2. Interface Completeness

### CRMStore ABC (`crm/base.py`)

| Method | In ABC | SQLiteCRM | HubSpotCRM |
|--------|--------|-----------|------------|
| `lookup` | Required | Yes | Yes |
| `upsert` | Required | Yes | Yes |
| `add_activity` | Required | Yes | Yes |
| `get_activities` | Required | Yes | Yes |
| `list_contacts` | Optional (default `[]`) | Yes | **ADDED** |
| `delete_contact` | Optional (default `False`) | Yes | No (inherits default) |
| `ping` | Optional (default `True`) | Yes | No (inherits default) |
| `close` | Optional (no-op) | Yes | Yes |

**Fixed:** `list_contacts()` added to ABC with default `[]`. HubSpotCRM now implements it via HubSpot v3 search (filter: contacts with `gtm_tier` set, sorted by last-modified).

**Remaining gap:** `delete_contact()` not implemented in HubSpotCRM (returns `False`). Documented in COMPLIANCE.md. Low priority — manual deletion via HubSpot dashboard suffices for demo.

### TraceStoreProtocol (`trace/base.py`)

**Full parity.** Both SQLite TraceStore and PostgresTraceStore implement all 15 protocol methods identically.

### EnrichmentProvider ABC (`enrichment/base.py`)

| Method | In ABC | PDLProvider | WaterfallProvider | FixtureProvider |
|--------|--------|-------------|-------------------|-----------------|
| `enrich` | Required | Yes | Yes | Yes |
| `close` | **Not in ABC** | Yes | Yes | No |

**Gap:** `close()` exists on PDL/Waterfall but not in the ABC. Minor — callers handle it ad-hoc.

---

## 3. Hardcoded Assumptions

### Blocker / Major

| Item | Location | Assessment |
|------|----------|------------|
| LLM provider dispatch | `llm_client.py:207-211` | `if/elif` for "mock"/"openai" only. Adding Anthropic requires modifying 8+ files (llm_client, score_lead, enrich_lead, extraction, signals, waterfall). No adapter/factory pattern. |
| Model defaults scattered | 8 files hardcode `"gpt-4o-mini"` | `GTM_MODEL` env var exists but doesn't cascade to enrichment extractors (`waterfall.py:118`, `extraction.py:267`, `signals.py:273`). |

### Minor (acceptable for demo, document for production)

| Item | Location | Assessment |
|------|----------|------------|
| Tier thresholds | `score_lead.py:36-40` | `70/45/20` hardcoded. Should be config for A/B testing. |
| Free email cap | `score_lead.py:43` | `69` hardcoded. |
| Max loop steps | `loop_agent.py:76` | `10` hardcoded. |
| Confidence gate | `loop_agent.py:79` | `0.50` hardcoded. |
| HubSpot property names | `hubspot_crm.py:30-45` | `gtm_tier`, `gtm_score`, etc. Not configurable. Acceptable — these are created by the setup script. |
| PDL endpoint URL | `pdl_provider.py:26` | Hardcoded. No override env var. |
| Cost estimation pricing | `store.py:127`, `pg_store.py:197` | Hardcoded gpt-4o-mini pricing. |

---

## 4. Motion Coupling — How Baked-In Is "Inbound"

### Reusable (motion-agnostic)

- **CRM layer** — lookup/upsert/activities are generic
- **Enrichment layer** — email → firmographics works for any lead source
- **Scoring rules** — point system based on signals, not source
- **Trace store** — event recording is motion-agnostic
- **Eval harness** — tier/route assertions work for any motion

### Inbound-specific (must change for outbound)

| Component | Coupling | What changes for outbound |
|-----------|----------|--------------------------|
| System prompt (`loop_agent.py:34-74`) | HIGH | Rewrite: "Given a target from a list" not "Given a new lead" |
| `draft_outreach` templates (`draft_outreach.py:6-41`) | HIGH | "Thanks for reaching out" → cold outreach templates |
| Pre-signal extraction (`loop_agent.py:196-225`) | MEDIUM | Analyzes lead's *message* for intent — no message in outbound targets |
| Default source (`api.py:71`) | LOW | `default="inbound_form"` → parameterize |
| Short-circuit opt-out (`loop_agent.py:424-442`) | LOW | Checks message for opt-out — outbound would check TCPA/suppression list |

### What must change to add outbound

1. **New system prompt** — frame as "evaluate target readiness for outreach" not "respond to inbound"
2. **New `draft_outreach` templates** — cold outreach, not response templates
3. **New pre-signal extraction** — firmographic readiness, not message intent
4. **New trigger** — CSV/list upload endpoint, not a form submission
5. **New eval** — reply-rate proxy, not tier accuracy

Everything else (enrichment, CRM, scoring, trace, eval harness structure) is reusable.

---

## 5. Swap Tests

### Salesforce CRM (instead of HubSpot)

| Change | Files |
|--------|-------|
| New adapter | `crm/salesforce_crm.py` (implements CRMStore) |
| Init branch | `api.py` — add `elif crm_backend == "salesforce"` |
| Env vars | `SALESFORCE_*` credentials |

**Friction: LOW.** CRMStore ABC covers the full contract. One new file + one env var branch.

### Apollo Enrichment (instead of PDL)

| Change | Files |
|--------|-------|
| New adapter | `enrichment/apollo_provider.py` (implements EnrichmentProvider) |
| Init branch | `api.py` — add `elif enrichment_backend == "apollo"` |
| Schema mapping | Apollo response → `EnrichmentResult` (different field names) |

**Friction: LOW-MODERATE.** EnrichmentProvider ABC is clean. Schema mapping is per-provider work.

### Anthropic LLM (instead of OpenAI)

| Change | Files |
|--------|-------|
| Provider dispatch | `llm_client.py` — add `elif provider == "anthropic"` branch |
| All LLM call sites | `score_lead.py`, `enrich_lead.py`, `extraction.py`, `signals.py`, `waterfall.py` |
| Model defaults | 8 files hardcode `"gpt-4o-mini"` |
| Cost estimation | `store.py:127`, `pg_store.py:197` |

**Friction: LOW (fixed in Phase N2).** `LLMProvider` ABC with
`OpenAIProvider`, `MockProvider`, `AnthropicProvider`. All calls route through
`chat()` → `LLMProvider.chat()`. Zero direct vendor SDK imports outside
`llm_provider.py`. Swap: `pip install anthropic` + `GTM_PROVIDER=anthropic` +
`ANTHROPIC_API_KEY`. One adapter + one env var.

---

## 6. Prioritized Remaining Leaks

| Priority | Item | Effort |
|----------|------|--------|
| ~~1~~ | ~~Extract `LLMProvider` interface~~ | **DONE (Phase N2)** |
| ~~2~~ | ~~Cascade `GTM_MODEL` to enrichment extractors~~ | **DONE (Phase N2)** |
| 3 | Add `close()` to EnrichmentProvider ABC | Trivial |
| 4 | Make tier thresholds configurable (env var or config file) | Low |
| ~~5~~ | ~~Add `delete_contact()` to HubSpotCRM~~ | **DONE (Phase N2)** |
| 6 | Make cost estimation pricing configurable per provider | Trivial |

---

## Fixed in This Commit

- `CRMStore.list_contacts()` added to ABC (optional, default `[]`)
- `HubSpotCRM.list_contacts()` implemented (HubSpot v3 search, filter by `gtm_tier` exists)
- `/leads` endpoint: `isinstance(_crm, SQLiteCRM)` removed → calls `_crm.list_contacts()` for any backend
- `hasattr()` guards on protocol methods removed (2 sites in `api.py`)
- 8 new tests: HubSpot list_contacts (3), SQLite list_contacts (3), ABC contract (2)
