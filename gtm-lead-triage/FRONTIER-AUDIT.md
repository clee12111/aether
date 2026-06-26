# FRONTIER AUDIT — GTM Lead-Triage
**Date:** 2026-06-26  
**Auditor:** Claude (pre-implementation audit)  
**Scope:** Three honesty gaps per FRONTIER.md  
**Verdict:** ALL THREE GAPS OPEN. Current code is theater on enrichment, scripted
on agency, and teaching-to-the-test on eval.

---

## Gap 1: Input Extraction — ENTIRELY MISSING

**Current state:** There is no extraction step. The system requires pre-parsed
fields and will not function without them.

| Check | Status | Evidence |
|-------|--------|----------|
| `POST /triage` accepts `raw_text` | MISSING | `api.py:54-63` — `TriageRequest` has only `email, name, company, message, source`. No `raw_text` field. |
| Raw email body → `Lead` extraction tests | MISSING | No extraction logic exists anywhere. |
| Email-only input → valid `Lead` | FAILS | `api.py:55` — `email` is `Field(...)` (required), but `lead.py:5` makes `email` required too. Submitting email-only technically works (other fields default to `""`), but there's no extraction or inference — the empty fields stay empty and flow through as-is. |
| Extraction confidence + field sources | MISSING | No extraction step exists to produce these. |
| Malformed input → explicit error | PARTIAL | Pydantic validation on `TriageRequest` catches type errors, but there's no semantic validation (e.g., detecting a raw email body was shoved into the `message` field). |

**Honest assessment:** This isn't a gap — it's an absence. The system assumes a
frontend or webhook has already parsed the inbound into fields. That assumption
is false for real GTM inbound (raw emails, free-text form boxes, webhook payloads
from tools like n8n/Zapier that pass the full body).

---

## Gap 2: Enrichment — FAKE

**Current state:** `enrich_lead.py` is a keyword matcher pretending to be an
enrichment service. It has no external data source.

| Check | Status | Evidence |
|-------|--------|----------|
| `EnrichmentProvider` ABC exists | MISSING | No such interface. `EnrichLeadTool` (line 92) does everything inline. |
| PDL or any external API call | MISSING | Zero HTTP calls in `enrich_lead.py`. No `httpx`, no `requests`, no network I/O. |
| Email validity (MX/DNS) | MISSING | No DNS lookup. `is_business_email` (line 108) checks domain against a 8-item `FREE_DOMAINS` set — that's it. A typo'd domain like `@gmial.com` passes as "business." |
| Invalid email → short-circuit | MISSING | Invalid emails flow through the full pipeline. No short-circuit. |
| PDL response → Pydantic model | MISSING | No PDL call exists. |
| Company-website fallback | MISSING | No HTTP fetch of any kind. |
| Rate-limit guard | MISSING | No rate limiting (no external calls to rate-limit). |
| Response cache | MISSING | No caching. Each call re-runs the same regex. |
| Regex demoted to MockProvider | N/A | Regex is the *only* provider. Not demoted because nothing replaces it. |

**What's specifically fake:**

1. **Industry inference** (`enrich_lead.py:24-45`): A list of 19 keyword→industry
   mappings. "fintech" → financial_services, "cloud" → technology. If the company
   name doesn't contain one of these words, industry = "unknown". A company named
   "Stripe" would be "unknown."

2. **Company size inference** (`enrich_lead.py:47-51`): "global" or "international"
   in the name → enterprise. "startup" or "llc" → smb. A 50,000-person company
   without these words in its name → "unknown."

3. **Seniority inference** (`enrich_lead.py:53-58`): Keyword match on the `name`
   field. Works when the user types "Julia Martinez, VP of Sales" but fails if
   they type "Julia Martinez" and their title is elsewhere (like in the email
   signature of a raw email body).

4. **Confidence score** (`enrich_lead.py:146-154`): `0.5 + 0.2*(business) +
   0.1*(industry known) + 0.1*(size known) + 0.1*(seniority known)`. This is a
   count of how many regex matches fired, dressed up as a probability. A lead
   with a business email and "fintech" in the company name gets 0.8 confidence
   regardless of whether that data is correct.

5. **LLM fallback** (`enrich_lead.py:124-144`): When `provider="openai"`, unknown
   fields are sent to GPT for guessing. This is still guessing — the LLM has no
   access to real firmographic data. It's a more expensive regex.

**Honest assessment:** This is the biggest honesty gap. The tool is named
"enrich_lead" but it doesn't enrich anything. It pattern-matches on strings the
user already provided and returns them with a fabricated confidence score. The
"source" field says `"regex"` or `"regex+llm"` — at least that's honest — but
the confidence score implies quality that doesn't exist.

---

## Gap 3: Agency + Eval — SCRIPTED AND GAMED

### 3a. The loop is scripted

| Check | Status | Evidence |
|-------|--------|----------|
| System prompt has no numbered workflow | FAILS | `loop_agent.py:29-35` — explicit numbered WORKFLOW: 1→2→3→4→5. The agent is told what to do in what order. |
| ≥ 4 distinct trace shapes | FAILS | At most 2 shapes exist: the full sequence (lookup→enrich→score→draft→final) and the skip-draft variant (lookup→enrich→score→final for cold/disqualified). The "skip enrichment on CRM hit" branch is a third shape in theory but only fires for the one CRM-seeded test case. |
| Invalid email terminates in ≤ 2 steps | FAILS | No invalid-email detection exists. An invalid email runs the full 4-5 step sequence. |
| Low-confidence enrichment → different path | FAILS | The agent never inspects enrichment confidence. The prompt doesn't mention confidence as a decision factor. Whatever enrichment returns, the agent proceeds to `score_lead`. |

