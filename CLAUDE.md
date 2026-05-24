# Aether — Agentic Workflow Reasoning Engine
# CLAUDE.md — Project Context for AI Assistants

## What This Project Is
Aether is an agentic workflow reasoning engine. Given a document corpus and 
a high-level goal, it reasons about what to do, calls tools to take actions, 
critiques the result, and produces an auditable output with a full trace.
Currently a plan-execute-critique system with a bounded revision loop, being 
upgraded into a reason-act-observe agent. The demo domain is finance; the 
architecture is intended to be domain-agnostic.

## What This Project Is NOT
- Not a LangChain / LangGraph / CrewAI wrapper (direct SDK; LangGraph only 
  AFTER the loop is hand-rolled and understood)
- Not a general-purpose assistant
- Finance is the demo; legal/health are measurement domains

## Architecture (current — audited reality)
User Goal + Documents
→ INGESTION (parse CSV/PDF/Excel/TXT → chunks, SHA-256 fingerprinting)
→ RAG (hybrid BM25 + dense via ChromaDB → RRF → cross-encoder rerank)
→ PLANNER (goal + context → structured ExecutionPlan)
→ EXECUTOR (ZERO LLM calls — deterministic Python tool dispatch)
→ CRITIC (output vs goal → CritiqueResult, verdict pass/partial/fail)
→ revision loop: "partial" → replan (max 2); "fail" → human_review_queue
→ TRACE STORE (every LLM + tool call → SQLite)
→ STREAMLIT UI (Run / Trace Explorer / Eval Dashboard)

### Key design facts (do not misdescribe)
- The EXECUTOR principle (evolved): data operations are deterministic;
  synthesis is an explicit, auditable LLM step.
  - load_data / run_sql / flag_item / write_report: ZERO LLM calls.
    Deterministic Python tool dispatch. The executor does not reason.
  - answer_from_context: the deliberate, isolated exception. ONE LLM call
    per invocation to synthesize a grounded answer from retrieved evidence.
    Clearly separated from data tools; returns grounded/insufficient_context
    flags so the critic can audit the synthesis. Do NOT blur this boundary
    by adding LLM calls to the other four tools.
  - There is NO executor model slot. answer_from_context routes via
    planner_provider/planner_model (same frontier model, same routing).
- CRITIC uses overall_verdict ("pass"/"partial"/"fail").
- CritiqueFlag uses severity ("critical"/"warning"/"info").

## Real Tools (five data tools + synthesis)
- load_data, run_sql, flag_item, write_report  ← deterministic, zero LLM
- retrieve_context  ← wired via runtime extra_tools, wraps HybridRetriever
- answer_from_context  ← wired via runtime extra_tools, makes ONE LLM call;
  takes question + context chunks, returns grounded synthesized answer.
  This is what enables reasoning over text documents, not just retrieval.

## Non-Negotiable Patterns
1. Every agent output is a Pydantic model. No freeform dicts.
2. Every LLM and tool call writes a row to the trace store.
3. Retry logic on all LLM calls (max 3, error context appended).
4. Structured output validation before any downstream use.
5. Direct SDK. No LangChain/CrewAI. (LangGraph: only after hand-rolling.)

## Model Routing (per-agent, provider-switchable)
- Each agent (planner, critic) has a provider: "ollama", "openai", or "anthropic".
- DEFAULT (current): OpenAI gpt-5.4-mini for both planner and critic.
  Set via PLANNER_PROVIDER=openai / CRITIC_PROVIDER=openai in .env.
  Model: planner_model_openai / critic_model_openai = "gpt-5.4-mini".
- Local fallback: Ollama (OpenAI-compatible endpoint, CPU inference).
  Preserved as documented fallback AND forensic comparison baseline.
  Do NOT delete. Switch via PLANNER_PROVIDER=ollama / CRITIC_PROVIDER=ollama.
  Local models: planner_model_local=mistral, critic_model_local=phi4-mini.
- Anthropic path preserved for future API baseline comparisons.
- Executor: NO model (deterministic). The old executor_model config is
  REMOVED — do not re-add it.
- Retriever, trace store, executor, embeddings: always LOCAL (no provider
  routing — ChromaDB, SQLite, sentence-transformers run on-machine).

## Local Model Infra (current reality)
- Ollama on CPU. GPU (AMD RX 6600 XT / gfx1032) is NOT used — Windows ROCm 
  does not detect it. Do NOT retry Windows ROCm; GPU is deferred to a 
  future Linux/WSL2 setup.
- Working local model: phi4-mini. ~15 tok/s on CPU. Acceptable for dev/eval.

## Stack
Python 3.11 + uv · anthropic SDK · openai SDK (for Ollama endpoint) · 
pydantic v2 + pydantic-settings · chromadb (local) · sentence-transformers 
+ rank-bm25 · flashrank · streamlit · duckdb + pandas · sqlite3 · pytest

## Current Upgrade (v1 scope — LOCKED)
Convert one-shot plan-execute into a hand-rolled REASON-ACT-OBSERVE loop:
model sees task → picks ONE tool → observes result → decides next → loops 
until done. Preserve deterministic executor and audit trail. Make 
retrieve_context callable mid-loop (DONE). Granular failure recovery 
instead of whole-run abort. Run across 2-3 domains measuring whether small 
local agentic decision quality holds (task success, steps-to-completion, 
wrong-tool-calls, recovery success).
OUT OF v1: parallel agents, MCP, Next.js UI, LangGraph.

## Working Style
- Claude.ai writes prompts pasted into Claude Code; prompts in code blocks.
- Brutal honesty over capitulation; push back on scope creep on merit.
- Engine before UI. Journal the build. Measurement over assertion.
- Forensic journal at docs/journal/ — every session ends with an entry.
- Validation log at docs/aether-validation-log.md — track where abstractions 
  fit/friction during the upgrade.

## For Claude Code
- This CLAUDE.md is ground truth. docs/original_plan.md and 
  docs/progress-historical.md are archived history, NOT current spec.
- Output only requested files with full content; each file block starts 
  with a # filename comment.
- All agent outputs are Pydantic. Trace store is SQLite. ChromaDB not Qdrant.
- Do not add dependencies without checking pyproject.toml. No LangChain.
- END EVERY SESSION with a recap: what changed, what's next.
