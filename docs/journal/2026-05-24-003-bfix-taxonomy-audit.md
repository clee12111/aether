# 2026-05-24-003 — B-fix experiment, failure taxonomy, artifact re-audit

## WORKED ON
B-fix experiment (temporal alignment + calculation completion prompt insertion into
_SYSTEM_PROMPT), full failure taxonomy of the 49 baseline misses, adversarial
artifact re-audit of 17 candidate artifact records.

Note: chunk-size sweep (512/50) referenced in decisions below was conducted in a
prior session; this entry records the standing decision.

---

## DECISIONS

**B-fix REVERTED.** Net −3 (10 recovered, 13 regressed). Two diagnosed mechanisms:
1. The "CALCULATION COMPLETION" clause caused premature is_final without write_report
   — n_max_steps dropped 8→2, producing 5 new STOP records not in the baseline.
2. The "TEMPORAL AND COLUMN ALIGNMENT" clause broke multi-row aggregation by
   over-narrowing the agent's attention to a single column/period, causing regressions
   on questions requiring sums or averages across rows.

Baseline 75.5% stands. Conclusion: a system-prompt change broadcasts to all 200
records simultaneously. Recovery on the targeted few was outweighed by disruption to
the many. The same asymmetry appeared in the earlier RagForensics reranking result —
a targeted fix that looked correct in isolation measured as net-negative across the
full distribution.

**Chunk size 512/50 ADOPTED as a latency optimisation only.** −33% query latency,
recall flat (R@5 0.845→0.850, within noise). This is NOT an accuracy change. Recall
was confirmed before adopting. The intuition that ingestion was the weak link was
disproven three separate ways by the session's own measurements (see below).

**Failure taxonomy of 49 baseline misses (final buckets):**

| Bucket | n | Notes |
|--------|---|-------|
| FORMULA | 12 | Right data, wrong formula structure — sign errors, missing final arithmetic, wrong denominator |
| ARTIFACT-STRONG | 11 | Gold demonstrably answers a different question; adversarially audited |
| ROW | 9 | Right table, wrong row/column/year selected |
| STOP | 6 | Model had data; terminated without write_report or API error |
| UNKNOWN | 5 | Gold not reconstructable from ingested values; cause undetermined |
| PARSING | 5 | Correct value genuinely absent from retrieved context |
| AMBIGUOUS | 1 | test_79: "how much greater" = ratio or difference; both defensible |

---

## MEASUREMENTS

**Raw score:** 151/200 = 75.5% (Number-Match v2, confirmed with rescore script).

**Adjusted score (excluding 11 ARTIFACT-STRONG records):** 151/189 = 79.9%.
test_124 (non-standard CAGR formula) excluded from the artifact count and kept in
denominator — the standard CAGR argument is strong but the question wording is loose
enough that a hostile reviewer could dispute it. Erring on the side of not inflating.

**Three independent diagnostic passes, consistent conclusion:** data reaches the
model; failures are downstream of retrieval and parsing quality.
- 5-record deep inspection: 4 of 5 had correct values legibly present; 1 (test_108)
  had a genuine retrieval miss (wrong table fetched).
- 52-record recovered/regressed diff (bfix vs baseline): confirmed mechanisms, showed
  n_max_steps regression pattern independently.
- 49-record full bucketing: PARSING = 5 records. These are retrieval-recall tail
  (correct values in unfetched document sections, not garbled by the parser). Expected
  return from parser investment on this benchmark ≈ 0.

---

## WHAT SURPRISED ME

**The "obvious" prompt fix measured wrong.** The B-fix addressed the two largest
failure buckets (FORMULA: 12, ROW: 9) with guidance that was precisely targeted at
the observed failure modes. It recovered 10 records. It regressed 13. The recovered/
regressed diff was essential — without it, a before/after score comparison alone would
have shown −1.5pp and no explanation. The diff revealed the mechanism (STOP
regressions, aggregation regressions) that made the fix net-negative. Lesson:
system-prompt surgery requires a record-level diff, not just a score delta.

