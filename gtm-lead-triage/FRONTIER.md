# FRONTIER.md — GTM Lead-Triage: Frontier Bar

## Purpose
This file defines the explicit, falsifiable bar for "frontier-grade" work on
three identified honesty gaps. Each gap has: what frontier means, what the check
is, and what "median" looks like (so we can detect it).

---

## Gap 1: Input Extraction — Structured intake from unstructured inbound

### The problem
The current system requires `{email, name, company, message, source}` as
pre-parsed fields (see `Lead` model in `gtm_triage/models/lead.py:4-9` and
`TriageRequest` in `gtm_triage/api.py:54-63`). Real inbound is messy: a raw
email body, a free-text message box, sometimes just an email address with no
other fields.

### Frontier bar
1. **An extraction step exists** — an LLM call (or regex cascade + LLM fallback)
   that accepts raw, unstructured text and emits a strict Pydantic `Lead` model.
2. **Minimal-input acceptance** — the system produces a valid `Lead` from:
   - Just an email address (all other fields empty/inferred)
   - A raw email body (From/Subject/Body — fields extracted, not pre-parsed)
   - A free-text message box with no structured fields
3. **Strict schema validation** — extraction output is validated against the
   Pydantic model before entering the triage loop. Malformed extraction fails
   loudly, never silently passes garbage downstream.
4. **Extraction confidence** — the extraction step returns a confidence score
   and flags which fields were inferred vs. explicitly stated.

### Falsifiable check
- [ ] `POST /triage` accepts a `raw_text` field (alternative to structured fields).
      When `raw_text` is provided, structured fields are ignored and the
      extraction step runs.
- [ ] Unit tests: 5+ cases of raw email bodies → correct `Lead` extraction.
- [ ] Unit tests: email-only input → valid `Lead` with empty/inferred fields.
- [ ] Extraction output includes `extraction_confidence` and `field_sources` dict.
- [ ] A malformed/unparseable input returns an explicit extraction error, not a
      silent default.

### Median fallback (what to avoid)
- Requiring all fields pre-parsed (current state).
- A regex-only extractor that handles 2-3 formats but breaks on real email bodies.
- An LLM call with no schema validation (freeform dict, not Pydantic).

---

## Gap 2: Real Enrichment — Actual firmographic data, not guessing

### The problem
`enrich_lead.py` does not enrich — it *guesses* industry/size/seniority from
keyword matching on domain + message text (lines 24-58, 66-89). The confidence
score (lines 146-154) is fabricated: it's a sum of booleans, not a measure of
data quality. There is no external data source, no email validation, no real
firmographics.

### Frontier bar
1. **Real external provider** — at least one external enrichment API is called.
   Default: People Data Labs (PDL) Person Enrichment API (free dev tier, 100
   calls/month, raw REST via `httpx`). Provider is swappable behind an interface
   (like `CRMStore` is for CRM backends).
2. **Zero-cost waterfall** — before hitting PDL (rate-limited), run free checks:
   - **Email validity**: MX/DNS lookup + disposable-domain blocklist. Invalid
     email → short-circuit to disqualified, no enrichment needed.
   - **PDL call**: on valid business email, call PDL Person Enrichment.
   - **Fallback on miss**: if PDL returns no match, fetch company website
     (domain → homepage) + LLM read for basic firmographics.
3. **Source + confidence tagging** — every enrichment field carries its source
   (`pdl`, `dns`, `llm_fallback`, `regex`, `crm`) and a per-field confidence.
   The overall confidence is derived from source quality, not boolean sums.
4. **Swappable provider interface** — enrichment provider is behind an ABC
   (`EnrichmentProvider`) with `enrich(email, name, company) -> EnrichmentResult`.
   PDL is the default implementation; others (Clearbit, Apollo, etc.) slot in
   without changing the tool.
5. **Free-tier only** — PDL free dev tier (100/month). No paid plan required.
   Rate limiting and caching (by email) to stay within quota.

### Falsifiable check
- [ ] `EnrichmentProvider` ABC exists with at least two implementations:
      `PDLProvider` and `MockProvider`.
