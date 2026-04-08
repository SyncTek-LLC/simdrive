"""M10: SimulatorAIContext — aggregates driver sub-module state for Claude.

Collects a unified snapshot of the iOS Simulator state (logs, network,
performance, app state, crashes) into a :class:`DriverContext` dataclass and
formats it as markdown text suitable for injection into Claude's context window.

INIT-2026-492 — SpecterQA iOS Simulator Driver, Phase 3.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, List, Optional

logger = logging.getLogger("specterqa.ios.drivers.simulator.ai_context")


# ---------------------------------------------------------------------------
# DriverContext — aggregated snapshot
# ---------------------------------------------------------------------------


@dataclass
class DriverContext:
    """Immutable snapshot of all driver sub-module state at a point in time.

    Fields:
        screenshot_base64: Base-64 encoded PNG screenshot (may be empty string
            if no screenshot was captured).
        recent_logs: List of :class:`~specterqa.ios.drivers.simulator.console.LogEntry`
            objects from the console monitor.
        active_requests: List of
            :class:`~specterqa.ios.drivers.simulator.network.NetworkRequest`
            objects currently in-flight or recently completed.
        perf_snapshot: A
            :class:`~specterqa.ios.drivers.simulator.perf.PerfSnapshot` or
            ``None`` if performance data is unavailable.
        app_state: Dict returned by
            :class:`~specterqa.ios.drivers.simulator.state.StateInspector`.
        crashes: List of
            :class:`~specterqa.ios.drivers.simulator.crash.CrashReport` objects
            detected since the last check.
    """

    screenshot_base64: str
    recent_logs: List[Any]
    active_requests: List[Any]
    perf_snapshot: Optional[Any]
    app_state: dict[str, Any]
    crashes: List[Any]


# ---------------------------------------------------------------------------
# SimulatorAIContext
# ---------------------------------------------------------------------------


class SimulatorAIContext:
    """Aggregates iOS Simulator driver sub-module state for Claude.

    Collects data from all active sub-modules (console, network, perf, state,
    crash) and formats the result for injection into Claude's context window.

    Args:
        redactor: Optional
            :class:`~specterqa.ios.security.redactor.DataRedactor` instance.
            When provided, all formatted text passes through the redactor
            before being returned.  The redactor must expose a
            ``redact_string(text: str) -> str`` method.
    """

    def __init__(self, redactor: Optional[Any] = None) -> None:
        self._redactor = redactor

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_context(
        self,
        screenshot_b64: str = "",
        *,
        console: Optional[Any] = None,
        network: Optional[Any] = None,
        perf: Optional[Any] = None,
        state: Optional[Any] = None,
        crash: Optional[Any] = None,
    ) -> DriverContext:
        """Aggregate sub-module state into a :class:`DriverContext`.

        Calls each sub-module's primary query method and assembles the results.
        Missing (``None``) sub-modules produce empty/neutral values — this
        method never raises due to an absent sub-module.

        Args:
            screenshot_b64: Base-64 encoded PNG screenshot string.
            console: Optional :class:`ConsoleMonitor` instance.
                ``recent()`` is called with no positional args — implementations
                must support being called as ``console.recent()``.
            network: Optional :class:`NetworkInspector` instance.
                ``active()`` is called to retrieve in-flight requests, with
                ``recent()`` as a fallback if ``active()`` raises.
            perf: Optional :class:`PerfProfiler` instance.
                ``snapshot()`` is called.
            state: Optional :class:`StateInspector` instance.
                ``snapshot()`` is called.
            crash: Optional :class:`CrashDetector` instance.
                ``check()`` is called.

        Returns:
            A populated :class:`DriverContext`.
        """
        # --- console logs --------------------------------------------------
        recent_logs: List[Any] = []
        if console is not None:
            try:
                recent_logs = list(console.recent())
            except Exception as exc:  # noqa: BLE001 — aggregation boundary
                logger.debug("console.recent() failed: %s", exc)
                recent_logs = []

        # --- network requests ---------------------------------------------
        active_requests: List[Any] = []
        if network is not None:
            try:
                active_requests = list(network.active())
            except Exception as exc:  # noqa: BLE001 — aggregation boundary
                logger.debug("network.active() failed: %s", exc)
                try:
                    active_requests = list(network.recent())
                except Exception as exc2:  # noqa: BLE001
                    logger.debug("network.recent() failed: %s", exc2)
                    active_requests = []

        # --- performance snapshot -----------------------------------------
        perf_snapshot: Optional[Any] = None
        if perf is not None:
            try:
                perf_snapshot = perf.snapshot()
            except Exception as exc:  # noqa: BLE001 — aggregation boundary
                logger.debug("perf.snapshot() failed: %s", exc)
                perf_snapshot = None

        # --- app state ----------------------------------------------------
        app_state: dict[str, Any] = {}
        if state is not None:
            try:
                result = state.snapshot()
                app_state = result if isinstance(result, dict) else {}
            except Exception as exc:  # noqa: BLE001 — aggregation boundary
                logger.debug("state.snapshot() failed: %s", exc)
                app_state = {}

        # --- crash reports ------------------------------------------------
        crashes: List[Any] = []
        if crash is not None:
            try:
                crashes = list(crash.check())
            except Exception as exc:  # noqa: BLE001 — aggregation boundary
                logger.debug("crash.check() failed: %s", exc)
                crashes = []

        return DriverContext(
            screenshot_base64=screenshot_b64,
            recent_logs=recent_logs,
            active_requests=active_requests,
            perf_snapshot=perf_snapshot,
            app_state=app_state,
            crashes=crashes,
        )

    def format_for_claude(self, ctx: DriverContext) -> str:
        """Format a :class:`DriverContext` as a markdown string for Claude.

        Sections emitted (always present, even if empty):
        - ``## Recent Logs``
        - ``## Network Activity``
        - ``## Performance``
        - ``## App State``
        - ``## Crashes``

        Args:
            ctx: The :class:`DriverContext` to format.

        Returns:
            A markdown string.  If a :class:`DataRedactor` was injected at
            construction time, the entire output passes through
            ``redactor.redact_string()`` before being returned.
        """
        sections: list[str] = []

        # --- Recent Logs --------------------------------------------------
        sections.append("## Recent Logs")
        if ctx.recent_logs:
            for entry in ctx.recent_logs:
                ts = getattr(entry, "timestamp", "")
                level = getattr(entry, "level", "")
                msg = getattr(entry, "message", str(entry))
                sections.append(f"[{ts}] [{level.upper()}] {msg}")
        else:
            sections.append("(none)")

        # --- Network Activity ---------------------------------------------
        sections.append("")
        sections.append("## Network Activity")
        if ctx.active_requests:
            for req in ctx.active_requests:
                method = getattr(req, "method", "?")
                url = getattr(req, "url", "?")
                status = getattr(req, "status_code", None)
                duration = getattr(req, "duration_ms", None)
                line = f"{method} {url}"
                if status is not None:
                    line += f" → {status}"
                if duration is not None:
                    line += f" ({duration:.1f}ms)"
                sections.append(line)
        else:
            sections.append("(none)")

        # --- Performance --------------------------------------------------
        sections.append("")
        sections.append("## Performance")
        if ctx.perf_snapshot is not None:
            snap = ctx.perf_snapshot
            memory_mb = getattr(snap, "memory_mb", None)
            cpu_percent = getattr(snap, "cpu_percent", None)
            thread_count = getattr(snap, "thread_count", None)
            fps_estimate = getattr(snap, "fps_estimate", None)
            if memory_mb is not None:
                sections.append(f"Memory: {memory_mb:.1f} MB")
            if cpu_percent is not None:
                sections.append(f"CPU: {cpu_percent:.1f}%")
            if thread_count is not None:
                sections.append(f"Threads: {thread_count}")
            if fps_estimate is not None:
                sections.append(f"FPS: {fps_estimate:.0f}")
        else:
            sections.append("(unavailable)")

        # --- App State ----------------------------------------------------
        sections.append("")
        sections.append("## App State")
        if ctx.app_state:
            try:
                sections.append(json.dumps(ctx.app_state, indent=2, default=str))
            except (TypeError, ValueError):
                sections.append(str(ctx.app_state))
        else:
            sections.append("(empty)")

        # --- Crashes ------------------------------------------------------
        sections.append("")
        sections.append("## Crashes")
        if ctx.crashes:
            for crash in ctx.crashes:
                exc_type = getattr(crash, "exception_type", "Unknown")
                ts = getattr(crash, "timestamp", "")
                last_exc = getattr(crash, "last_exception", None)
                backtrace = getattr(crash, "backtrace", [])
                sections.append(f"[{ts}] {exc_type}")
                if last_exc:
                    sections.append(f"  Last exception: {last_exc}")
                if backtrace:
                    sections.append(f"  Backtrace: {', '.join(str(f) for f in backtrace[:5])}")
        else:
            sections.append("(none)")

        output = "\n".join(sections)

        # Apply redaction if a redactor was injected
        if self._redactor is not None:
            try:
                output = self._redactor.redact_string(output)
            except Exception as exc:  # noqa: BLE001 — redactor is user-provided
                logger.debug("redactor.redact_string() failed: %s", exc)

        return output

    def build_system_prompt(self, product_name: str = "") -> str:
        """Return an iOS Simulator-specific system prompt for Claude.

        The prompt instructs Claude on how to reason about the iOS Simulator
        driver context and what to focus on when diagnosing issues.

        Args:
            product_name: The name of the app under test (embedded in the
                prompt for personalisation).

        Returns:
            A plain-text system prompt string.
        """
        app_clause = f" for **{product_name}**" if product_name else ""
        return (
            f"You are an expert iOS test automation assistant{app_clause}.\n\n"
            "You are analysing a live iOS Simulator session. The context provided to you "
            "contains real-time data captured directly from the simulator:\n\n"
            "- **Recent Logs**: console output from `xcrun simctl log stream`\n"
            "- **Network Activity**: HTTP requests intercepted from the simulator\n"
            "- **Performance**: CPU, memory, thread count, and FPS from `ps` / `simctl`\n"
            "- **App State**: NSUserDefaults and container filesystem snapshot\n"
            "- **Crashes**: crash reports from `~/Library/Logs/DiagnosticReports`\n\n"
            "Your role:\n"
            "1. Identify bugs, regressions, and unexpected behaviour in the iOS app.\n"
            "2. Correlate log entries, network requests, performance metrics, and "
            "crash reports to diagnose root causes.\n"
            "3. Propose actionable next steps (tap targets, assertions, repro steps) "
            "that a simulator driver can execute.\n"
            "4. Never leak sensitive data — all credentials and tokens are pre-redacted.\n\n"
            "Focus on the iPhone/iPad simulator context and the iOS platform conventions "
            "(UIKit, SwiftUI, NSURLSession, XCTest) when reasoning about observed behaviour."
        )
