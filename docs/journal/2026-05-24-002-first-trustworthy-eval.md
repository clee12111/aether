# 2026-05-24-002 — First Trustworthy Full Agentic Eval

## Session goal
Get a trustworthy full agentic eval number after fixing measurement integrity.

## Decisions made
- Trace inspector built as first-class debugging tool (local, not Langfuse —
  hand-built trace is the differentiator)
- Debug from traces, not by re-running the suite
- Fixed measurement instruments BEFORE drawing conclusions (trace truncation,
  state leaks, JSON repair)
- is_final+tool collision: chose loop-fix (Option 1, dispatch then terminate)
  over prompt-discipline — robustness over relying on small-model
  instruction-following

## What happened
- Three state leaks fixed (retriever, flags, table registry) — module-scoped
  fixture contamination
- Trace store was truncating raw_response at 500 chars and mis-reporting
  valid JSON as malformed — measurement bug manufacturing failures
- First trustworthy run: ~4/13 pass (~31%), 78 min
- Dominant failure isolated: run_sql Binder errors (model writes invalid SQL)

## Measurements
- ~31% pass on completed cases
- Invalid-JSON failures: 12→6 (6 remaining are genuine, not capture artifacts)
- SQL Binder errors: 8/15
- Wall time: 78 min, ~5 min/case

## Next session
SQL robustness — schema injection at run_sql step + error-feedback-into-
observation so the model corrects column names. Then re-run. Consider: is the
low number a Mistral-on-CPU ceiling that the Anthropic baseline would clear?
Run the SAME suite on the Anthropic provider to get the API-vs-local gap —
that comparison IS the forensic thesis.
