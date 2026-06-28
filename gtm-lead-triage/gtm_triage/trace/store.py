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

_CREATE_OUTCOMES = """
CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id     TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL,
    predicted_tier TEXT NOT NULL,
    actual_outcome TEXT NOT NULL,
    recorded_by    TEXT,
    recorded_at    TEXT NOT NULL
);
"""

_CREATE_IDX = "CREATE INDEX IF NOT EXISTS idx_trace_run ON trace_events(run_id);"
_CREATE_IDX_OUTCOMES = "CREATE INDEX IF NOT EXISTS idx_outcomes_run ON outcomes(run_id);"

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
        self._conn.execute(_CREATE_OUTCOMES)
        self._conn.execute(_CREATE_IDX)
        self._conn.execute(_CREATE_IDX_OUTCOMES)
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

    def delete_by_email(self, email: str) -> int:
        """Delete all trace events and idempotency records for runs involving
        this email (right-to-erasure). Returns the count of deleted events."""
        # Find run_ids that reference this email in run_start payload
        rows = self._conn.execute(
            "SELECT DISTINCT run_id FROM trace_events WHERE event_type = 'run_start' "
            "AND payload LIKE ?",
            (f'%"email": "{email}"%',),
        ).fetchall()
        run_ids = [r["run_id"] for r in rows]
        if not run_ids:
            return 0

        placeholders = ",".join("?" * len(run_ids))
        # Delete trace events
        self._conn.execute(
            f"DELETE FROM trace_events WHERE run_id IN ({placeholders})",
            run_ids,
        )
        # Delete idempotency records
        self._conn.execute(
            f"DELETE FROM idempotency_keys WHERE run_id IN ({placeholders})",
            run_ids,
        )
        self._conn.commit()
        return len(run_ids)

    # ── Health check ──────────────────────────────────────────────────────

    def ping(self) -> bool:
        """Lightweight health check — runs SELECT 1."""
        try:
            self._conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    # ── Outcome loop (K7 stub) ─────────────────────────────────────────

    def record_outcome(
        self,
        run_id: str,
        predicted_tier: str,
        actual_outcome: str,
        recorded_by: str = "",
    ) -> str:
        """Record the actual outcome for a triage run. Write-once (raises on dup)."""
        outcome_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO outcomes (outcome_id, run_id, predicted_tier, actual_outcome, recorded_by, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (outcome_id, run_id, predicted_tier, actual_outcome, recorded_by or "", now),
        )
        self._conn.commit()
        return outcome_id

    def get_outcome(self, run_id: str) -> dict[str, Any] | None:
        """Return the outcome for a run_id, or None."""
        row = self._conn.execute(
            "SELECT * FROM outcomes WHERE run_id = ?", (run_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_outcome_metrics(self) -> dict[str, dict[str, Any]]:
        """Compute precision-against-outcome per predicted tier.

        Returns empty per-tier objects if no outcomes exist.
        """
        # Get all predictions (from idempotency_keys results)
        pred_rows = self._conn.execute(
            "SELECT run_id, result FROM idempotency_keys"
        ).fetchall()

        tier_counts: dict[str, dict[str, int]] = {}
        for row in pred_rows:
            result = json.loads(row["result"])
            tier = result.get("final_tier", "unknown")
            if tier not in tier_counts:
                tier_counts[tier] = {"predicted": 0, "with_outcome": 0, "converted": 0}
            tier_counts[tier]["predicted"] += 1

        # Join with outcomes
        outcome_rows = self._conn.execute(
            "SELECT run_id, predicted_tier, actual_outcome FROM outcomes"
        ).fetchall()

        for row in outcome_rows:
            tier = row["predicted_tier"]
            if tier not in tier_counts:
                tier_counts[tier] = {"predicted": 0, "with_outcome": 0, "converted": 0}
            tier_counts[tier]["with_outcome"] += 1
            if row["actual_outcome"] == "converted":
                tier_counts[tier]["converted"] += 1

        # Compute precision
        result: dict[str, dict[str, Any]] = {}
        for tier, counts in sorted(tier_counts.items()):
            with_outcome = counts["with_outcome"]
            converted = counts["converted"]
            result[tier] = {
                "predicted": counts["predicted"],
                "with_outcome": with_outcome,
                "converted": converted,
                "precision": round(converted / with_outcome, 4) if with_outcome > 0 else None,
            }
        return result

    def close(self) -> None:
        self._conn.close()
