# 2026-05-24-004 — Grounded visual output shipped end-to-end; PDF table extraction fixed

## What was done

Shipped the grounded visual output capability and fixed the PDF table extraction
pipeline, then verified the full document-to-chart pipeline eyes-on.

## Decisions

1. **Camelot replaces pdfplumber for table extraction.** `pdfplumber.extract_tables()`
   returned 0 tables on text-layout financial PDFs (the income statement test case).
   Camelot stream mode extracted all 8 tables from the same PDF. Local, free, no API
   call — preserves the local-first principle. Added to `load_data.py`'s PDF branch.

2. **Register all tables, not just the largest.** `load_data` now returns a manifest
   of all extracted tables (name, page number, columns, row count) and registers each
   as `<table_name>_0`, `<table_name>_1`, etc. The agent picks the right table from
   the manifest using page/column hints. The largest-table alias (`income_stmt` →
   `income_stmt_0`, the intro text) is latent — did not confuse the agent in testing,
   but should be fixed when next touched.

3. **render_visual: grounded by construction.** New deterministic tool (zero LLM calls)
   that takes already-computed findings and emits a Vega-Lite grouped bar chart spec.
   Data values are copied verbatim from prior tool observations (run_sql rows). The
   tool validates field presence and numeric types, returning `insufficient_data: true`
   if the data can't support a chart. Linear y-axis enforced.

4. **Grounding banner broadened to two refusal channels.** The UI's refusal banner now
   fires on either: (a) `answer_from_context` returning `insufficient_context`, or
   (b) critic verdict `fail` with a `missing_data` flag. Previously only channel (a)
   was detected, missing runs where the model expressed refusal via `write_report`
   with null values.

## Measurements (verified end-to-end, eyes-on)

**Visual pipeline test:** "Show me a bar chart of revenue, gross profit, operating
expenses for 2002" on the XYZ Company income statement PDF.

- `load_data` → Camelot stream → 8 tables registered
- `run_sql SELECT * FROM income_statement_2` → full income statement, numeric columns
- `render_visual` → `grounded: true`, spec built from SQL rows
- Chart rendered via `vl-convert` PNG and `st.vega_lite_chart`

| Metric             | Chart value | PDF ground truth | Match |
|--------------------|-------------|------------------|-------|
| Revenue            | 1,104,786   | 1,104,786        | Exact |
| Gross Profit       | 364,672     | 364,672          | Exact |
| Operating Expenses | 286,817     | 286,817          | Exact |

All values traceable: bar → Vega-Lite spec → render_visual args → SQL row → PDF cell.

**Earlier $3 error eliminated.** The text-path recompute (Revenue - COGS, misread
740,114 as 740,111 → 364,675) is now bypassed: SQL reads the GROSS PROFIT line item
directly → 364,672 exact.

**Grounding banner tests:**
- Answerable query (gross profit 2002): verdict pass, no banner. Correct.
- Unanswerable query (Q3 quarterly revenue, annual-only PDF): verdict fail +
  `missing_data` flag, full refusal banner. Correct.

## Constraint boundary (for demo honesty)

- Works cleanly on tables with **plain numerics** (income statement).
- Balance sheet, cash flow, and notes tables do NOT coerce to numeric yet —
  parenthesized negatives `(16,149)` and `--` dashes block `pd.to_numeric`.
  Those tables are queryable as strings but arithmetic/charts on them are not
  yet supported.

## Next (optional polish, none blocking demo)

- Parenthesized-negative + dash coercion for balance sheet / cash flow coverage.
- Fix `income_stmt` alias (currently points at intro text page, not income statement).
- Bubble-style trace visualization in the UI (cosmetic).
- Strategy fork still open: ComplianceOS vs FinanceBench research paper.
