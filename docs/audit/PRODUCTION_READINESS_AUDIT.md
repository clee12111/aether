# Production Readiness Audit — gtm-lead-triage

**Date:** 2026-06-27 (initial), 2026-06-27 (reconciled)
**Auditor:** Cold audit (no prior involvement in build), then verified close-out
**Scope:** Full system on `main` — correctness, security, reliability, privacy, observability, eval integrity, tests/CI
**Deployment context:** Portfolio demo on free-tier (Render), low traffic, single instance

---

## Executive Summary

The gtm-lead-triage service is architecturally sound: clean deterministic/LLM
boundary, honest eval harness, structured observability, fail-closed auth, and
comprehensive SSRF guards.

**For the actual deployment scenario** (portfolio demo, low traffic, single
instance), the system is shippable with the fixes applied in Phase M close-out.
The original audit rated findings against a 1000-RPS production context; this
reconciled version recalibrates severity for a portfolio demo.

**Reconciliation note:** The raw audit agents produced two false findings
(claimed `pg_store.py` lacks `ping()` and `delete_by_email()` — both exist at
`:344` and `:324`). These were caught during verification and never entered
this report. All remaining findings below are verified against actual code.

---

## Verified Findings (severity-calibrated for portfolio demo)

### Correctness

| # | Finding | Severity | Location | Verified |
|---|---------|----------|----------|----------|
| C1 | **Website fallback hardcoded off.** `skip_website=True` in production. Docs claim "fallback on miss." Feature is built but disabled. | Minor | `api.py:187` | TRUE |
| C2 | **PDL enrichment off by default.** `ENRICHMENT_PROVIDER` defaults to `"mock"`. Acceptable for demo — mock is the intended demo mode. | Info | `api.py:178-187` | TRUE |
| C3 | **DIG_DEEPER trace incomplete.** `_dig_deeper_enrich()` runs as a subroutine, not a traced tool call. Hidden from trace inspector. | Minor | `loop_agent.py:280-365` | TRUE |
| C4 | **No PDL quota guard.** Free-tier exhaustion silently degrades. Low risk at demo volume. | Info | `pdl_provider.py:156-220` | TRUE |
| C5 | **Injection flag detect-only.** `injection_flagged=True` set in enrichment but never consumed by scorer. Flag is dead data. | Minor | `enrich_lead.py:184`, `score_lead.py` (no read) | TRUE |

### Security

| # | Finding | Severity | Location | Verified |
|---|---------|----------|----------|----------|
| S1 | **Message text reaches scoring LLM unsanitized.** Embedded in `_SCORE_USER` template. Bounded [-10,+10] limits blast radius. For a portfolio demo (no adversarial attackers), this is acceptable. | Minor | `llm_client.py:319-323` | TRUE |
| S2 | **DNS rebinding resolve-then-fetch gap.** `resolve_and_validate()` returns safe IPs but httpx re-resolves. Requires attacker on local network — irrelevant for demo. | Info | `security.py:67-87` | TRUE |
| S3 | **Injection detection patterns incomplete.** Missing XML tags, role-play, multiline. Low impact given bounded LLM adjustment. | Info | `security.py:112-127` | TRUE |

### Reliability

| # | Finding | Severity | Location | Verified |
|---|---------|----------|----------|----------|
| R1 | **Retries defined but not wired.** `retry_with_backoff()` has zero call sites. At demo volume (single-digit requests), transient failures are rare and manually retriable. | Minor | `resilience.py:27-57` | TRUE |
| R2 | **Circuit breaker defined but not instantiated.** Same — zero call sites. At demo volume, cascade failure is not a realistic scenario. | Minor | `resilience.py:62-157` | TRUE |
| R3 | **LLM failure aborts triage.** `chat()` at `loop_agent.py:452` has no try/except. OpenAI error → 500. This IS visible during a live demo. | **Major** | `loop_agent.py:452-462` | TRUE |
| R4 | **HubSpot errors not caught at API boundary.** `raise_for_status()` propagates. If HubSpot is down during demo, triage succeeds but CRM upsert crashes with 500. | Minor | `hubspot_crm.py:84-113` | TRUE |
| R5 | **No app-level request timeout.** `asyncio.to_thread()` at `api.py:407` unbounded. Stacked timeouts (30s+15s+8s) could make a demo request hang visibly. | Minor | `api.py:407` | TRUE |

