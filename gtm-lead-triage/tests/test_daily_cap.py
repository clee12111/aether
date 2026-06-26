"""Unit tests for the daily query cap in the trace store."""

from gtm_triage.trace.store import TraceStore


class TestDailyUsage:
    def test_starts_at_zero(self):
        store = TraceStore(":memory:")
        assert store.get_daily_usage() == 0

    def test_increment(self):
        store = TraceStore(":memory:")
        assert store.increment_daily_usage() == 1
        assert store.increment_daily_usage() == 2
        assert store.get_daily_usage() == 2

    def test_over_cap_returns_count(self):
        store = TraceStore(":memory:")
        cap = 3
        for _ in range(cap + 2):
            store.increment_daily_usage()
        # Count is 5, over the cap of 3
        assert store.get_daily_usage() == 5
        assert store.get_daily_usage() >= cap


class TestResultByRunId:
    def test_returns_none_when_missing(self):
        store = TraceStore(":memory:")
        assert store.get_result_by_run_id("nonexistent") is None

    def test_returns_stored_result(self):
        store = TraceStore(":memory:")
        store.store_idempotency_key("key-1", "run-1", {"tier": "hot", "score": 80})
        result = store.get_result_by_run_id("run-1")
        assert result is not None
        assert result["tier"] == "hot"
        assert result["score"] == 80
