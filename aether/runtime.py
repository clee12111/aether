# aether/runtime.py

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone

from aether.agents.critic import CriticAgent
from aether.agents.executor import ExecutorAgent
from aether.agents.planner import PlannerAgent
from aether.config import settings
from aether.ingestion.loader import DocumentLoader
from aether.models.trace import TraceEvent
from aether.rag.retriever import HybridRetriever
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
    def __init__(self, extra_tools: dict | None = None) -> None:
        self.loader = DocumentLoader()
        self.retriever = HybridRetriever()
        self.planner = PlannerAgent()
        self.executor = ExecutorAgent(extra_tools=extra_tools)
        self.critic = CriticAgent()
        self.max_revisions = 2
        self._store = TraceStore(settings.db_path)
        # Ensure human_review_queue table exists (not part of TraceStore schema)
        with sqlite3.connect(str(settings.db_path)) as conn:
            conn.execute(_CREATE_REVIEW_QUEUE)

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
