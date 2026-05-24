# Aether — Agentic Workflow Reasoning Engine
# CLAUDE.md — Project Context for AI Assistants

## What This Project Is
Aether is an agentic workflow reasoning engine. Given a document corpus and a
high-level goal, it reasons about what to do ONE STEP AT A TIME, calls tools to
take actions, observes the result, critiques the final output, and produces an
auditable result with a full trace.

It is a **reason-act-observe (RAO) loop** with a deterministic execution core and
a verification step. Finance is the primary/demo domain, but the engine is
domain-agnostic and has been demonstrated on legal and medical documents with no
code changes.

## What This Project Is NOT
- Not a LangChain / LangGraph / CrewAI wrapper — direct SDK, every decision is
  visible code. Do NOT add these frameworks.
- Not a general-purpose assistant.
- Finance is the primary domain; the engine is domain-agnostic (proven on
  finance + legal + medical).

## Ground Truth
This file reflects the CURRENT code. Where any older doc conflicts with this file
or the code, the CODE is ground truth. Archived docs
(docs/archive/eval_analysis_15case_ARCHIVED.md, docs/archive/progress-historical.md)
are history, not current spec.

## Architecture (current — RAO loop)

```
User Goal + Documents
→ INGESTION (parse CSV/PDF/Excel/TXT → ~512-char chunks w/ overlap, SHA-256 fingerprint)
→ HYBRID RETRIEVAL (BM25 + dense via ChromaDB → RRF merge → cross-encoder rerank → top-5)
→ LOOP AGENT (reasons each step: sees goal + all prior steps → picks ONE tool →
   observes result → decides next; path discovered at runtime; is_final ends it)
→ EXECUTOR (dispatches the chosen tool; deterministic for data tools, NO LLM —
   except answer_from_context, the one isolated synthesis tool)
→ (loop repeats until is_final=true OR max_steps ceiling)
→ CRITIC (output vs goal → verdict pass/partial/fail, structured flags)
→ TRACE STORE (every reasoning step, tool call, observation → SQLite; reasoning/
   tool/is_final are first-class queryable fields)
```

NOTE: the original one-shot `run()` (plan-all-steps-upfront) is preserved in
runtime.py as a baseline/comparison path. `run_agentic()` is the current system.

### Key design facts (do not misdescribe)
- The LOOP AGENT reasons at EACH step (one tool, observe, decide next) — NOT a
  one-shot planner that emits all steps upfront. Path is discovered at runtime.
- The EXECUTOR is deterministic for data tools (load_data, run_sql, flag_item,
  write_report, render_visual) — ZERO LLM calls there. The ONE deliberate
  exception is answer_from_context (the synthesis/grounding tool, an isolated LLM
  call). The evolved principle: "data operations are deterministic; synthesis is an
  explicit, auditable LLM step." Do NOT blur this boundary.
- The CRITIC uses overall_verdict ("pass"/"partial"/"fail"). Flag categories are
  domain-NEUTRAL: result_mismatch, incomplete_coverage, calculation_error,
  missing_data, policy_violation, data_quality, unsupported_claim, other.
- answer_from_context returns {answer, grounded, insufficient_context, input_tokens,
  output_tokens}. It MUST refuse to fabricate — returns INSUFFICIENT_CONTEXT when
  evidence is absent. This is the anti-fabrication guard and is a core trust
  property. It is OPTIONAL (the model may synthesize in its own traced reasoning);
  auditability comes from the reasoning being fully traced, not from forcing the tool.
- is_final + tool collision: if is_final=true AND a tool is named, the loop
  dispatches that tool THEN terminates (honors the final action; termination
  driven by is_final, not by which tool ran).

## Real Tools (seven)
- load_data           — CSV/Excel/PDF → DuckDB. PDF tables via Camelot stream-mode;
                        financial-number coercion ($, commas, parenthesized negatives,
                        dash-zeros). Registers all extracted tables with a manifest.
