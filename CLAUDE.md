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

This generalizes the pattern from a prior crypto-tax project (ChainTax) into a
domain-flexible agentic engine.

## What This Project Is NOT
- Not a LangChain / LangGraph / CrewAI wrapper — direct SDK, every decision is
  visible code. Do NOT add these frameworks.
- Not a general-purpose assistant.
- Finance is the primary domain; the engine is domain-agnostic (proven on
  finance + legal + medical).

## GROUND TRUTH NOTE
This file reflects the CURRENT code after the agentic upgrade was completed. Where
any older doc conflicts with this file or the code, the CODE is ground truth.
Archived/superseded docs (docs/eval_analysis_15case_ARCHIVED.md,
docs/progress-historical.md) are history, not current spec.

## Architecture (current — RAO loop)

```
User Goal + Documents
→ INGESTION (parse CSV/PDF/Excel/TXT → ~800-char chunks w/ overlap, SHA-256 fingerprint)
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

NOTE: the original one-shot `run()` (plan-all-steps-upfront) is PRESERVED in
runtime.py as a baseline/comparison path. `run_agentic()` is the current system.

### Key design facts (do not misdescribe)
- The LOOP AGENT reasons at EACH step (one tool, observe, decide next) — NOT a
  one-shot planner that emits all steps upfront. Path is discovered at runtime.
- The EXECUTOR is deterministic for data tools (load_data, run_sql, flag_item,
  write_report) — ZERO LLM calls there. The ONE deliberate exception is
  answer_from_context (the synthesis/grounding tool, an isolated LLM call). The
  evolved principle: "data operations are deterministic; synthesis is an explicit,
  auditable LLM step." Do NOT blur this boundary.
- The CRITIC uses overall_verdict ("pass"/"partial"/"fail"). Flag categories are
  domain-NEUTRAL: result_mismatch, incomplete_coverage, calculation_error,
  missing_data, policy_violation, data_quality, unsupported_claim, other.
- answer_from_context returns {answer, grounded, insufficient_context, input_tokens,
  output_tokens}. It
  MUST refuse to fabricate — returns INSUFFICIENT_CONTEXT when evidence is absent.
  This is the anti-fabrication guard and is a core trust property. It is OPTIONAL
  (the model may synthesize in its own traced reasoning); auditability comes from
  the reasoning being fully traced, not from forcing the tool.
- is_final + tool collision: if is_final=true AND a tool is named, the loop
  dispatches that tool THEN terminates (honors the final action; termination
  driven by is_final, not by which tool ran).

## Real Tools (six)
- load_data           — CSV/Excel (or a parsed markdown table) → DuckDB
- run_sql             — DuckDB SQL over loaded tables; deterministic computation
- retrieve_context    — pull relevant chunks from the hybrid retriever
- answer_from_context — synthesize a GROUNDED answer from evidence; refuses to
                        fabricate (INSUFFICIENT_CONTEXT). The one LLM-in-executor tool.
- flag_item           — append structured findings (feeds the critic)
- write_report        — write JSON/text output

Table-routing: when a document has a table, it can be parsed into a DataFrame and
loaded via load_data so the agent targets rows precisely with run_sql instead of
reasoning over flattened table text.

## Non-Negotiable Patterns
1. Every agent output is a Pydantic model. No freeform dicts.
2. Every LLM call and tool call writes a row to the trace store.
3. Retry logic on LLM calls (with error context appended).
4. Structured output validation before downstream use.
5. Direct SDK, multi-provider. No LangChain/CrewAI.
6. Distrust metrics until the instrument is verified. (Hard-won: many "failures"
   this project hit were measurement/tooling bugs — truncation caps, parsing bugs,
   scoring-normalization gaps — not the engine. Always inspect raw behavior before
   concluding the system is wrong. Verify scorers don't launder wrong answers.)

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
- streamlit · duckdb + pandas · sqlite3 (trace) · pytest

## Current Results (honest)
- Retrieval (n=200 over 380 contexts / 2,476 chunks): R@1 0.68, R@3 0.81,
  R@5 0.86, MRR@3 0.74, nDCG@5 0.78
- End-to-end FinQA (n=200, gpt-5.4-mini, table-routing, Number-Match v2 scored):
  75.5% (single run; variance re-run pending to finalize)
- Generalization PROVEN on finance + legal + medical (same engine, no code changes)
- Grounding guard PROVEN on an adversarial trap (refused to fabricate a value the
  document didn't state)
- See docs/aether-validation-log.md for the full honest progression and breakdowns.

## Project Structure
```
aether/
├── CLAUDE.md, README.md, pyproject.toml, uv.lock, .env.example
├── aether/
│   ├── ingestion/loader.py    # CSV/PDF/Excel/TXT → Chunk objects
│   ├── rag/retriever.py       # hybrid BM25 + dense (Chroma) + RRF + cross-encoder
│   ├── agents/                # loop_agent.py, planner.py (baseline), executor.py,
│   │                          #   critic.py, llm_client.py (provider routing)
│   ├── tools/                 # load_data, run_sql, retrieve_context,
│   │                          #   answer_from_context, flag_item, write_report
│   ├── models/                # agent_action, plan, trace, critique, chunk (Pydantic)
│   ├── trace/                 # store.py (SQLite) + inspect.py (trace inspector CLI)
│   ├── prompts/finance/       # domain-swappable few-shots (PROMPTS_DIR)
│   ├── runtime.py             # run() [baseline one-shot] + run_agentic() [RAO loop]
│   └── config.py              # provider/model routing, retrieval k's
├── ui/app.py                  # Streamlit app (NOTE: built for old run(); likely
│                              #   needs rewiring to run_agentic() — verify before demo)
├── evals/{retrieval,end_to_end}/   # 15-case suites
├── eval_e2e_200.py, eval_retrieval.py, score_number_match.py   # FinQA benchmark (root)
├── scripts/test_run.py        # working demo entry point
├── data/{demo,uploads}/
└── docs/                      # validation log, journal/, archived analysis
```

## Known State / Caveats
- ui/app.py was built for the old run() path; it likely drifted from run_agentic()
  and the new trace shape. VERIFY it runs on the current engine before any demo work.
- Finance few-shots cause mild contamination on non-finance docs (agent may attempt
  load_data on a text file, fail loudly, self-correct in one step). Neutralizing
  them is a planned next step.
- Table-parser handles the benchmark's uniform tables cleanly; real-world irregular
  tables (merged cells, multi-row headers) would exercise the fallback more.
- No conversation state (ConvFinQA multi-turn out of scope).

## What's Next (not yet done)
- Variance re-run to finalize the headline FinQA number (~180 records fits the
  2.5M/day mini token budget at ~13.6k tokens/record).
- Demo UI: verify/rewire ui/app.py to run_agentic(), surface the live reasoning
  trace, three-domain demo.
- Legal-domain benchmark; neutralize finance few-shots; optional bare-model baseline.

## Working Style (dual-Claude workflow)
- Claude.ai writes prompts pasted into Claude Code; prompts in code blocks, analysis
  outside.
- Brutal honesty over capitulation; push back on scope creep on merit.
- Measurement over assertion. Verify the instrument before trusting a number.
- Journal the build (docs/journal/, docs/aether-validation-log.md).

## For Claude Code / Other AI Assistants
- The code is ground truth; this file reflects it. Archived docs are history.
- Output only requested files with full content; each file block starts with a
  # filename comment.
- All agent outputs are Pydantic models. Trace store is SQLite. ChromaDB (not Qdrant).
- Do not add dependencies without checking pyproject.toml. Do not add LangChain.
- Do not break the deterministic-executor boundary (answer_from_context is the only
  LLM-in-executor exception).
- Claude Code has historically failed to save files to disk silently — confirm
  writes landed.