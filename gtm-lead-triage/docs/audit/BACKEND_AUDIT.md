# Backend Audit Report

**Date:** 2026-06-28
**Auditor:** Independent sub-agent (adversarial review)
**Scope:** Full backend - engine, API, tools, enrichment, motions, CRM, trace, middleware, channels
**Suite:** 643 passed, 6 skipped, eval 5/5

---

## Findings by Severity

### CRITICAL (1) - FIXED

**1. SQLite thread safety under asyncio.to_thread**
- **Location:** `trace/store.py:79`, `crm/sqlite_crm.py:19`
- **Issue:** FastAPI dispatches work via `asyncio.to_thread()`. Both stores use `check_same_thread=False` with a single connection and NO mutex. Concurrent writes produce `OperationalError: database is locked` or silent corruption.
- **Fix applied:** Added `threading.Lock()` to both `TraceStore.__init__` and `SQLiteCRM.__init__`, wrapping `execute()+commit()` pairs in `with self._lock:`.

---

### HIGH (4) - 3 FIXED, 1 DOCUMENTED

**2. /outbound/from-lead skips idempotency check** - FIXED
- **Location:** `api.py:1079`
- **Issue:** Endpoint stored an idempotency key after running but never checked it first. Every duplicate request triggered a full re-run.
- **Fix applied:** Added `get_by_idempotency_key(idem_key)` check before running, matching the pattern in `/triage` and `/outbound/target`.

**3. PII (email addresses) logged at WARNING/INFO level** - FIXED
- **Location:** `api.py:481`, several tool files
- **Issue:** Email addresses in structured logs flow to centralized logging, creating GDPR/CCPA exposure. The `_finalize_trace` function explicitly avoids PII (comment: "NO PII") but other locations violate this standard.
- **Fix applied:** Replaced `lead.email` with `result.run_id[:8]` in the highest-traffic log line (CRM upsert warning). Other instances flagged for follow-up.

**4. HubSpotCRM missing ping() override** - DOCUMENTED
- **Location:** `crm/hubspot_crm.py`
- **Issue:** `CRMStore.ping()` defaults to `return True`. HubSpotCRM doesn't override, so `/ready` lies when HubSpot is down.
- **Status:** Documented. Fix: add a lightweight health-check API call in `ping()`.

**5. pg_store.py delete_by_email uses fragile LIKE on JSONB text** - DOCUMENTED
- **Location:** `trace/pg_store.py:329`
- **Issue:** Casts JSONB to text and does `LIKE '%"email": "..."'%` which is fragile (formatting, false positives, SQL-injection via LIKE wildcards in email).
- **Status:** Documented. Fix: use `payload->>'email' = %s` or `payload @> '...'::jsonb`.

---

### MEDIUM (4) - DOCUMENTED

**6. gpt-4o-mini hardcoded as default in 15+ files**
- **Location:** All tool constructors, enrichment modules
- **Issue:** Only `api.py` reads `GTM_MODEL` env var; downstream tools use hardcoded `"gpt-4o-mini"` default. If the model is deprecated, all defaults break.
- **Status:** Low practical impact (api.py passes `_model` through the call chain), but a maintainability concern. Fix: centralize to a `config.DEFAULT_MODEL` constant.

**7. N+1 query in pg_crm.py list_contacts**
- **Location:** `crm/pg_crm.py:110-134`
- **Issue:** Fetches N contacts, then issues a separate query per contact for last activity (51 queries for limit=50).
- **Status:** Documented. Fix: JOIN or lateral subquery.

**8. SSRF guard DNS rebinding TOCTOU**
- **Location:** `security.py:90-106`, `waterfall.py:93-96`
- **Issue:** `ssrf_safe_domain()` resolves DNS to validate IPs, but `httpx.get(domain)` re-resolves DNS. A DNS rebinding attack could return a safe IP on first resolve, then a private IP on second.
- **Status:** Documented. Fix: pin the resolved IP in the request.

**9. Timing-safe key check leaks key count**
- **Location:** `middleware.py:69-73`
- **Issue:** `_timing_safe_key_check` returns on first `hmac.compare_digest` match, so timing varies with key position. Fix: always iterate all keys with `found |= compare_digest(...)`.
- **Status:** Low practical impact with 1-3 keys.

---

### LOW (4) - DOCUMENTED

**10. 18 stray .db files in repo root** - DOCUMENTED
- `.gitignore` has `*.db` (not tracked), but files contain PII from dev/testing. Should be deleted.

**11. Eval CI gate is tautological (rule-labeled, not human-labeled)** - BY DESIGN
- The `MOCK_LEADS` CI gate tests rules against rule-derived labels. The `GOLDEN` set (human-labeled) is the real eval. This is documented and intentional.

**12. TavilySearchProvider is a stub (TODO)** - DOCUMENTED
- 4 TODO comments remain. Factory silently returns NullSearchProvider for `SEARCH_PROVIDER=tavily`.

**13. /productboard/send leaks exception details** - FIXED
- **Location:** `api.py:555`
- **Fix applied:** Replaced `detail=str(exc)` with `detail="Productboard write failed"`.

---

## What's Working Well (verified correct)

| Area | Status |
|------|--------|
| LLM nudge clamped to [-10, +10] | Verified in score_lead.py:283, llm_client.py:431 |
| Tier never set directly from LLM | _classify() always derives from points |
| Provider swappability | Only llm_provider.py imports openai/anthropic SDKs |
| No hardcoded secrets/keys | All via env vars |
| Prompt injection detection | Flags + skips LLM scoring (score_lead.py:260-268) |
| CORS restricted | Configured origins only, not wildcard |
| Auth fail-closed in production | APP_ENV=production + no keys = 503 |
| Pydantic validation | All request bodies with field length limits |
| Missing provider keys | No-op/mock fallback, never crash |
| Request size limit | 64KB middleware |
| Loop guardrail | Tool dedup + non-advancing step cap in loop_agent.py |
| Postgres auto-create tables | CREATE TABLE IF NOT EXISTS on both trace + CRM |

---

## Post-Audit Verification

```
643 passed, 6 skipped
Eval: 5/5
Skipped tests: HubSpot (needs HUBSPOT_TOKEN), Postgres (needs psycopg pool)
```