- run_sql             — DuckDB SQL over loaded tables; deterministic computation
- retrieve_context    — pull relevant chunks from the hybrid retriever
- answer_from_context — synthesize a GROUNDED answer from evidence; refuses to
                        fabricate (INSUFFICIENT_CONTEXT). The one LLM-in-executor tool.
- flag_item           — append structured findings (feeds the critic)
- write_report        — write JSON/text output
- render_visual       — builds a grounded Vega-Lite chart spec from computed findings;
                        values copied verbatim from tool outputs, never model-generated;
                        refuses (insufficient_data) rather than fabricating a chart.

Table-routing: when a document has a table, it can be parsed into a DataFrame and
loaded via load_data so the agent targets rows precisely with run_sql instead of
reasoning over flattened table text.

## Non-Negotiable Patterns
1. Every agent output is a Pydantic model. No freeform dicts.
2. Every LLM call and tool call writes a row to the trace store.
3. Retry logic on LLM calls (with error context appended).
4. Structured output validation before downstream use.
5. Direct SDK, multi-provider. No LangChain/CrewAI.
6. Measurement over assertion. Verify the instrument before trusting a number.
   (Hard-won: many "failures" this project hit were measurement/tooling bugs —
   truncation caps, parsing bugs, scoring-normalization gaps — not the engine.
   Always inspect raw behavior before concluding the system is wrong. Verify
   scorers don't launder wrong answers.)

## Model Routing (provider-swappable)
- Default reasoning provider: OpenAI gpt-5.4-mini (planner + critic).
- Local Ollama (Mistral) supported as a fallback AND as a comparison baseline.
- Anthropic supported as an alternative provider.
- Set via PLANNER_PROVIDER / CRITIC_PROVIDER + the per-provider model fields in
  config.py / .env. The pipeline (retrieval, embeddings, reranker, trace,
  execution) is local regardless of reasoning provider.

## Stack
- Python 3.11 + uv · openai + anthropic SDKs (direct, provider-swappable) ·
  pydantic v2 + pydantic-settings
- chromadb (local) · sentence-transformers + rank-bm25 · flashrank reranker
- camelot-py (PDF table extraction) · vl-convert-python (chart rendering)
- streamlit · duckdb + pandas · sqlite3 (trace) · pytest

## Current Results (honest)
- Retrieval (n=200 over 380 contexts / 2,476 chunks): R@1 0.68, R@3 0.81,
  R@5 0.86, MRR@3 0.74, nDCG@5 0.78
- End-to-end FinQA (n=200, gpt-5.4-mini, table-routing, Number-Match v2 scored):
  75.5% raw (151/200); 79.5% on benchmark-fair questions (10 records excluded as
  benchmark-defective, enumerated in the validation log). Single run; variance
  re-run pending.
- Generalization proven on finance + legal + medical (same engine, no code changes)
- Grounding guard proven on adversarial traps: engine refused to fabricate values
  the document didn't state, and refused to hallucinate missing years for a chart
  request when only two years of data existed.
- 700-row CSV aggregation verified: SQL GROUP BY/SUM totals matched independent
  pandas computation exactly across all segments, including negative values.
- See docs/aether-validation-log.md for the full honest progression and breakdowns.

## Project Structure
```
aether/
├── CLAUDE.md, README.md, pyproject.toml, uv.lock, .env.example
├── aether/
│   ├── ingestion/loader.py      # CSV/PDF/Excel/TXT → Chunk objects
│   ├── ingestion/table_parser.py # markdown-table → DataFrame for SQL routing
│   ├── rag/retriever.py         # hybrid BM25 + dense (Chroma) + RRF + cross-encoder
│   ├── agents/                  # loop_agent.py, planner.py (baseline), executor.py,
│   │                            #   critic.py, llm_client.py (provider routing)
│   ├── tools/                   # load_data, run_sql, retrieve_context,
│   │                            #   answer_from_context, flag_item, write_report,
│   │                            #   render_visual
│   ├── models/                  # agent_action, plan, trace, critique, chunk (Pydantic)
│   ├── trace/                   # store.py (SQLite) + inspect.py (trace inspector CLI)
│   ├── prompts/finance/         # domain-swappable few-shots (PROMPTS_DIR)
│   ├── runtime.py               # run() [baseline one-shot] + run_agentic() [RAO loop]
│   └── config.py                # provider/model routing, retrieval k's
├── ui/app.py                    # Streamlit app (Run tab, Trace Explorer, Eval Dashboard)
├── evals/                       # evaluation suites + benchmark runners
│   ├── end_to_end/              #   15-case suite (cases.json, test_e2e.py, test_e2e_agentic.py)
│   ├── retrieval/               #   15-case suite (cases.json, test_retrieval.py)
│   ├── results/                 #   n=200 eval outputs (jsonl, summary json)
│   ├── eval_e2e_200.py          #   n=200 FinQA benchmark runner
│   ├── eval_retrieval.py        #   n=200 retrieval eval runner
│   └── score_number_match.py    #   Number-Match v2 scorer
├── scripts/test_run.py          # working demo entry point
├── data/
│   ├── demo/                    # demo documents (fund CSVs, legal PDF, medical PDF)
│   ├── finqa_contexts/          # 380 FinQA benchmark contexts
│   └── uploads/                 # runtime output (gitignored)
├── tests/                       # pytest suite
└── docs/                        # validation log, journal/, archive/
```

## Streamlit UI
The UI (ui/app.py) has three tabs:
- **Run tab:** live `run_agentic()` execution with grounded visual output
  (render_visual → Vega-Lite chart rendered via st.vega_lite_chart). Grounding-guard
  banner fires on both refusal channels (answer_from_context insufficient_context;
  critic fail + missing_data flag). Download button for write_report output.
- **Trace Explorer:** grouped-phase reasoning trace. Each RAO step shows a
  human-readable phase label ("Loaded the document", "Queried the data", etc.) with
  the model's reasoning, expandable to technical drill-down (tool name, args, raw
  result). Grounding-guard steps styled distinctly. Critic verdict + flags at bottom.
