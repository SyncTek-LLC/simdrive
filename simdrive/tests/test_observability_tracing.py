"""Tests for simdrive.observability.tracing — TDD.

Validates:
  - Span creation with name + metadata
  - Span records start/end timestamps
  - Span duration_ms is correct
  - Nested spans (parent_span_id)
  - Span serialisation to dict (for log emission)
  - Context manager usage
"""
from __future__ import annotations

import time


from simdrive.observability.tracing import Span, start_span, TraceContext


class TestSpan:
    def test_span_has_name(self) -> None:
        span = Span(name="journey.step")
        assert span.name == "journey.step"

    def test_span_has_id(self) -> None:
        span = Span(name="test")
        assert span.span_id is not None
        assert len(span.span_id) > 0

    def test_span_ids_are_unique(self) -> None:
        s1 = Span(name="a")
        s2 = Span(name="b")
        assert s1.span_id != s2.span_id

    def test_span_finish_records_end_time(self) -> None:
        span = Span(name="test")
        span.finish()
        assert span.ended_at is not None

    def test_span_duration_ms_positive(self) -> None:
        span = Span(name="test")
        time.sleep(0.01)  # 10ms
        span.finish()
        assert span.duration_ms is not None
        assert span.duration_ms >= 5.0  # at least 5ms

    def test_span_to_dict_keys(self) -> None:
        span = Span(name="journey.observe", metadata={"tool": "observe"})
        span.finish()
        d = span.to_dict()
        assert "span_id" in d
        assert "name" in d
        assert "started_at" in d
        assert "ended_at" in d
        assert "duration_ms" in d

    def test_span_metadata_included(self) -> None:
        span = Span(name="test", metadata={"step": 3, "tool": "tap"})
        d = span.to_dict()
        assert d.get("metadata") == {"step": 3, "tool": "tap"} or (
            d.get("step") == 3 and d.get("tool") == "tap"
        )

    def test_span_parent_id(self) -> None:
        parent = Span(name="parent")
        child = Span(name="child", parent_span_id=parent.span_id)
        assert child.parent_span_id == parent.span_id
        d = child.to_dict()
        assert d.get("parent_span_id") == parent.span_id


class TestStartSpan:
    def test_start_span_returns_span(self) -> None:
        span = start_span("test.operation")
        assert isinstance(span, Span)

    def test_start_span_with_metadata(self) -> None:
        span = start_span("test.op", metadata={"key": "value"})
        assert span.name == "test.op"


class TestContextManager:
    def test_span_as_context_manager(self) -> None:
        with start_span("ctx.test") as span:
            assert isinstance(span, Span)
        assert span.ended_at is not None

    def test_context_manager_finishes_on_exit(self) -> None:
        with start_span("ctx.finish") as span:
            time.sleep(0.005)
        assert span.duration_ms is not None
        assert span.duration_ms >= 1.0

    def test_context_manager_finishes_on_exception(self) -> None:
        span = None
        try:
            with start_span("ctx.exc") as span:
                raise ValueError("test error")
        except ValueError:
            pass
        assert span is not None
        assert span.ended_at is not None


class TestTraceContext:
    def test_trace_context_push_pop(self) -> None:
        ctx = TraceContext()
        span = Span(name="root")
        ctx.push(span)
        assert ctx.current() is span
        ctx.pop()
        assert ctx.current() is None

    def test_trace_context_nested(self) -> None:
        ctx = TraceContext()
        parent = Span(name="parent")
        child = Span(name="child")
        ctx.push(parent)
        ctx.push(child)
        assert ctx.current() is child
        ctx.pop()
        assert ctx.current() is parent
