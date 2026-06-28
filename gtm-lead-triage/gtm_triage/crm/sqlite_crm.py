from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from gtm_triage.crm.base import CRMStore


class SQLiteCRM(CRMStore):
    """SQLite-backed CRM store with contact records and activity timeline.

    Uses a SINGLE persistent connection for the store's lifetime.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS crm_records (
                email TEXT PRIMARY KEY,
                data  TEXT NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS crm_activities (
                activity_id TEXT PRIMARY KEY,
                email       TEXT NOT NULL,
                activity    TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )"""
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_activity_email ON crm_activities(email)"
        )
        self._conn.commit()

    def lookup(self, email: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT data FROM crm_records WHERE email = ?", (email,)
        ).fetchone()
        if row is None:
            return {"found": False}
        record = json.loads(row["data"])
        record["found"] = True
        return record

    def upsert(self, email: str, data: dict[str, Any]) -> None:
        blob = json.dumps(data, default=str)
        self._conn.execute(
            "INSERT INTO crm_records (email, data) VALUES (?, ?) "
            "ON CONFLICT(email) DO UPDATE SET data = excluded.data",
            (email, blob),
        )
        self._conn.commit()

    def add_activity(self, email: str, activity: dict[str, Any]) -> dict[str, Any] | None:
        # Dedup on (run_id + action) — if this exact delivery already exists, return it
        run_id = activity.get("run_id", "")
        action = activity.get("action", "")
        if run_id and action:
            existing = self._conn.execute(
                "SELECT activity_id, email, activity, created_at FROM crm_activities WHERE email = ?",
                (email,),
            ).fetchall()
            for row in existing:
                stored = json.loads(row["activity"])
                if stored.get("run_id") == run_id and stored.get("action") == action:
                    d = dict(row)
                    d["activity"] = stored
                    return d  # already recorded — return existing, no duplicate

        activity_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO crm_activities (activity_id, email, activity, created_at) "
            "VALUES (?, ?, ?, ?)",
            (activity_id, email, json.dumps(activity, default=str), now),
        )
        self._conn.commit()
        return None  # new activity recorded

    def get_activities(self, email: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM crm_activities WHERE email = ? ORDER BY created_at DESC",
            (email,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["activity"] = json.loads(d["activity"])
            result.append(d)
        return result

    def list_contacts(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT email, data FROM crm_records ORDER BY rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        results = []
        for r in rows:
            record = json.loads(r["data"])
            record["email"] = r["email"]
            # Attach last activity
            act_row = self._conn.execute(
                "SELECT activity, created_at FROM crm_activities WHERE email = ? ORDER BY created_at DESC LIMIT 1",
                (r["email"],),
            ).fetchone()
            if act_row:
                record["last_activity"] = json.loads(act_row["activity"]).get("action", "")
                record["last_activity_at"] = act_row["created_at"]
            results.append(record)
        return results

    def delete_contact(self, email: str) -> bool:
        """Delete contact record + all activities for this email (right-to-erasure)."""
        row = self._conn.execute(
            "SELECT email FROM crm_records WHERE email = ?", (email,)
        ).fetchone()
        if row is None:
            return False
        self._conn.execute("DELETE FROM crm_activities WHERE email = ?", (email,))
        self._conn.execute("DELETE FROM crm_records WHERE email = ?", (email,))
        self._conn.commit()
        return True

    def ping(self) -> bool:
        """Lightweight health check — runs SELECT 1 on the connection."""
        try:
            self._conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    def close(self) -> None:
        self._conn.close()
