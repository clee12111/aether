"""Conformance test: both trace stores implement the TraceStoreProtocol.

Iterates every public method on the Protocol and asserts that both concrete
stores (a) have the method and (b) have a matching signature. Catches
interface drift at test time without requiring mypy in CI.

# future: add testcontainers-based integration tests against real Postgres
"""

from __future__ import annotations

import inspect

import pytest

from gtm_triage.trace.base import TraceStoreProtocol


def _protocol_methods() -> list[str]:
    """Return the public method names defined on TraceStoreProtocol."""
    return [
        name
        for name in dir(TraceStoreProtocol)
        if not name.startswith("_")
        and callable(getattr(TraceStoreProtocol, name, None))
    ]


def _get_concrete_classes() -> list[type]:
    """Import and return both concrete store classes."""
    from gtm_triage.trace.store import TraceStore
    classes: list[type] = [TraceStore]
    try:
        from gtm_triage.trace.pg_store import PostgresTraceStore
        classes.append(PostgresTraceStore)
    except ImportError:
        pass  # psycopg not installed — skip Postgres conformance
    return classes


_METHODS = _protocol_methods()
_CLASSES = _get_concrete_classes()


class TestProtocolConformance:
    """Every concrete store must be a structural subtype of TraceStoreProtocol."""

    @pytest.mark.parametrize("cls", _CLASSES, ids=lambda c: c.__name__)
    def test_runtime_checkable(self, cls: type):
        """The class itself satisfies isinstance checks against the Protocol."""
        # runtime_checkable only checks method existence, not signatures —
        # signature parity is verified in the next test.
        assert issubclass(cls, TraceStoreProtocol), (
            f"{cls.__name__} does not satisfy TraceStoreProtocol"
        )

    @pytest.mark.parametrize("method", _METHODS, ids=str)
    @pytest.mark.parametrize("cls", _CLASSES, ids=lambda c: c.__name__)
    def test_method_exists(self, cls: type, method: str):
        """The concrete class has every method the Protocol declares."""
        assert hasattr(cls, method), (
            f"{cls.__name__} is missing method '{method}' "
            f"required by TraceStoreProtocol"
        )

    @pytest.mark.parametrize("method", _METHODS, ids=str)
    @pytest.mark.parametrize("cls", _CLASSES, ids=lambda c: c.__name__)
    def test_signature_matches(self, cls: type, method: str):
        """The concrete method's signature matches the Protocol's."""
        proto_sig = inspect.signature(getattr(TraceStoreProtocol, method))
        impl_sig = inspect.signature(getattr(cls, method))
        assert proto_sig == impl_sig, (
            f"{cls.__name__}.{method} signature mismatch:\n"
            f"  Protocol: {proto_sig}\n"
            f"  Actual:   {impl_sig}"
        )
