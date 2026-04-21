# Aether Evaluation Analysis

## Summary

| Suite        | Result    | Rate |
|--------------|-----------|------|
| Retrieval    | 24/25     | 96%  |
| End-to-end   | 11/15     | 73%  |

- Retrieval variance: 0.00 stdev across 5 runs. Pipeline contains no stochastic components (deterministic embedding, BM25, and cross-encoder rerank), so runs are exactly reproducible.
- End-to-end variance: Full e2e variance measured empirically in one run: suite pass rate fluctuates between 10 and 13 of 15 cases across runs due to planner SQL generation non-determinism. The reliable floor is 11/15 -- cases that pass regardless of LLM stochasticity.

---

## Retrieval Eval — 25 cases

Tests precision@5 of the hybrid retriever (Chroma dense + BM25 + flashrank reranker) across three document types: a fund capital accounts CSV, a fund agreement (TXT), and a compliance policy (TXT).

**Categories:**
- **Data queries (13):** Factual lookups against the CSV — partner names, balances, distributions, ownership percentages.
- **Policy queries (10):** Lookups against the fund agreement (5) and compliance policy (5) — caps, thresholds, retention rules.
- **Cross-document (2):** Queries whose answer requires reasoning across both the CSV data and a policy document.

### Passing cases (24)

**Data queries: 13/13**
| # | Query | Expected chunk |
|---|-------|---------------|
| 1 | Which partner has the highest distribution? | Ironwood |
| 2 | What is Ironwood's ownership percentage? | Ironwood |
| 3 | Which partners received more than $500,000 in distributions? | 960000 |
| 4 | What is Sequoia Capital's opening balance? | Sequoia |
| 5 | How much net income was allocated to Tiger Global Fund? | Tiger Global |
| 6 | What is the closing balance for Founders Fund LP? | Founders Fund |
| 7 | Which partner has a 20% ownership stake? | Founders Fund |
| 8 | What are the total contributions across all partners? | contributions |
| 10 | What is Benchmark Capital's distribution amount? | 200000 |
| 11 | Which partner has the lowest closing balance? | closing_balance |
| 12 | How many partners have ownership above 15%? | ownership_pct |
| 13 | What is Andreessen Horowitz's net income allocation? | 100000 |
| 14 | Are there any partners with zero contributions this period? | contributions |

**Policy queries — fund agreement: 5/5**
| # | Query | Expected chunk |
|---|-------|---------------|
| 16 | What is the maximum distribution percentage any single partner can receive? | twenty-five percent |
| 17 | What is the preferred return rate? | eight percent (8%) |
| 18 | What tolerance is allowed for ownership percentage totals? | 0.5% |
| 19 | Can distributions be non-pro-rata? | General Partner |
| 20 | What is the LP/GP profit sharing split? | eighty percent (80%) |

**Policy queries — compliance policy: 5/5**
| # | Query | Expected chunk |
|---|-------|---------------|
| 21 | What transaction amount triggers a flag? | $100,000 |
| 22 | What is the concentration limit for a single position? | thirty percent (30%) |
| 23 | How many days to review flagged items? | five (5) business days |
| 24 | What are the client risk profile categories? | Conservative, Moderate, Growth, and Aggressive |
| 25 | How long must records be retained? | five (5) years |

**Cross-document queries: 1/2**
| # | Query | Expected chunk | Status |
|---|-------|---------------|--------|
| 15 | Which partner received distributions disproportionate to their ownership share? | Ironwood | PASS |

### Known limitation (1)

**Case 9:** "Is there a partner whose distribution exceeds their net income allocation by more than 5x?"

- **Expected chunk:** Ironwood (from the CSV)
- **What happens:** BM25 scores the fund agreement higher because the query language ("distribution exceeds ... allocation") closely matches the agreement's legal phrasing. The dense retriever also drifts toward the agreement. The CSV chunk containing Ironwood's actual numbers gets pushed below the top-5 cutoff.
- **Why this isn't a retrieval bug:** Similarity-based retrieval optimizes for lexical and semantic overlap with the query. When one document uses the same vocabulary as the query but doesn't contain the answer, and another document contains the answer but uses different vocabulary (column headers + numbers), similarity search will prefer the wrong document. This is a known fundamental limitation of retrieval-based systems on cross-document analytical queries.
- **Possible fixes:** Query expansion (rewrite the query to explicitly mention CSV columns), HyDE (generate a hypothetical answer and retrieve against that), or a two-stage approach where the planner identifies required data sources before retrieval.

---

## End-to-End Eval — 15 cases

Tests the full pipeline: ingest documents, retrieve context, plan steps, execute with tools (LoadData, RunSQL, FlagItem, WriteReport), critique the result. Each case specifies a goal, input files, expected verdict, minimum flag count, and (optionally) a partner that must be flagged.

### Reliably passing (11)

