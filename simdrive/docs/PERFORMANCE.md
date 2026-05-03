# SimDrive Performance Benchmarks

## Running the Benchmark Suite

```bash
# From the repo root:
python3 -m pytest simdrive/tests/perf/bench_observe.py simdrive/tests/perf/bench_tap.py simdrive/tests/perf/bench_journey_throughput.py -v -s
```

The perf benches are excluded from the normal test suite (`pytest simdrive/tests/ -k "not perf"`) because they use wall-clock measurements that can be slow and noisy in CI.

## What the Benchmarks Measure

All benches mock out real I/O (screenshot capture, HID injection, LLM calls). They measure **Python dispatch overhead** only:

| Benchmark | Operation | Absolute Cap |
|---|---|---|
| `bench_observe.py` | `observe()` plumbing with mocked screenshot | p95 < 50ms |
| `bench_tap.py` | `act.tap()` dispatch with mocked HID | p95 < 20ms |
| `bench_journey_throughput.py` | per-step runner loop with mocked LLM | p95 < 50ms/step |

## Regression Gate

Committed baselines live in `simdrive/tests/perf/bench_baselines.json`. Each benchmark will fail if its measured p95 exceeds **2× the baseline value**. This catches regressions introduced by new middleware, log call overhead, or dispatch layers.

### Baseline Philosophy

Baselines represent pure Python overhead, not real I/O time. This makes them:

- **Stable across machines** — no dependency on simulator or network speed
- **Sensitive to code changes** — new log calls, extra dict allocations, or middleware layers show up
- **Not representative of user-visible latency** — which includes real I/O and LLM call time

### Established Baselines (2026-05-02)

| Metric | P50 | P95 |
|---|---|---|
| `observe_dispatch` | 0.25ms | 0.24ms |
| `tap_dispatch` | 0.01ms | 0.01ms |
| `journey_loop_step` | 0.18ms/step | 0.17ms/step |

## Updating Baselines

If a genuine performance improvement is made:

1. Run the benchmarks and note the new measurements.
2. Update `bench_baselines.json` with the new p50/p95 values.
3. Commit the updated baseline file alongside the performance change.

Never update baselines to mask a regression — the gate exists to catch regressions, not to be bypassed.

## Real-World Performance Targets (from engineering spec §4.3)

These are for full end-to-end operations including real I/O. The perf benches do not directly enforce these (they mock I/O), but the regression gate ensures new code doesn't degrade the Python layer:

| Operation | P50 target | P95 target |
|---|---|---|
| `tool_observe` (sim, 1024×768) | < 600ms | < 1.2s |
| `tool_tap` (sim) | < 80ms | < 150ms |
| `tool_type_text` (sim, 20 chars) | < 1.5s | < 2.5s |
| Journey runner step (incl. Claude call) | < 4s | < 8s |
