"""TestSession — Non-blocking iOS Simulator clone lifecycle manager.

Clones the user's simulator, boots it headless, deploys the XCTest runner,
and tears it down cleanly when the test is done.  The user's simulator and
cursor are never touched.

INIT-2026-506 — SpecterQA iOS v3 session manager.
"""

from __future__ import annotations

import glob
import logging
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger("specterqa.ios.session_manager")

# Port range to try when 8222 is busy.
_PORT_RANGE = range(8222, 8231)

# Health check poll interval and timeout.
_HEALTH_POLL_INTERVAL_S = 1.0
_HEALTH_TIMEOUT_S = 30.0

# Default location where `runner build` places the xctestrun file.
_DEFAULT_RUNNER_BUILD_DIR = Path.home() / ".specterqa" / "runner-build"


class SessionError(Exception):
    """Raised when a simulator session operation fails."""


def _find_free_port(start: int = 8222, end: int = 8231) -> int:
    """Return the first TCP port in [start, end) that is not in use.

    Args:
        start: First port to try.
        end: Upper bound (exclusive).

    Returns:
        An unused port number.

    Raises:
        SessionError: If all ports in the range are occupied.
    """
    import socket

    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("localhost", port))
                return port
            except OSError:
                continue
    raise SessionError(
        f"All ports {start}–{end - 1} are occupied. "
        "Stop other SpecterQA sessions or XCTest runners and retry."
    )


