# Frontend / interface audit — live drive (Chrome)

Audited the running app end to end on live providers (OpenAI + Apollo + Brave + Productboard
fixture/seeded). What follows is verified by clicking, not by reading code.

## What works (confirmed live)

- **Inbound** — Form / Email / Chat all parse + triage live with a progressive loading state
  ("Analyzing lead…" / "Still working…") and a clean confirmation (tier + routing). Presets FILL,
  don't submit. (Clay not re-run this pass — identical adapter→triage path.)
- **Outbound** — account (company) view: companies grouped by domain, contact count + tier dot,
  free-email (gmail.com) as its own one-person account, "Hot first" sort, tier + channel legends.
  Per-contact draft loads on select. Campaign modal is auto-suggested (name/keywords/persona) and
  editable. Campaign **launches, persists, and the button flips to "Update campaign"** with a
  "launched" status — no duplicate.
- **Testing** — company-grouped, contact drill-down → three-column journey (Inbound / Outbound
  Email / Account Campaign). Pipeline diagram **highlights both rows** now. Live per-person e2e
  totals. **Loop guardrail works** (≈10 LLM calls per journey vs the old 86). Inbound-highlight and
  run-grouping bugs are fixed.
- **Productboard demand fires** — Figma brief shows `demand: "Figma requested: offline mode +
  collaborative feedback boards"`, and fit gets `is_requester(+25)`.
- **Architecture** — flowing serpentine road, fits in frame (Productboard node no longer clipped),
  numbered stations, category-colored chips, the PB read + write-back loop, live stats header.

## Findings (by severity)

### HIGH
1. **Apollo enrichment is inconsistent.** Datadog produced a rich brief (description, industry,
   size, tech, funding) → fit 95. Figma produced a **thin** brief (only the Productboard demand,
   no Apollo firmographics) → fit 30. Apollo live returns data for some domains and nothing for
   others (or intermittently errors/rate-limits). This directly tanks fit + draft quality. Add
   logging on the Apollo enrich call, confirm whether figma.com returns 0 or errors, and handle it
   (retry / fall back to PDL) so briefs are consistently rich.
2. **Junk data in the live DB.** Outbound + Testing show `bigcorp.com` (28), `corp.com` (~22),
   `enterprise.com`, `tempmail.com` — load-test artifacts. The clean slate didn't hold; something
   (load tests / accumulated runs) writes junk to the live store. Purge it and ensure load-test /
   bulk runs never write to the live DB (use :memory: in tests).

### MEDIUM
3. **Campaign returns 0 Apollo targets.** The campaign launches/persists/traces correctly, but
   "0 targets via Apollo" → the similar-company search found nothing for the figma ICP keywords, so
   the Outbound campaign section is empty. Tune the org-search query (or confirm it's an Apollo
   error). Related to #1.
4. **Campaign modeled as a pseudo-contact.** In Testing the campaign appears as a fake contact
   `campaign@figma.com` in the company's contact list (alongside real contacts), *in addition* to
   the proper Account Campaign column. Keep it account-level only — don't surface it as a contact.
5. **Persistence not confirmed across restarts.** Datadog (Form) and Stripe (Email) submitted
   earlier this session didn't appear in the company list — likely wiped by a restart between
   tests. Do a deliberate submit → restart → reload check to confirm leads persist (now on Postgres).

### LOW
6. **Two tiers surface inconsistently.** A contact shows "warm" in Outbound (inbound tier) but
   "cold" in Testing (outbound fit). Label which tier is which to avoid confusion.
7. **"Drafted" badge on a cold contact with no draft.** Cold fit → no draft is correct, but the
   "drafted" badge is misleading. Show "no draft (cold)" instead.
8. **First Email submit sat ~18s with no visible feedback** before a re-submit worked. Possible
   slow-first-request or a missed loading state — watch it.
9. **Architecture nits:** the "DRAFTS 0" stat reads 0 though drafts exist; typo "write-bsck" in the
   Productboard node label.

## Top priorities
1 (Apollo consistency) and 2 (junk data) are the two that visibly hurt the demo — a thin brief
makes the whole pipeline look weak, and the junk companies make it look unfinished. 3 and 4 finish
the campaign story.