### Privacy & Compliance

| # | Finding | Severity | Location | Verified |
|---|---------|----------|----------|----------|
| P1 | **HubSpot CRM deletion is a no-op.** Base class `delete_contact()` returns False. COMPLIANCE.md updated to document this gap honestly. | Minor | `base.py:32-37`, `hubspot_crm.py` (no override) | TRUE |
| P2 | **Trace payloads contain full PII.** `run_start` writes `lead.model_dump()`. Acceptable for demo (synthetic data), but documented. | Minor | `loop_agent.py:396-401` | TRUE |
| P3 | **COMPLIANCE.md inaccuracies.** Fixed in Phase M — now documents trace PII and HubSpot deletion gap honestly. | Fixed | `COMPLIANCE.md` | FIXED |

### Observability

| # | Finding | Severity | Location | Verified |
|---|---------|----------|----------|----------|
| O1 | **Outcome loop is manual-only.** POST endpoint exists, no webhook auto-sync. Fine for demo — manual recording works. | Info | `api.py:312-359` | TRUE |

### Eval Integrity

| # | Finding | Severity | Location | Verified |
|---|---------|----------|----------|----------|
| E1 | **dev_split ↔ holdout_v2 overlap: 1 case.** `a.novak@plaid.com` in both. Same label (cold), so no signal leakage. | Info | `dev_split.py:492`, `holdout_v2.py:385` | TRUE |
| E2 | **CI gate is narrow (5 mock cases).** No borderline cases, no holdout_v2 measurement in CI. Could miss scoring regressions. | Minor | `.github/workflows/gtm-ci.yml:43-53`, `evals/run_eval.py` | TRUE |
| E3 | **Scoring rules have no unit tests.** `_score_rules()` logic only tested end-to-end. | Minor | `score_lead.py:72-211` | TRUE |

---

## Findings Removed (false or inapplicable)

| Original | Reason removed |
|----------|---------------|
| "pg_store.py lacks ping()" | FALSE — `ping()` exists at `pg_store.py:344-351` |
| "pg_store.py lacks delete_by_email()" | FALSE — exists at `pg_store.py:324-340` |
| P4 "No automated retention" | Downgraded to Info — acceptable for demo with synthetic data |
| O2 "OTel not instrumented in loop" | Downgraded to Info — OTel is optional, no-ops cleanly |
| O3 "PII log guard is convention" | Downgraded to Info — convention is tested by `test_observability.py` |
| E4 "holdout_v2 lock is social" | Downgraded to Info — git history + DECISION.md are sufficient for a demo |

---

## Severity-Calibrated Top 5 (Portfolio Demo)

### 1. Wrap agent-loop `chat()` in try/except (Major — R3)

**Why it matters for demo:** An OpenAI API hiccup during a live demo crashes the
request with a 500 — the single most visible failure mode. Enrichment already
degrades gracefully; the LLM call should too.

**Location:** `loop_agent.py:452-462`
**Fix:** Catch OpenAI exceptions, return a partial result (enrichment-only tier
from rule score, no LLM adjustment) instead of crashing.

### 2. Wire `retry_with_backoff()` on the LLM call (Minor — R1, partial)

**Why it matters for demo:** A single transient timeout on the OpenAI call
aborts the triage. One retry with 0.5s backoff would silently recover most
transient failures during a demo. The function exists, just needs one call site.

**Location:** `resilience.py:27-57` → wrap `chat()` in `loop_agent.py:452`

### 3. Consume `injection_flagged` in scorer (Minor — C5)

**Why it matters for demo:** The injection detection pipeline runs but produces a
flag that's never read. Either consume it (e.g., zero out LLM adjustment when
flagged) or remove the dead code. Currently it's theater.

**Location:** `enrich_lead.py:184` (writes), `score_lead.py` (never reads)

