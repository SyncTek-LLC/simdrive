"""Structured observability for simdrive.

Submodules:
  logger      — get_logger(name); SIMDRIVE_DEBUG=1 → JSON, else human-readable
  metrics     — counters + histograms; dump_metrics() → Prometheus text
  tracing     — lightweight span-context helpers for journey-step traceability
  crash_sink  — local-first, scrubbed crash sink (~/.heka/crashes/*.json)
"""
from .crash_sink import (
    build_crash_record,
    install_crash_sink,
    list_crashes,
    record_crash,
)
from .logger import configure_logging, get_logger
from .metrics import (
    MetricsRegistry,
    dump_metrics,
    get_registry,
    increment_counter,
    record_histogram,
)
from .tracing import Span, TraceContext, start_span

__all__ = [
    "configure_logging",
    "get_logger",
    "MetricsRegistry",
    "dump_metrics",
    "get_registry",
    "increment_counter",
    "record_histogram",
    "Span",
    "TraceContext",
    "start_span",
    "build_crash_record",
    "install_crash_sink",
    "list_crashes",
    "record_crash",
]