**Single-file CSV — 7/7**
| Case ID | Goal summary | Files |
|---------|-------------|-------|
| flag_5pct_deviation | Flag partners with >5pt distribution/ownership deviation | capital accounts CSV |
| flag_10pct_deviation | Flag partners with >10pt distribution/ownership deviation | capital accounts CSV |
| largest_distribution | Identify the partner with the largest distribution | capital accounts CSV |
| ownership_over_20pct | Flag partners with >20% ownership | capital accounts CSV |
| reconcile_total_distributions | Verify closing balance = opening - distributions + income | capital accounts CSV |
| distribution_exceeds_opening | Flag partners where distribution >30% of opening balance | capital accounts CSV |
| ironwood_anomaly | Analyze Ironwood Ventures for distribution anomalies | capital accounts CSV |

**Multi-file agreement — 2/3**
| Case ID | Goal summary | Files |
|---------|-------------|-------|
| small_holder_cap | Verify <15% owners didn't exceed 1.5x distribution cap (Sec 4.3) | capital accounts CSV + fund agreement |
| quarterly_cap_check | Check if any partner's distribution exceeds the 25% quarterly cap | capital accounts CSV + fund agreement |

**Transaction log — 1/3**
| Case ID | Goal summary | Files |
|---------|-------------|-------|
| suspicious_fees | Flag positive fees (should always be negative) | transaction log CSV |

**Compliance cross-reference — 1/2**
| Case ID | Goal summary | Files |
|---------|-------------|-------|
| compliance_threshold_flag | Flag transactions exceeding $100K threshold (Sec 2.1) | compliance policy + transaction log |

### Intermittent (passing on some runs)

These cases pass when the planner generates valid SQL, but fail when it hallucinates schema details. They are not reliably reproducible in either direction.

| Case ID | Goal summary | Files | Notes |
|---------|-------------|-------|-------|
| ownership_tolerance_check | Verify ownership percentages sum to 100% within 0.5% tolerance | capital accounts CSV | Fails when planner puts aggregates in WHERE instead of HAVING |
| compliance_review | Review all transactions for compliance policy violations | compliance policy + transaction log | Fails when planner uses INTERVAL arithmetic on string dates |

### Resolved

**Category 1 (JSON serialization of Decimal)** was fixed by adding `default=str` to `json.dumps` calls in write_report and the e2e test harness. `quarterly_cap_check` now reliably passes.

### Failures — categorized

#### Category 2: Planner SQL hallucination (1 reliable failure, 2 intermittent)

The planner generates SQL that references columns, tables, or syntax that don't exist in the actual data.

**allocation_mismatch (reliable failure):** Identify income allocations that don't match expected ownership-based shares.
- **Hallucination:** Invented a table name `ownership` that doesn't exist. The planner assumed a normalized schema instead of the flat CSV it was given.

**ownership_tolerance_check (intermittent):** Verify ownership percentages sum to 100% within 0.5% tolerance.
- **Hallucination:** Used aggregates directly in a `WHERE` clause (`WHERE SUM(ownership_pct) > ...`). DuckDB rejects this — aggregates must be in `HAVING` or wrapped in a CTE/subquery. Passes on runs where the planner generates a CTE instead.

**compliance_review (intermittent):** Review all transactions for compliance policy violations.
- **Hallucination:** Treated a string date column (`transaction_date`) as a native date type and used `INTERVAL '30 days'` arithmetic on it. DuckDB can't subtract intervals from strings. Passes on runs where the planner uses `CAST()` or string comparison instead.

**Root cause:** The planner receives grounding context that includes column *names* (injected from the loaded data), but not column *types* or DuckDB-specific SQL constraints. The LLM fills in the gaps with assumptions from its training data — often PostgreSQL or MySQL idioms that don't transfer.

**Fix:** Expand the grounding context passed to the planner to include:
1. Column data types (e.g., `transaction_date VARCHAR`, not just `transaction_date`)
2. 2-3 few-shot SQL examples demonstrating CTE rewrites for aggregate filters, proper `CAST()` for string-to-date conversion, and DuckDB-specific syntax
3. An explicit negative constraint: "Do not reference tables not listed above"

- **Estimated effort:** 2-3 hours (modify planner prompt + grounding injection in executor, add few-shot examples, re-run evals).
- **Estimated impact:** Would stabilize the 2 intermittent cases and likely resolve `allocation_mismatch`.

#### Category 3: FlagItemTool attribution bug (1 case)

**duplicate_transactions:** Find duplicate transactions (same date, partner, type, amount) in the multi-quarter transaction log.

