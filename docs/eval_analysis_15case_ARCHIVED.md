> ⚠️ SUPERSEDED — This documents the early 15-case eval suite (small internal benchmark).
> Current authoritative numbers: README and `docs/aether-validation-log.md`
> (n=200 FinQA benchmark: **75.5% e2e**, **R@5 = 86.0%**).
> Retained for the failure-mode analysis only.

---

# Aether Evaluation Analysis

## Summary

| Suite        | Result    | Rate |
|--------------|-----------|------|
| Retrieval    | 24/25     | 96%  |
| End-to-end   | 13/15     | 87%  |

- Retrieval variance: 0.00 stdev across 5 runs. Pipeline contains no stochastic components (deterministic embedding, BM25, and cross-encoder rerank), so runs are exactly reproducible.
- End-to-end variance: Full e2e variance measured empirically across multiple runs: suite pass rate fluctuates between 13 and 14 of 15 cases. The reliable floor is 13/15 -- cases that pass regardless of LLM stochasticity. The one intermittent case (`compliance_threshold_flag`) passes on most runs but occasionally fails due to critic over-escalation.

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

### Reliably passing (13)

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

**Multi-file agreement — 3/3**
| Case ID | Goal summary | Files |
|---------|-------------|-------|
| small_holder_cap | Verify <15% owners didn't exceed 1.5x distribution cap (Sec 4.3) | capital accounts CSV + fund agreement |
| quarterly_cap_check | Check if any partner's distribution exceeds the 25% quarterly cap | capital accounts CSV + fund agreement |
| ownership_tolerance_check | Verify ownership percentages sum to 100% within 0.5% tolerance | capital accounts CSV |

**Transaction log — 2/3**
| Case ID | Goal summary | Files |
|---------|-------------|-------|
| suspicious_fees | Flag positive fees (should always be negative) | transaction log CSV |
| duplicate_transactions | Find duplicate transactions (same date, partner, type, amount) | transaction log CSV |

**Compliance cross-reference — 1/2**
| Case ID | Goal summary | Files |
|---------|-------------|-------|
| compliance_review | Review all transactions for compliance policy violations | compliance policy + transaction log |

### Resolved

**Category 1 (JSON serialization of Decimal)** was fixed by adding `default=str` to `json.dumps` calls in write_report and the e2e test harness. `quarterly_cap_check` now reliably passes.

**Category 3 (FlagItemTool attribution bug)** was fixed by restructuring FlagItemTool to check for explicit `item_id` args before the SQL-row bulk path. The old code unconditionally entered the bulk path whenever `prior_results` contained SQL output, ignoring the planner's per-item `item_id` and returning the last row's partner_name for all flags. The fix also added optional `filter_column`/`filter_value` support in the bulk path for row-level filtering. `duplicate_transactions` now reliably passes. Two previously intermittent cases (`ownership_tolerance_check`, `compliance_review`) also stabilized as reliably passing.

### Failures — categorized

#### Category 2: Planner SQL hallucination (1 case)

**allocation_mismatch:** Identify income allocations that don't match expected ownership-based shares.
- **Hallucination:** Invented a table name `ownership` that doesn't exist. The planner assumed a normalized schema instead of the flat CSV it was given.
- **Root cause:** The planner receives grounding context that includes column *names* (injected from the loaded data), but not column *types* or DuckDB-specific SQL constraints. The LLM fills in the gaps with assumptions from its training data.
- **Fix:** Expand the grounding context passed to the planner to include column data types, few-shot DuckDB SQL examples, and an explicit negative constraint: "Do not reference tables not listed above."
- **Estimated effort:** 2-3 hours.

#### Category 4/5: Critic over-escalation and output truncation (1 case)

**compliance_threshold_flag:** Flag transactions exceeding $100K threshold (Sec 2.1) using compliance policy and transaction log.

- **Failure mode:** Intermittent. Passes on most runs, but occasionally the critic flags truncated output or data quality issues and returns `fail` despite the executor producing substantially correct results. Counted as a failure per the min(observed, reliable_floor) rule.
- **Root cause:** Two interacting issues. (1) The executor's trace payload truncates tool results to 1000 characters (`str(result)[:1000]`), which can cut off the last record in large SQL results. The critic sees truncated data and escalates. (2) The critic prompt is too strict on what constitutes "pass" vs "escalate," causing it to fail cases where the core goal is met but secondary quality signals (completeness, formatting) don't meet an implicit standard.
- **Fix:** (a) Increase or remove the trace payload truncation limit so the critic sees complete results. (b) Adjust critic few-shot examples to distinguish "goal met with minor gaps" from genuine failures.
- **Estimated effort:** 1-2 hours.

---

## What I Would Fix Next

1. **Planner SQL grounding** (Category 2) — the only reliable failure. `allocation_mismatch` fails every run because the planner invents a table that doesn't exist. Adding column types and DuckDB-specific constraints to the grounding context is a prompt-only change with no architectural risk. *Estimated effort: 2-3 hours.*

2. **Critic calibration and output truncation** (Category 4/5) — `compliance_threshold_flag` passes most runs but fails intermittently. Fixing the trace truncation and tuning the critic prompt would stabilize it. *Estimated effort: 1-2 hours.*

If both fixes land, projected pass rate moves from 13/15 (87%) to 15/15 (100%).

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
