"""Benchmark: observe dispatch latency.

Measures the Python overhead of the observe() dispatch chain (screenshot,
SoM detection, sidecar write) with mocked simulator backend.

Run with:
    pytest simdrive/tests/perf/bench_observe.py -v

P95 target (mocked): < 2ms overhead (the actual screenshot capture is I/O,
not counted here). The regression gate is 2× the baseline in bench_baselines.json.
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

import json
from pathlib import Path as _Path

_BASELINES_PATH = _Path(__file__).parent / "bench_baselines.json"


def _check_regression(metric_name: str, measured_p95_ms: float) -> None:
    """Fail if measured p95 exceeds 2× the baseline."""
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


@pytest.fixture()
def tmp_observe_dir(tmp_path: Path) -> Path:
    return tmp_path / "observe_bench"


def _run_observe_iterations(out_dir: Path, n: int) -> list[float]:
    """Run observe() with mocked screenshot N times; return per-call latencies in ms."""
    from simdrive import observe

    out_dir.mkdir(parents=True, exist_ok=True)

    # Create a minimal 1×1 PNG in tmp so PIL can open it
    import struct
    import zlib
    def _make_1px_png(path: Path) -> None:
        """Write a valid 1×1 white PNG to `path`."""
        def _chunk(tag: bytes, data: bytes) -> bytes:
            crc = zlib.crc32(tag + data) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

        header = b"\x89PNG\r\n\x1a\n"
        ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        idat_raw = b"\x00\xff\xff\xff"  # filter byte + RGB
        idat_compressed = zlib.compress(idat_raw)
        png_bytes = (
            header
            + _chunk(b"IHDR", ihdr_data)
            + _chunk(b"IDAT", idat_compressed)
            + _chunk(b"IEND", b"")
        )
        path.write_bytes(png_bytes)

    latencies: list[float] = []

    with (
        patch("simdrive.observe.sim.screenshot", side_effect=lambda udid, p: _make_1px_png(p)),
        patch("simdrive.observe.som.detect_marks", return_value=[]),
        patch("simdrive.observe.get_bounds", return_value=None),
    ):
        for i in range(n):
            t0 = time.perf_counter()
            obs = observe.observe("test-udid-bench", out_dir, annotate=False)
            latencies.append((time.perf_counter() - t0) * 1000.0)

    return latencies


def _p95(data: list[float]) -> float:
    sorted_data = sorted(data)
    idx = max(0, int(0.95 * len(sorted_data)) - 1)
    return sorted_data[idx]


@pytest.mark.perf
def test_observe_dispatch_p95(tmp_observe_dir: Path) -> None:
    """Observe dispatch p95 must not exceed 2× baseline."""
    latencies = _run_observe_iterations(tmp_observe_dir, N_ITERATIONS)
    p95 = _p95(latencies)
    p50 = _p95(latencies[:len(latencies)//2])  # approximate p50

    print(f"\nobserve_dispatch: n={N_ITERATIONS}, p50={p50:.2f}ms, p95={p95:.2f}ms")
    _check_regression("observe_dispatch", p95)

    # Hard cap: pure dispatch overhead should never exceed 50ms on any machine
    assert p95 < 50.0, f"observe dispatch p95={p95:.1f}ms exceeds absolute cap of 50ms"
