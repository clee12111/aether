# Aether

A workflow reasoning engine for financial documents. Given messy CSVs, PDFs, and text files plus a plain-language goal, Aether discovers the steps needed to achieve that goal, executes them with tools, critiques the output, and produces an auditable result with a full trace.

## What it is

Financial data work is messy. A compliance analyst gets a pile of CSVs, a fund agreement PDF, and a policy document, and needs to reconcile numbers, cross-reference rules, and flag violations. The steps vary depending on the data and the goal. Aether automates this: you provide documents and a goal, and it figures out what to do, does it, and tells you whether it worked.

The system runs a planner/executor/critic loop over a hybrid RAG layer. The **planner** (Claude Opus) reads the goal and retrieved context, then generates a structured execution plan with tool calls. The **executor** dispatches those tool calls (load data into DuckDB, run SQL, flag items, write reports) with no LLM in the loop. The **critic** (Claude Haiku) compares the output against the original goal and returns a structured verdict. If the verdict is "partial," the system re-plans with the critic's feedback, up to 2 revision cycles. If the verdict is "fail," it escalates to a human review queue. Every LLM call and tool call is recorded in a SQLite trace store.

The engine is domain-agnostic. Finance is the demo domain, but the only finance-specific content lives in `aether/prompts/finance/` (few-shot examples) and `data/demo/` (sample documents). Swap `PROMPTS_DIR` to a different directory, provide different documents, and the engine works the same way. See [`aether/prompts/README.md`](aether/prompts/README.md) for details.

## Architecture

```
User Goal + Documents
  |
  v
INGESTION          CSV / PDF / Excel / TXT --> chunked documents
  |
  v
HYBRID RAG         BM25 + dense (all-MiniLM-L6-v2) --> RRF merge --> flashrank rerank
  |
  v
PLANNER            goal + top-k chunks --> ExecutionPlan     [Claude Opus]
  |
  v
EXECUTOR           step-by-step tool dispatch (no LLM)       [DuckDB, flag, report]
  |
  v
CRITIC             output vs goal --> CritiqueResult          [Claude Haiku]
  |
  +---> verdict: pass ----> return result
  +---> verdict: partial -> re-plan with feedback (max 2 revisions)
  +---> verdict: fail ----> escalate to human review queue
  |
  v
TRACE STORE        every LLM call + tool call --> SQLite (WAL mode)
```

## Results

| Eval suite | Cases | Result | Notes |
|---|---|---|---|
| Retrieval precision@5 | 25 | 24/25 (96%) | stdev 0.00 across 5 runs; pipeline is deterministic |
| End-to-end | 15 | 10/15 (67%) | up from 7/15 baseline; 5 failures analyzed in docs/eval_analysis.md |

Cost per end-to-end run: ~$0.65 (Opus planner + Haiku critic; executor is LLM-free).

The one persistent retrieval failure is a cross-document query where single-query retrieval cannot surface chunks from two different document types simultaneously. The five end-to-end failures break down into: 1 test harness bug (JSON serialization, trivial fix), 3 planner SQL schema errors (schema grounding gap), and 1 non-deterministic executor behavior (flag attribution). Full analysis in [`docs/eval_analysis.md`](docs/eval_analysis.md).

## Key design decisions

- **Per-agent model routing.** Opus for planning (complex reasoning), Haiku for critique (structured comparison), no LLM in the executor (mechanical tool dispatch). Cost and capability matched to task. Configured in [`aether/config.py`](aether/config.py).
- **Direct Anthropic SDK, no LangChain.** Every decision in the system is visible code. No framework abstractions hiding retry logic, prompt assembly, or output parsing.
- **Pydantic validation on every agent output with retry-on-error.** The planner and critic both validate their LLM responses against Pydantic models. On validation failure, the error message is fed back to the LLM for up to 3 retries. This turns LLM non-determinism into structured reliability.
- **Hybrid retrieval with runtime-inferred query classification.** BM25 and dense search have complementary failure modes. A cross-encoder reranker reconciles them. Query classification (data vs. policy intent) is inferred dynamically from indexed column metadata, not hardcoded.
- **Engine/domain separation via `aether/prompts/`.** Few-shot examples are external text files loaded at init. Swap domains by pointing `PROMPTS_DIR` to a different directory. No engine code changes required.
- **SQLite trace of every LLM and tool call.** Auditability as a first-class concern. The trace store records run ID, agent, event type, input/output tokens, duration, and full payloads. Queryable in the Streamlit trace explorer.

## Quickstart

Prerequisites: Python 3.11, [uv](https://docs.astral.sh/uv/), an `ANTHROPIC_API_KEY`.

```bash
# Install dependencies
uv sync

# Set your API key
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# Run the demo (fund capital accounts reconciliation)
uv run python scripts/test_run.py

# Launch the Streamlit UI (run viewer + trace explorer + eval dashboard)
uv run streamlit run ui/app.py

# Run the retrieval eval suite (25 cases, 5 runs, variance report)
uv run python scripts/measure_retrieval_variance.py
```

## Project structure

```
aether/
  ingestion/       Document parsing: CSV, PDF, Excel, TXT --> Chunk objects
  rag/             Hybrid retriever: BM25 + Chroma dense + flashrank reranker
  agents/          Planner, Executor, Critic agent implementations
  tools/           Tool layer: LoadData, RunSQL, FlagItem, WriteReport
  models/          Pydantic models: ExecutionPlan, CritiqueResult, TraceEvent, Chunk
  trace/           SQLite trace store (WAL mode, indexed on run_id/event_type)
  prompts/         Domain-swappable few-shot examples (finance/ is the demo domain)
  config.py        Settings with per-agent model routing
  runtime.py       Orchestration loop: ingest -> retrieve -> plan -> execute -> critique

ui/                Streamlit app: Run tab, Trace Explorer, Eval Dashboard
evals/
  retrieval/       25-case retrieval precision@5 eval suite
  end_to_end/      15-case end-to-end eval suite (goal -> verdict)
scripts/           Demo runners, debug harnesses, variance measurement
data/
  demo/            Sample financial documents (CSV, TXT)
  uploads/         Runtime output directory (gitignored)
docs/              Eval gap analysis and project documentation
```

## What Aether is NOT

- Not a LangChain/LangGraph/CrewAI wrapper.
- Not a general-purpose AI assistant.
- Not trying to solve every domain. Finance is the demo; the engine is domain-agnostic.
- Not building a complex frontend. Streamlit is the MVP surface.

## Status

Weeks 1-6 complete: ingestion layer, hybrid RAG with retrieval evals, planner/executor/critic agent loop, 40-case eval suite, Streamlit UI with run viewer and trace explorer.

Planned: ComplianceOS — a thin product layer on top of Aether for RIA compliance workflows. Same engine, separate surface.
