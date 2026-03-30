"""Tests for M10: SimulatorAIContext.

TDD Phase 3 — INIT-2026-492, SpecterQA iOS Simulator Driver.
These tests are written BEFORE the implementation exists and must be
importable even when the implementation module is absent.

Module under test (to be created by CodeAtlas):
    specterqa/ios/drivers/simulator/ai_context.py  — SimulatorAIContext, DriverContext
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard — tests are importable even if impl is missing.
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.drivers.simulator.ai_context import (  # type: ignore[import]
        DriverContext,
        SimulatorAIContext,
    )
    _AI_CONTEXT_AVAILABLE = True
except ImportError:
    _AI_CONTEXT_AVAILABLE = False
    DriverContext = None  # type: ignore[assignment,misc]
    SimulatorAIContext = None  # type: ignore[assignment,misc]

needs_ai_context = pytest.mark.skipif(
    not _AI_CONTEXT_AVAILABLE,
    reason="specterqa.ios.drivers.simulator.ai_context not yet implemented",
)

# ---------------------------------------------------------------------------
# Helpers — build minimal mock sub-module instances
# ---------------------------------------------------------------------------


def _make_log_entry(message: str = "test message", level: str = "info") -> MagicMock:
    """Build a mock LogEntry."""
    entry = MagicMock()
    entry.message = message
    entry.level = level
    entry.timestamp = "2026-03-29T10:00:00Z"
    entry.subsystem = "com.example.app"
    entry.category = "network"
    entry.process = "MyApp"
    entry.thread_id = 42
    entry.is_error = level in ("error", "fault")
    return entry


def _make_network_request(url: str = "https://api.example.com/v1/data") -> MagicMock:
    """Build a mock NetworkRequest."""
    req = MagicMock()
    req.request_id = "req-001"
    req.method = "GET"
    req.url = url
    req.host = "api.example.com"
    req.path = "/v1/data"
    req.status_code = 200
    req.duration_ms = 123.4
    req.error = None
    return req


def _make_perf_snapshot() -> MagicMock:
    """Build a mock PerfSnapshot."""
    snap = MagicMock()
    snap.memory_mb = 145.2
    snap.cpu_percent = 12.5
    snap.thread_count = 18
    snap.disk_usage_mb = 32.0
    snap.fps_estimate = 60.0
    snap.timestamp = 1743247200.0
    return snap


def _make_crash_report(
    exception_type: str = "EXC_BAD_ACCESS",
    last_exception: str | None = "Assertion failed: index out of range",
) -> MagicMock:
    """Build a mock CrashReport."""
    crash = MagicMock()
    crash.timestamp = "2026-03-29T10:01:00Z"
    crash.exception_type = exception_type
    crash.exception_code = "0x0000000000000001"
    crash.crashing_thread = 0
    crash.backtrace = ["frame0", "frame1"]
    crash.last_exception = last_exception
    crash.app_version = "1.0.0"
    return crash


def _make_console_monitor(logs: list | None = None) -> MagicMock:
    """Build a mock ConsoleMonitor that returns the given log entries."""
    monitor = MagicMock()
    monitor.recent.return_value = logs if logs is not None else [_make_log_entry()]
    monitor.errors.return_value = []
    return monitor


def _make_network_inspector(requests: list | None = None) -> MagicMock:
    """Build a mock NetworkInspector that returns the given requests."""
    inspector = MagicMock()
    inspector.active.return_value = requests if requests is not None else [_make_network_request()]
    inspector.recent.return_value = requests if requests is not None else [_make_network_request()]
    return inspector


def _make_perf_profiler(snapshot: MagicMock | None = None) -> MagicMock:
    """Build a mock PerfProfiler that returns the given snapshot."""
    profiler = MagicMock()
    profiler.snapshot.return_value = snapshot if snapshot is not None else _make_perf_snapshot()
    return profiler


def _make_state_inspector(state: dict | None = None) -> MagicMock:
    """Build a mock StateInspector that returns the given state snapshot."""
    inspector = MagicMock()
    inspector.snapshot.return_value = state if state is not None else {
        "user_defaults": {"onboarding_complete": True},
        "keychain": "[REDACTED]",
        "container_size_mb": 12.4,
    }
    return inspector


def _make_crash_detector(crashes: list | None = None) -> MagicMock:
    """Build a mock CrashDetector that returns the given crash reports."""
    detector = MagicMock()
    detector.check.return_value = crashes if crashes is not None else []
    return detector


# ===========================================================================
# Test 1: DriverContext dataclass has all required fields
# ===========================================================================


@needs_ai_context
class TestDriverContextDataclass:
    """DriverContext must be a dataclass with the required fields."""

    def test_driver_context_has_required_fields(self):
        """DriverContext must have screenshot_base64, recent_logs, active_requests,
        perf_snapshot, app_state, and crashes fields."""
        fields = {f.name for f in dataclasses.fields(DriverContext)}
        required = {
            "screenshot_base64",
            "recent_logs",
            "active_requests",
            "perf_snapshot",
            "app_state",
            "crashes",
        }
        assert required.issubset(fields), (
            f"DriverContext is missing fields: {required - fields}"
        )

    def test_driver_context_is_dataclass(self):
        """DriverContext must be a proper dataclass."""
        assert dataclasses.is_dataclass(DriverContext)

    def test_driver_context_instantiates_with_all_fields(self):
        """DriverContext can be constructed with all required fields."""
        ctx = DriverContext(
            screenshot_base64="abc123",
            recent_logs=[_make_log_entry()],
            active_requests=[_make_network_request()],
            perf_snapshot=_make_perf_snapshot(),
            app_state={"key": "value"},
            crashes=[],
        )
        assert ctx.screenshot_base64 == "abc123"
        assert len(ctx.recent_logs) == 1
        assert len(ctx.active_requests) == 1
        assert ctx.app_state == {"key": "value"}
        assert ctx.crashes == []


# ===========================================================================
# Test 2: build_context aggregates from all sub-module instances
# ===========================================================================


@needs_ai_context
class TestBuildContextAggregation:
    """build_context should call each sub-module and assemble a DriverContext."""

    def test_build_context_returns_driver_context(self):
        """build_context must return a DriverContext instance."""
        builder = SimulatorAIContext()
        ctx = builder.build_context(
            screenshot_b64="dGVzdA==",
            console=_make_console_monitor(),
            network=_make_network_inspector(),
            perf=_make_perf_profiler(),
            state=_make_state_inspector(),
            crash=_make_crash_detector(),
        )
        assert isinstance(ctx, DriverContext)

    def test_build_context_stores_screenshot(self):
        """build_context must store the screenshot_b64 in DriverContext."""
        builder = SimulatorAIContext()
        ctx = builder.build_context(
            screenshot_b64="dGVzdA==",
            console=_make_console_monitor(),
            network=_make_network_inspector(),
            perf=_make_perf_profiler(),
            state=_make_state_inspector(),
            crash=_make_crash_detector(),
        )
        assert ctx.screenshot_base64 == "dGVzdA=="

    def test_build_context_calls_console_recent(self):
        """build_context must call console.recent() to get logs."""
        builder = SimulatorAIContext()
        console = _make_console_monitor(logs=[_make_log_entry("network call")])
        ctx = builder.build_context(
            screenshot_b64="x",
            console=console,
            network=_make_network_inspector(),
            perf=_make_perf_profiler(),
            state=_make_state_inspector(),
            crash=_make_crash_detector(),
        )
        console.recent.assert_called()
        assert len(ctx.recent_logs) >= 1

    def test_build_context_calls_crash_check(self):
        """build_context must call crash.check() to get crash reports."""
        builder = SimulatorAIContext()
        crash_report = _make_crash_report()
        crash = _make_crash_detector(crashes=[crash_report])
        ctx = builder.build_context(
            screenshot_b64="x",
            console=_make_console_monitor(),
            network=_make_network_inspector(),
            perf=_make_perf_profiler(),
            state=_make_state_inspector(),
            crash=crash,
        )
        crash.check.assert_called()
        assert len(ctx.crashes) >= 1

    def test_build_context_calls_state_snapshot(self):
        """build_context must call state.snapshot() to get app state."""
        builder = SimulatorAIContext()
        state = _make_state_inspector(state={"key": "val"})
        ctx = builder.build_context(
            screenshot_b64="x",
            console=_make_console_monitor(),
            network=_make_network_inspector(),
            perf=_make_perf_profiler(),
            state=state,
            crash=_make_crash_detector(),
        )
        state.snapshot.assert_called()
        assert ctx.app_state is not None


# ===========================================================================
# Test 3: build_context handles None/missing sub-modules gracefully
# ===========================================================================


@needs_ai_context
class TestBuildContextNoneHandling:
    """build_context must not raise when sub-modules are None."""

    def test_build_context_handles_none_console(self):
        """build_context must not raise when console=None."""
        builder = SimulatorAIContext()
        ctx = builder.build_context(
            screenshot_b64="x",
            console=None,
            network=_make_network_inspector(),
            perf=_make_perf_profiler(),
            state=_make_state_inspector(),
            crash=_make_crash_detector(),
        )
        assert isinstance(ctx, DriverContext)
        assert ctx.recent_logs == [] or ctx.recent_logs is not None

    def test_build_context_handles_none_crash(self):
        """build_context must not raise when crash=None."""
        builder = SimulatorAIContext()
        ctx = builder.build_context(
            screenshot_b64="x",
            console=_make_console_monitor(),
            network=_make_network_inspector(),
            perf=_make_perf_profiler(),
            state=_make_state_inspector(),
            crash=None,
        )
        assert isinstance(ctx, DriverContext)
        assert ctx.crashes == [] or ctx.crashes is not None

    def test_build_context_handles_all_none_modules(self):
        """build_context must not raise when all sub-modules are None."""
        builder = SimulatorAIContext()
        ctx = builder.build_context(
            screenshot_b64="x",
            console=None,
            network=None,
            perf=None,
            state=None,
            crash=None,
        )
        assert isinstance(ctx, DriverContext)


# ===========================================================================
# Tests 4–8: format_for_claude sections
# ===========================================================================


@needs_ai_context
class TestFormatForClaude:
    """format_for_claude must include each required section."""

    def _make_full_context(self) -> Any:
        """Build a DriverContext with all fields populated."""
        return DriverContext(
            screenshot_base64="dGVzdA==",
            recent_logs=[_make_log_entry("GET /api/users 200")],
            active_requests=[_make_network_request()],
            perf_snapshot=_make_perf_snapshot(),
            app_state={"onboarding": True},
            crashes=[],
        )

    def test_format_includes_recent_logs_section(self):
        """format_for_claude output must contain '## Recent Logs'."""
        builder = SimulatorAIContext()
        ctx = self._make_full_context()
        output = builder.format_for_claude(ctx)
        assert "## Recent Logs" in output

    def test_format_includes_network_activity_section(self):
        """format_for_claude output must contain '## Network Activity'."""
        builder = SimulatorAIContext()
        ctx = self._make_full_context()
        output = builder.format_for_claude(ctx)
        assert "## Network Activity" in output

    def test_format_includes_performance_section(self):
        """format_for_claude output must contain '## Performance'."""
        builder = SimulatorAIContext()
        ctx = self._make_full_context()
        output = builder.format_for_claude(ctx)
        assert "## Performance" in output

    def test_format_includes_app_state_section(self):
        """format_for_claude output must contain '## App State'."""
        builder = SimulatorAIContext()
        ctx = self._make_full_context()
        output = builder.format_for_claude(ctx)
        assert "## App State" in output

    def test_format_includes_crashes_section_with_exception_details(self):
        """format_for_claude output must include '## Crashes' and exception type
        when crashes are present."""
        builder = SimulatorAIContext()
        crash = _make_crash_report(exception_type="EXC_BAD_ACCESS", last_exception="index out of range")
        ctx = DriverContext(
            screenshot_base64="x",
            recent_logs=[],
            active_requests=[],
            perf_snapshot=_make_perf_snapshot(),
            app_state={},
            crashes=[crash],
        )
        output = builder.format_for_claude(ctx)
        assert "## Crashes" in output
        assert "EXC_BAD_ACCESS" in output

    def test_format_returns_empty_sections_when_no_data(self):
        """format_for_claude must still return all section headers even when
        lists are empty."""
        builder = SimulatorAIContext()
        ctx = DriverContext(
            screenshot_base64="",
            recent_logs=[],
            active_requests=[],
            perf_snapshot=None,
            app_state={},
            crashes=[],
        )
        output = builder.format_for_claude(ctx)
        # All sections must still be present even with empty data
        assert "## Recent Logs" in output
        assert "## Network Activity" in output
        assert "## App State" in output


# ===========================================================================
# Tests 9–10: build_system_prompt
# ===========================================================================


@needs_ai_context
class TestBuildSystemPrompt:
    """build_system_prompt must return an iOS-specific prompt string."""

    def test_system_prompt_contains_ios_instructions(self):
        """build_system_prompt must mention iOS-specific interaction context."""
        builder = SimulatorAIContext()
        prompt = builder.build_system_prompt(product_name="MyApp")
        # Must reference iOS / simulator context
        assert any(
            keyword in prompt.lower()
            for keyword in ("ios", "simulator", "iphone", "ipad")
        ), f"Expected iOS-specific instructions in system prompt, got: {prompt[:200]}"

    def test_system_prompt_includes_product_name(self):
        """build_system_prompt must embed the product_name in the output."""
        builder = SimulatorAIContext()
        prompt = builder.build_system_prompt(product_name="HealthAtlas")
        assert "HealthAtlas" in prompt


# ===========================================================================
# Test 11: DataRedactor integration
# ===========================================================================


@needs_ai_context
class TestDataRedactorIntegration:
    """When a DataRedactor is injected, sensitive data must be redacted in output."""

    def test_redactor_applied_to_format_output(self):
        """SimulatorAIContext with a redactor should sanitise the formatted string.

        We inject a mock redactor that replaces any string with '[REDACTED]' and
        verify that format_for_claude calls it (i.e. the output does not contain
        raw sensitive data from logs).
        """
        mock_redactor = MagicMock()
        # Redactor.redact_string should return a safe version
        mock_redactor.redact_string.side_effect = lambda s: s.replace(
            "Bearer secret-token-123", "[REDACTED]"
        )

        builder = SimulatorAIContext(redactor=mock_redactor)
        sensitive_log = _make_log_entry(
            message="Authorization: Bearer secret-token-123", level="info"
        )
        ctx = DriverContext(
            screenshot_base64="x",
            recent_logs=[sensitive_log],
            active_requests=[],
            perf_snapshot=None,
            app_state={},
            crashes=[],
        )
        output = builder.format_for_claude(ctx)
        # Raw token must not appear in the formatted output
        assert "Bearer secret-token-123" not in output
