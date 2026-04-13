"""TestSession — Non-blocking iOS Simulator clone lifecycle manager.

Clones the user's simulator, boots it headless, deploys the XCTest runner,
and tears it down cleanly when the test is done.  The user's simulator and
cursor are never touched.

INIT-2026-506 — SpecterQA iOS v3 session manager.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger("specterqa.ios.session_manager")

# Port range to try when 8222 is busy.
_PORT_RANGE = range(8222, 8231)

# Health check poll interval and timeout.
# 0.5s interval catches runner readiness ~2x faster than 1s without hammering the port.
_HEALTH_POLL_INTERVAL_S = 0.5
_HEALTH_TIMEOUT_S = 60.0

# Default location where `runner build` places the xctestrun file.
_DEFAULT_RUNNER_BUILD_DIR = Path.home() / ".specterqa" / "runner-build"

# Filename written into the build dir after a successful build.  Contains the
# installed package version so we can detect stale builds on session start.
_VERSION_MARKER_FILENAME = ".specterqa-version"


def _current_package_version() -> str:
    """Return the currently installed specterqa-ios package version.

    Returns:
        Version string (e.g. ``"11.3.0"``), or ``"unknown"`` if it cannot be
        determined.
    """
    try:
        import specterqa

        return specterqa.__version__
    except (ImportError, AttributeError):
        return "unknown"


def _needs_rebuild(build_dir: Path) -> bool:
    """Check whether the cached runner build is stale.

    The build is considered stale when:
    - The version marker file does not exist in *build_dir*, or
    - The marker's content does not match the currently installed package
      version, or
    - The package version cannot be determined (fail-safe → rebuild).

    Args:
        build_dir: The runner derived-data directory (e.g. ``~/.specterqa/runner-build``).

    Returns:
        True if a rebuild is required, False if the cached build is current.
    """
    current = _current_package_version()
    if current == "unknown":
        return True

    marker = build_dir / _VERSION_MARKER_FILENAME
    if not marker.exists():
        return True

    cached = marker.read_text(encoding="utf-8").strip()
    return cached != current


def write_version_marker(build_dir: Path) -> None:
    """Write (or overwrite) the version marker file after a successful build.

    Args:
        build_dir: The runner derived-data directory where the marker is stored.
    """
    version = _current_package_version()
    marker = build_dir / _VERSION_MARKER_FILENAME
    try:
        marker.write_text(version + "\n", encoding="utf-8")
        logger.debug("Wrote version marker %s → %s", marker, version)
    except OSError as exc:
        logger.warning("Could not write version marker %s: %s", marker, exc)


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
        f"All ports {start}–{end - 1} are occupied. Stop other SpecterQA sessions or XCTest runners and retry."
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
        raise SessionError(f"simctl {' '.join(args)} failed (exit {result.returncode}):\n{result.stderr.strip()}")
    return result


def _find_xctestrun(build_dir: Path) -> Optional[Path]:
    """Locate the first .xctestrun file produced by ``runner build``.

    Args:
        build_dir: Root of the runner derived-data directory.

    Returns:
        Path to the .xctestrun file, or None if not found.
    """
    # Search recursively — project-injection puts xctestrun in
    # <bundle_id>/DerivedData/Build/Products/, standalone in Build/Products/
    for match in build_dir.rglob("*.xctestrun"):
        return match
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

    raise SessionError(f"Runner at {url} did not become healthy within {timeout_s:.0f}s. Last error: {last_error}")


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
        clone: bool = False,
    ) -> None:
        self.source_udid = source_udid
        self.app_path = app_path
        self.bundle_id = bundle_id
        self.clone = clone
        self._runner_build_dir = runner_build_dir or _DEFAULT_RUNNER_BUILD_DIR

        self._clone_udid: Optional[str] = None
        self._clone_name: Optional[str] = None
        self._runner_process: Optional[subprocess.Popen] = None
        self._port: int = 8222
        self._target_udid: Optional[str] = None  # the sim we actually deploy to

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
        2. Check source boot state; shutdown if booted (simctl clone requires SHUTDOWN).
        3. Clone the source simulator with a unique name.
        4. Restore the source simulator to its original boot state.
        5. Boot the clone headless (no Simulator.app window).
        6. Install the app bundle if *app_path* was given.
        7. Deploy the XCTest runner via ``xcodebuild test-without-building``.
        8. Poll ``/health`` until the runner responds or timeout.

        Raises:
            SessionError: On any failure.  Cleans up partial state on error.
        """
        try:
            self._start()
        except Exception:  # noqa: BLE001 — all errors from _start() must trigger cleanup before re-raise
            # Best-effort cleanup — don't mask the original error.
            try:
                self._teardown()
            except OSError as cleanup_exc:
                logger.debug("Teardown during start() cleanup failed: %s", cleanup_exc)
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

    @staticmethod
    def _kill_stale_runners() -> None:
        """Kill any orphaned xcodebuild test-without-building processes.

        When MCP connections drop or ios_stop_session isn't called, the old
        xcodebuild process keeps running. Starting a new one on the same sim
        causes resource contention and crashes.
        """
        import signal

        result = subprocess.run(
            ["pgrep", "-f", "xcodebuild.*test-without-building"],
            capture_output=True,
            text=True,
        )
        for pid_str in result.stdout.strip().split("\n"):
            pid_str = pid_str.strip()
            if pid_str:
                try:
                    pid = int(pid_str)
                    os.kill(pid, signal.SIGKILL)
                    logger.info("Killed stale xcodebuild process: %d", pid)
                except (ValueError, ProcessLookupError, PermissionError) as exc:
                    logger.warning("Could not kill stale runner PID %s: %s", pid_str, exc)

    def _cleanup_stale_clones(self) -> None:
        """Delete leftover specterqa-test-* clones from previous runs.

        Stale clones accumulate when sessions are interrupted (e.g. crashes,
        SIGKILL).  Removing them before creating a new one keeps the device
        list tidy and avoids UDID collisions.
        """
        result = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "-j"],
            capture_output=True,
            text=True,
        )
        try:
            devices = json.loads(result.stdout)
        except json.JSONDecodeError:
            return
        for runtime_devices in devices.get("devices", {}).values():
            for device in runtime_devices:
                if device.get("name", "").startswith("specterqa-test-"):
                    udid = device["udid"]
                    try:
                        if device.get("state") == "Booted":
                            _simctl("shutdown", udid, check=False)
                        _simctl("delete", udid, check=False)
                        logger.info("Cleaned up stale clone: %s (%s)", device["name"], udid)
                    except Exception as exc:
                        logger.warning("Could not clean up stale clone %s: %s", udid, exc)

    def _is_sim_booted(self, udid: str) -> bool:
        """Check if a simulator is in Booted state.

        Args:
            udid: The simulator UDID to check.

        Returns:
            True if the simulator's state is ``"Booted"``, False otherwise.
        """
        result = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "-j"],
            capture_output=True,
            text=True,
        )
        try:
            devices = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False
        for runtime_devices in devices.get("devices", {}).values():
            for device in runtime_devices:
                if device.get("udid") == udid:
                    return device.get("state") == "Booted"
        return False

    def _start(self) -> None:
        """Internal start implementation (called by start() with error cleanup).

        Two modes:
        - **Direct mode** (default, ``clone=False``): Deploy runner directly to the
          booted simulator.  Faster (~5s startup).  XCTest runner doesn't steal the
          mouse — user can keep working.  App state is shared with the user's sim.
        - **Clone mode** (``clone=True``): Clone the sim for full isolation.
          Slower (~15s startup) but app state is disposable.  Use for CI or when
          test actions (login, delete) would corrupt the user's data.
        """
        # Step 0 — kill stale xcodebuild processes from previous sessions.
        self._kill_stale_runners()

        # Step 1 — resolve "booted" to a real UDID.
        source = self._resolve_udid(self.source_udid)
        logger.info("Source simulator: %s", source)

        if self.clone:
            self._start_clone_mode(source)
        else:
            self._start_direct_mode(source)

        # Disable hardware keyboard on the target sim — iOS 26+ sims default
        # to hardware keyboard which causes XCUIApplication.typeText() to crash.
        # This enables the software keyboard so typing works reliably.
        target = self._target_udid
        if target:
            try:
                _simctl(
                    "spawn",
                    target,
                    "defaults",
                    "write",
                    "com.apple.Preferences",
                    "HardwareKeyboardAutomaticallyUsed",
                    "-bool",
                    "NO",
                    check=False,
                )
                logger.info("Disabled hardware keyboard on %s", target)
            except Exception as exc:
                logger.warning("Could not disable hardware keyboard: %s", exc)

        # Common — deploy runner and wait for health (both modes).
        self._port = _find_free_port()
        self._deploy_runner()

        health_url = f"{self.runner_url}/health"
        logger.info("Waiting for runner health at %s...", health_url)
        _wait_for_health(health_url, timeout_s=_HEALTH_TIMEOUT_S)
        logger.info("Runner is healthy on port %d", self._port)

    def _start_direct_mode(self, source_udid: str) -> None:
        """Deploy runner directly to the booted simulator — no cloning."""
        logger.info("Direct mode — deploying to %s (no clone)", source_udid)
        self._target_udid = source_udid

        # Ensure the sim is booted.
        if not self._is_sim_booted(source_udid):
            logger.info("Simulator not booted — booting...")
            _simctl("boot", source_udid)
            time.sleep(3)

        # Install app if provided.
        if self.app_path:
            resolved = Path(self.app_path).resolve()
            logger.info("Installing %s on %s...", resolved.name, source_udid)
            _simctl("install", source_udid, str(resolved))
            logger.info("Launching %s...", self.bundle_id)
            _simctl("launch", source_udid, self.bundle_id)
            time.sleep(2)

    def _start_clone_mode(self, source_udid: str) -> None:
        """Clone the sim, boot the clone, deploy runner to clone."""
        # Clean up stale clones from previous interrupted sessions.
        self._cleanup_stale_clones()

        # Shutdown source for cloning (simctl clone requires Shutdown state).
        was_booted = self._is_sim_booted(source_udid)
        if was_booted:
            logger.info("Shutting down source for clone...")
            _simctl("shutdown", source_udid)

        # Clone.
        clone_name = f"specterqa-test-{uuid.uuid4().hex[:8]}"
        self._clone_name = clone_name
        logger.info("Cloning simulator as '%s'...", clone_name)
        result = _simctl("clone", source_udid, clone_name)
        self._clone_udid = result.stdout.strip()
        if not self._clone_udid:
            raise SessionError(f"simctl clone did not return a UDID. stdout={result.stdout!r}")
        self._target_udid = self._clone_udid
        logger.info("Clone UDID: %s", self._clone_udid)

        # Boot source and clone in parallel.
        source_boot_thread: Optional[threading.Thread] = None
        if was_booted:
            source_boot_thread = threading.Thread(
                target=lambda: _simctl("boot", source_udid),
                daemon=True,
            )
            source_boot_thread.start()

        _simctl("boot", self._clone_udid)
        if source_boot_thread is not None:
            source_boot_thread.join()

        time.sleep(3)

        # Install and launch app on clone.
        if self.app_path:
            resolved = Path(self.app_path).resolve()
            logger.info("Installing %s on %s...", resolved.name, self._clone_udid)
            _simctl("install", self._clone_udid, str(resolved))
            logger.info("Launching %s...", self.bundle_id)
            _simctl("launch", self._clone_udid, self.bundle_id)
            time.sleep(2)

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

        raise SessionError("No booted simulator found. Boot one first with: specterqa-ios boot")

    def _rebuild_runner(self) -> None:
        """Rebuild the XCTest runner from the installed package source.

        Resolves the Swift runner source directory relative to the installed
        specterqa-ios package (not a temp directory) and runs ``xcodebuild
        build-for-testing``.  On success, writes the version marker so
        subsequent sessions skip the rebuild.

        Raises:
            SessionError: If the runner source cannot be found or the build fails.
        """
        build_dir = self._runner_build_dir

        # Resolve Swift source: installed package root → runner/
        try:
            import specterqa.ios as _pkg

            # src/specterqa/ios → parent × 3 → repo/package root → runner/
            pkg_ios_dir = Path(_pkg.__file__).parent
            pkg_root = pkg_ios_dir.parent.parent  # specterqa → src root
            runner_dir = pkg_root / "runner"
            if not (runner_dir / "SpecterQARunner.xcodeproj").exists():
                # Installed wheel: package root is site-packages/specterqa_ios/
                # runner/ is a sibling of the top-level package dir.
                runner_dir = pkg_ios_dir.parent / "runner"
        except (ImportError, OSError) as exc:
            raise SessionError(f"Cannot locate runner source directory: {exc}") from exc

        xcodeproj = runner_dir / "SpecterQARunner.xcodeproj"
        if not xcodeproj.exists():
            raise SessionError(
                f"Runner Xcode project not found at {xcodeproj}.\n"
                "The specterqa-ios package may be missing runner source files."
            )

        build_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Auto-rebuilding runner from %s → %s", runner_dir, build_dir)

        result = subprocess.run(
            [
                "xcodebuild",
                "build-for-testing",
                "-project",
                str(xcodeproj),
                "-scheme",
                "SpecterQARunner",
                "-sdk",
                "iphonesimulator",
                "-destination",
                "generic/platform=iOS Simulator",
                "-derivedDataPath",
                str(build_dir),
                "CODE_SIGN_IDENTITY=-",
                "CODE_SIGNING_REQUIRED=NO",
                "CODE_SIGNING_ALLOWED=YES",
                "DEVELOPMENT_TEAM=",
                "SUPPORTED_PLATFORMS=iphonesimulator",
            ],
            cwd=str(runner_dir),
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            raise SessionError(
                f"Auto-rebuild failed (exit {result.returncode}).\n"
                f"Run 'specterqa-ios runner build --verbose' for full output.\n"
                f"stderr tail:\n{result.stderr[-1500:]}"
            )

        logger.info("Auto-rebuild succeeded.")
        # Stamp the version marker so subsequent sessions skip the rebuild.
        write_version_marker(build_dir)

    def _deploy_runner(self) -> None:
        """Launch the XCTest runner via ``xcodebuild test-without-building``.

        Finds the .xctestrun file in the runner build directory and starts
        xcodebuild as a background process.  The port is passed via the
        SPECTERQA_PORT environment variable so the Swift runner can bind to it.

        If the cached build is stale (version marker mismatch) the runner is
        rebuilt automatically before deployment.

        Raises:
            SessionError: If no .xctestrun is found or the process fails to start.
        """
        # ── Cache invalidation ───────────────────────────────────────────────
        # Rebuild automatically when the installed package version has changed
        # since the last build.  This prevents stale runners (compiled from an
        # older source tree) from missing newly-added HTTP endpoints.
        if _needs_rebuild(self._runner_build_dir):
            logger.info(
                "Runner build is stale or missing version marker — triggering rebuild "
                "(installed=%s, marker=%s)",
                _current_package_version(),
                self._runner_build_dir / _VERSION_MARKER_FILENAME,
            )
            self._rebuild_runner()

        xctestrun = _find_xctestrun(self._runner_build_dir)
        if xctestrun is None:
            raise SessionError(
                f"No .xctestrun found in {self._runner_build_dir}.\nBuild the runner first: specterqa-ios runner build"
            )

        # Inject SPECTERQA_BUNDLE_ID and SPECTERQA_PORT into the xctestrun
        # plist. Shell env vars don't propagate through xcodebuild to the
        # test process — they must be in the plist's EnvironmentVariables.
        self._inject_xctestrun_env(
            xctestrun,
            {
                "SPECTERQA_PORT": str(self._port),
                "SPECTERQA_BUNDLE_ID": self.bundle_id or "",
            },
        )

        cmd = [
            "xcodebuild",
            "test-without-building",
            "-xctestrun",
            str(xctestrun),
            "-destination",
            f"id={self._target_udid or self._clone_udid}",
        ]

        logger.info("Deploying runner: %s", " ".join(cmd))
        self._runner_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @staticmethod
    def _inject_xctestrun_env(xctestrun_path: Path, env_vars: dict[str, str]) -> None:
        """Inject environment variables into the .xctestrun plist.

        Shell env vars don't reach the XCTest process — they must be set in
        the plist's EnvironmentVariables dict for each test configuration.

        Uses PlistBuddy for reliable plist manipulation.
        """
        import plistlib

        with open(xctestrun_path, "rb") as f:
            plist = plistlib.load(f)

        # Modern xctestrun plists use TestConfigurations → TestTargets structure.
        # Env vars must go into each TestTarget's EnvironmentVariables dict.
        injected = False
        for tc in plist.get("TestConfigurations", []):
            for tt in tc.get("TestTargets", []):
                if "EnvironmentVariables" not in tt:
                    tt["EnvironmentVariables"] = {}
                tt["EnvironmentVariables"].update(env_vars)
                target_name = tt.get("BlueprintName", "?")
                logger.info("Injected env vars into test target '%s': %s", target_name, list(env_vars.keys()))
                injected = True

        # Fallback for older plist formats with top-level test configs.
        if not injected:
            for key, config in plist.items():
                if key.startswith("__") or not isinstance(config, dict):
                    continue
                if "EnvironmentVariables" not in config:
                    config["EnvironmentVariables"] = {}
                config["EnvironmentVariables"].update(env_vars)
                logger.info("Injected env vars into xctestrun config '%s': %s", key, list(env_vars.keys()))

        with open(xctestrun_path, "wb") as f:
            plistlib.dump(plist, f)

    # ------------------------------------------------------------------
    # Internal — teardown
    # ------------------------------------------------------------------

    def _teardown(self) -> None:
        """Kill the runner process and clean up.

        In direct mode: SIGKILL xcodebuild (not SIGTERM — SIGTERM triggers
        xcodebuild's cleanup which shuts down the sim), then re-boot the sim
        if xcodebuild killed it.

        In clone mode: shutdown and delete the clone.
        """
        target = self._target_udid
        is_direct = self._clone_udid is None

        # Kill xcodebuild — use SIGKILL in direct mode to prevent it from
        # shutting down the user's simulator during its cleanup sequence.
        if self._runner_process is not None:
            try:
                if is_direct:
                    self._runner_process.kill()  # SIGKILL — no cleanup
                else:
                    self._runner_process.terminate()  # SIGTERM — let it clean up clone
                self._runner_process.wait(timeout=5)
            except Exception as exc:
                logger.warning("Could not stop runner process: %s", exc)
            self._runner_process = None

        # Clone mode: shutdown and delete the clone.
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

        # Direct mode: re-boot the sim if xcodebuild's death killed it.
        if is_direct and target:
            time.sleep(1)
            if not self._is_sim_booted(target):
                logger.info("Re-booting simulator %s after runner teardown...", target)
                try:
                    _simctl("boot", target, check=False)
                except Exception as exc:
                    logger.warning("Re-boot failed: %s", exc)

        self._target_udid = None

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"TestSession(source={self.source_udid!r}, clone={self._clone_udid!r}, port={self._port})"
