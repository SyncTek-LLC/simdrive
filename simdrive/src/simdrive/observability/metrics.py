"""Counter and histogram primitives for simdrive observability.

Tracks four canonical metrics:
  journey_runs_total      — counter: number of journey executions
  tap_latency_ms          — histogram: per-tap round-trip time
  observe_latency_ms      — histogram: per-observe call time
  claude_call_cost_usd    — histogram: per-LLM-call cost in USD

Usage::

    from simdrive.observability.metrics import increment_counter, record_histogram, dump_metrics

    increment_counter("journey_runs_total")
    record_histogram("tap_latency_ms", 42.5)
    print(dump_metrics())   # Prometheus text format

The module exposes:
  - MetricsRegistry  — isolated instance for unit-testing
  - get_registry()   — global singleton
  - increment_counter(), record_histogram(), dump_metrics() — global helpers
"""
from __future__ import annotations

import bisect
import threading
from typing import Optional


class MetricsRegistry:
    """Isolated metrics store — one counter dict + one histogram dict.

    WHY isolated instances instead of only globals: tests can create a fresh
    MetricsRegistry() without touching shared state, avoiding flaky ordering.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}

    # ── Counters ───────────────────────────────────────────────────────────

    def increment(self, name: str, amount: float = 1.0) -> None:
        """Increment counter `name` by `amount` (default 1)."""
        with self._lock:
            self._counters[name] = self._counters.get(name, 0.0) + amount

    def get_counter(self, name: str) -> float:
        with self._lock:
            return self._counters.get(name, 0.0)

    # ── Histograms ─────────────────────────────────────────────────────────

    def record(self, name: str, value: float) -> None:
        """Record a histogram observation for `name`."""
        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = []
            bisect.insort(self._histograms[name], value)

    def get_histogram_count(self, name: str) -> int:
        with self._lock:
            return len(self._histograms.get(name, []))

    def get_histogram_sum(self, name: str) -> float:
        with self._lock:
            return sum(self._histograms.get(name, []))

    def percentile(self, name: str, pct: float) -> float:
        """Return the p{pct} value from histogram `name`.

        Uses nearest-rank method. Returns 0.0 for an empty histogram.
        `pct` is in [0, 100].
        """
        with self._lock:
            data = self._histograms.get(name, [])
        if not data:
            return 0.0
        # Nearest-rank formula: index = ceil(pct/100 * n) - 1
        n = len(data)
        idx = max(0, min(n - 1, int((pct / 100.0) * n)))
        return data[idx]

    # ── Prometheus text export ─────────────────────────────────────────────

    def dump_prometheus(self) -> str:
        """Return a Prometheus-text-format string of all metrics.

        Format::

            # HELP journey_runs_total Total journey executions
            # TYPE journey_runs_total counter
            journey_runs_total 42

            # HELP tap_latency_ms Histogram of tap latency in ms
            # TYPE tap_latency_ms histogram
            tap_latency_ms_count 100
            tap_latency_ms_sum 4500.0
            tap_latency_ms_p50 44.0
            tap_latency_ms_p95 85.0
        """
        lines: list[str] = []
        with self._lock:
            counters = dict(self._counters)
            histograms = {k: list(v) for k, v in self._histograms.items()}

        for name, value in sorted(counters.items()):
            lines += [
                f"# HELP {name} simdrive counter",
                f"# TYPE {name} counter",
                f"{name} {value}",
                "",
            ]

        for name, data in sorted(histograms.items()):
            if not data:
                continue
            count = len(data)
            total = sum(data)
            p50 = data[max(0, int(0.50 * count) - 1)] if count else 0.0
            p95 = data[max(0, int(0.95 * count) - 1)] if count else 0.0
            lines += [
                f"# HELP {name} simdrive histogram",
                f"# TYPE {name} histogram",
                f"{name}_count {count}",
                f"{name}_sum {total}",
                f"{name}_p50 {p50}",
                f"{name}_p95 {p95}",
                "",
            ]

        return "\n".join(lines)

    # ── Reset ──────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Clear all counters and histograms (primarily for testing)."""
        with self._lock:
            self._counters.clear()
            self._histograms.clear()


# ── Global singleton ────────────────────────────────────────────────────────

_GLOBAL_REGISTRY: Optional[MetricsRegistry] = None
_REGISTRY_LOCK = threading.Lock()


def get_registry() -> MetricsRegistry:
    """Return the process-wide MetricsRegistry singleton."""
    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        with _REGISTRY_LOCK:
            if _GLOBAL_REGISTRY is None:
                _GLOBAL_REGISTRY = MetricsRegistry()
    return _GLOBAL_REGISTRY


def increment_counter(name: str, amount: float = 1.0) -> None:
    """Increment a global counter."""
    get_registry().increment(name, amount)


def record_histogram(name: str, value: float) -> None:
    """Record a global histogram observation."""
    get_registry().record(name, value)


def dump_metrics() -> str:
    """Return the global registry in Prometheus text format."""
    return get_registry().dump_prometheus()
