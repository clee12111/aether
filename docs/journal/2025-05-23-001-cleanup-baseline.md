**Session goal:** Establish a clean, audited baseline before upgrade work begins.

**Decisions made:**
- Streamlit UI kept (not replaced with Next.js) — the loop is the interview signal,
  not the frontend
- Breadth deferred — strict sequence: loop → local models → domain expansion
- LangGraph deferred — hand-roll the RAO loop first, adopt LangGraph in phase 2
  after understanding what it abstracts
- v1 scope locked: RAO loop + deterministic executor preserved + retrieve_context
  fixed + Ollama-served swappable model slots + domain-general tools +
  cross-domain measurement

**What happened:**
- Removed executor_model dead config (config.py:36, zero other references)
- Removed QDRANT_HOST, QDRANT_PORT, QDRANT_COLLECTION from .env.example
- Deleted docker-compose.yml (Qdrant-only, never used)
- Removed fastapi + uvicorn from pyproject.toml (FastAPI backend was planned,
  never built)
- Moved 4 floating scripts to scratch/: test_ingestion.py, test_run.py,
  debug_flag_case.py, measure_retrieval_variance.py
- Deleted empty scripts/ directory
- Committed as 73c4878

**Pre-existing issues noted (not fixed):**
- ownership_over_20pct e2e test fails: must_flag_partner "Sequoia Capital LP"
  no longer in demo data after synthetic rename commit 5a8dddd — test fixture
  not updated to match
- retrieve_context ghost tool: in planner prompt, not registered in executor
  dispatch dict — fix is step 3 of upgrade sequence
- flag_10pct_deviation e2e fails: planner bulk-flags all 6 rows instead
  of filtering first; critic correctly returns "fail"; deferred to RAO
  loop (logged in validation log)

**Measurements:**
- pytest baseline: 11 passed, 2+ known failures (all pre-existing)
- flag_10pct_deviation: planner bulk-flagging issue, deferred to RAO loop
- Further e2e runs suspended — synthetic demo data will be replaced during
  domain expansion; running Anthropic API calls against it has no value
- Validation log updated with flag_10pct_deviation finding

**Next session:** Begin upgrade work. First task: fix retrieve_context
(register it in executor dispatch dict so it's a real callable tool).
Then begin hand-rolled RAO loop.
