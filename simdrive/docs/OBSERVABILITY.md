# SimDrive Observability

## Enabling Debug Mode

Set `SIMDRIVE_DEBUG=1` to activate structured JSON logging at DEBUG level:

```bash
SIMDRIVE_DEBUG=1 simdrive run --journey sign_in
```

Without this variable, the logger emits INFO-and-above in human-readable format to stderr.

## Log Shape

### Human-readable (default)

```
2026-05-02 14:35:01 [INFO] simdrive.recorder: recording started
2026-05-02 14:35:02 [DEBUG] simdrive.observe: observe complete
```

### JSON (SIMDRIVE_DEBUG=1)

One JSON object per line, emitted to stderr:

```json
{"timestamp": "2026-05-02T14:35:01Z", "level": "INFO", "name": "simdrive.recorder", "message": "recording started", "recording_name": "login-flow", "session_id": "sess-abc123"}
{"timestamp": "2026-05-02T14:35:02Z", "level": "DEBUG", "name": "simdrive.observe", "message": "observe complete", "udid": "ABC123", "latency_ms": 342.1, "marks_count": 7, "target": "simulator"}
```

All JSON records include:

| Field | Type | Description |
|---|---|---|
| `timestamp` | ISO-8601 UTC | When the event was recorded |
| `level` | string | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `name` | string | Logger name (e.g. `simdrive.journey.runner`) |
| `message` | string | Human-readable description |
| Extra fields | any | Tool-specific context (see below) |

## Logger Names

| Name | Module | Key extra fields |
|---|---|---|
| `simdrive.recorder` | `recorder.py` | `recording_name`, `session_id`, `yaml_path` |
| `simdrive.observe` | `observe.py` | `udid`, `latency_ms`, `marks_count`, `target` |
| `simdrive.act` | `act.py` | `x`, `y`, `latency_ms` |
| `simdrive.journey.runner` | `journey/runner.py` | `journey_name`, `step_idx`, outcome fields |
| `simdrive.license.validator` | `license/validator.py` | `tier`, `expires_at`, `customer_email` |
| `simdrive.cloud.auth` | `cloud/auth.py` | `tier`, `customer_email`, `path` |

## Metrics Output

The `simdrive.observability.metrics` module tracks four canonical metrics:

| Metric | Type | Description |
|---|---|---|
| `journey_runs_total` | counter | Total number of journey executions |
| `tap_latency_ms` | histogram | Per-tap dispatch latency (Python layer) |
| `observe_latency_ms` | histogram | Per-observe call latency (screenshot + SoM) |
| `claude_call_cost_usd` | histogram | Per-LLM-call cost in USD |

### Accessing Metrics

```python
from simdrive.observability.metrics import dump_metrics, get_registry

# Prometheus text format
print(dump_metrics())

# Programmatic
reg = get_registry()
print(reg.get_counter("journey_runs_total"))
print(reg.percentile("tap_latency_ms", 95))
```

## Tracing

Each journey step can be wrapped in a `Span` for correlating log lines:

```python
from simdrive.observability.tracing import start_span

with start_span("journey.step", metadata={"step_idx": 3, "tool": "tap"}) as span:
    # do work
    log.debug("step action", extra={"span_id": span.span_id})
# span.duration_ms is now set
```

Spans are serialized to dicts via `span.to_dict()` and can be emitted in the JSON log stream.

## Using the Logger

```python
from simdrive.observability.logger import get_logger

log = get_logger("simdrive.my_module")
log.info("something happened", extra={"key": "value"})
log.debug("detailed trace", extra={"latency_ms": 42.5})
```

`get_logger()` auto-configures the root `simdrive` logger on first call if `configure_logging()` hasn't been called. Call `configure_logging()` explicitly at startup to apply env-var settings before any log calls.