- [ ] PDL call uses raw `httpx`, not a vendor SDK.
- [ ] Email validity check (MX lookup + disposable-domain list) runs before PDL.
- [ ] Invalid email → short-circuit, no PDL call made (unit test).
- [ ] PDL response is parsed into the `Enrichment` Pydantic model with
      `source="pdl"` per field.
- [ ] On PDL miss, company-website fetch + LLM read runs as fallback (integration
      test with mock HTTP).
- [ ] Rate-limit guard: after 100 calls, PDL is skipped and fallback runs.
- [ ] Response cache: same email within a session doesn't re-call PDL.
- [ ] Old regex logic is demoted to `MockProvider` (kept for CI/deterministic
      tests only).

### Median fallback (what to avoid)
- Keeping the regex guesser as the "real" enrichment and calling it done.
- Adding an API call but with no fallback (PDL miss → empty data).
- Hard-coding PDL with no provider interface.
- Using a paid-tier API and shipping a cost surprise.

---

## Gap 3: Genuinely Agentic Loop + De-Gamed Eval

### The problem (two sub-problems)

**3a. The loop is scripted, not agentic.** The system prompt in
`loop_agent.py:27-68` prescribes a fixed sequence: `crm_lookup → enrich_lead →
score_lead → draft_outreach → finalize`. The "SKIP if CRM has complete profile"
and "ONLY for hot/warm" are the only branches, and they're hardcoded in the
prompt. The trace never shows genuinely different shapes for different leads.
This is a state machine wearing an agent costume.

