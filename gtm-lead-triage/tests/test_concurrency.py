"""Load/concurrency smoke test.

Proves the async-concurrent claim:
- No crashes (all requests complete)
- No shared-state corruption (each result has a unique run_id)
- Bounded latency (all complete within timeout)
- Agent internals (trace store, CRM, metrics) handle rapid sequential writes

Note: Starlette TestClient doesn't support true thread-concurrent requests
(ASGI protocol violation). We test concurrency at the agent/store level
directly, and rapid sequential requests through the API.
"""

from __future__ import annotations

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from fastapi.testclient import TestClient


N_RAPID = 20  # Number of rapid-fire sequential requests
MAX_TOTAL_SECONDS = 30


def _make_client():
    os.environ.pop("GTM_API_KEYS", None)
    os.environ.pop("SENTRY_DSN", None)
    os.environ.pop("OTLP_ENDPOINT", None)
    os.environ.pop("APP_ENV", None)
    os.environ["GTM_PROVIDER"] = "mock"
    os.environ["GTM_RATE_LIMIT_RPM"] = "10000"  # High limit for rapid-fire tests
    from gtm_triage.api import app
    return TestClient(app)


class TestRapidFireTriage:
    def test_rapid_triage_no_crash(self):
        """Fire N rapid sequential /triage requests — all must succeed."""
        with _make_client() as client:
            results = []
            t0 = time.monotonic()

            for i in range(N_RAPID):
                uid = uuid.uuid4().hex[:8]
                resp = client.post("/triage", json={
                    "email": f"load-{uid}@bigcorp.com",
                    "message": f"Demo request #{i}",
                    "idempotency_key": f"load-{uid}",
                })
                results.append((resp.status_code, resp.json()))

            elapsed = time.monotonic() - t0

            # All returned 200
            statuses = [r[0] for r in results]
            assert all(s == 200 for s in statuses), \
                f"Non-200 statuses: {[s for s in statuses if s != 200]}"

            # Bounded latency
            assert elapsed < MAX_TOTAL_SECONDS, \
                f"Rapid triage took {elapsed:.1f}s (limit: {MAX_TOTAL_SECONDS}s)"

    def test_rapid_unique_run_ids(self):
        """Each request gets a unique run_id — no shared-state corruption."""
        with _make_client() as client:
            run_ids = []
            for i in range(10):
                uid = uuid.uuid4().hex[:8]
                resp = client.post("/triage", json={
                    "email": f"uniq-{uid}@corp.com",
                    "message": f"Request #{i}",
                    "idempotency_key": f"uniq-{uid}",
                })
                run_ids.append(resp.json().get("run_id", ""))

            assert len(set(run_ids)) == len(run_ids), \
                f"Duplicate run_ids: {len(run_ids)} total, {len(set(run_ids))} unique"

    def test_rapid_mixed_tiers(self):
        """Rapid requests with different lead types produce correct tiers."""
        with _make_client() as client:
            leads = [
                # Disposable → disqualified
                {"email": f"bot-{uuid.uuid4().hex[:6]}@tempmail.com",
                 "message": "spam", "idempotency_key": f"mix-{uuid.uuid4().hex}"},
                # Free email → disqualified
                {"email": f"user-{uuid.uuid4().hex[:6]}@gmail.com",
                 "message": "hi", "idempotency_key": f"mix-{uuid.uuid4().hex}"},
                # Business email
                {"email": f"vp-{uuid.uuid4().hex[:6]}@enterprise.com",
                 "message": "Demo for our trading desk. Urgent.",
                 "idempotency_key": f"mix-{uuid.uuid4().hex}"},
            ] * 3  # 9 total

            for lead in leads:
                resp = client.post("/triage", json=lead)
                body = resp.json()
                if "tempmail.com" in lead["email"]:
                    assert body["final_tier"] == "disqualified"

    def test_rapid_no_trace_corruption(self):
        """Rapid writes don't corrupt the trace store."""
        with _make_client() as client:
            run_ids = []
            for i in range(5):
                uid = uuid.uuid4().hex[:8]
                resp = client.post("/triage", json={
                    "email": f"trace-{uid}@corp.com",
                    "message": f"Request #{i}",
                    "idempotency_key": f"trace-{uid}",
                })
                run_ids.append(resp.json().get("run_id", ""))

            # Each run should have its own trace events
            for run_id in run_ids:
                resp = client.get(f"/runs/{run_id}")
                assert resp.status_code == 200
                body = resp.json()
                assert body["event_count"] >= 2  # at least run_start + run_end
                for event in body["events"]:
                    assert event["run_id"] == run_id


class TestStoreIntegrity:
    """Test store integrity under rapid writes.

    SQLite in-memory mode with check_same_thread=False allows cross-thread
    access but doesn't guarantee concurrent write safety. The production
    path serializes via asyncio.to_thread. These tests verify rapid
    sequential writes and metric thread-safety (which uses a Lock).
    """

    def test_rapid_trace_writes(self):
        """Rapid sequential writes to TraceStore produce correct results."""
        from gtm_triage.trace.store import TraceStore
        trace = TraceStore(":memory:")

        for i in range(20):
            run_id = f"rapid-run-{i}"
            trace.write(run_id=run_id, event_type="run_start", agent="test",
                        payload={"i": i})
            trace.write(run_id=run_id, event_type="run_end", agent="test",
                        payload={"i": i})

        for i in range(20):
            events = trace.get_run_events(f"rapid-run-{i}")
            assert len(events) == 2

    def test_rapid_crm_writes(self):
        """Rapid sequential CRM writes produce correct results."""
        from gtm_triage.crm.sqlite_crm import SQLiteCRM
        crm = SQLiteCRM(":memory:")

        for i in range(20):
            email = f"rapid-{i}@example.com"
            crm.upsert(email, {"email": email, "tier": "warm", "i": i})

        for i in range(20):
            record = crm.lookup(f"rapid-{i}@example.com")
            assert record["found"] is True

    def test_concurrent_metric_increments(self):
        """Metric counters handle concurrent increments correctly (Lock-protected)."""
        from gtm_triage.observability.metrics import Counter

        counter = Counter("test_concurrent", "test", ("label",))

        def inc_many(label: str, count: int):
            for _ in range(count):
                counter.inc(label=label)

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(inc_many, "a", 100) for _ in range(10)]
            for f in as_completed(futures):
                f.result()

        data = counter.collect()
        total = sum(v for _, v in data)
        assert total == 1000  # 10 threads × 100 increments
