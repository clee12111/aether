# Aether — Workflow Reasoning Engine
# CLAUDE.md — Project Context for AI Assistants

## What This Project Is
Aether is a workflow reasoning engine for financial data. 
Given a messy financial document corpus (CSVs, PDFs, Excel) and a high-level goal, 
it discovers what steps are needed, executes them with tools, critiques the output, 
and produces an auditable result with a full trace.

This generalizes the pattern from ChainTax (crypto tax pipeline) into a domain-flexible engine for finance.

## What This Project Is NOT
- Not a LangChain/LangGraph/CrewAI wrapper
- Not a general-purpose AI assistant
- Not trying to solve every domain — finance is the demo domain
- Not building a complex frontend yet (Streamlit only for MVP)

## Output Rules (MANDATORY — follow every time)
- When I ask for specific files, output ONLY the requested files in clean ``` blocks.
- Each file block must start with a # filename comment as the first line.
- Do NOT output files I did not ask for.
- ALWAYS end your response with a "## Recap" section containing:
  - What files were created or changed (one bullet per file)
  - What the key changes were
  - What to do next
- This recap is mandatory. Never skip it.

## Architecture (Do Not Change Without Discussion)

User Goal + Documents 
→ INGESTION LAYER (parse CSV/PDF/Excel/TXT → chunks)
→ RAG LAYER (hybrid BM25 + dense → reranker → chunks)
→ PLANNER (goal + context → ExecutionPlan)
→ EXECUTOR (step-by-step tool dispatch → structured output)
→ CRITIC (output vs goal → CritiqueResult)
→ TRACE STORE (every LLM call + tool call → SQLite)
→ STREAMLIT UI (run viewer + trace explorer)

## Non-Negotiable Patterns
1. Every agent output is a Pydantic model. No freeform dicts.
2. Every LLM call and tool call writes a row to the trace store.
3. Retry logic on all LLM calls (max 3 attempts with error context).
4. Structured output validation before any downstream use.
5. Never use LangChain, LangGraph, or CrewAI.

## Cost Optimization (Per-Agent Model Routing)
- Planner → claude-opus-4-5 (complex reasoning)
- Executor → claude-haiku-4-5-20251001 (mechanical routing)
- Critic → claude-haiku-4-5-20251001 (structured comparison)
- Chat → claude-sonnet-4-6 (user-facing)
- Use settings.planner_model, settings.executor_model, etc. — not settings.claude_model

## Stack
- Python 3.11 + uv
- anthropic SDK (direct)
- pydantic v2 + pydantic-settings
- chromadb (local, embedded)
- sentence-transformers + rank-bm25
- streamlit
- duckdb + pandas
- pytest + rich
- sqlite3 (trace)

## Project Structure
aether/
├── CLAUDE.md
├── pyproject.toml
├── .env.example
├── aether/
│   ├── ingestion/
│   ├── rag/
│   ├── agents/
│   ├── tools/
│   ├── models/
│   ├── trace/
│   ├── runtime.py
│   └── config.py
├── ui/
│   └── app.py
├── evals/
│   ├── retrieval/
│   │   ├── cases.json
│   │   └── test_retrieval.py
│   └── end_to_end/
│       ├── cases.json
│       └── test_e2e.py
├── data/
│   ├── demo/
│   └── uploads/
└── tests/

## Prompting Rules
- System prompts live in aether/agents/{agent_name}.py as constants
- Few-shot examples are included in every system prompt
- Output format is always specified as JSON schema in the prompt
- Never tell the model to "do its best" — give concrete success criteria

## Current Status
[x] Week 1: Ingestion + RAG + retrieval evals
[x] Week 2: Planner agent
[x] Week 3: Executor + tool layer
[x] Week 4: Critic + run loop — FIRST PASS ACHIEVED
[x] Week 5: Eval framework — 25 retrieval + 15 e2e cases, multi-file scenarios
[x] Week 6: Streamlit UI — 3-tab app (Run / Trace Explorer / Eval Dashboard)
[ ] Week 7: ComplianceOS expansion + live eval wiring

## Files Completed
- aether/config.py — Settings with per-agent model routing (planner/executor/critic/chat)
- aether/models/plan.py — ExecutionPlan + PlanStep with topological sort
- aether/models/trace.py — TraceEvent with classmethods (for_llm_call, for_tool_call, for_validation_error)
- aether/models/critique.py — CritiqueResult + CritiqueFlag (verdict: pass/partial/fail, severity: critical/warning/info)
- aether/trace/store.py — TraceStore class (SQLite, WAL mode, indexes on run_id/event_type/created_at)
- aether/ingestion/loader.py — DocumentLoader: CSV + pdfplumber PDF + Excel + TXT → Chunk list
- aether/rag/retriever.py — HybridRetriever: Chroma dense + BM25 + flashrank reranker (lazy imports for Windows perf)
- aether/agents/planner.py — PlannerAgent: retry×3, TraceEvent.for_llm_call, settings.planner_model
- aether/agents/executor.py — ExecutorAgent: topological step dispatch, retry×2, TraceEvent.for_tool_call, pluggable extra_tools
- aether/agents/critic.py — CriticAgent: settings.critic_model, returns CritiqueResult (overall_verdict/flags/summary)
- aether/runtime.py — AetherRuntime: full ingest→retrieve→plan→execute→critique→revise loop, human_review_queue, extra_tools passthrough
- aether/tools/base.py — BaseTool ABC
- aether/tools/load_data.py — LoadDataTool: CSV/Excel → DuckDB registry, path fallback to data/demo + data/uploads
- aether/tools/run_sql.py — RunSQLTool: DuckDB execute, CTE auto-rewrite for window-function-in-WHERE errors
- aether/tools/flag_item.py — FlagItemTool: flexible arg keys, bulk flag from SQL prior_results rows
- aether/tools/write_report.py — WriteReportTool: JSON or text to data/uploads/
- data/demo/fund_capital_accounts.csv — 6 partners, Ironwood Ventures LLC deliberate 24.1pt distribution violation
- data/demo/fund_agreement.txt — Horizon Growth Partners Fund I LP agreement (Sections 4.1–5.2: 25% cap, 1.5x small-holder cap, 8% preferred return, pro-rata rule, 0.5% ownership tolerance)
- data/demo/multi_quarter_transactions.csv — 40 rows Q1–Q4 2024, 6 partners, embedded anomalies: 2 duplicate txns, 1 suspicious positive fee, 1 ownership-mismatched allocation
- data/demo/compliance_policy.txt — Horizon Wealth Advisors RIA compliance policy ($100K flag threshold, 30% concentration limit, suitability, 5-day review, 5-year retention)
- scripts/test_run.py — End-to-end smoke test, prints verdict/flags/PASS/FAIL
- ui/app.py — Streamlit 3-tab app: Run Aether (goal + upload → plan/output/critique), Trace Explorer (run selector + event viewer), Eval Dashboard (hardcoded metrics)
- evals/retrieval/cases.json — 25 retrieval eval cases (15 CSV + 5 fund agreement + 5 compliance policy)
- evals/retrieval/test_retrieval.py — pytest parametrized retrieval precision@3, indexes all 3 demo files
- evals/end_to_end/cases.json — 15 e2e eval cases (7 single-file CSV + 3 multi-file agreement + 3 transaction log + 2 compliance cross-ref)
- evals/end_to_end/test_e2e.py — pytest parametrized e2e eval, relaxed verdict assertions, executor output flag counting, -m quick mode

## Known Patterns Established This Session
- TraceStore is a class — use TraceStore(settings.db_path), not init_db()/write_event() free functions
- CritiqueResult uses overall_verdict ("pass"/"partial"/"fail"), NOT recommendation/goal_achieved
- CritiqueFlag uses severity ("critical"/"warning"/"info"), NOT "low"/"medium"/"high"
- Revision loop triggers on overall_verdict == "partial"; escalation on overall_verdict == "fail"
- Heavy ML imports (sentence_transformers, chromadb, flashrank) are lazy-loaded inside methods
- SQL window functions in WHERE clauses are auto-rewritten as CTEs in RunSQLTool
- LoadDataTool resolves bare filenames against data/demo/ and data/uploads/ as fallbacks
- ExecutorAgent accepts extra_tools dict — custom tools merge into default registry without modifying Executor code
- AetherRuntime passes extra_tools through to ExecutorAgent
- DocumentLoader supports .txt files via character-window chunking (same as PDF text splitting)
- E2e eval verdicts: Critic returns "pass" when goal is achieved (flagging violations IS achieving the goal); use "any" when verdict is legitimately unpredictable
- E2e eval flag counting: check executor output (result["output"]) for flagged items, not critique["flags"] — critique flags are process issues, not data findings

## Demo Scenario
Fund CSV + TXT agreement + transaction log + compliance policy → reconcile, flag issues, cross-reference rules (expanded from ChainTax style)

## For Codex / Other AI Assistants
Output only requested files with full content.
Keep code minimal and focused.
Prefer chromadb over any server-based vector DB.
This project uses direct Anthropic SDK calls. 
All agent outputs are Pydantic models. 
Do not introduce new dependencies without checking pyproject.toml. 
Do not add LangChain. 
The trace store is SQLite.