"""
SQLite-backed trace store for Aether.

Every LLM call, tool call, validation error, and lifecycle event is written
here as an immutable row. The store is the single source of truth for
auditing, debugging, and the Streamlit trace explorer.

Schema (single table ``trace_events``):
    event_id        TEXT PRIMARY KEY   — UUID4
    run_id          TEXT NOT NULL      — groups events into runs
    step_id         TEXT               — nullable PlanStep reference
    event_type      TEXT NOT NULL      — discriminator (see EventType)
    agent           TEXT NOT NULL      — emitting component
    model           TEXT               — Claude model ID (LLM events only)
    input_tokens    INTEGER            — prompt token count (LLM events only)
    output_tokens   INTEGER            — completion token count (LLM events only)
    prompt_hash     TEXT               — first 16 hex chars of SHA-256(prompt)
    payload         TEXT NOT NULL      — JSON-serialised event content
    error           TEXT               — error message on failure paths
    duration_ms     INTEGER            — wall-clock time in milliseconds
    attempt         INTEGER NOT NULL   — retry attempt number (1 = first)
    created_at      TEXT NOT NULL      — ISO-8601 UTC timestamp

Usage::

    from aether.trace.store import TraceStore
    from aether.models.trace import TraceEvent

    store = TraceStore("./aether_trace.db")
    store.write_event(TraceEvent(...))
    events = store.get_run_events(run_id="abc-123")
    runs   = store.get_all_runs()
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from aether.models.trace import TraceEvent

logger = logging.getLogger(__name__)

# ── DDL ───────────────────────────────────────────────────────────────────────

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS trace_events (
    event_id        TEXT    PRIMARY KEY,
    run_id          TEXT    NOT NULL,
    step_id         TEXT,
    event_type      TEXT    NOT NULL,
    agent           TEXT    NOT NULL,
    model           TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    prompt_hash     TEXT,
    payload         TEXT    NOT NULL,
    error           TEXT,
    duration_ms     INTEGER,
    attempt         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL
);
"""

_CREATE_IDX_RUN_ID = "CREATE INDEX IF NOT EXISTS idx_run_id ON trace_events(run_id);"
_CREATE_IDX_EVENT_TYPE = "CREATE INDEX IF NOT EXISTS idx_event_type ON trace_events(event_type);"
_CREATE_IDX_CREATED_AT = "CREATE INDEX IF NOT EXISTS idx_created_at ON trace_events(created_at);"

_INSERT_EVENT = """
INSERT INTO trace_events (
    event_id, run_id, step_id, event_type, agent, model,
    input_tokens, output_tokens, prompt_hash, payload,
    error, duration_ms, attempt, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_SELECT_RUN_EVENTS = """
SELECT * FROM trace_events
WHERE run_id = ?
ORDER BY created_at ASC, rowid ASC;
"""

_SELECT_ALL_RUNS = """
SELECT
    run_id,
    MIN(created_at)  AS started_at,
    MAX(created_at)  AS last_event_at,
    COUNT(*)         AS event_count,
    SUM(CASE WHEN input_tokens  IS NOT NULL THEN input_tokens  ELSE 0 END) AS total_input_tokens,
    SUM(CASE WHEN output_tokens IS NOT NULL THEN output_tokens ELSE 0 END) AS total_output_tokens,
    SUM(CASE WHEN duration_ms   IS NOT NULL THEN duration_ms   ELSE 0 END) AS total_duration_ms,
    SUM(CASE WHEN event_type = 'validation_error' THEN 1 ELSE 0 END)       AS validation_errors,
    MAX(CASE WHEN event_type = 'run_end'
             THEN json_extract(payload, '$.status') END)                   AS final_status
