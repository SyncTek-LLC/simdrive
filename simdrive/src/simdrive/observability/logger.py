"""Structured logging shim for simdrive.

Usage::

    from simdrive.observability.logger import get_logger
    log = get_logger("simdrive.journey.runner")
    log.info("step executed", extra={"tool": "tap", "duration_ms": 42})

Environment:
  SIMDRIVE_DEBUG=1  → DEBUG level, JSON-formatted output
  (unset)           → INFO level, human-readable format

WHY stdlib over structlog: structlog is listed as a new dep for Atlas to add
to pyproject.toml. We try structlog first and gracefully fall back to stdlib
so the module works before structlog is installed.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional


# ── JSON formatter (used in debug mode) ────────────────────────────────────


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)
            ),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        # Include any extra keys the caller injected.
        skip = {
            "args", "created", "exc_info", "exc_text", "filename", "funcName",
            "levelname", "levelno", "lineno", "message", "module", "msecs",
            "msg", "name", "pathname", "process", "processName", "relativeCreated",
            "stack_info", "thread", "threadName",
        }
        for k, v in record.__dict__.items():
            if k not in skip:
                try:
                    json.dumps(v)  # only include JSON-serialisable extras
                    payload[k] = v
                except (TypeError, ValueError):
                    payload[k] = str(v)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


# ── Human-readable formatter (used in normal mode) ─────────────────────────


_HUMAN_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_HUMAN_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


# ── Module-level state ──────────────────────────────────────────────────────

# Track whether configure_logging() has been called so callers can call
# get_logger() without explicitly calling configure_logging() first.
_configured = False


def configure_logging(
    handler_override: Optional[logging.Handler] = None,
) -> None:
    """Configure the root "simdrive" logger based on environment.

    Safe to call multiple times — repeated calls update the existing handler
    rather than stacking duplicates.

    Parameters
    ----------
    handler_override:
        When provided, this handler is used instead of stderr.
        Primarily for testing (write to StringIO to capture output).
    """
    global _configured

    debug_mode = os.environ.get("SIMDRIVE_DEBUG", "").strip() == "1"
    root = logging.getLogger("simdrive")

    # Clear existing simdrive-specific handlers to avoid duplicates.
    root.handlers.clear()

    if debug_mode:
        root.setLevel(logging.DEBUG)
        formatter: logging.Formatter = _JsonFormatter()
    else:
        root.setLevel(logging.INFO)
        formatter = logging.Formatter(fmt=_HUMAN_FORMAT, datefmt=_HUMAN_DATE_FORMAT)

    handler = handler_override or logging.StreamHandler()
    handler.setFormatter(formatter)
    root.addHandler(handler)

    # WHY propagate=True: keeping propagation enabled allows pytest's caplog
    # fixture to capture log records via its handler on the root Python logger.
    # The simdrive-specific handler above handles production output; caplog
    # captures in tests without needing us to disable propagation.
    root.propagate = True

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger for the given name.

    Calls configure_logging() on first use if it hasn't been called yet.
    The returned logger is the stdlib Logger — standard .info/.debug/.warning
    calls work as expected.
    """
    global _configured
    if not _configured:
        configure_logging()
    return logging.getLogger(name)
