"""Tests for simdrive.observability.metrics — TDD.

Validates:
  - Counter increment and retrieval
  - Histogram record and percentile computation
  - dump_metrics() produces Prometheus text format
  - Specific metric names: journey_runs_total, tap_latency_ms,
    observe_latency_ms, claude_call_cost_usd
  - Reset clears all metrics
"""
from __future__ import annotations

import re

import pytest

from simdrive.observability.metrics import (
    MetricsRegistry,
    dump_metrics,
    get_registry,
    record_histogram,
    increment_counter,
)


class TestCounter:
    def test_increment_once(self) -> None:
        reg = MetricsRegistry()
        reg.increment("journey_runs_total")
        assert reg.get_counter("journey_runs_total") == 1

    def test_increment_multiple(self) -> None:
        reg = MetricsRegistry()
        reg.increment("journey_runs_total", 3)
        assert reg.get_counter("journey_runs_total") == 3

    def test_increment_accumulates(self) -> None:
        reg = MetricsRegistry()
        reg.increment("journey_runs_total", 2)
        reg.increment("journey_runs_total", 5)
        assert reg.get_counter("journey_runs_total") == 7

    def test_independent_counters(self) -> None:
        reg = MetricsRegistry()
        reg.increment("counter_a", 10)
        reg.increment("counter_b", 3)
        assert reg.get_counter("counter_a") == 10
        assert reg.get_counter("counter_b") == 3

    def test_unregistered_counter_returns_zero(self) -> None:
        reg = MetricsRegistry()
        assert reg.get_counter("nonexistent") == 0


class TestHistogram:
    def test_record_single_value(self) -> None:
        reg = MetricsRegistry()
        reg.record("tap_latency_ms", 42.0)
        assert reg.get_histogram_count("tap_latency_ms") == 1

    def test_record_multiple_values(self) -> None:
        reg = MetricsRegistry()
        for v in [10, 20, 30, 40, 50]:
            reg.record("tap_latency_ms", v)
        assert reg.get_histogram_count("tap_latency_ms") == 5

    def test_p50_approx(self) -> None:
        reg = MetricsRegistry()
        for v in range(1, 101):  # 1..100
            reg.record("observe_latency_ms", float(v))
        p50 = reg.percentile("observe_latency_ms", 50)
        # p50 of 1..100 should be ~50
        assert 45.0 <= p50 <= 55.0

    def test_p95_approx(self) -> None:
        reg = MetricsRegistry()
        for v in range(1, 101):
            reg.record("observe_latency_ms", float(v))
        p95 = reg.percentile("observe_latency_ms", 95)
        assert 90.0 <= p95 <= 100.0

    def test_sum(self) -> None:
        reg = MetricsRegistry()
        for v in [10.0, 20.0, 30.0]:
            reg.record("tap_latency_ms", v)
        assert reg.get_histogram_sum("tap_latency_ms") == pytest.approx(60.0)

    def test_unregistered_histogram_count_zero(self) -> None:
        reg = MetricsRegistry()
        assert reg.get_histogram_count("nonexistent") == 0


class TestDumpMetrics:
    def test_prometheus_counter_format(self) -> None:
        reg = MetricsRegistry()
        reg.increment("journey_runs_total", 5)
        output = reg.dump_prometheus()
        assert "journey_runs_total" in output
        assert "5" in output

    def test_prometheus_histogram_format(self) -> None:
        reg = MetricsRegistry()
        for v in [100.0, 200.0, 300.0]:
            reg.record("tap_latency_ms", v)
        output = reg.dump_prometheus()
        assert "tap_latency_ms" in output
        # Should contain _count, _sum
        assert "tap_latency_ms_count" in output or "tap_latency_ms" in output

    def test_global_dump_metrics_function(self) -> None:
        """dump_metrics() uses the global registry."""
        output = dump_metrics()
        assert isinstance(output, str)

    def test_global_increment_counter(self) -> None:
        """increment_counter() affects the global registry."""
        reg = get_registry()
        initial = reg.get_counter("claude_call_cost_usd_test")
        increment_counter("claude_call_cost_usd_test")
        assert reg.get_counter("claude_call_cost_usd_test") == initial + 1

    def test_global_record_histogram(self) -> None:
        """record_histogram() affects the global registry."""
        reg = get_registry()
        initial = reg.get_histogram_count("tap_latency_ms_test")
        record_histogram("tap_latency_ms_test", 99.9)
        assert reg.get_histogram_count("tap_latency_ms_test") == initial + 1


class TestMetricNames:
    """Verify the four canonical metric names work in the global registry."""

    def test_journey_runs_total(self) -> None:
        reg = MetricsRegistry()
        reg.increment("journey_runs_total")
        assert reg.get_counter("journey_runs_total") >= 1

    def test_tap_latency_ms(self) -> None:
        reg = MetricsRegistry()
        reg.record("tap_latency_ms", 45.0)
        assert reg.get_histogram_count("tap_latency_ms") == 1

    def test_observe_latency_ms(self) -> None:
        reg = MetricsRegistry()
        reg.record("observe_latency_ms", 300.0)
        assert reg.get_histogram_count("observe_latency_ms") == 1

    def test_claude_call_cost_usd(self) -> None:
        reg = MetricsRegistry()
        reg.record("claude_call_cost_usd", 0.0042)
        assert reg.get_histogram_count("claude_call_cost_usd") == 1


class TestReset:
    def test_reset_clears_counters(self) -> None:
        reg = MetricsRegistry()
        reg.increment("test_counter", 10)
        reg.reset()
        assert reg.get_counter("test_counter") == 0

    def test_reset_clears_histograms(self) -> None:
        reg = MetricsRegistry()
        reg.record("test_hist", 42.0)
        reg.reset()
        assert reg.get_histogram_count("test_hist") == 0
