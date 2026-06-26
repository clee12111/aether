"""Protocol defining the trace-store contract.

Both TraceStore (SQLite) and PostgresTraceStore implement this interface.
Adding a method to one store without the other is caught by the conformance
test in tests/test_trace_store_parity.py (signature-level) and by mypy if
enabled (structural subtyping).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TraceStoreProtocol(Protocol):
    """Contract that every trace-store backend must satisfy."""

    # ── Core trace events ───────────────────────────────────────────────

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
    ) -> str: ...

    def get_run_events(self, run_id: str) -> list[dict[str, Any]]: ...

    def get_run_stats(self, run_id: str) -> dict[str, Any]: ...

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]: ...

    # ── Idempotency ─────────────────────────────────────────────────────

    def get_by_idempotency_key(self, key: str) -> dict[str, Any] | None: ...

    def store_idempotency_key(
        self, key: str, run_id: str, result: dict[str, Any],
    ) -> None: ...

    # ── Daily usage cap ─────────────────────────────────────────────────

    def get_daily_usage(self) -> int: ...

    def increment_daily_usage(self) -> int: ...

    # ── Result lookup ───────────────────────────────────────────────────

    def get_result_by_run_id(self, run_id: str) -> dict[str, Any] | None: ...

    # ── Lifecycle ───────────────────────────────────────────────────────

    def close(self) -> None: ...
