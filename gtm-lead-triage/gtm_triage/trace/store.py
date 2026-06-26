"""Lightweight SQLite trace store for the GTM lead-triage agent.

Every event (run_start, llm_call, tool_call, tool_response, run_end) writes a
row keyed by run_id. Uses a SINGLE persistent connection for in-memory stores.

Phase 1.5: added input_tokens, output_tokens, duration_ms columns to support
latency and cost measurement per run.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS trace_events (
    event_id      TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    agent         TEXT NOT NULL,
    payload       TEXT NOT NULL,
    error         TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    duration_ms   INTEGER,
    created_at    TEXT NOT NULL
);
"""

_CREATE_IDEMPOTENCY = """
CREATE TABLE IF NOT EXISTS idempotency_keys (
    idem_key   TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    result     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

_CREATE_DAILY_USAGE = """
CREATE TABLE IF NOT EXISTS daily_usage (
    usage_date TEXT PRIMARY KEY,
    count      INTEGER NOT NULL DEFAULT 0
);
"""

_CREATE_IDX = "CREATE INDEX IF NOT EXISTS idx_trace_run ON trace_events(run_id);"

_INSERT = """
INSERT INTO trace_events
    (event_id, run_id, event_type, agent, payload, error, input_tokens, output_tokens, duration_ms, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""


class TraceStore:
    """Append-only SQLite trace store.

    Holds a single persistent connection. For :memory: databases this is
    critical — a new connection would be an empty database every time.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_CREATE_TABLE)
        self._conn.execute(_CREATE_IDEMPOTENCY)
        self._conn.execute(_CREATE_DAILY_USAGE)
        self._conn.execute(_CREATE_IDX)
        self._conn.commit()

    def write(
        self,
        *,
        run_id: str,
        event_type: str,
        agent: str,
        payload: dict[str, Any],
        error: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        duration_ms: int | None = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            _INSERT,
            (event_id, run_id, event_type, agent,
             json.dumps(payload, default=str), error,
             input_tokens, output_tokens, duration_ms, now),
        )
        self._conn.commit()
        return event_id

    def get_run_events(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM trace_events WHERE run_id = ? ORDER BY created_at ASC",
            (run_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            result.append(d)
        return result

    def get_run_stats(self, run_id: str) -> dict[str, Any]:
        """Return aggregated stats for a run: total tokens, duration, estimated cost.

        Cost estimate uses gpt-4o-mini pricing as a baseline:
          input:  $0.15 / 1M tokens
          output: $0.60 / 1M tokens
        """
        row = self._conn.execute(
            """SELECT
                COALESCE(SUM(input_tokens), 0)  AS total_input_tokens,
                COALESCE(SUM(output_tokens), 0) AS total_output_tokens,
                COALESCE(SUM(duration_ms), 0)    AS total_duration_ms,
                COUNT(CASE WHEN input_tokens IS NOT NULL THEN 1 END) AS llm_call_count
            FROM trace_events
            WHERE run_id = ?""",
            (run_id,),
        ).fetchone()
        d = dict(row)
        inp = d["total_input_tokens"]
        out = d["total_output_tokens"]
        d["estimated_cost_usd"] = round(inp * 0.15 / 1_000_000 + out * 0.60 / 1_000_000, 6)
        return d

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT run_id,
                      MIN(created_at) AS started_at,
                      COUNT(*) AS event_count
               FROM trace_events
               GROUP BY run_id
               ORDER BY MIN(created_at) DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        results = []
        for r in rows:
            run_id = r["run_id"]
            # Get run_end payload for tier/route/email
            end_row = self._conn.execute(
                "SELECT payload FROM trace_events WHERE run_id = ? AND event_type = 'run_end' LIMIT 1",
                (run_id,),
            ).fetchone()
            entry: dict[str, Any] = {
                "run_id": run_id,
                "started_at": r["started_at"],
                "event_count": r["event_count"],
            }
            if end_row:
                p = json.loads(end_row["payload"])
                entry["lead_email"] = p.get("lead_email", "")
                entry["final_tier"] = p.get("final_tier", "")
                entry["final_route"] = p.get("final_route", "")
                entry["steps"] = p.get("steps_taken", 0)
            results.append(entry)
        return results

    # ── Idempotency ────────────────────────────────────────────────────────

    def get_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT run_id, result FROM idempotency_keys WHERE idem_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return {"run_id": row["run_id"], "result": json.loads(row["result"])}

    def store_idempotency_key(self, key: str, run_id: str, result: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR IGNORE INTO idempotency_keys (idem_key, run_id, result, created_at) "
            "VALUES (?, ?, ?, ?)",
            (key, run_id, json.dumps(result, default=str), now),
        )
        self._conn.commit()

    # ── Daily usage cap ─────────────────────────────────────────────────────

    def get_daily_usage(self) -> int:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = self._conn.execute(
            "SELECT count FROM daily_usage WHERE usage_date = ?", (today,),
        ).fetchone()
        return row["count"] if row else 0

    def increment_daily_usage(self) -> int:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._conn.execute(
            "INSERT INTO daily_usage (usage_date, count) VALUES (?, 1) "
            "ON CONFLICT(usage_date) DO UPDATE SET count = count + 1",
            (today,),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT count FROM daily_usage WHERE usage_date = ?", (today,),
        ).fetchone()
        return row["count"] if row else 1

    # ── Result lookup by run_id ──────────────────────────────────────────

    def get_result_by_run_id(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT result FROM idempotency_keys WHERE run_id = ?", (run_id,),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["result"])

    def close(self) -> None:
        self._conn.close()
