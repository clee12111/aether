"""OpenTelemetry instrumentation — no-op without SDK or OTLP_ENDPOINT.

Provides a get_tracer() function that returns either a real OTel tracer or
a no-op stub. Manual spans for tool calls and LLM calls are created via
start_span() which works with both.

OTel trace-id is injected into the logging contextvars (otel_trace_id_var)
for cross-system correlation.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Generator

logger = logging.getLogger(__name__)

# Try importing OTel — soft dependency
_HAS_OTEL = False
_tracer = None

try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.resources import Resource
    _HAS_OTEL = True
except ImportError:
    pass


class _NoOpSpan:
    """Minimal no-op span for when OTel is unavailable."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, status: Any) -> None:
        pass

    def record_exception(self, exc: Exception) -> None:
        pass

    def end(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _NoOpTracer:
    """No-op tracer — returns no-op spans."""

    def start_span(self, name: str, **kwargs) -> _NoOpSpan:
        return _NoOpSpan()

    @contextmanager
    def start_as_current_span(self, name: str, **kwargs) -> Generator:
        yield _NoOpSpan()


_noop_tracer = _NoOpTracer()


def init_tracing() -> None:
    """Initialize OTel tracing if SDK is installed and OTLP_ENDPOINT is set.

    No-op (silent) if either condition is false.
    """
    global _tracer, _HAS_OTEL

    otlp_endpoint = os.environ.get("OTLP_ENDPOINT", "")

    if not _HAS_OTEL or not otlp_endpoint:
        return

    try:
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        resource = Resource.create({"service.name": "gtm-lead-triage"})
        provider = TracerProvider(resource=resource)

        # Try gRPC exporter first, fall back to HTTP
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
        except ImportError:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
            except ImportError:
                logger.info("No OTLP exporter available — OTel tracing disabled")
                return

        provider.add_span_processor(BatchSpanProcessor(exporter))
        otel_trace.set_tracer_provider(provider)
        _tracer = otel_trace.get_tracer("gtm-lead-triage")

        # Auto-instrument FastAPI if available
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            FastAPIInstrumentor().instrument()
        except ImportError:
            pass

    except Exception as exc:
        logger.warning("OTel initialization failed: %s", exc)


def get_tracer():
    """Return the OTel tracer or a no-op stub."""
    return _tracer if _tracer is not None else _noop_tracer


def get_current_trace_id() -> str:
    """Return the current OTel trace ID as a hex string, or empty."""
    if not _HAS_OTEL:
        return ""
    try:
        span = otel_trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.trace_id:
            return format(ctx.trace_id, "032x")
    except Exception:
        pass
    return ""


@contextmanager
def traced_span(name: str, attributes: dict[str, Any] | None = None) -> Generator:
    """Context manager that creates a child span (or no-op).

    Injects OTel trace-id into the logging contextvar for correlation.
    """
    tracer = get_tracer()
    if isinstance(tracer, _NoOpTracer):
        yield _NoOpSpan()
        return

    with tracer.start_as_current_span(name) as span:
        if attributes:
            for k, v in attributes.items():
                span.set_attribute(k, v)

        # Inject trace-id into log context
        trace_id = get_current_trace_id()
        if trace_id:
            from gtm_triage.observability.logging import otel_trace_id_var
            token = otel_trace_id_var.set(trace_id)
            try:
                yield span
            finally:
                otel_trace_id_var.reset(token)
        else:
            yield span