### 4. Add scoring-rule unit tests (Minor — E3)

**Why it matters for demo:** Scoring is the core decision logic. Without unit
tests, a threshold typo (45→54) would silently change tier boundaries and slip
through CI. The 5-case mock gate only catches gross failures.

**Location:** `score_lead.py:72-211` — needs direct `_score_rules()` tests

### 5. Enable website fallback or remove the claim (Minor — C1)

**Why it matters for demo:** ARCHITECTURE.md claims "fallback on miss" but
`skip_website=True` means it never runs. Either flip the flag or remove the
claim from docs to be honest.

**Location:** `api.py:187` (flip to `False`) or `ARCHITECTURE.md` (remove claim)

---

## Deployment Config Fixes Applied (Phase M)

### Dockerfile

| Issue | Fix |
|-------|-----|
| Ran as root | Added `adduser appuser` + `USER appuser` |
| Inline pip install (fragile, no lockfile) | Created `requirements.txt`, `COPY` + `pip install -r` |
| No healthcheck | Added `HEALTHCHECK` calling `/ready` |
| No .dockerignore | Created `.dockerignore` excluding `.env`, tests, docs, `.git` |
| Missing `psycopg_pool` | Added to `requirements.txt` |

### render.yaml

| Issue | Fix |
|-------|-----|
| `healthCheckPath: /health` (always-200 stub) | Changed to `/ready` (truthful probe, returns 503 on failure) |
| No `APP_ENV=production` (auth disabled) | Added `APP_ENV: production` |
| No `GTM_API_KEYS` (auth keys not configurable) | Added as `sync: false` secret |

### COMPLIANCE.md

| Issue | Fix |
|-------|-----|
| Claimed LLM prompts not stored | Corrected: trace payloads contain lead PII via tool args |
| Claimed deletion covers all backends | Documented HubSpot gap honestly |

---

## Dependency/CVE Audit

```
$ pip-audit -r requirements.txt
No known vulnerabilities found

$ pip-audit -r requirements-dev.txt
No known vulnerabilities found
```

**Parent project isolation confirmed:** GTM service imports zero parent-project
dependencies (no aiohttp, chromadb, langchain, rank_bm25, sentence_transformers,
or camelot). The Dockerfile copies only `gtm_triage/` — evals, tests, and docs
are excluded via `.dockerignore`.

---

## Eval Reproducibility

### Mock gate (deterministic, keyless)
```
$ python -m evals.run_eval
Score: 5/5 — All leads triaged correctly.
```

### Holdout v2 OpenAI+PDL (temp=0)
- **Locked number:** 22/35 (62.9%) — committed at `452fe4b`
- **Artifact:** `evals/results/eval_holdout_v2_openai_pdl_FINAL_LOCK.jsonl` (37 lines: 1 meta + 35 cases + 1 summary)
- **Determinism verified:** Runs 1, 2, 3 are tier-for-tier identical (23/35 = 65.7% with default extractor). FINAL_LOCK (22/35, extractor A) is a different configuration, also temp=0.
- **LLM temperature:** `temperature=0` confirmed at `llm_client.py:223`
- **PDL cassettes committed:** `enrichment/cache/pdl_cassettes.json` — synthetic data, deterministic enrichment replay

---

## What's Genuinely Good

These are not just "present" — they're well-implemented and tested:

- **Deterministic/LLM boundary** — cleaner than most production systems
- **Auth fail-closed in production** — with timing-safe comparison
- **SSRF IP validation** — covers IPv4, IPv6, mapped addresses, cloud metadata, CGNAT
- **Input validation** — field caps, body size limit, rate limiting, all with tests
- **Readiness probe** — actually calls SELECT 1, returns 503 on failure
- **Structured logging** — JSON with correlation IDs, PII-free at INFO
- **Eval harness honesty** — holdout built independently, no keyword gaming,
  false-hot rate tracked, 62.9% reported as-is
- **Idempotency** — dedup by key, tested in integration suite
- **405 tests green, 76%+ coverage, CI gate passing**