**What's specifically scripted:**

The system prompt (`loop_agent.py:27-68`) is a disguised state machine:
```
1. crm_lookup — always first
2. enrich_lead — SKIP if CRM complete
3. score_lead — always
4. draft_outreach — ONLY for hot/warm
5. Finalize
```

The model is not "reasoning" — it's following instructions. The `_inject_context`
function (`loop_agent.py:120-174`) further cements this by auto-filling tool
arguments from prior steps, removing even the need for the model to reference
its own observations.

### 3b. The eval is gamed

| Check | Status | Evidence |
|-------|--------|----------|
| ≥ 30 leads, ≥ 10 independently sourced | FAILS | 32 leads total (22 golden + 10 holdout), but ZERO are independently sourced. All were written by the system author. |
| Per-tier precision/recall | MISSING | `run_eval.py` reports only aggregate accuracy (correct/total). No per-tier breakdown. |
| False-hot vs. false-cold separation | MISSING | Not tracked. |
| ≥ 5 adversarial/messy leads | PARTIAL | There's 1 prompt-injection case (`cases.py:238`), 1 foreign-language case (`cases.py:299`), 1 empty-message VP case (`cases.py:224`). That's 3, and they're not truly adversarial — they're well-structured with clean fields. No typos, no missing emails, no mixed-language with broken encoding, no real-world messiness. |
| Trace-shape diversity assertion | MISSING | No assertion on trace shapes in any eval runner. |

**What's specifically gamed:**

1. **Leads designed to satisfy rules:** Every lead in `cases.py` and `holdout.py`
   has perfectly formatted fields. Company names contain industry keywords
   ("Fintech", "Healthcare", "Cloud Tech") that map exactly to the enrichment
   regex. Seniority is always in the name field ("VP of Sales", "CTO", "Manager").
   These leads were written to be parseable by the regex enrichment, not to
   represent real-world inbound.

2. **Holdout is not independent:** `holdout.py:1-7` says "written AFTER the 4
   rules were finalized" — but by the same person who wrote the rules. The leads
   follow the same patterns: clean email + clear title + industry keyword in
   company name + intent keyword in message. This is not a holdout; it's more
   training data.

3. **Mock-only CI gate:** `run_eval.py` runs with `provider="mock"`, which means
   the LLM loop agent uses mock responses (not real LLM calls). The eval tests
   the *rules*, not the *agent*. The agent's ability to reason, adapt, or handle
   unexpected inputs is never tested.

---

## Summary Verdict

| Gap | Status | Severity |
|-----|--------|----------|
| 1. Input Extraction | ENTIRELY MISSING | Medium — can be added without disrupting existing code |
| 2. Real Enrichment | FAKE — keyword guessing, no external data | High — core value proposition is fabricated |
| 3a. Agentic Loop | SCRIPTED — numbered state machine in prompt | High — "agentic" claim is false |
| 3b. Eval Quality | GAMED — author-written, no per-tier metrics | High — can't trust accuracy numbers |

**Overall:** The system works as a deterministic scoring pipeline with an LLM
wrapper. That's not nothing — the scoring rules are thoughtful, the trace
infrastructure is real, the CRM integration works. But the three claims that
differentiate it (handles unstructured input, enriches with real data, reasons
agentically) are all false. The enrichment is the most dishonest: the tool is
named for a capability it doesn't have.

---

## Proposed Phase Plan

### Phase A: Enrichment Provider Interface + Email Validation (foundation)
- Create `EnrichmentProvider` ABC + `MockProvider` (demote current regex).
- Implement email validity: MX/DNS check + disposable-domain blocklist.
- Wire `EnrichLeadTool` to use provider interface.
- Invalid email → short-circuit in loop agent.
- Tests: mock provider parity, invalid email short-circuit.

### Phase B: PDL Integration + Waterfall
- Implement `PDLProvider` (raw `httpx`, free tier).
- Waterfall: email validity → PDL → company-website fallback.
- Per-field source + confidence tagging.
- Response cache (by email, in-memory).
- Rate-limit guard (100/month counter).
- Tests: PDL integration (with mocked HTTP), cache behavior, rate limit.

### Phase C: Input Extraction
- Add `raw_text` field to `TriageRequest`.
- Extraction step: LLM → strict Pydantic `Lead` schema.
- Extraction confidence + field sources.
- Tests: raw email bodies, email-only input, malformed input.

### Phase D: De-Script the Loop
- Rewrite system prompt: tools + decision criteria, no numbered sequence.
- Add branching logic: invalid email → short-circuit, low-confidence → re-enrich,
  CRM hit → skip enrich, ambiguous intent → different path.
- Remove or simplify `_inject_context` — let the agent construct its own args.
- Tests: trace-shape diversity assertion.

### Phase E: De-Game the Eval
- Source ≥ 10 leads independently (separate LLM with no knowledge of rules, or
  anonymized real-world data).
- Add messy/adversarial cases (typos, missing fields, mixed language, conflicting
  signals).
- Per-tier precision/recall reporting.
- False-hot vs. false-cold separation.
- Trace-shape diversity check in eval runner.

### Phase F: Integration + Frontier Audit
- End-to-end test: raw text → extraction → enrichment → agentic triage → CRM update.
- Re-run frontier-audit against all FRONTIER.md checks.
- Ship or iterate.

**Dependencies:** A→B (provider interface before PDL). C is independent of A/B.
D depends on A (needs invalid-email short-circuit). E is independent but should
follow D (eval tests the new loop). F is last.

**Estimated free-tier cost:** PDL: 100 calls/month (sufficient for eval + demo).
OpenAI: extraction step adds ~1 LLM call per lead. Within existing daily cap.
