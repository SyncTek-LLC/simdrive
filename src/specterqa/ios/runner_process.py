"""RunnerProcess — single owner of the XCTest runner process lifecycle.

Every path that needs a runner asks RunnerProcess for one.
No path bypasses it.

INIT-2026-525 — SpecterQA iOS v14.0.0a1.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from enum import Enum, auto
from pathlib import Path
from typing import Optional

logger = logging.getLogger("specterqa.ios.runner_process")

# ---------------------------------------------------------------------------
# Import helpers at module level so tests can patch them
# ---------------------------------------------------------------------------

# These are imported here (not just inside methods) so that
# `patch("specterqa.ios.runner_process._needs_rebuild", ...)` works in tests.
# The imports are guarded against circular-import risk with a try/except.
try:
    from specterqa.ios.session_manager import (  # noqa: E402
        _needs_rebuild,
        _find_xctestrun,
        _DEFAULT_RUNNER_BUILD_DIR,
        TestSession,
    )
except ImportError:
    # Graceful degradation in minimal test environments
    _needs_rebuild = None  # type: ignore[assignment]
    _find_xctestrun = None  # type: ignore[assignment]
    _DEFAULT_RUNNER_BUILD_DIR = None  # type: ignore[assignment]
    TestSession = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RunnerBuildError(RuntimeError):
    """xcodebuild -scheme / build step failed."""

    def __init__(self, message: str, stderr_tail: str = "") -> None:
        self.stderr_tail = stderr_tail
        super().__init__(message)


class RunnerDeployError(RuntimeError):
    """xcodebuild test-without-building failed — loud, no fallback.

    Attributes:
        udid:          Simulator UDID that was targeted.
        port:          Port the runner was meant to listen on.
        build_dir:     Build directory searched for the .xctestrun.
        stderr_tail:   Last ~1500 chars of xcodebuild stderr.
        suggested_fix: Human-readable next steps.
    """

    def __init__(
        self,
        message: str,
        udid: str = "",
        port: int = 8222,
        build_dir: Optional[Path] = None,
        stderr_tail: str = "",
        suggested_fix: str = "",
    ) -> None:
        self.udid = udid
        self.port = port
        self.build_dir = build_dir
        self.stderr_tail = stderr_tail
        self.suggested_fix = suggested_fix
        super().__init__(message)

    def __str__(self) -> str:
        # First line is the original message (passed to super().__init__)
        msg = self.args[0] if self.args else "xcodebuild test-without-building failed."
        lines = [
            f"RunnerDeployError: {msg}",
            f"  UDID: {self.udid}",
            f"  Port: {self.port}",
            f"  Build dir: {self.build_dir}",
        ]
        if self.stderr_tail:
            lines += ["  xcodebuild stderr:", f"    {self.stderr_tail}"]
        if self.suggested_fix:
            lines += [f"\n  Next steps:\n    {self.suggested_fix}"]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


class RunnerState(Enum):
    IDLE = auto()       # No process. Port unallocated.
    BUILDING = auto()   # xcodebuild -scheme running (runner compile).
    DEPLOYED = auto()   # xcodebuild test-without-building launched; awaiting /health.
    RUNNING = auto()    # /health returned 200. Ready for requests.
    STOPPED = auto()    # Gracefully stopped. Port released.
    FAILED = auto()     # Unrecoverable. Error stored in self.last_error.


# ---------------------------------------------------------------------------
# Module-level registry
# ---------------------------------------------------------------------------

_registry: dict[tuple[str, int], "RunnerProcess"] = {}
_registry_lock = threading.Lock()


# ---------------------------------------------------------------------------
# RunnerProcess
# ---------------------------------------------------------------------------


class RunnerProcess:
    """Single owner of the XCTest runner process lifecycle.

    One instance per (udid, port) pair. All callers share the same instance
    via RunnerProcess.acquire(udid, port).

    Concurrency:
        The instance lock (_lock) serialises deploy + healthcheck so that two
        concurrent callers both get a RUNNING instance without double-launching.
    """

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def acquire(cls, udid: str, port: int = 8222) -> "RunnerProcess":
        """Return existing instance for (udid, port) or create a new IDLE one."""
        key = (udid, port)
        with _registry_lock:
            if key not in _registry:
                instance = cls.__new__(cls)
                instance._udid = udid
                instance._port = port
                instance._state = RunnerState.IDLE
                instance._process: Optional[subprocess.Popen] = None  # type: ignore[assignment]
                instance._last_error: Optional[str] = None
                instance._lock = threading.Lock()
                _registry[key] = instance
                logger.debug("RunnerProcess.acquire: created new instance for (%s, %d)", udid, port)
            return _registry[key]

    @classmethod
    def _clear_registry(cls) -> None:
        """Clear the instance registry. Test helper only."""
        with _registry_lock:
            _registry.clear()

    # ── Public constructor guard ──────────────────────────────────────────────

    def __init__(self) -> None:
        # Direct instantiation is not the intended path — use acquire().
        # This guard is here so tests that accidentally call the constructor get a
        # clear error instead of a silently misconfigured object.
        raise RuntimeError(
            "Use RunnerProcess.acquire(udid, port) — do not instantiate directly."
        )

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def build(self, build_dir: Path, force: bool = False) -> None:
        """Build the Swift runner (hash-gated).

        For Phase 1 this delegates to the existing session_manager build machinery.
        The state transitions IDLE → BUILDING → IDLE (build only, no process).
        Full reimplementation in Phase 2 when wheel restructure lands.

        Raises:
            RunnerBuildError: If xcodebuild fails.
        """
        with self._lock:
            prev = self._state
            self._transition(RunnerState.BUILDING)
            try:
                if not force and not _needs_rebuild(build_dir):
                    logger.debug("RunnerProcess.build: cache hit — skipping rebuild")
                    self._transition(prev)  # Back to whatever state we came from
                    return

                # Delegate to session_manager's rebuild logic
                session_stub = object.__new__(TestSession)
                session_stub._runner_build_dir = build_dir  # type: ignore[attr-defined]
                TestSession._rebuild_runner(session_stub)
                self._transition(prev)
            except Exception as exc:
                self._last_error = str(exc)
                self._transition(RunnerState.FAILED)
                raise RunnerBuildError(str(exc)) from exc

    def deploy(self, bundle_id: str, port: Optional[int] = None) -> None:
        """Inject env into xctestrun, launch xcodebuild test-without-building.

        Idempotent if already RUNNING on the same port.
        Raises RunnerDeployError on xcodebuild failure — LOUD, no fallback.

        State: IDLE/STOPPED → DEPLOYED → RUNNING (via healthcheck()).
        """
        with self._lock:
            if self._state in (RunnerState.RUNNING, RunnerState.DEPLOYED):
                logger.debug(
                    "RunnerProcess.deploy: already %s on :%d — idempotent noop",
                    self._state.name,
                    self._port,
                )
                return

            if self._state == RunnerState.FAILED:
                raise RunnerDeployError(
                    f"RunnerProcess is in FAILED state: {self._last_error}",
                    udid=self._udid,
                    port=self._port,
                )

            effective_port = port if port is not None else self._port

            build_dir = _DEFAULT_RUNNER_BUILD_DIR

            # Auto-rebuild if stale
            if _needs_rebuild(build_dir):
                logger.info("RunnerProcess.deploy: stale build — triggering rebuild")
                prev_state = self._state
                self._transition(RunnerState.BUILDING)
                try:
                    if TestSession is not None:
                        session_stub = object.__new__(TestSession)
                        session_stub._runner_build_dir = build_dir  # type: ignore[attr-defined]
                        TestSession._rebuild_runner(session_stub)
                except Exception as exc:
                    self._last_error = str(exc)
                    self._transition(RunnerState.FAILED)
                    raise RunnerDeployError(
                        f"Runner build failed before deploy: {exc}",
                        udid=self._udid,
                        port=effective_port,
                        build_dir=build_dir,
                    ) from exc
                self._transition(RunnerState.IDLE)

            xctestrun = _find_xctestrun(build_dir)
            if xctestrun is None:
                raise RunnerDeployError(
                    "No .xctestrun found — build the runner first: specterqa-ios runner build",
                    udid=self._udid,
                    port=effective_port,
                    build_dir=build_dir,
                    suggested_fix=(
                        "1. Run: specterqa-ios runner build\n"
                        "    2. Verify the simulator is booted: xcrun simctl list | grep Booted\n"
                        "    3. See docs/troubleshooting.md for known Xcode 16 / iOS 18.4 issues."
                    ),
                )

            # Inject env vars into the xctestrun plist
            TestSession._inject_xctestrun_env(
                Path(xctestrun),
                {
                    "SPECTERQA_PORT": str(effective_port),
                    "SPECTERQA_BUNDLE_ID": bundle_id or "",
                },
            )

            cmd = [
                "xcodebuild",
                "test-without-building",
                "-xctestrun", str(xctestrun),
                "-destination", f"id={self._udid}",
            ]

            self._transition(RunnerState.DEPLOYED)
            logger.info("RunnerProcess.deploy: launching xcodebuild — %s", " ".join(cmd))

            try:
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                self._last_error = str(exc)
                self._transition(RunnerState.FAILED)
                raise RunnerDeployError(
                    f"xcodebuild not found — is Xcode installed? {exc}",
                    udid=self._udid,
                    port=effective_port,
                    build_dir=build_dir,
                    suggested_fix="Install Xcode and run: sudo xcode-select --switch /Applications/Xcode.app",
                ) from exc

    def healthcheck(self, timeout_s: float = 90.0) -> bool:
        """Poll /health until 200 or timeout. Transition DEPLOYED → RUNNING on success.

        Returns True on success. On failure, transitions to FAILED and returns False.
        """
        import urllib.request
        import urllib.error

        url = f"http://localhost:{self._port}/health"
        deadline = time.monotonic() + timeout_s
        poll_interval = 0.5

        while time.monotonic() < deadline:
            # Check if the process exited early
            if self._process is not None and self._process.poll() is not None:
                rc = self._process.returncode
                stderr_out = ""
                if self._process.stderr:
                    try:
                        stderr_out = self._process.stderr.read().decode("utf-8", errors="replace")
                    except Exception:
                        pass
                self._last_error = f"xcodebuild exited with code {rc}"
                self._transition(RunnerState.FAILED)
                raise RunnerDeployError(
                    f"xcodebuild test-without-building exited with code {rc} during startup",
                    udid=self._udid,
                    port=self._port,
                    stderr_tail=stderr_out[-1500:] if stderr_out else "",
                    suggested_fix=(
                        "1. Run: specterqa-ios runner build\n"
                        "    2. Verify the simulator is booted: xcrun simctl list | grep Booted\n"
                        "    3. See docs/troubleshooting.md for known Xcode 16 / iOS 18.4 issues."
                    ),
                )

            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    if resp.status == 200:
                        self._transition(RunnerState.RUNNING)
                        logger.info("RunnerProcess.healthcheck: runner healthy on :%d", self._port)
                        return True
            except (urllib.error.URLError, ConnectionRefusedError, OSError):
                pass  # Not ready yet

            time.sleep(poll_interval)

        self._last_error = f"Runner did not become healthy within {timeout_s}s"
        self._transition(RunnerState.FAILED)
        return False

    def stop(self, shutdown_sim: bool = False) -> None:
        """Terminate xcodebuild process. Release port.

        shutdown_sim=True only when the caller is tearing down the simulator.
        State: RUNNING/DEPLOYED → STOPPED.
        """
        with self._lock:
            if self._process is not None:
                try:
                    if shutdown_sim:
                        # SIGTERM lets xcodebuild clean up (clone mode)
                        self._process.terminate()
                    else:
                        # SIGKILL — don't let xcodebuild tear down the sim
                        self._process.kill()
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                except Exception as exc:
                    logger.warning("RunnerProcess.stop: could not stop process: %s", exc)
                self._process = None

            self._transition(RunnerState.STOPPED)

            # Remove from registry so a future acquire() creates a fresh IDLE instance
            key = (self._udid, self._port)
            with _registry_lock:
                _registry.pop(key, None)

    def relaunch_app(self, bundle_id: str) -> None:
        """Kill + relaunch the user's app without stopping the runner.

        Uses simctl terminate + simctl launch. Runner HTTP server stays up.
        Target: < 2s. Does NOT restart xcodebuild.
        State: RUNNING → RUNNING (no state change).

        Phase 2: full implementation with elapsed_ms tracking and foreground verification.
        """
        if self._state != RunnerState.RUNNING:
            raise RuntimeError(
                f"relaunch_app requires RUNNING state; current state is {self._state.name}"
            )
        # Stub — Phase 2 implementation
        subprocess.run(
            ["xcrun", "simctl", "terminate", self._udid, bundle_id],
            capture_output=True,
        )
        subprocess.run(
            ["xcrun", "simctl", "launch", self._udid, bundle_id],
            capture_output=True,
        )

    def allocate_port(self) -> int:
        """Find a free port in _PORT_RANGE. Raises RuntimeError if all busy."""
        import socket

        _PORT_RANGE = range(8222, 8232)
        for p in _PORT_RANGE:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("localhost", p))
                    return p
                except OSError:
                    continue
        raise RuntimeError(f"No free port found in range {_PORT_RANGE}")

    # ── Introspection ────────────────────────────────────────────────────────

    @property
    def state(self) -> RunnerState:
        return self._state

    @property
    def port(self) -> int:
        return self._port

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def udid(self) -> str:
        return self._udid

    # ── Diagnostics ─────────────────────────────────────────────────────────

    def diagnostics(self) -> dict:
        """Return a diagnostics dict suitable for healthcheck responses."""
        return {
            "state": self._state.name,
            "udid": self._udid,
            "port": self._port,
            "last_error": self._last_error,
            "pid": self._process.pid if self._process else None,
            "process_alive": (
                self._process is not None and self._process.poll() is None
            ),
        }

    # ── Internal ─────────────────────────────────────────────────────────────

    def _transition(self, new_state: RunnerState) -> None:
        old = self._state
        self._state = new_state
        if old != new_state:
            logger.info(
                "RunnerProcess(%s:%d): %s → %s",
                self._udid,
                self._port,
                old.name,
                new_state.name,
            )

    def __repr__(self) -> str:
        return (
            f"RunnerProcess(udid={self._udid!r}, port={self._port}, "
            f"state={self._state.name})"
        )