**3b. The eval is teaching-to-the-test.** Both `cases.py` (22 leads) and
`holdout.py` (10 leads) were authored by the same person who wrote the scoring
rules. The leads are clean, well-structured, and designed to satisfy the rule
set. The holdout set (`holdout.py:1-7` — "written AFTER the 4 rules were
finalized") is still in the same style. There are no genuinely adversarial,
messy, or ambiguous cases that would stress-test the system.

### Frontier bar

**3a. Genuinely branching loop:**
1. **Path varies observably** — the agent's trace shows at least 4 distinct
   shapes across the test set:
   - CRM hit with complete profile → skip enrichment entirely
   - Invalid email → short-circuit to disqualified (no enrichment, no scoring)
   - Low-confidence or conflicting enrichment → re-enrich or dig deeper
   - Ambiguous intent → different tool sequence than clear intent
2. **Observations drive decisions** — the agent's reasoning at each step
   references the *output* of the prior step, not just the plan. If enrichment
   returns low confidence, the agent should react (retry, fallback, flag).
3. **No hardcoded sequence in prompt** — the system prompt describes available
   tools and decision criteria, not a numbered workflow. The agent discovers
   the path.
4. **Short-circuit on garbage** — invalid email or clear spam terminates in
   ≤ 2 steps, not 4-5.

**3b. De-gamed eval:**
1. **Independent sourcing** — at least 10 test leads are sourced independently
   of the scoring rubric (e.g., from real-world form submissions, anonymized
   CRM data, or generated by a separate person/model with no knowledge of the
   rules).
2. **Messy/adversarial cases** — the test set includes:
   - Typos, misspellings, mixed-language text
   - Missing fields (email only, no name/company)
   - Prompt injection attempts (already have one — need more variety)
   - Conflicting signals (C-level + free email + spam-like message)
   - Edge cases the rules don't cover (government, nonprofit, .edu)
3. **Per-tier precision/recall** — eval reports precision and recall for each
   tier (hot/warm/cold/disqualified), not just overall accuracy. Small-N caveat
   is stated explicitly.
4. **False-hot vs. false-cold separation** — the eval distinguishes between
   "scored too high" (false-hot: wasted AE time) and "scored too low"
   (false-cold: lost deal). These have asymmetric business costs and must be
   reported separately.
5. **Trace-shape assertion** — the eval checks that traces are not all the same
   shape. At least 3 distinct tool-call sequences must appear across the test set.

### Falsifiable check
- [ ] System prompt does NOT contain a numbered workflow (1-2-3-4-5).
- [ ] Trace inspector shows ≥ 4 distinct tool-call sequences across the test set.
- [ ] Invalid-email lead terminates in ≤ 2 steps.
- [ ] Low-confidence enrichment triggers a different next-step than high-confidence.
- [ ] Eval test set has ≥ 30 leads, of which ≥ 10 are independently sourced.
- [ ] Eval output includes per-tier precision/recall table.
- [ ] Eval output separates false-hot from false-cold counts.
- [ ] ≥ 5 adversarial/messy leads in the test set (typos, missing fields, mixed
      language, conflicting signals).
- [ ] Trace-shape diversity assertion in the eval runner.

### Median fallback (what to avoid)
- Adding one more `if` branch and calling the loop "agentic."
- Keeping the numbered prompt and claiming the model "chooses" the sequence.
- Writing more leads in the same clean style and calling them "holdout."
- Reporting only aggregate accuracy without per-tier breakdown.

---

## Gap 3a (Phase D): De-Scripted Loop — Signal-Driven Branching

### The problem
The loop agent follows a fixed sequence (`crm_lookup → enrich_lead → score_lead
→ draft_outreach → finalize`) regardless of what it observes. The mock LLM
client (`llm_client.py:77-149`) is a literal if/elif chain. The system prompt
(`loop_agent.py:27-68`) prescribes a numbered workflow. Signals like email
validity, enrichment confidence, extraction confidence, and intent are available
but the loop never reads them.

### Frontier bar
1. **>=5 distinct, signal-justified trace shapes** — each branch driven by a
   real observation (not padding):
   - **SHORT_CIRCUIT_INVALID**: invalid/disposable email → disqualify in <=2
     steps, skip enrichment (no wasted PDL credit)
   - **SHORT_CIRCUIT_INTENT**: opt_out or legal_or_compliance intent → disqualify
     immediately after extraction, skip enrichment+scoring
   - **CRM_HIT_SKIP_ENRICH**: CRM has a complete profile → skip enrichment,
     route on existing data
   - **LOW_CONFIDENCE_GATE**: extraction or enrichment returned low-confidence
     seniority → downgrade to "unknown" before scoring rather than granting full
     points on shaky data (fixes lemonade false-hot)
   - **CLEAN_FULL_PATH**: high-confidence signals → straight through (crm →
     enrich → score → draft if warm+)

2. **Trace records which path** — `TriageResult` includes a `trace_path` label
   so the Trace Explorer and eval harness can assert shape diversity.

3. **Confidence-gating rule**: seniority/intent with confidence < 0.50 is
   downgraded to "unknown"/0 points before entering scoring. This is the
   mechanism that prevents the lemonade false-hot (third-person "our CTO shared"
   gets 0.75 confidence from extraction but should be gated when the pattern
   matches a third-person reference).

4. **No numbered workflow in system prompt** — tools + decision criteria, not
   a step-by-step recipe.

### Falsifiable check
- [ ] `TriageResult` has a `trace_path` field populated for every run.
- [ ] >= 5 distinct `trace_path` values appear across holdout_v2 + golden sets.
- [ ] `x9z@yopmail.com` (disposable) terminates in <= 2 steps with path
      SHORT_CIRCUIT_INVALID.
- [ ] `hr@nvidia.com` (opt_out intent) terminates in <= 2 steps with path
      SHORT_CIRCUIT_INTENT.
- [ ] `e.brook@lemonade.com` is NOT false-hot (confidence gate downgrades the
      third-person CTO seniority).
- [ ] Unit test: lead with seniority_confidence < 0.50 gets seniority downgraded
      to "unknown" before scoring.
- [ ] Mock CI gate (5 MOCK_LEADS) still passes — the branching logic handles
      the original test set correctly.

### Median fallback (what to avoid)
- Adding `if` branches to the mock LLM client without recording which path ran.
- Keeping the numbered system prompt and calling it "agentic."
- Hard-coding a confidence threshold that only fires on the one known false-hot.

---

## How to use this file
Build against these checks. When implementation is done, run `frontier-audit`
against each checkbox. A check is either met (with evidence: file, line, test
output) or not. No partial credit.