- **Eval Dashboard:** live-computed from eval output files (not hardcoded). Shows
  e2e pass rate (raw + benchmark-fair) and retrieval metrics (R@1/3/5, MRR@3, nDCG@5).

## Known State / Caveats
- Finance few-shots cause mild contamination on non-finance docs (agent may attempt
  load_data on a text file, fail loudly, self-correct in one step). Neutralizing
  them is a planned next step.
- PDF table extraction uses Camelot stream-mode, which handles whitespace-aligned
  financial tables that pdfplumber misses. KNOWN LIMITATION: Camelot splits some
  multi-section tables (e.g. a balance sheet's assets and liabilities on one page
  are extracted as assets-only). Layout-aware parsing is future work.
- Financial-number coercion handles $, commas, parenthesized negatives "(x)" → -x,
  and dash-zeros "$-" → null. Columns convert only if ≥80% of values parse (protects
  categorical columns).
- No OCR: ingestion uses pdfplumber text extraction (requires a PDF text layer).
  Born-digital PDFs/CSVs/TXT work. Scanned/image-only PDFs raise an ingestion error.
- No conversation state (ConvFinQA multi-turn out of scope).

## What's Next (not yet done)
- Variance re-run to finalize the headline FinQA number.
- Legal-domain benchmark; neutralize finance few-shots; optional bare-model baseline.
- Layout-aware PDF parsing for multi-section tables (balance sheet).
- Graceful termination on unfindable data (stop-after-N-non-advancing-queries +
  partial report, instead of looping to max_steps).

## For Claude Code / Other AI Assistants
- The code is ground truth; this file reflects it. Archived docs are history.
- All agent outputs are Pydantic models. Trace store is SQLite. ChromaDB (not Qdrant).
- Do not add dependencies without checking pyproject.toml. Do not add LangChain.
- Do not break the deterministic-executor boundary (answer_from_context is the only
  LLM-in-executor exception; render_visual is deterministic).
