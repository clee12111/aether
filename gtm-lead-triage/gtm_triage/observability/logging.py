"""Structured JSON logging with request/run-id correlation.

Two formatters:
  - JSONFormatter (default, production): newline-delimited JSON per record.
  - TextFormatter (LOG_FORMAT=text): human-readable for local dev.

Request-id propagation via contextvars — set in middleware, visible in all
downstream log records (agent, executor, tool calls).

PII rule: email, name, company, message NEVER appear at INFO or above.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

# ── Context vars for request/run correlation ───────────────────────────────

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
run_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="")
otel_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("otel_trace_id", default="")


# ── JSON formatter ─────────────────────────────────────────────────────────

class JSONFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object.

    Fields: ts, level, logger, message, plus all extras as top-level keys.
    Context vars (request_id, run_id, otel_trace_id) are auto-injected.
    """

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Inject context vars
        req_id = request_id_var.get("")
        if req_id:
            entry["request_id"] = req_id
        r_id = run_id_var.get("")
        if r_id:
            entry["run_id"] = r_id
        otel_id = otel_trace_id_var.get("")
        if otel_id:
            entry["otel_trace_id"] = otel_id

        # Flatten extra fields (skip standard LogRecord attrs)
        _STANDARD = {
            "name", "msg", "args", "created", "relativeCreated", "exc_info",
            "exc_text", "stack_info", "lineno", "funcName", "filename",
            "module", "pathname", "thread", "threadName", "process",
            "processName", "msecs", "levelname", "levelno", "message",
            "taskName",
        }
        for key, val in record.__dict__.items():
            if key.startswith("_") or key in _STANDARD:
                continue
            if key in ("request_id", "run_id", "otel_trace_id"):
                # Already injected from context vars
                if key not in entry:
                    entry[key] = val
                continue
            entry[key] = val

        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            entry["exception"] = record.exc_text

        return json.dumps(entry, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable formatter for local dev (LOG_FORMAT=text)."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )


# ── Setup ──────────────────────────────────────────────────────────────────

def setup_logging(log_format: str | None = None, log_level: str | None = None) -> None:
    """Configure the root logger with structured formatting.

    Args:
        log_format: "json" (default) or "text".
        log_level: Python log level name (default "INFO").
    """
    fmt = log_format or os.environ.get("LOG_FORMAT", "json")
    level = log_level or os.environ.get("LOG_LEVEL", "INFO")

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates on re-init
    for h in root.handlers[:]:
        root.removeHandler(h)

    handler = logging.StreamHandler()
    if fmt == "text":
        handler.setFormatter(TextFormatter())
    else:
        handler.setFormatter(JSONFormatter())

    root.addHandler(handler)