FROM trace_events
GROUP BY run_id
ORDER BY started_at DESC;
"""

_SELECT_EVENT_BY_ID = "SELECT * FROM trace_events WHERE event_id = ?;"


# ── TraceStore ────────────────────────────────────────────────────────────────

class TraceStore:
    """Append-only SQLite writer and reader for Aether trace events.

    Thread safety: Each public method opens and closes its own connection,
    so the store is safe to use from multiple threads or coroutines
    (SQLite WAL mode is enabled on init).

    Args:
        db_path: File path for the SQLite database. Will be created if it
                 does not exist. Pass ``":memory:"`` for an in-process
                 ephemeral store (useful in tests).
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._init_db()
        logger.info("TraceStore initialised at %s", self.db_path)

    # ── Internal helpers ───────────────────────────────────────────────────────

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Open a connection, enable WAL + foreign keys, yield, then close."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Create the schema and indexes if they do not already exist."""
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE)
            conn.execute(_CREATE_IDX_RUN_ID)
            conn.execute(_CREATE_IDX_EVENT_TYPE)
            conn.execute(_CREATE_IDX_CREATED_AT)

    @staticmethod
    def _event_to_row(event: TraceEvent) -> tuple[Any, ...]:
        """Serialise a TraceEvent into the ordered tuple expected by _INSERT_EVENT."""
        return (
            event.event_id,
            event.run_id,
            event.step_id,
            event.event_type,
            event.agent,
            event.model,
            event.input_tokens,
            event.output_tokens,
            event.prompt_hash,
            json.dumps(event.payload, default=str),
            event.error,
            event.duration_ms,
            event.attempt,
            event.created_at.isoformat(),
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> TraceEvent:
        """Deserialise a SQLite row back into a TraceEvent."""
        d = dict(row)
        d["payload"] = json.loads(d["payload"])
        raw_ts = d["created_at"]
        # Handle both "+00:00" and "Z" suffixes from older rows
        if raw_ts.endswith("Z"):
            raw_ts = raw_ts[:-1] + "+00:00"
        d["created_at"] = datetime.fromisoformat(raw_ts).replace(tzinfo=timezone.utc)
        return TraceEvent(**d)

    # ── Public API ─────────────────────────────────────────────────────────────

    def write_event(self, event: TraceEvent) -> None:
        """Persist a single TraceEvent to the store.

        This is the hot path — called after every LLM and tool call. It is
        intentionally synchronous and raises on any SQLite error so callers
        know immediately if tracing has broken.

        Args:
            event: A fully-validated TraceEvent instance.

        Raises:
            sqlite3.IntegrityError: If event_id already exists (duplicate write).
            sqlite3.OperationalError: On database I/O failures.
        """
        row = self._event_to_row(event)
        with self._connect() as conn:
            conn.execute(_INSERT_EVENT, row)
        logger.debug(
            "Trace event written: run=%s type=%s agent=%s event_id=%s",
            event.run_id,
            event.event_type,
            event.agent,
            event.event_id,
        )

    def write_events(self, events: list[TraceEvent]) -> None:
        """Persist multiple TraceEvents in a single transaction.

        Prefer this over repeated ``write_event`` calls when flushing a batch.

        Args:
            events: List of fully-validated TraceEvent instances.
        """
        if not events:
            return
        rows = [self._event_to_row(e) for e in events]
        with self._connect() as conn:
            conn.executemany(_INSERT_EVENT, rows)
        logger.debug("Batch wrote %d trace events for run=%s", len(events), events[0].run_id)

    def get_run_events(self, run_id: str) -> list[TraceEvent]:
        """Return all events for a run, ordered by created_at ascending.

        Args:
            run_id: The run identifier to fetch events for.

        Returns:
            List of TraceEvent objects (may be empty if run_id not found).
        """
        with self._connect() as conn:
            rows = conn.execute(_SELECT_RUN_EVENTS, (run_id,)).fetchall()
        events = [self._row_to_event(r) for r in rows]
        logger.debug("Fetched %d events for run=%s", len(events), run_id)
        return events

    def get_all_runs(self) -> list[dict[str, Any]]:
        """Return a summary row for every run in the store, newest first.

        Each dict in the returned list has the keys:
            run_id, started_at, last_event_at, event_count,
            total_input_tokens, total_output_tokens, total_duration_ms,
            validation_errors, final_status

        Returns:
            List of run summary dicts, ordered by started_at descending.
        """
        with self._connect() as conn:
            rows = conn.execute(_SELECT_ALL_RUNS).fetchall()
        return [dict(r) for r in rows]

    def get_event(self, event_id: str) -> TraceEvent | None:
        """Fetch a single event by its event_id.

        Args:
            event_id: UUID4 of the event.

        Returns:
            The TraceEvent if found, or None.
        """
        with self._connect() as conn:
            row = conn.execute(_SELECT_EVENT_BY_ID, (event_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_event(row)

    def get_run_token_usage(self, run_id: str) -> dict[str, int]:
        """Return aggregated token counts for a run.

        Args:
            run_id: The run identifier.

        Returns:
            Dict with keys: input_tokens, output_tokens, total_tokens.
        """
        sql = """
            SELECT
                COALESCE(SUM(input_tokens),  0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens
            FROM trace_events
            WHERE run_id = ? AND event_type IN ('llm_call', 'llm_response');
        """
        with self._connect() as conn:
            row = conn.execute(sql, (run_id,)).fetchone()
        d = dict(row)
        d["total_tokens"] = d["input_tokens"] + d["output_tokens"]
        return d
