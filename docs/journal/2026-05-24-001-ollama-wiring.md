# 2026-05-24-001 — Ollama Provider Wiring

**Session goal:** Wire planner + critic to local Ollama; verify a local
model can produce a valid plan.

**Decisions made:**
- Both agents provider-switchable (planner_provider/critic_provider),
  default ollama, single config field each
- llm_client.chat() returns ChatResult(text, input_tokens, output_tokens)
  — preserves trace-store token logging across both providers
- per-agent max_tokens preserved (planner 4096, critic 1500)
- Created CLAUDE.md (was missing from repo entirely)

**What happened:**
- Wiring works end-to-end: valid ExecutionPlan first attempt, real tokens
  in trace, routing correct
- phi4-mini dropped flag_item (logged in validation log as first
  small-model finding — semantic omission, not malformed output)
- ~6 tok/s on CPU for the large planner prompt; 81s for one plan

**Measurements:** planner phi4-mini: 3 steps, 81.2s, 1002/484 tokens,
attempt 1/3, flag_item omitted

**Next session:** Decide planner model strategy (phi4-mini vs pull
Mistral 7B). Then build the hand-rolled RAO loop — where per-step
reasoning may actually FIX the flag_item omission, since the model
observes intermediate results instead of planning blind.