**Chunk size moved latency but not recall.** The prior conviction that weak ingestion
was limiting accuracy held up to zero empirical support across three measurement
passes. R@5 at 0.86 is not the bottleneck. The 49-miss taxonomy confirms it: only 5
PARSING failures, all in retrieval tail, none attributable to chunk quality.

---

## NEXT

- **UI rebuild (own session):** ui/app.py is wired to the old one-shot run() path with
  hardcoded stats and a stale trace shape. Needs full restructure onto run_agentic()
  with live reasoning traces surfaced. Scope as a standalone session before any demo.

- **OPTIONAL engine fix — deterministic write_report guard:** reject is_final in the
  loop executor if write_report has never been called in the current run. Recovers ~4
  STOP records with no regression risk (code constraint, not prompt nudge). Low effort,
  low risk; do before the next eval run.

- **Variance re-run of baseline:** pending 2.5M/day cap reset. The B-fix variance run
  is moot — fix reverted. Re-run the baseline (same config) to put error bars on the
  75.5% headline. Budget: 200 × 8,500 ≈ 1.7M tokens, fits in one day.

---

## GUARD RUN (appended same session)

**write_report guard added** (loop-level code, not prompt). Run at 512/50 config.
Result: 154/200 observed, forced_stop_no_write_report fired **0 times**.

The guard recovered nothing because no record skipped write_report this run — the
baseline STOP records did not reproduce as STOPs (model nondeterminism on borderline
records). The +3 vs baseline is within the documented ±2-3pp variance envelope, NOT
a real improvement. Guard retained as correct insurance (it will catch the omission
when it occurs) but it is not the source of the delta.

**Honest score remains 75.5% raw / 79.9% benchmark-fair; 154 is a high-variance
single observation, not a new headline.**

**DECISION: Failure causation question closed.** Four convergent analyses all agree —
data reaches the model, failures are downstream reasoning/formula, parsing ≈ 0 value:
1. 5-record deep inspection
2. 52-record causal split (bfix recovered/regressed diff)
3. 49-record adversarial bucketing
4. 17-record artifact re-audit

Any further audit is confirmation, not new evidence.

**NEXT:**
- **UI rebuild (ui/app.py on run_agentic, live traces, drop hardcoded stats) — own session.**
- OPTIONAL: 4000-char truncation spot-check on the 5 PARSING/UNKNOWN records if a
  quick win is wanted, but not expected to move the number.

---

## 5th-PASS BOTTOM-UP AUDIT (appended)

AUDIT (5th-pass bottom-up): produced one real finding and one false one.

**REAL:** retrieval is non-deterministic — RRF tie-break in `retriever.py:468` uses
`sorted()` with no secondary key, so ties break arbitrarily run-to-run. This is a
genuine instrument bug; it explains part of the observed ±2-3pp eval variance
previously attributed to the model. FIX: add secondary sort key (score, then
chunk_id) for deterministic ordering.

**FALSE:** the audit's "33% RETRIEVAL" headline is a formula-matcher artifact. Without
FinQA's `program` field (dropped by T2-RAGBench), the matcher reverse-engineers
operands by finding ANY source-number pair producing the gold value, and hand-checking
showed 2 of 4 sampled matches were coincidental (matched a year as an operand; matched
wrong values producing a similar ratio). The audit's OWN conclusion: RETRIEVAL vs
SELECTION cannot be cleanly split without the program field. Only FORMULA (8) and
INCOMPLETE (2) are matcher-independent and trustworthy; the remaining 41 are an
unsplittable generation+retrieval blob.

**VERDICT:** failure causation unchanged from the hand-done adversarial 49-bucket
analysis, which remains the most reliable classification (it read the model's actual
answers against the actual tables rather than guessing operands). Five analyses have
now converged. Causation question CLOSED — the program-field gap is a hard floor on
attribution precision; further audits cannot beat it.

**SCORE (final):** 75.5% raw / 79.5% benchmark-fair (10 defensible artifact
exclusions). Guard run observed 154 within variance. write_report guard + RRF
determinism fix are correctness improvements that do not materially move the headline.

**NEXT:** UI rebuild, fresh session.
