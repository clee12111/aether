"""Postgres-backed CRM store using the same DATABASE_URL as the trace store.

Mirrors SQLiteCRM's schema (crm_records + crm_activities) but on Postgres
so deployed runs persist their lead slate across restarts.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from gtm_triage.crm.base import CRMStore

logger = logging.getLogger(__name__)


class PostgresCRM(CRMStore):
    """Postgres-backed CRM store with contact records and activity timeline."""

    def __init__(self, dsn: str) -> None:
        self._conn = psycopg.connect(dsn, row_factory=dict_row)
        self._conn.autocommit = False
        self._migrate()

    def _migrate(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """CREATE TABLE IF NOT EXISTS crm_records (
                    email TEXT PRIMARY KEY,
                    data  TEXT NOT NULL
                )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS crm_activities (
                    activity_id TEXT PRIMARY KEY,
                    email       TEXT NOT NULL,
                    activity    TEXT NOT NULL,
                    created_at  TEXT NOT NULL
                )"""
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_crm_activity_email ON crm_activities(email)"
            )
        self._conn.commit()

    def lookup(self, email: str) -> dict[str, Any]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT data FROM crm_records WHERE email = %s", (email,))
            row = cur.fetchone()
        if row is None:
            return {"found": False}
        record = json.loads(row["data"])
        record["found"] = True
        return record

    def upsert(self, email: str, data: dict[str, Any]) -> None:
        blob = json.dumps(data, default=str)
        with self._conn.cursor() as cur:
            cur.execute(
                """INSERT INTO crm_records (email, data) VALUES (%s, %s)
                   ON CONFLICT(email) DO UPDATE SET data = EXCLUDED.data""",
                (email, blob),
            )
        self._conn.commit()

    def add_activity(self, email: str, activity: dict[str, Any]) -> dict[str, Any] | None:
        run_id = activity.get("run_id", "")
        action = activity.get("action", "")
        if run_id and action:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT activity_id, email, activity, created_at FROM crm_activities WHERE email = %s",
                    (email,),
                )
                for row in cur.fetchall():
                    stored = json.loads(row["activity"])
                    if stored.get("run_id") == run_id and stored.get("action") == action:
                        d = dict(row)
                        d["activity"] = stored
                        return d

        activity_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO crm_activities (activity_id, email, activity, created_at) VALUES (%s, %s, %s, %s)",
                (activity_id, email, json.dumps(activity, default=str), now),
            )
        self._conn.commit()
        return None

    def get_activities(self, email: str) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT activity_id, email, activity, created_at FROM crm_activities WHERE email = %s ORDER BY created_at DESC",
                (email,),
            )
            rows = cur.fetchall()
        return [
            {**dict(r), "activity": json.loads(r["activity"])}
            for r in rows
        ]

    def list_contacts(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            # Use ctid for ordering (Postgres equivalent of rowid)
            cur.execute(
                "SELECT email, data FROM crm_records ORDER BY ctid DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()

        results = []
        for r in rows:
            record = json.loads(r["data"])
            record["email"] = r["email"]
            # Attach last activity
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT activity, created_at FROM crm_activities WHERE email = %s ORDER BY created_at DESC LIMIT 1",
                    (r["email"],),
                )
                act_row = cur.fetchone()
            if act_row:
                record["last_activity"] = json.loads(act_row["activity"]).get("action", "")
                record["last_activity_at"] = act_row["created_at"]
            results.append(record)
        return results

    def delete_contact(self, email: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute("SELECT email FROM crm_records WHERE email = %s", (email,))
            if cur.fetchone() is None:
                return False
            cur.execute("DELETE FROM crm_activities WHERE email = %s", (email,))
            cur.execute("DELETE FROM crm_records WHERE email = %s", (email,))
        self._conn.commit()
        return True

    def ping(self) -> bool:
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        except Exception:
            return False

    def close(self) -> None:
        self._conn.close()
