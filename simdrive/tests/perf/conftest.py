"""pytest configuration for perf benchmarks.

WHY separate from main conftest.py: perf tests run with a different invocation
(`pytest simdrive/tests/perf/`) and should NOT run in the normal test suite.
This conftest only applies to the perf/ directory.

Regression gate: if a measured p95 exceeds 2× the baseline value stored in
bench_baselines.json, the test fails. This catches performance regressions
introduced by surgical edits or new log call overhead.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# Baseline JSON path relative to this conftest file.
_BASELINES_PATH = Path(__file__).parent / "bench_baselines.json"


def pytest_configure(config: pytest.Config) -> None:
    """Register the custom perf marker so -W doesn't emit warnings."""
    config.addinivalue_line(
        "markers",
        "perf: mark a test as a performance benchmark (excluded from normal suite)",
    )


def load_baselines() -> dict:
    """Load the committed baseline measurements."""
    if _BASELINES_PATH.exists():
        return json.loads(_BASELINES_PATH.read_text())
    return {}


def check_regression(metric_name: str, measured_p95_ms: float) -> None:
    """Raise AssertionError if measured p95 exceeds 2× the baseline.

    If no baseline exists for this metric, record it but do not fail
    (first run sets the baseline, it doesn't gate against it).
    """
    baselines = load_baselines()
    if metric_name not in baselines:
        return  # No baseline yet — first run is free

    baseline_p95 = baselines[metric_name]["p95_ms"]
    limit = baseline_p95 * 2.0
    assert measured_p95_ms <= limit, (
        f"REGRESSION: {metric_name} p95={measured_p95_ms:.1f}ms "
        f"exceeds 2× baseline ({baseline_p95:.1f}ms → limit {limit:.1f}ms). "
        "Check recent changes to observe.py / act.py / recorder.py for overhead."
    )
