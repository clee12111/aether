# Aether

A workflow reasoning engine for financial documents. Given messy CSVs, PDFs, and text files plus a plain-language goal, Aether discovers the steps needed to achieve that goal, executes them with tools, critiques the output, and produces an auditable result with a full trace.

## What it is

Financial data work is messy. A compliance analyst gets a pile of CSVs, a fund agreement PDF, and a policy document, and needs to reconcile numbers, cross-reference rules, and flag violations. The steps vary depending on the data and the goal. Aether automates this: you provide documents and a goal, and it figures out what to do, does it, and tells you whether it worked.

The system runs a hand-rolled reason-act-observe loop over a hybrid RAG layer. On each step the **loop agent** (gpt-5.4-mini) picks one tool, dispatches it, observes the result, and decides the next step. Tools are deterministic (load data into DuckDB, run SQL, retrieve context, flag items, write reports) with zero LLM in the execution path, except for `answer_from_context` which makes one explicit, auditable LLM call to synthesize a grounded answer from retrieved evidence. The **critic** (gpt-5.4-mini) compares the final output against the original goal and returns a structured verdict. Every LLM call and tool call is recorded in a SQLite trace store.

The engine is domain-agnostic. Finance is the demo domain, but the only finance-specific content lives in `aether/prompts/finance/` (few-shot examples) and `data/demo/` (sample documents). Swap `PROMPTS_DIR` to a different directory, provide different documents, and the engine works the same way. See [`aether/prompts/README.md`](aether/prompts/README.md) for details. Built after a prior crypto tax tooling project ([chaintax](https://github.com/clee12111/chaintax)) that surfaced cross-source data reconciliation as the deeper problem than tax filing itself.

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
REASON-ACT-OBSERVE LOOP                                [gpt-5.4-mini]
  |  pick tool --> dispatch --> observe --> decide next
  |  tools: load_data, run_sql, retrieve_context, answer_from_context,
  |          flag_item, write_report  (executor is deterministic, zero LLM)
  |
  v
CRITIC             output vs goal --> CritiqueResult   [gpt-5.4-mini]
  |
  +---> verdict: pass ----> return result
  +---> verdict: partial -> re-plan with feedback (max 2 revisions)
  +---> verdict: fail ----> escalate to human review queue
  |
  v
TRACE STORE        every LLM call + tool call --> SQLite (WAL mode)
```

## Results

Evaluated on the [T2-RAGBench](https://huggingface.co/datasets/G4KMU/t2-ragbench) FinQA test split.

| Eval | Cases | Result | Notes |
|---|---|---|---|
| Retrieval R@5 | 200 queries / 380-doc pool | 86.0% | R@1=68.0%, R@3=80.5%, MRR@3=73.8%, nDCG@5=77.8% |
| End-to-end (Number-Match v2) | 200 | 151/200 (75.5%) | gpt-5.4-mini; v2 normalizer recovers convention-mismatch cases |

The 24.5% miss rate breaks down: ~14% formula/interpretation convention mismatches (model outputs percent, gold expects decimal), ~6% wrong-row/period selection, ~5% genuine reasoning ceiling. The Number-Match v2 normalizer already recovers the convention-mismatch cases; remaining misses are model-limit or benchmark artifacts.

## Key design decisions

- **Reason-act-observe loop, not one-shot planning.** The model sees the goal, picks one tool, observes the result, then decides the next step. This allows mid-loop retrieval, course-correction on bad SQL, and granular failure recovery — impossible in a static plan.
- **Deterministic executor, explicit synthesis.** `load_data`, `run_sql`, `flag_item`, `write_report` make zero LLM calls. `answer_from_context` is the deliberate exception — one auditable LLM call that returns `grounded`/`insufficient_context` flags so the critic can verify it. The boundary is enforced in code, not convention.
- **Per-agent model routing, provider-switchable.** Planner and critic each have a configurable provider (`openai`, `ollama`, `anthropic`). Default is OpenAI gpt-5.4-mini. Local Ollama (phi4-mini, CPU) preserved as fallback. Configured in [`aether/config.py`](aether/config.py) and `.env`.
- **Pydantic validation on every agent output with retry-on-error.** The loop agent and critic both validate their LLM responses against Pydantic models. On validation failure, the error message is fed back to the LLM for up to 3 retries.
- **Hybrid retrieval with cross-encoder reranking.** BM25 and dense search have complementary failure modes. A flashrank cross-encoder reranker reconciles them. Query classification (data vs. policy intent) is inferred dynamically from indexed column metadata, not hardcoded.
- **Engine/domain separation via `aether/prompts/`.** Few-shot examples are external text files loaded at init. Swap domains by pointing `PROMPTS_DIR` to a different directory. No engine code changes required.
- **SQLite trace of every LLM and tool call.** Auditability as a first-class concern. The trace store records run ID, agent, event type, input/output tokens, duration, and full payloads. Queryable in the Streamlit trace explorer.

## Quickstart

Prerequisites: Python 3.11, [uv](https://docs.astral.sh/uv/), an `OPENAI_API_KEY`.

```bash
# Install dependencies
uv sync

# Set your API key
cp .env.example .env
# Edit .env and set OPENAI_API_KEY=sk-...

# Run the demo (fund compliance check)
uv run python scripts/test_run.py

# Launch the Streamlit UI (run viewer + trace explorer + eval dashboard)
uv run streamlit run ui/app.py

# Run the full retrieval eval (200 queries, 380-doc pool)
uv run python eval_retrieval.py

# Run the full end-to-end eval (200 FinQA records, incremental/resumable)
uv run python eval_e2e_200.py
```

## Project structure

```
aether/
  ingestion/       Document parsing: CSV, PDF, Excel, TXT --> Chunk objects
  rag/             Hybrid retriever: BM25 + Chroma dense + flashrank reranker
  agents/          Loop agent, planner, executor, critic implementations
  tools/           Tool layer: load_data, run_sql, retrieve_context,
                   answer_from_context, flag_item, write_report
  models/          Pydantic models: ExecutionPlan, CritiqueResult, TraceEvent, Chunk
  trace/           SQLite trace store (WAL mode, indexed on run_id/event_type)
  prompts/         Domain-swappable few-shot examples (finance/ is the demo domain)
  config.py        Settings with per-agent model routing and provider switching
  runtime.py       AetherRuntime: ingest -> RAG -> reason-act-observe loop -> critique

ui/                Streamlit app: Run tab, Trace Explorer, Eval Dashboard
evals/
  end_to_end/      15-case end-to-end eval suite (goal -> verdict)
eval_retrieval.py  200-query retrieval eval (R@1/3/5, MRR@3, nDCG@5)
eval_e2e_200.py    200-record end-to-end eval with Number-Match v2 scoring
scripts/
  test_run.py      Demo runner: fund compliance check end-to-end
data/
  demo/            Sample financial documents (CSV, TXT)
  uploads/         Runtime output directory (gitignored)
docs/              Eval gap analysis, journal, and project documentation
```

## What Aether is NOT

- Not a LangChain/LangGraph/CrewAI wrapper. Direct SDK; every decision is visible code.
- Not a general-purpose AI assistant.
- Not trying to solve every domain. Finance is the demo; the engine is domain-agnostic.
- Not building a complex frontend. Streamlit is the MVP surface.
- **No OCR.** Ingestion uses pdfplumber text extraction, which requires a PDF text layer. Born-digital PDFs, CSVs, and TXT files work. Screenshots, photos, and scanned (image-only) PDFs are not supported — they raise an ingestion error. OCR preprocessing is a documented next step, not built.

## Status

Foundational implementation complete: ingestion layer (CSV/PDF/Excel/TXT), hybrid RAG with retrieval evals, hand-rolled reason-act-observe loop, planner/executor/critic agents, eval harness (200-record FinQA benchmark), Streamlit UI with run viewer and trace explorer. Built solo over ~8 weeks.

### Next: retrieval and reasoning quality

The n=200 eval surfaces two failure modes worth addressing:

- **Cross-document queries.** When the answer requires evidence from two document types simultaneously, single-query retrieval misses one. Fix: planner-driven query decomposition — rewrite the goal into sub-queries, retrieve per sub-query, merge results before synthesis.
- **Formula convention mismatches.** Model outputs percentage where gold expects decimal (e.g. 31.3% vs 0.313). The v2 normalizer already rescues these for eval scoring; the underlying fix is better fewshots that show the expected output format.
- **Wrong-row/period selection.** SQL queries sometimes pull the wrong fiscal year. Fix: richer column metadata in the prompt so the model can identify temporal anchors before writing the query.
