"""Unit tests for PostgresTraceStore against a mocked psycopg connection.

Verifies correct SQL, parameter passing, and return shapes WITHOUT a live
Postgres database.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest


def _make_store():
    """Create a PostgresTraceStore with a fully mocked psycopg.connect."""
    with patch("gtm_triage.trace.pg_store.psycopg") as mock_psycopg:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_psycopg.connect.return_value = mock_conn

        from gtm_triage.trace.pg_store import PostgresTraceStore
        store = PostgresTraceStore("postgresql://test:test@localhost/test")

        # Verify schema creation was called
        assert mock_cursor.execute.call_count == 4  # events + idx + idempotency + daily_usage
        mock_conn.commit.assert_called_once()

        # Reset mocks for test usage
        mock_cursor.reset_mock()
        mock_conn.reset_mock()

        return store, mock_conn, mock_cursor


class TestWrite:
    def test_inserts_event(self):
        store, conn, cur = _make_store()

        event_id = store.write(
            run_id="run-1",
            event_type="llm_call",
            agent="loop_agent",
            payload={"key": "value"},
            input_tokens=100,
            output_tokens=50,
            duration_ms=200,
        )

        assert event_id  # UUID string
        cur.execute.assert_called_once()
        sql = cur.execute.call_args[0][0]
        assert "INSERT INTO trace_events" in sql
        params = cur.execute.call_args[0][1]
        assert params[1] == "run-1"
        assert params[2] == "llm_call"
        assert params[3] == "loop_agent"
        assert '"key": "value"' in params[4]
        conn.commit.assert_called_once()


class TestGetRunEvents:
    def test_returns_parsed_events(self):
        store, conn, cur = _make_store()

        now = datetime.now(timezone.utc)
        cur.fetchall.return_value = [
            {
                "event_id": "e1",
                "run_id": "run-1",
                "event_type": "run_start",
                "agent": "loop_agent",
                "payload": {"lead": {"email": "test@acme.com"}},
                "error": None,
                "input_tokens": None,
                "output_tokens": None,
                "duration_ms": None,
                "created_at": now,
            }
        ]

        events = store.get_run_events("run-1")

        assert len(events) == 1
        assert events[0]["event_type"] == "run_start"
        assert events[0]["payload"]["lead"]["email"] == "test@acme.com"
        assert isinstance(events[0]["created_at"], str)  # converted to ISO


class TestGetRunStats:
    def test_computes_stats(self):
        store, conn, cur = _make_store()

        cur.fetchone.return_value = {
            "total_input_tokens": 500,
            "total_output_tokens": 200,
            "total_duration_ms": 1000,
            "llm_call_count": 5,
        }

        stats = store.get_run_stats("run-1")

        assert stats["total_input_tokens"] == 500
        assert stats["total_output_tokens"] == 200
        assert stats["llm_call_count"] == 5
        assert "estimated_cost_usd" in stats
        assert stats["estimated_cost_usd"] == round(500 * 0.15 / 1_000_000 + 200 * 0.60 / 1_000_000, 6)


class TestListRuns:
    def test_returns_run_summaries(self):
        store, conn, cur = _make_store()

        now = datetime.now(timezone.utc)
        # First call: list query
        # Second call: end_row query
        cur.fetchall.return_value = [
            {"run_id": "run-1", "started_at": now, "event_count": 15}
        ]
        cur.fetchone.return_value = {
            "payload": {"lead_email": "test@acme.com", "final_tier": "hot", "final_route": "ae_immediate", "steps_taken": 5}
        }

        runs = store.list_runs(10)

        assert len(runs) == 1
        assert runs[0]["run_id"] == "run-1"
        assert runs[0]["lead_email"] == "test@acme.com"
        assert runs[0]["final_tier"] == "hot"
        assert isinstance(runs[0]["started_at"], str)


class TestIdempotency:
    def test_store_and_get(self):
        store, conn, cur = _make_store()

        store.store_idempotency_key("key-1", "run-1", {"tier": "hot"})
        sql = cur.execute.call_args[0][0]
        assert "INSERT INTO idempotency_keys" in sql
        assert "ON CONFLICT" in sql
        conn.commit.assert_called_once()

    def test_get_returns_none_when_missing(self):
        store, conn, cur = _make_store()
        cur.fetchone.return_value = None

        result = store.get_by_idempotency_key("missing")
        assert result is None

    def test_get_returns_result(self):
        store, conn, cur = _make_store()
        cur.fetchone.return_value = {
            "run_id": "run-1",
            "result": {"tier": "hot", "run_id": "run-1"},
        }

        result = store.get_by_idempotency_key("key-1")
        assert result is not None
        assert result["run_id"] == "run-1"
        assert result["result"]["tier"] == "hot"


class TestDailyUsage:
    def test_get_returns_zero_when_no_row(self):
        store, conn, cur = _make_store()
        cur.fetchone.return_value = None

        assert store.get_daily_usage() == 0

    def test_get_returns_count(self):
        store, conn, cur = _make_store()
        cur.fetchone.return_value = {"count": 42}

        assert store.get_daily_usage() == 42
        sql = cur.execute.call_args[0][0]
        assert "daily_usage" in sql

    def test_increment_inserts_and_returns(self):
        store, conn, cur = _make_store()
        # First fetchone (after upsert) returns the new count
        cur.fetchone.return_value = {"count": 1}

        result = store.increment_daily_usage()

        assert result == 1
        # Should have executed the upsert INSERT
        calls = cur.execute.call_args_list
        upsert_sql = calls[0][0][0]
        assert "INSERT INTO daily_usage" in upsert_sql
        assert "ON CONFLICT" in upsert_sql
        conn.commit.assert_called_once()

    def test_increment_updates_existing(self):
        store, conn, cur = _make_store()
        cur.fetchone.return_value = {"count": 5}

        result = store.increment_daily_usage()

        assert result == 5
        upsert_sql = cur.execute.call_args_list[0][0][0]
        assert "daily_usage.count + 1" in upsert_sql


class TestGetResultByRunId:
    def test_returns_none_when_missing(self):
        store, conn, cur = _make_store()
        cur.fetchone.return_value = None

        assert store.get_result_by_run_id("run-missing") is None

    def test_returns_result_dict(self):
        store, conn, cur = _make_store()
        cur.fetchone.return_value = {
            "result": {"tier": "warm", "run_id": "run-1"},
        }

        result = store.get_result_by_run_id("run-1")
        assert result is not None
        assert result["tier"] == "warm"

    def test_parses_string_result(self):
        store, conn, cur = _make_store()
        cur.fetchone.return_value = {
            "result": '{"tier": "cold", "run_id": "run-2"}',
        }

        result = store.get_result_by_run_id("run-2")
        assert result is not None
        assert result["tier"] == "cold"
