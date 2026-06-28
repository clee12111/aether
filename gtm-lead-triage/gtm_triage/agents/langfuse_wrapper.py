"""Langfuse integration — active only when LANGFUSE_PUBLIC_KEY is set.

When keys are absent, every function is a no-op. No import errors, no side
effects, no cost. The eval/CI path (provider=mock, no keys) is unchanged.

Usage from chat():
    from gtm_triage.agents.langfuse_wrapper import get_trace_span, record_generation

    span = get_trace_span(run_id, metadata)     # returns span or None
    record_generation(span, name, model, ...)   # no-op if span is None
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_langfuse = None
_initialized = False
_enabled = False

# Cache: run_id -> trace span (so all chat() calls for one lead share a trace)
_trace_spans: dict[str, Any] = {}


def _init() -> None:
    global _langfuse, _initialized, _enabled
    if _initialized:
        return
    _initialized = True

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    host = (os.environ.get("LANGFUSE_HOST", "") or os.environ.get("LANGFUSE_BASE_URL", "")).strip()

    if not (public_key and secret_key):
        _enabled = False
        return

    try:
        from langfuse import Langfuse
        _langfuse = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host or None,
        )
        _enabled = True
        logger.info("Langfuse enabled (host=%s)", host or "cloud")
    except Exception as exc:
        logger.warning("Langfuse init failed, disabling: %s", exc)
        _enabled = False


def get_trace_span(
    run_id: str,
    metadata: dict[str, Any] | None = None,
) -> Any:
    """Get or create a trace-level span for this run_id. Returns None if disabled."""
    _init()
    if not _enabled or _langfuse is None:
        return None

    if run_id in _trace_spans:
        return _trace_spans[run_id]

    span = _langfuse.start_observation(
        name=f"triage-{run_id[:8]}",
        as_type="span",
        metadata=metadata or {},
    )
    _trace_spans[run_id] = span
    return span


def record_generation(
    trace_span: Any,
    *,
    name: str,
    model: str,
    input_text: str,
    output_text: str,
    input_tokens: int,
    output_tokens: int,
    duration_ms: int,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record an LLM call as a Langfuse generation under the trace span."""
    if trace_span is None:
        return

    try:
        gen = trace_span.start_observation(
            name=name,
            as_type="generation",
            model=model,
            input=input_text[:2000],  # truncate for dashboard readability
            metadata=metadata or {},
        )
        gen.update(
            output=output_text[:2000],
            usage_details={
                "input": input_tokens,
                "output": output_tokens,
            },
        )
        gen.end()
    except Exception as exc:
        logger.warning("Langfuse record_generation failed: %s", exc)


def end_trace(run_id: str, metadata: dict[str, Any] | None = None) -> None:
    """End the trace span for a run and flush. Call at run_end."""
    _init()
    span = _trace_spans.pop(run_id, None)
    if span is None:
        return

    try:
        if metadata:
            span.update(metadata=metadata)
        span.end()
        if _langfuse is not None:
            _langfuse.flush()
    except Exception as exc:
        logger.warning("Langfuse end_trace failed: %s", exc)


def is_enabled() -> bool:
    _init()
    return _enabled
