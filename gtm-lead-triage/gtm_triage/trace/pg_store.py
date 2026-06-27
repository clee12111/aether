"""Postgres-backed trace store using psycopg (sync).

Drop-in replacement for TraceStore (SQLite). Selected when DATABASE_URL is set.
Same method signatures, same return shapes.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row


_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS trace_events (
    event_id      TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    agent         TEXT NOT NULL,
    payload       JSONB NOT NULL,
    error         TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    duration_ms   INTEGER,
    created_at    TIMESTAMPTZ NOT NULL
);
"""

_CREATE_EVENTS_IDX = """
CREATE INDEX IF NOT EXISTS idx_trace_run ON trace_events(run_id);
"""

_CREATE_IDEMPOTENCY = """
CREATE TABLE IF NOT EXISTS idempotency_keys (
    idem_key   TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    result     JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
"""

_CREATE_DAILY_USAGE = """
CREATE TABLE IF NOT EXISTS daily_usage (
    usage_date TEXT PRIMARY KEY,
    count      INTEGER NOT NULL DEFAULT 0
);
"""


class PostgresTraceStore:
    """Append-only Postgres trace store with the same interface as TraceStore."""

    def __init__(self, dsn: str) -> None:
        self._conn = psycopg.connect(dsn, row_factory=dict_row)
        self._conn.autocommit = False
        with self._conn.cursor() as cur:
            cur.execute(_CREATE_EVENTS)
            cur.execute(_CREATE_EVENTS_IDX)
            cur.execute(_CREATE_IDEMPOTENCY)
            cur.execute(_CREATE_DAILY_USAGE)
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
        now = datetime.now(timezone.utc)
        with self._conn.cursor() as cur:
            cur.execute(
                """INSERT INTO trace_events
                   (event_id, run_id, event_type, agent, payload, error,
                    input_tokens, output_tokens, duration_ms, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (event_id, run_id, event_type, agent,
                 json.dumps(payload, default=str), error,
                 input_tokens, output_tokens, duration_ms, now),
            )
        self._conn.commit()
        return event_id

    def get_run_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM trace_events WHERE run_id = %s ORDER BY created_at ASC",
                (run_id,),
            )
            rows = cur.fetchall()
        for r in rows:
            if isinstance(r.get("payload"), str):
                r["payload"] = json.loads(r["payload"])
            if isinstance(r.get("created_at"), datetime):
                r["created_at"] = r["created_at"].isoformat()
        return rows

    def get_run_stats(self, run_id: str) -> dict[str, Any]:
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT
                    COALESCE(SUM(input_tokens), 0)  AS total_input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS total_output_tokens,
                    COALESCE(SUM(duration_ms), 0)    AS total_duration_ms,
                    COUNT(CASE WHEN input_tokens IS NOT NULL THEN 1 END) AS llm_call_count
                FROM trace_events
                WHERE run_id = %s""",
                (run_id,),
            )
            row = cur.fetchone()
        d = dict(row) if row else {
            "total_input_tokens": 0, "total_output_tokens": 0,
            "total_duration_ms": 0, "llm_call_count": 0,
        }
        inp = d["total_input_tokens"]
        out = d["total_output_tokens"]
        d["estimated_cost_usd"] = round(inp * 0.15 / 1_000_000 + out * 0.60 / 1_000_000, 6)
        return d

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT run_id,
                          MIN(created_at) AS started_at,
                          COUNT(*) AS event_count
                   FROM trace_events
                   GROUP BY run_id
                   ORDER BY MIN(created_at) DESC
                   LIMIT %s""",
                (limit,),
            )
            rows = cur.fetchall()

        results = []
        for r in rows:
            run_id = r["run_id"]
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT payload FROM trace_events WHERE run_id = %s AND event_type = 'run_end' LIMIT 1",
                    (run_id,),
                )
                end_row = cur.fetchone()

            started = r["started_at"]
            if isinstance(started, datetime):
                started = started.isoformat()

            entry: dict[str, Any] = {
                "run_id": run_id,
                "started_at": started,
                "event_count": r["event_count"],
            }
            if end_row:
                p = end_row["payload"]
                if isinstance(p, str):
                    p = json.loads(p)
                entry["lead_email"] = p.get("lead_email", "")
                entry["final_tier"] = p.get("final_tier", "")
                entry["final_route"] = p.get("final_route", "")
                entry["steps"] = p.get("steps_taken", 0)
            results.append(entry)
        return results

    # ── Idempotency ────────────────────────────────────────────────────────

    def get_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT run_id, result FROM idempotency_keys WHERE idem_key = %s",
                (key,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        result = row["result"]
        if isinstance(result, str):
            result = json.loads(result)
        return {"run_id": row["run_id"], "result": result}

    def store_idempotency_key(self, key: str, run_id: str, result: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        with self._conn.cursor() as cur:
            cur.execute(
                """INSERT INTO idempotency_keys (idem_key, run_id, result, created_at)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (idem_key) DO NOTHING""",
                (key, run_id, json.dumps(result, default=str), now),
            )
        self._conn.commit()

    # ── Daily usage cap ─────────────────────────────────────────────────────

    def get_daily_usage(self) -> int:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT count FROM daily_usage WHERE usage_date = %s",
                (today,),
            )
            row = cur.fetchone()
        return row["count"] if row else 0

    def increment_daily_usage(self) -> int:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO daily_usage (usage_date, count) VALUES (%s, 1) "
                "ON CONFLICT (usage_date) DO UPDATE SET count = daily_usage.count + 1",
                (today,),
            )
        self._conn.commit()
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT count FROM daily_usage WHERE usage_date = %s",
                (today,),
            )
            row = cur.fetchone()
        return row["count"] if row else 1

    # ── Result lookup by run_id ──────────────────────────────────────────

    def get_result_by_run_id(self, run_id: str) -> dict[str, Any] | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT result FROM idempotency_keys WHERE run_id = %s",
                (run_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        result = row["result"]
        if isinstance(result, str):
            result = json.loads(result)
        return result

    def close(self) -> None:
        self._conn.close()
