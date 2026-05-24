# aether/runtime.py

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone

from aether.agents.critic import CriticAgent
from aether.agents.executor import ExecutorAgent
from aether.agents.loop_agent import LoopAgent
from aether.agents.planner import PlannerAgent
from aether.config import settings
from aether.ingestion.loader import DocumentLoader
from aether.models.agent_action import AgentObservation, LoopState, LoopStep
from aether.models.plan import ExecutionPlan, PlanStep
from aether.models.trace import TraceEvent
from aether.rag.retriever import HybridRetriever
from aether.tools.retrieve_context import RetrieveContextTool
from aether.trace.store import TraceStore

logger = logging.getLogger(__name__)

# DocumentLoader.load(path) -> list[Chunk]
# HybridRetriever.index(chunks) and .retrieve(query) -> list[Chunk]

_CREATE_REVIEW_QUEUE = """
CREATE TABLE IF NOT EXISTS human_review_queue (
    id         TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    reason     TEXT NOT NULL,
    plan       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class AetherRuntime:
    def __init__(self, extra_tools: dict | None = None, eval_mode: bool = False) -> None:
        self.loader = DocumentLoader()
        self.retriever = HybridRetriever(ephemeral=eval_mode)
        retrieve_tool = RetrieveContextTool(self.retriever)
        runtime_tools = {"retrieve_context": retrieve_tool}
        if extra_tools:
            runtime_tools.update(extra_tools)
        self.planner = PlannerAgent()
        self.executor = ExecutorAgent(extra_tools=runtime_tools)
        self.critic = CriticAgent()
        self.max_revisions = 2
        self._store = TraceStore(settings.db_path)
        # Ensure human_review_queue table exists (not part of TraceStore schema)
        with sqlite3.connect(str(settings.db_path)) as conn:
            conn.execute(_CREATE_REVIEW_QUEUE)

    def reset_tool_state(self) -> None:
        """Reset all per-run accumulated state in executor tools (flags, loaded tables, etc.)."""
        self.executor.reset_tool_state()

    def run_agentic(self, goal: str, file_paths: list[str], max_steps: int = 10) -> dict:
        """Hand-rolled REASON-ACT-OBSERVE loop.

        The model picks ONE tool per iteration, observes the result, then decides
        the next tool. This is additive — the one-shot run() baseline is untouched.

        Flow:
          ingest → retrieve (once, upfront) → build LoopState
          loop until is_final or max_steps:
            decide (LoopAgent) → dispatch_one (ExecutorAgent) → observe → append
          critique (CriticAgent, once, on accumulated state)
        """
        run_id = str(uuid.uuid4())
        self._store.write_event(TraceEvent(
            run_id=run_id,
            agent="runtime",
            event_type="run_start",
            payload={"goal": goal, "document_ids": file_paths, "mode": "agentic"},
        ))
        logger.info("Agentic run %s started — goal: %s", run_id, goal)

        # ── 1. Ingest + index ─────────────────────────────────────────────────
        all_chunks = []
        for path in file_paths:
            all_chunks.extend(self.loader.load(path))
        self.retriever.index(all_chunks)

        # ── 2. Initial retrieval ───────────────────────────────────────────────
        context_chunks = self.retriever.retrieve(goal)

        # ── 3. Build initial LoopState ────────────────────────────────────────
        loop_agent = LoopAgent()
        state = LoopState(
            run_id=run_id,
            goal=goal,
            initial_context=[c.content for c in context_chunks],
        )

        # ── 4. RAO loop ───────────────────────────────────────────────────────
        while len(state.steps) < max_steps and not state.is_complete:
            step_index = len(state.steps)
            step_id = f"rao_step_{step_index}"

            # 4a. Decide
            action, in_tok, out_tok = loop_agent.decide(state, file_paths=file_paths)
            logger.info(
                "Step %d: tool=%r is_final=%s in_tok=%d out_tok=%d",
                step_index, action.tool, action.is_final, in_tok, out_tok,
            )

            # 4c. Check for completion.
            #
            # The model may legitimately combine its final action with the
            # completion signal (e.g. write_report + is_final=true in one
            # response). The loop honours the named tool before terminating
            # so the action is never silently dropped. Termination is always
            # driven by the model's is_final — not by which tool was called.
            #
            # Two cases:
            #   is_final=true, tool=""  → terminate immediately (no dispatch)
            #   is_final=true, tool=X   → dispatch X, record observation, then terminate
            if action.is_final and not action.tool.strip():
                logger.info("Step %d: is_final=true, no tool — terminating", step_index)
                state.is_complete = True
                state.stop_reason = "is_final"
                break

            # 4d. Dispatch one tool through the existing executor
            result_dict, error_msg = self.executor.dispatch_one(
                tool_name=action.tool,
                args=action.tool_args,
                run_id=run_id,
                step_id=step_id,
            )

            # 4e. Wrap into AgentObservation
            observation = AgentObservation(
                success=error_msg is None,
                output=result_dict,
                error=error_msg,
            )

            # 4f. Append to loop state
            state.steps.append(LoopStep(
                step_index=step_index,
                action=action,
                observation=observation,
            ))
            logger.info(
                "Step %d observation: success=%s output_keys=%s",
                step_index, observation.success, list(result_dict.keys()),
            )

            # 4g. If this step carried is_final=true, terminate now that the
            #     tool has been dispatched and recorded.
            if action.is_final:
                logger.info(
                    "Step %d: is_final=true with tool=%r — dispatched, now terminating",
                    step_index, action.tool,
                )
                state.is_complete = True
                state.stop_reason = "is_final"
                break

        # ── 5. Max-steps guard ────────────────────────────────────────────────
        if not state.is_complete:
            state.stop_reason = "max_steps"
            logger.warning("Agentic run %s hit max_steps=%d without is_final", run_id, max_steps)

        # ── 6. Critic pass on accumulated state ───────────────────────────────
        # Build a minimal ExecutionPlan so the existing critic interface is satisfied.
        # Critic only uses plan.steps[*].name — no topological logic needed.
        plan_steps = [
            PlanStep(
                step_id=f"step_{ls.step_index}",
                name=ls.action.tool,
                description=ls.action.reasoning[:200],
                tool=ls.action.tool,
                tool_args=ls.action.tool_args,
                expected_output="RAO loop step",
            )
            for ls in state.steps
        ] or [
            # Critic requires min 1 step — add a placeholder if loop exited immediately
            PlanStep(
                step_id="step_0",
                name="no_op",
                description="Loop exited before any tool was called",
                tool="write_report",
                expected_output="n/a",
            )
        ]
        proxy_plan = ExecutionPlan(
            run_id=run_id,
            goal=goal,
            steps=plan_steps,
            reasoning=f"RAO loop: {len(state.steps)} step(s), stop_reason={state.stop_reason}",
        )
        executor_output = {
            f"step_{ls.step_index}": ls.observation.output
            for ls in state.steps
        }
        critique = self.critic.run(goal, proxy_plan, executor_output, run_id)

        # ── 7. Return ─────────────────────────────────────────────────────────
        result = {
            "run_id": run_id,
            "loop_state": state.model_dump(),
            "critique": critique.model_dump(),
            "steps_taken": len(state.steps),
            "stop_reason": state.stop_reason,
        }
        self._store.write_event(TraceEvent(
            run_id=run_id,
            agent="runtime",
            event_type="run_end",
            payload={
                "status": "success",
                "summary": f"{critique.overall_verdict}, {len(state.steps)} step(s), {state.stop_reason}",
            },
        ))
        logger.info(
            "Agentic run %s complete — %s (%d steps, %s)",
            run_id, critique.overall_verdict, len(state.steps), state.stop_reason,
        )
        return result

    def run(self, goal: str, file_paths: list[str], run_id: str | None = None) -> dict:
        run_id = run_id or str(uuid.uuid4())
        self._store.write_event(TraceEvent(
            run_id=run_id,
            agent="runtime",
            event_type="run_start",
            payload={"goal": goal, "document_ids": file_paths},
        ))
        logger.info("Run %s started — goal: %s", run_id, goal)

        # Ingest + index
        all_chunks = []
        for path in file_paths:
            chunks = self.loader.load(path)
            all_chunks.extend(chunks)
        self.retriever.index(all_chunks)

        # Retrieve
        chunks = self.retriever.retrieve(goal)

        # Plan → Execute → Critique
        plan = self.planner.run(goal, chunks, run_id, file_paths=file_paths)
        state = self.executor.run(plan)
        critique = self.critic.run(goal, plan, state, run_id)

        # Revision loop — retry on partial verdict
        revision_count = 0
        while critique.overall_verdict == "partial" and revision_count < self.max_revisions:
            revision_count += 1
            flag_text = "\n".join(f"- {f.description}" for f in critique.flags)
            augmented_goal = f"{goal}\n\nPrevious issues found:\n{flag_text}"
            logger.info("Revision %d/%d — re-planning", revision_count, self.max_revisions)
            plan = self.planner.run(augmented_goal, chunks, run_id, file_paths=file_paths)
            state = self.executor.run(plan)
            critique = self.critic.run(augmented_goal, plan, state, run_id)

        # Escalate on fail verdict
        if critique.overall_verdict == "fail":
            _queue_human_review(settings.db_path, run_id, critique.summary, plan.model_dump_json())

        result = {
            "run_id": run_id,
            "plan": plan.model_dump(),
            "output": state,
            "critique": critique.model_dump(),
            "revisions": revision_count,
        }
        self._store.write_event(TraceEvent(
            run_id=run_id,
            agent="runtime",
            event_type="run_end",
            payload={"status": "success", "summary": f"{critique.overall_verdict}, {revision_count} revision(s)"},
        ))
        logger.info("Run %s complete — %s (%d revision(s))", run_id, critique.overall_verdict, revision_count)
        return result


def _queue_human_review(db_path: object, run_id: str, reason: str, plan_json: str) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO human_review_queue (id, run_id, reason, plan, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), run_id, reason, plan_json, datetime.now(timezone.utc).isoformat()),
        )
    logger.warning("Run %s escalated to human review: %s", run_id, reason)
