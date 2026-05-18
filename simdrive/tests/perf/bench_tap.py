"""Benchmark: tap dispatch latency.

Measures the Python overhead of act.tap() with mocked HID backend.

Run with:
    pytest simdrive/tests/perf/bench_tap.py -v

P95 target (mocked): < 1.5ms overhead. Regression gate: 2× baseline.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

import json
from pathlib import Path as _Path

_BASELINES_PATH = _Path(__file__).parent / "bench_baselines.json"


def _check_regression(metric_name: str, measured_p95_ms: float) -> None:
    """Fail if measured p95 exceeds 2× the baseline from bench_baselines.json."""
    if not _BASELINES_PATH.exists():
        return
    baselines = json.loads(_BASELINES_PATH.read_text())
    if metric_name not in baselines:
        return
    baseline_p95 = baselines[metric_name]["p95_ms"]
    limit = baseline_p95 * 2.0
    assert measured_p95_ms <= limit, (
        f"REGRESSION: {metric_name} p95={measured_p95_ms:.1f}ms "
        f"exceeds 2× baseline ({baseline_p95:.1f}ms → limit {limit:.1f}ms)."
    )

N_ITERATIONS = 1000


def _p95(data: list[float]) -> float:
    sorted_data = sorted(data)
    idx = max(0, int(0.95 * len(sorted_data)) - 1)
    return sorted_data[idx]


@pytest.mark.perf
def test_tap_dispatch_p95() -> None:
    """Tap dispatch p95 must not exceed 2× baseline."""
    from simdrive import act

    latencies: list[float] = []

    with (
        patch.object(act, "_backend", return_value="hid"),
        patch("simdrive.act.hid_inject.tap", return_value=None),
        patch("simdrive.act.hid_inject.device_size_points", return_value=(390.0, 844.0, 3.0)),
    ):
        for _ in range(N_ITERATIONS):
            t0 = time.perf_counter()
            act.tap(100, 200, 390, 844, udid="bench-udid")
            latencies.append((time.perf_counter() - t0) * 1000.0)

    p95 = _p95(latencies)
    p50 = _p95(latencies[:len(latencies)//2])

    print(f"\ntap_dispatch: n={N_ITERATIONS}, p50={p50:.2f}ms, p95={p95:.2f}ms")
    _check_regression("tap_dispatch", p95)

    # Hard cap: pure dispatch overhead should never exceed 20ms on any machine
    assert p95 < 20.0, f"tap dispatch p95={p95:.1f}ms exceeds absolute cap of 20ms"