- **Failure mode:** The SQL correctly identifies both duplicate pairs (Ironwood and Benchmark), but the FlagItemTool assigns `item_id: "Benchmark Capital"` to all four flags instead of correctly attributing two flags to each partner. The critic catches this and returns a `fail` verdict.
- **Root cause:** Likely a loop variable bug in FlagItemTool where the `item_id` is captured by reference rather than by value, or a prompt ambiguity where the executor's per-flag instructions don't clearly specify which partner each flag belongs to. When the LLM generates multiple flag calls in sequence, the last-resolved `item_id` overwrites earlier ones.
- **Fix:** Audit FlagItemTool for iterator state management. If the bug is in the tool, fix the loop variable binding. If it's prompt-driven, consider splitting into `flag_items` (bulk, takes a list) and `flag_item` (single) to remove ambiguity about per-item attribution.
- **Estimated effort:** 1-2 hours.
- **Estimated impact:** 1 case resolved. Most consistent failure in the suite. May also improve flag attribution quality in passing cases where it wasn't caught because the test only checks flag count, not flag content.

#### Category 4: Critic over-escalation (1 case)

**distribution_exceeds_opening:** Flag partners where distribution exceeds 30% of opening balance.

- **Failure mode:** The pipeline produces correct output — the executor identifies the right partner and raises the flag. But the critic returns a `fail` verdict despite its own summary indicating the goal was met ("correctly identified one partner with excessive distributions, flag was raised as required").
- **Root cause:** Critic calibration issue. The critic prompt may be too strict on what constitutes "pass" vs "escalate," causing it to fail cases where the data output is correct but some secondary quality signal (e.g., report formatting, flag metadata) doesn't meet an implicit standard.
- **Fix:** Adjust critic few-shot examples to include cases where correct output with minor metadata gaps should receive "pass" or "partial," not "fail." May also need an explicit instruction: "If the goal's core requirement is met, do not return fail for cosmetic issues."
- **Estimated effort:** 1-2 hours with few-shot tuning.
- **Estimated impact:** 1 case resolved. Would also reduce false escalations to human review queue across all runs.

#### Category 5: Executor output truncation and data quality (1 case)

**compliance_threshold_flag:** Flag transactions exceeding $100K threshold (Sec 2.1) using compliance policy and transaction log.

- **Failure mode:** The critic flagged a truncated final record and a duplicate transaction in the executor output. The pipeline produced partially correct results, but data quality issues in the output prevented a clean pass.
- **Root cause:** Not yet fully diagnosed. Possible causes: executor truncating SQL result rows, DuckDB query returning more rows than expected due to join duplication, or write_report serializing a subset of results. Needs investigation to isolate whether the issue is in RunSQLTool, the executor's result passing, or WriteReportTool.
- **Estimated effort:** Needs investigation first — 1-2 hours to diagnose, then fix depends on findings.
- **Estimated impact:** 1 case resolved if the truncation source is identified and fixed.

---

## What I Would Fix Next

1. **FlagItemTool attribution bug** (Category 3) — most consistent failure. The `duplicate_transactions` case fails reliably every run because of incorrect `item_id` assignment. *Estimated effort: 1-2 hours.*

2. **Planner SQL grounding** (Category 2) — would stabilize the 2 intermittent cases (`ownership_tolerance_check`, `compliance_review`) and likely resolve `allocation_mismatch`. Adding column types and few-shot DuckDB examples to the grounding context is a prompt-only change with no architectural risk. *Estimated effort: 2-3 hours.*

3. **Critic calibration** (Category 4) — `distribution_exceeds_opening` produces correct output but gets a wrong verdict. Few-shot tuning in the critic prompt to distinguish "goal met with minor gaps" from genuine failures. *Estimated effort: 1-2 hours.*

4. **Executor output completeness** (Category 5) — `compliance_threshold_flag` needs investigation before a fix can be scoped. Lowest priority since the root cause is unclear. *Estimated effort: 1-2 hours to diagnose.*

If fixes 1-3 land and the intermittent cases stabilize, projected pass rate moves from 11/15 (73%) to 14-15/15 (93-100%).

---

## What I Would Do Differently From The Start

Building Aether taught me that LLM engineering has a different cost 
structure than normal software, and I wish I'd internalized that before 
writing agent code. If I started over, the first thing I'd do is build 
an eval harness that lets me run one case at a time — the `-k` filter 
pattern I eventually learned — so I could iterate on the planner 
without paying for a full 15-case suite every time.

The second thing I'd do is set up schema grounding from day one, even 
if minimal. The most expensive failures I hit were planner 
hallucinations — SQL that referenced tables and columns that don't 
exist, or syntax that works in PostgreSQL but not DuckDB. The planner 
wasn't wrong in a random way; it was confidently wrong, generating 
plausible-sounding queries against an imagined schema. I hadn't 
appreciated how often LLMs produce output that is contextually relevant 
but not grounded in the actual data. Injecting real column names and 
types into the planner's prompt eliminated an entire category of 
failures in a single change.

Third, I'd structure the context layer as hybrid from the start — 
grounding for enumerable environment details (file paths, schemas, 
tool signatures) and RAG for the unstructured knowledge (policies, 
rules, documentation). I initially treated RAG as the universal 
solution, but retrieval is the wrong tool for injecting deterministic 
context. Grounding is cheaper, faster, and more reliable when you can 
enumerate what the LLM needs to know.