def _simctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run ``xcrun simctl <args>`` and return the CompletedProcess.

    Args:
        *args: Arguments forwarded to simctl (e.g. ``"boot"``, ``"<udid>"``).
        check: Raise SessionError on non-zero return code when True.

    Returns:
        Completed subprocess.

    Raises:
        SessionError: When *check* is True and the command fails.
    """
    cmd = ["xcrun", "simctl", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SessionError(
            f"simctl {' '.join(args)} failed (exit {result.returncode}):\n"
            f"{result.stderr.strip()}"
        )
    return result


def _find_xctestrun(build_dir: Path) -> Optional[Path]:
    """Locate the first .xctestrun file produced by ``runner build``.

    Args:
        build_dir: Root of the runner derived-data directory.

    Returns:
        Path to the .xctestrun file, or None if not found.
    """
    pattern = str(build_dir / "Build" / "Products" / "*.xctestrun")
    matches = glob.glob(pattern)
    if matches:
        return Path(matches[0])
    return None


def _wait_for_health(url: str, timeout_s: float = _HEALTH_TIMEOUT_S) -> None:
    """Poll GET *url* until ``{"status": "ok"}`` is returned or timeout.

    Args:
        url: Health check URL (e.g. ``http://localhost:8222/health``).
        timeout_s: Maximum seconds to wait before raising SessionError.

    Raises:
        SessionError: If the runner does not become healthy within *timeout_s*.
    """
    import urllib.request
    import json as _json

    deadline = time.monotonic() + timeout_s
    last_error: Optional[Exception] = None

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                data = _json.loads(resp.read())
                if data.get("status") == "ok":
                    logger.debug("Runner healthy at %s", url)
                    return
        except Exception as exc:
            last_error = exc

        time.sleep(_HEALTH_POLL_INTERVAL_S)

    raise SessionError(
        f"Runner at {url} did not become healthy within {timeout_s:.0f}s. "
        f"Last error: {last_error}"
    )


class TestSession:
    """Manages an isolated test simulator clone for non-blocking execution.

    Clones the source simulator, boots the clone headless, optionally installs
    an app, deploys the XCTest runner, and tears everything down on stop().

    Usage::

        session = TestSession(app_path="./build/MyApp.app", bundle_id="com.example.app")
        session.start()
        # ... run tests against session.runner_url ...
        session.stop()

    Or as a context manager::

        with TestSession(app_path="./MyApp.app") as session:
            requests.post(f"{session.runner_url}/tap", json={"x": 195, "y": 304})

    Args:
        source_udid: UDID of the simulator to clone (default: ``"booted"``).
        app_path: Path to a .app bundle to install on the clone (optional).
        bundle_id: Bundle ID of the app under test (optional; used for logging).
        runner_build_dir: Directory containing the compiled .xctestrun.
            Defaults to ``~/.specterqa/runner-build/``.
    """

    def __init__(
        self,
        source_udid: str = "booted",
        app_path: Optional[str] = None,
        bundle_id: Optional[str] = None,
        runner_build_dir: Optional[Path] = None,
    ) -> None:
        self.source_udid = source_udid
        self.app_path = app_path
        self.bundle_id = bundle_id
        self._runner_build_dir = runner_build_dir or _DEFAULT_RUNNER_BUILD_DIR

        self._clone_udid: Optional[str] = None
        self._clone_name: Optional[str] = None
        self._runner_process: Optional[subprocess.Popen] = None
        self._port: int = 8222

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def runner_url(self) -> str:
        """HTTP base URL to the running XCTest runner."""
        return f"http://localhost:{self._port}"

    @property
    def clone_udid(self) -> Optional[str]:
        """UDID of the cloned simulator, or None before start() is called."""
        return self._clone_udid

    def start(self) -> None:
        """Clone sim, boot headless, install app, deploy runner, wait for health.

        Steps:
        1. Resolve the source UDID when ``"booted"`` is requested.
        2. Clone the source simulator with a unique name.
        3. Boot the clone headless (no Simulator.app window).
        4. Install the app bundle if *app_path* was given.
        5. Deploy the XCTest runner via ``xcodebuild test-without-building``.
        6. Poll ``/health`` until the runner responds or timeout.

        Raises:
            SessionError: On any failure.  Cleans up partial state on error.
        """
        try:
            self._start()
        except Exception:
            # Best-effort cleanup — don't mask the original error.
            try:
                self._teardown()
            except Exception:
                pass
            raise

    def stop(self) -> None:
        """Kill the runner process, shutdown, and delete the cloned simulator."""
        self._teardown()

    def __enter__(self) -> "TestSession":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Internal — start sequence
    # ------------------------------------------------------------------

    def _start(self) -> None:
        """Internal start implementation (called by start() with error cleanup)."""
        # Step 1 — resolve "booted" to a real UDID.
        source = self._resolve_udid(self.source_udid)
        logger.info("Source simulator: %s", source)

        # Step 2 — clone.
        clone_name = f"specterqa-test-{uuid.uuid4().hex[:8]}"
        self._clone_name = clone_name
        logger.info("Cloning simulator as '%s'...", clone_name)
        result = _simctl("clone", source, clone_name)
        # simctl clone prints the new UDID to stdout.
        self._clone_udid = result.stdout.strip()
        if not self._clone_udid:
            raise SessionError(
                f"simctl clone did not return a UDID. stdout={result.stdout!r}"
            )
        logger.info("Clone UDID: %s", self._clone_udid)

        # Step 3 — boot headless.
        logger.info("Booting clone headless...")
        _simctl("boot", self._clone_udid)
        # Give CoreSimulator a moment to finish booting.
        time.sleep(2)

        # Step 4 — install app (optional).
        if self.app_path:
            resolved = Path(self.app_path).resolve()
            logger.info("Installing %s on %s...", resolved.name, self._clone_udid)
            _simctl("install", self._clone_udid, str(resolved))

        # Step 5 — deploy XCTest runner.
        self._port = _find_free_port()
        self._deploy_runner()

        # Step 6 — wait for health.
        health_url = f"{self.runner_url}/health"
        logger.info("Waiting for runner health at %s...", health_url)
        _wait_for_health(health_url, timeout_s=_HEALTH_TIMEOUT_S)
        logger.info("Runner is healthy on port %d", self._port)

    def _resolve_udid(self, udid: str) -> str:
        """Resolve ``"booted"`` to the actual UDID of the booted simulator.

        Args:
            udid: A literal UDID or the string ``"booted"``.

        Returns:
            Concrete UDID string.

        Raises:
            SessionError: If ``"booted"`` is requested but no simulator is booted.
        """
        if udid != "booted":
            return udid

        import json as _json

        result = _simctl("list", "devices", "--json")
        data = _json.loads(result.stdout)
        for _rt, devices in data.get("devices", {}).items():
            for dev in devices:
                if dev.get("state") == "Booted":
                    return dev["udid"]

        raise SessionError(
            "No booted simulator found. "
            "Boot one first with: specterqa-ios boot"
        )

    def _deploy_runner(self) -> None:
        """Launch the XCTest runner via ``xcodebuild test-without-building``.

        Finds the .xctestrun file in the runner build directory and starts
        xcodebuild as a background process.  The port is passed via the
        SPECTERQA_PORT environment variable so the Swift runner can bind to it.

        Raises:
            SessionError: If no .xctestrun is found or the process fails to start.
        """
        xctestrun = _find_xctestrun(self._runner_build_dir)
        if xctestrun is None:
            raise SessionError(
                f"No .xctestrun found in {self._runner_build_dir}.\n"
                "Build the runner first: specterqa-ios runner build"
            )

        cmd = [
            "xcodebuild", "test-without-building",
            "-xctestrun", str(xctestrun),
            "-destination", f"id={self._clone_udid}",
        ]

        env = os.environ.copy()
        env["SPECTERQA_PORT"] = str(self._port)

        logger.info("Deploying runner: %s", " ".join(cmd))
        self._runner_process = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # ------------------------------------------------------------------
    # Internal — teardown
    # ------------------------------------------------------------------

    def _teardown(self) -> None:
        """Kill the runner process and remove the cloned simulator."""
        # Kill the xcodebuild process.
        if self._runner_process is not None:
            try:
                self._runner_process.terminate()
                self._runner_process.wait(timeout=5)
            except Exception as exc:
                logger.warning("Could not terminate runner process: %s", exc)
            self._runner_process = None

        # Shutdown and delete the clone.
        if self._clone_udid is not None:
            try:
                _simctl("shutdown", self._clone_udid, check=False)
            except Exception as exc:
                logger.warning("simctl shutdown failed: %s", exc)
            try:
                _simctl("delete", self._clone_udid, check=False)
                logger.info("Deleted clone %s", self._clone_udid)
            except Exception as exc:
                logger.warning("simctl delete failed: %s", exc)
            self._clone_udid = None
            self._clone_name = None

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"TestSession(source={self.source_udid!r}, "
            f"clone={self._clone_udid!r}, "
            f"port={self._port})"
        )
