# COMPLIANCE.md — GTM Lead-Triage: Data Protection Posture

## What data is collected

| Data category | Source | Storage location | Retention |
|---------------|--------|-----------------|-----------|
| Lead email, name, company, message | Inbound form / API | CRM (SQLite or HubSpot) | Configurable; default indefinite |
| Enrichment fields (industry, size, seniority) | PDL API, email domain | CRM record (merged) | Same as CRM record |
| Triage result (tier, route, score breakdown) | Computed | Trace store (SQLite or Postgres) | Configurable; default indefinite |
| LLM call logs (prompt lengths, token counts) | OpenAI API | Trace store | Same as trace |
| Outreach draft (subject, body) | Generated | Trace store (tool response) | Same as trace |

**Not stored:**
- Raw PDL API responses (only mapped fields persisted)
- Website HTML content (fetched transiently, not persisted)
- API keys or tokens (env-only, never in DB or logs)

**Note:** Trace events record tool-call arguments (including lead email, name,
company, and message) and tool responses. LLM prompt/response text is not stored
separately, but lead PII is present in trace payloads. The `DELETE /contacts/{email}`
endpoint removes all trace events for a given email.

## Lawful basis

**Legitimate interest** (GDPR Art. 6(1)(f)) for B2B lead enrichment and
qualification. The processing is necessary for the legitimate business
interest of qualifying inbound sales leads. Data subjects are business
contacts submitting inquiries through a commercial form.

For leads who express opt-out intent, processing stops immediately
(SHORT_CIRCUIT_INTENT → disqualified → drop).

## Data subject rights

### Right to erasure (Art. 17)

`DELETE /contacts/{email}` removes:
- CRM contact record (SQLite CRM: full deletion; **HubSpot CRM: not yet
  implemented** — base class no-op returns False)
- All CRM activities for that email (SQLite only; HubSpot: see above)
- All trace events for runs involving that email (SQLite and Postgres)
- All idempotency records for those runs (SQLite and Postgres)

The endpoint returns a confirmation with counts of deleted records.

**Known gap:** HubSpot CRM backend does not implement `delete_contact()`. If
using HubSpot, deletion must be performed manually via the HubSpot dashboard
until the API integration is completed.

### Right to access (Art. 15)

`GET /contacts/{email}` returns the full CRM record and activity timeline.
`GET /runs/{run_id}` returns the full trace for a specific triage run.

## Retention

Default: indefinite (no automatic deletion). Configurable via:
- Manual deletion: `DELETE /contacts/{email}`
- Bulk retention: implement a sweep job keyed on `created_at` timestamps

Recommended production setting: 90-day retention for trace data, indefinite
for CRM records (until deletion request).

## Sub-processors

| Provider | Data shared | Purpose | DPA required |
|----------|------------|---------|:------------:|
| **OpenAI** | Lead name, email, message (in LLM prompts) | Extraction + scoring nudge | Yes |
| **People Data Labs** | Lead email | Person/company enrichment | Yes |
| **HubSpot** (optional) | Lead email, name, company, tier, score | CRM storage | Yes |
| **Neon** (optional) | Trace events, triage results | Postgres trace storage | Yes |

## PII minimization

- Enrichment persists only the fields scoring needs (industry, company_size,
  seniority, is_business_email), not the full PDL response.
- PDL cassettes (committed for CI) contain only synthetic/fictional contacts
  at real domains — no real individuals' PII.
- LLM prompts include the lead's message (necessary for extraction) but
  responses are parsed to typed signals; raw LLM output is not persisted.

## Security measures

- API authentication (bearer token / API key) on all endpoints except /health.
- Auth fails closed in production (APP_ENV=production + no keys → 503).
- Rate limiting per key/IP.
- SSRF guard on all outbound HTTP (blocks private/loopback/link-local IPs).
- Prompt-injection detection (flag, don't block — scorer is deterministic).
- Secrets from env only; .env gitignored.
- All external calls have explicit timeouts.
