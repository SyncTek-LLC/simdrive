"""Minimal span-context helpers for journey-step traceability.

WHY no real OpenTelemetry: the spec says "no real OTel yet — just structured
spans in logs." This module gives every journey step a span_id that links
log lines together without pulling in an OTEL SDK.

Usage::

    from simdrive.observability.tracing import start_span

    with start_span("journey.step", metadata={"step_idx": 3, "tool": "tap"}) as span:
        # do work
        log.info("step complete", extra={"span_id": span.span_id})
    # span.duration_ms is set on exit
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Span:
    """A single traced operation.

    Attributes
    ----------
    name:           Human-readable operation name (e.g. "journey.step.tap").
    span_id:        Unique ID for this span (UUID4 hex).
    parent_span_id: ID of the enclosing span, if any.
    started_at:     Unix timestamp when the span was created.
    ended_at:       Unix timestamp when finish() was called (None until then).
    duration_ms:    Wall-clock duration in milliseconds (None until finish()).
    metadata:       Caller-supplied key/value pairs included in to_dict().
    """

    name: str
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    parent_span_id: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    duration_ms: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def finish(self) -> None:
        """Mark the span as complete and compute duration."""
        if self.ended_at is None:
            self.ended_at = time.time()
            self.duration_ms = (self.ended_at - self.started_at) * 1000.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict for log emission."""
        return {
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }

    # ── Context manager protocol ───────────────────────────────────────────

    def __enter__(self) -> "Span":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.finish()


class TraceContext:
    """Thread-local stack of active spans.

    Allows nested spans to discover their parent without passing explicit
    parent_span_id everywhere.

    Usage::

        ctx = TraceContext()
        with start_span("outer") as outer:
            ctx.push(outer)
            inner = Span(name="inner", parent_span_id=ctx.current().span_id)
            # ...
            ctx.pop()
    """

    def __init__(self) -> None:
        self._stack: list[Span] = []

    def push(self, span: Span) -> None:
        self._stack.append(span)

    def pop(self) -> Optional[Span]:
        if self._stack:
            return self._stack.pop()
        return None

    def current(self) -> Optional[Span]:
        return self._stack[-1] if self._stack else None


def start_span(name: str, *, metadata: Optional[dict[str, Any]] = None,
               parent_span_id: Optional[str] = None) -> Span:
    """Create and return a new Span (as a context manager).

    Parameters
    ----------
    name:           Operation name.
    metadata:       Optional key/value context (tool, step_idx, journey_name…).
    parent_span_id: Parent span ID for nesting.
    """
    return Span(
        name=name,
        metadata=metadata or {},
        parent_span_id=parent_span_id,
    )
