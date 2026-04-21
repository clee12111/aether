# Aether Evaluation Analysis

## Summary

| Suite        | Result    | Rate |
|--------------|-----------|------|
| Retrieval    | 24/25     | 96%  |
| End-to-end   | 10/15     | 67%  |

- Retrieval variance: 0.00 stdev across 5 runs. Pipeline contains no stochastic components (deterministic embedding, BM25, and cross-encoder rerank), so runs are exactly reproducible.
- End-to-end variance: not measured. A full suite run costs ~$0.65 (roughly $0.05 per case × 15 cases); multi-run variance measurement would multiply that linearly. Deferred until specific failure cases warranted investigation. 

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

### Passing (10)

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

**Multi-file agreement — 1/3**
| Case ID | Goal summary | Files |
|---------|-------------|-------|
| small_holder_cap | Verify <15% owners didn't exceed 1.5x distribution cap (Sec 4.3) | capital accounts CSV + fund agreement |

**Transaction log — 1/3**
| Case ID | Goal summary | Files |
|---------|-------------|-------|
| suspicious_fees | Flag positive fees (should always be negative) | transaction log CSV |

**Compliance cross-reference — 1/2**
| Case ID | Goal summary | Files |
|---------|-------------|-------|
| compliance_threshold_flag | Flag transactions exceeding $100K threshold (Sec 2.1) | compliance policy + transaction log |

### Failures — categorized

#### Category 1: Test harness bug (1 case)

**quarterly_cap_check:** Check if any partner's distribution exceeds the 25% quarterly cap from the fund agreement.

- **Failure mode:** `TypeError: Object of type Decimal is not JSON serializable` when the executor tries to serialize DuckDB query results.
- **Root cause:** DuckDB returns `decimal.Decimal` objects for numeric columns. The executor's `json.dumps()` call in result packaging doesn't handle non-standard numeric types.
- **Fix:** Add `default=str` to the `json.dumps()` call in the executor's result serialization path. Trivial one-line fix.
- **Estimated effort:** 15 minutes.
- **Estimated impact:** 1 case resolved. This case would otherwise pass — the planner and SQL are correct.

#### Category 2: Planner SQL hallucination (3 cases)

All three cases fail because the planner generates SQL that references columns, tables, or syntax that don't exist in the actual data.

**allocation_mismatch:** Identify income allocations that don't match expected ownership-based shares.
- **Hallucination:** Invented a table name `ownership_pct` that doesn't exist. The planner assumed a normalized schema instead of the flat CSV it was given.

**ownership_tolerance_check:** Verify ownership percentages sum to 100% within 0.5% tolerance.
- **Hallucination:** Used aggregates directly in a `WHERE` clause (`WHERE SUM(ownership_pct) > ...`). DuckDB rejects this — aggregates must be in `HAVING` or wrapped in a CTE/subquery.

**compliance_review:** Review all transactions for compliance policy violations.
- **Hallucination:** Treated a string date column (`transaction_date`) as a native date type and used `INTERVAL '30 days'` arithmetic on it. DuckDB can't subtract intervals from strings.

**Root cause:** The planner receives grounding context that includes column *names* (injected from the loaded data), but not column *types* or DuckDB-specific SQL constraints. The LLM fills in the gaps with assumptions from its training data — often PostgreSQL or MySQL idioms that don't transfer.

**Fix:** Expand the grounding context passed to the planner to include:
1. Column data types (e.g., `transaction_date VARCHAR`, not just `transaction_date`)
2. 2-3 few-shot SQL examples demonstrating CTE rewrites for aggregate filters, proper `CAST()` for string-to-date conversion, and DuckDB-specific syntax
3. An explicit negative constraint: "Do not reference tables not listed above"

- **Estimated effort:** 2-3 hours (modify planner prompt + grounding injection in executor, add few-shot examples, re-run evals).
- **Estimated impact:** 2/3 cases likely resolved. The `compliance_review` case involves multi-step reasoning (cross-referencing policy thresholds with transaction data) and may still struggle even with better grounding.

#### Category 3: Non-deterministic executor behavior (1 case)

**duplicate_transactions:** Find duplicate transactions (same date, partner, type, amount) in the multi-quarter transaction log.

- **Failure mode:** The SQL correctly identifies both duplicate pairs (Ironwood and Benchmark), but the FlagItemTool assigns `item_id: "Benchmark Capital"` to all four flags instead of correctly attributing two flags to each partner.
- **Root cause:** Likely a loop variable bug in FlagItemTool where the `item_id` is captured by reference rather than by value, or a prompt ambiguity where the executor's per-flag instructions don't clearly specify which partner each flag belongs to. When the LLM generates multiple flag calls in sequence, the last-resolved `item_id` overwrites earlier ones.
- **Fix:** Audit FlagItemTool for iterator state management. If the bug is in the tool, fix the loop variable binding. If it's prompt-driven, consider splitting into `flag_items` (bulk, takes a list) and `flag_item` (single) to remove ambiguity about per-item attribution.
- **Estimated effort:** 1-2 hours.
- **Estimated impact:** 1 case resolved. May also improve flag attribution quality in passing cases where it wasn't caught because the test only checks flag count, not flag content.

---

## What I Would Fix Next

1. **Planner SQL grounding** (Category 2) — highest impact. Three cases fail because the planner hallucinates schema details. Adding column types and few-shot DuckDB examples to the grounding context is a prompt-only change with no architectural risk. Expected to resolve 2 of 3 cases. *Estimated effort: 2-3 hours.*

2. **JSON serialization bug** (Category 1) — lowest effort. One line (`default=str`) unblocks a case that already produces the correct result. *Estimated effort: 15 minutes.*

3. **FlagItemTool iterator bug** (Category 3) — resolves the last failure category and improves flag attribution reliability across all cases. *Estimated effort: 1-2 hours.*

If all three fixes land, projected pass rate moves from 10/15 (67%) to 14-15/15 (93-100%).

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