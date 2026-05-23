# Aether Progress

## Completed

### Week 1: Ingestion + RAG
- `aether/models/chunk.py` — Chunk + ChunkMetadata Pydantic models
- `aether/ingestion/loader.py` — DocumentLoader (CSV, PDF, Excel → Chunks)
- `aether/rag/retriever.py` — HybridRetriever (BM25 + dense + flashrank rerank, Chroma backend)
- `aether/config.py` — pydantic-settings config with all fields
- `aether/trace/store.py` — SQLite trace store
- `aether/models/trace.py` — TraceEvent model
- `test_ingestion.py` — smoke test (loader → index → retrieve), confirmed working

### Week 2: Planner Agent
- `aether/models/plan.py` — PlanStep + ExecutionPlan with validators + topological_order()
- `aether/agents/planner.py` — PlannerAgent (goal + chunks → ExecutionPlan, 3-retry loop)

## Stack
- Chroma (local, no Docker) for vector store
- sentence-transformers (all-MiniLM-L6-v2) for embeddings
- flashrank (ms-marco-MiniLM-L-12-v2) for reranking
- rank-bm25 for keyword search
- Anthropic SDK (claude-sonnet-4-6) for LLM calls
- SQLite for trace store

## Next Steps (when credits are back)
1. Test PlannerAgent end-to-end with a real goal + demo CSV
2. Week 3: Executor (step-by-step tool dispatch)
3. Week 4: Critic agent + run loop
4. Week 5: Eval framework
5. Week 6: Streamlit UI
