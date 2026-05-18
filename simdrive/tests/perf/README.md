# SimDrive Perf Benchmarks

## Overview

These benchmarks measure the Python-layer dispatch overhead of core SimDrive operations with all real I/O (screenshot capture, HID injection, LLM calls) mocked out. They enforce that the observability instrumentation added in Component 9 doesn't introduce excessive overhead.

## Running

```bash
# Run perf benchmarks only:
pytest simdrive/tests/perf/ -v

# Run normal unit suite (excludes perf):
pytest simdrive/tests/ -v --tb=short -k "not perf"
```

The perf tests are intentionally excluded from the main `pytest simdrive/tests/` run because they use `time.perf_counter()` measurements that can be slow/noisy in CI and don't belong in the normal test gate.

## Regression Gate

`conftest.py` loads `bench_baselines.json` and fails any benchmark whose measured p95 exceeds **2× the baseline value**. The gate uses the committed baselines, so:

- First run against a fresh baseline file: benchmarks record measurements but do not gate.
- Subsequent runs: the 2× gate is enforced automatically.

## Baseline Philosophy

Baselines in `bench_baselines.json` represent the **Python dispatch overhead** of the operation, not the total wall-clock time including real I/O. This makes them stable across machines (CI, local, etc.) while still catching regressions introduced by new middleware, log calls, or dispatch layers.

## Metrics Captured

| Benchmark | Operation | P95 Baseline |
|---|---|---|
| `bench_observe.py` | `observe()` dispatch (mocked screenshot) | 2ms |
| `bench_tap.py` | `act.tap()` dispatch (mocked HID) | 1.5ms |
| `bench_journey_throughput.py` | per-step loop overhead (mocked LLM+tools) | 8ms |

## Adding a New Baseline

If a performance improvement is made and the new measurements are lower than the committed baseline, update `bench_baselines.json` with the new measurements and commit it alongside the code change.
