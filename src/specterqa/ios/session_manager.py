"""TestSession — iOS test session lifecycle manager.

Manages simulator sessions (direct, clone) and physical device sessions.
For simulators: clones the user's simulator, boots it headless, deploys the
XCTest runner, and tears it down cleanly when the test is done.  The user's
simulator and cursor are never touched.
For physical devices: skips all simctl operations, deploys the runner via
xcodebuild, and connects to the device over USB/WiFi.

INIT-2026-506 — SpecterQA iOS v3 session manager.
"""

from __future__ import annotations

import hashlib
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
# installed package version for logging/diagnostic purposes.
_VERSION_MARKER_FILENAME = ".specterqa-version"

# Filename storing a SHA-256 content-hash of runner Sources/ + project.pbxproj.
# _needs_rebuild() gates on this hash rather than the version string.
# Introduced in v13.2.1 to fix B2: patch releases that don't change Swift sources
# no longer trigger an unnecessary rebuild.
_RUNNER_HASH_FILENAME = ".runner-hash"


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


def _compute_runner_source_hash() -> str:
    """Compute a SHA-256 digest over the runner Swift sources + project.pbxproj.

    Uses the *runner/* tree (single source of truth) so the hash reflects what
    will actually be compiled, not the wheel-copy in runner_source/.

    The hash covers:
    - Each ``*.swift`` file in ``runner/Sources/``, sorted by filename
    - ``runner/SpecterQARunner.xcodeproj/project.pbxproj``

    Returns:
        Hex-encoded SHA-256 digest string.
    """
    # Locate runner/Sources/ — v14.0.0+ uses importlib.resources against the
    # top-level ``runner`` package. Falls back to legacy runner_source/ layout
    # and then to dev-tree repo root for backward compat.
    import importlib.resources as _irl

    runner_source_dir: Path | None = None
    runner_pbxproj: Path | None = None

    # 1. v14+ runner top-level package
    try:
        _rp = Path(str(_irl.files("runner")))
        if (_rp / "Sources").exists():
            runner_source_dir = _rp / "Sources"
            runner_pbxproj = _rp / "SpecterQARunner.xcodeproj" / "project.pbxproj"
    except (ModuleNotFoundError, TypeError, AttributeError):
        pass

    if runner_source_dir is None or not runner_source_dir.exists():
        # 2. Legacy wheel layout: specterqa/ios/runner_source/
        this_file = Path(__file__)
        _legacy = this_file.parent / "runner_source" / "Sources"
        if _legacy.exists():
            runner_source_dir = _legacy
            runner_pbxproj = this_file.parent / "runner_source" / "SpecterQARunner.xcodeproj" / "project.pbxproj"

    if runner_source_dir is None or not runner_source_dir.exists():
        # 3. Dev-tree fallback
        this_file = Path(__file__)
        repo_root = this_file.parents[3]
        runner_source_dir = repo_root / "runner" / "Sources"
        runner_pbxproj = repo_root / "runner" / "SpecterQARunner.xcodeproj" / "project.pbxproj"

    if runner_source_dir is None:
        runner_source_dir = Path("/nonexistent")
    if runner_pbxproj is None:
        runner_pbxproj = Path("/nonexistent")

    hasher = hashlib.sha256()
    swift_paths = sorted(runner_source_dir.glob("*.swift"), key=lambda p: p.name)
    for p in swift_paths:
        hasher.update(p.name.encode())
        hasher.update(p.read_bytes())

    if runner_pbxproj.exists():
        hasher.update(runner_pbxproj.read_bytes())

    return hasher.hexdigest()


def _needs_rebuild(build_dir: Path) -> bool:
    """Check whether the cached runner build is stale.

    v13.2.1+ uses a content-hash of ``runner/Sources/*.swift`` + ``project.pbxproj``
    rather than the installed version string.  This ensures that patch releases
    that don't change Swift sources do NOT trigger an unnecessary rebuild (B2 fix).

    The build is considered stale when ANY of the following are true:
    - No ``.runner-hash`` file exists in *build_dir* (fresh install or migration
      from the old version-marker scheme — treated as first run, hash written on
      next successful build)
    - The stored hash does not match the current computed hash
    - No ``.xctestrun`` file exists in *build_dir*

    The ``.specterqa-version`` file is still written after builds for diagnostic
    purposes but is no longer used to gate rebuilds.

    Args:
        build_dir: The runner derived-data directory (e.g. ``~/.specterqa/runner-build``).

    Returns:
        True if a rebuild is required, False if the cached build is current.
    """
    # 1. xctestrun presence check — no binary → always rebuild
    if _find_xctestrun(build_dir) is None:
        logger.debug("_needs_rebuild: no .xctestrun in %s → rebuild", build_dir)
        return True

    # 2. Hash file presence check — missing means fresh install or migration
    hash_marker = build_dir / _RUNNER_HASH_FILENAME
    if not hash_marker.exists():
        logger.debug("_needs_rebuild: no %s → rebuild (first run / migration)", _RUNNER_HASH_FILENAME)
        return True

    # 3. Hash comparison — rebuild only if sources actually changed
    try:
        stored = hash_marker.read_text(encoding="utf-8").strip()
        current = _compute_runner_source_hash()
        if stored != current:
            logger.debug(
                "_needs_rebuild: runner-hash mismatch (stored=%s…, current=%s…) → rebuild",
                stored[:12], current[:12],
            )
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("_needs_rebuild: could not compute runner hash (%s) → rebuild", exc)
        return True

    logger.debug("_needs_rebuild: hash matches → no rebuild needed")
    return False


def write_version_marker(build_dir: Path) -> None:
    """Write (or overwrite) the version marker and runner hash after a successful build.

    Writes two files:
    - ``.specterqa-version`` — human-readable installed version (diagnostic only)
    - ``.runner-hash``       — SHA-256 of Sources/ + pbxproj (gates rebuild check)

    Args:
        build_dir: The runner derived-data directory where the markers are stored.
    """
    version = _current_package_version()
    marker = build_dir / _VERSION_MARKER_FILENAME
    try:
        marker.write_text(version + "\n", encoding="utf-8")
        logger.debug("Wrote version marker %s → %s", marker, version)
    except OSError as exc:
        logger.warning("Could not write version marker %s: %s", marker, exc)

    # Write the content-hash so subsequent sessions skip unnecessary rebuilds.
    hash_marker = build_dir / _RUNNER_HASH_FILENAME
    try:
        current_hash = _compute_runner_source_hash()
        hash_marker.write_text(current_hash + "\n", encoding="utf-8")
        logger.debug("Wrote runner hash %s → %s…", hash_marker, current_hash[:12])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not write runner hash %s: %s", hash_marker, exc)


class SessionError(Exception):
    """Raised when a simulator session operation fails."""


def _discover_physical_devices() -> list[dict]:
    """List connected physical iOS devices via devicectl.

    Returns:
        List of dicts with keys: udid, name, model, os_version, identifier.
        Empty list if devicectl is unavailable or no devices are connected.
    """
    try:
        import tempfile
        json_out = Path(tempfile.mktemp(suffix=".json"))
        result = subprocess.run(
            ["xcrun", "devicectl", "list", "devices", "--json-output", str(json_out)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 or not json_out.exists():
            return []
        data = json.loads(json_out.read_text())
        json_out.unlink(missing_ok=True)
        devices = []
        for d in data.get("result", {}).get("devices", []):
            transport = d.get("connectionProperties", {}).get("transportType", "")
            if transport in ("wired", "localNetwork"):
                devices.append(
                    {
                        "udid": d.get("hardwareProperties", {}).get("udid", ""),
                        "name": d.get("deviceProperties", {}).get("name", ""),
                        "model": d.get("hardwareProperties", {}).get("marketingName", ""),
                        "os_version": d.get("deviceProperties", {}).get("osVersionNumber", ""),
                        "identifier": d.get("identifier", ""),  # CoreDevice identifier
                    }
                )
        return devices
    except Exception:  # noqa: BLE001 — best-effort device discovery
        return []


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
        device_type: str = "simulator",  # "simulator" or "physical"
    ) -> None:
        self.source_udid = source_udid
        self.app_path = app_path
        self.bundle_id = bundle_id
        self.clone = clone
        self.device_type = device_type
        self._runner_build_dir = runner_build_dir or _DEFAULT_RUNNER_BUILD_DIR

        self._clone_udid: Optional[str] = None
        self._clone_name: Optional[str] = None
        self._runner_process: Optional[subprocess.Popen] = None
        self._runner = None  # RunnerProcess instance (v14 lifecycle owner)
        self._port: int = 8222
        self._target_udid: Optional[str] = None  # the sim or device we actually deploy to
        self._device_host: Optional[str] = None  # IP/hostname for physical device runner

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def runner_url(self) -> str:
        """HTTP base URL to the running XCTest runner.

        For simulators: always ``http://localhost:<port>``.
        For physical devices: ``http://<device-ip>:<port>``.
        """
        host = self._device_host if self._device_host else "localhost"
        return f"http://{host}:{self._port}"

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

        Processes that are already owned by a RunnerProcess registry entry are
        NOT killed — they were deployed intentionally (e.g. by the MCP layer's
        pre-deploy block) and are still healthy.
        """
        import signal

        try:
            from specterqa.ios.runner_process import RunnerProcess  # noqa: PLC0415
            _owned = RunnerProcess.owned_pids()
        except Exception:
            _owned = set()

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
                    if pid in _owned:
                        logger.debug(
                            "Skipping xcodebuild PID %d — owned by RunnerProcess registry", pid
                        )
                        continue
                    os.kill(pid, signal.SIGKILL)
                    logger.info("Killed stale xcodebuild process: %d", pid)
                except ValueError as exc:
                    logger.warning("Could not parse stale runner PID %r: %s", pid_str, exc)
                except ProcessLookupError:
                    pass  # PID already exited — expected, not an error
                except PermissionError as exc:
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

        Three modes:
        - **Direct mode** (default, ``clone=False``, ``device_type="simulator"``): Deploy
          runner directly to the booted simulator.  Faster (~5s startup).  XCTest
          runner doesn't steal the mouse — user can keep working.  App state is shared
          with the user's sim.
        - **Clone mode** (``clone=True``, ``device_type="simulator"``): Clone the sim
          for full isolation.  Slower (~15s startup) but app state is disposable.  Use
          for CI or when test actions (login, delete) would corrupt the user's data.
        - **Physical device mode** (``device_type="physical"``): Skip all simctl
          operations.  The app must already be installed on the device.  Runner is
          deployed via xcodebuild (iphoneos SDK + code signing).  The runner HTTP
          server is reached via the device's IP address.
        """
        # Step 0 — kill stale xcodebuild processes from previous sessions.
        self._kill_stale_runners()

        if self.device_type == "physical":
            self._start_physical_mode()
            return

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

    def _start_physical_mode(self) -> None:
        """Deploy runner to a USB- or WiFi-connected physical iOS device.

        Physical device rules:
        - No simctl operations (no clone, no boot, no install via simctl).
        - App must already be installed on the device.
        - Runner is built with the ``iphoneos`` SDK and requires code signing.
        - Runner HTTP server on the device is reached via the device's IP address.

        Raises:
            SessionError: If no device is connected, the runner cannot be
                deployed, or the runner does not become healthy in time.
        """
        # Resolve UDID — auto-detect if not provided.
        if self.source_udid and self.source_udid != "booted":
            self._target_udid = self.source_udid
        else:
            devices = _discover_physical_devices()
            if not devices:
                raise SessionError(
                    "No physical iOS device connected. "
                    "Connect via USB or enable WiFi pairing in Xcode → Devices."
                )
            self._target_udid = devices[0]["udid"]
            logger.info(
                "Auto-detected physical device: %s (%s)",
                devices[0]["name"],
                self._target_udid,
            )

        logger.info("Physical device mode — target UDID: %s", self._target_udid)

        # Find a free port on the Mac side for iproxy forwarding.
        self._port = _find_free_port()
        # The runner on the device always binds to 8222.
        device_port = 8222

        # Deploy runner — _deploy_runner reads self.device_type to pick iphoneos SDK.
        self._deploy_runner()

        # Start iproxy: forward localhost:PORT → device:8222 via USB.
        # This is the ONLY reliable way to reach the runner on a physical device —
        # direct IP (tunnelIPAddress) is IPv6-only and often not routable.
        try:
            self._iproxy_process = subprocess.Popen(
                ["iproxy", f"{self._port}:{device_port}", "-u", self._target_udid],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("iproxy started: localhost:%d → device:%d (UDID=%s)", self._port, device_port, self._target_udid)
        except FileNotFoundError:
            raise SessionError(
                "iproxy not found. Install with: brew install libimobiledevice\n"
                "iproxy forwards the device's runner port to localhost via USB."
            )

        # Physical device: runner is on localhost via iproxy, same as simulator.
        self._device_host = "localhost"

        health_url = f"{self.runner_url}/health"
        logger.info("Waiting for physical device runner health at %s...", health_url)
        # Physical devices take longer: app launch + runner init + iproxy setup.
        _wait_for_health(health_url, timeout_s=120.0)
        logger.info("Runner is healthy on physical device, port %d", self._port)

    def _get_device_ip(self) -> str:
        """Resolve the physical device's reachable hostname or IP address.

        Queries devicectl for the device's connection hostname, then resolves
        it to an IP via ``socket.gethostbyname``.  Falls back to the Bonjour
        hostname ``<udid>.local`` if resolution fails.

        Returns:
            IP address string (e.g. ``"192.168.1.42"``), or the ``.local``
            hostname as a last resort.
        """
        import socket

        try:
            import tempfile as _tf
            _json_out = Path(_tf.mktemp(suffix=".json"))
            result = subprocess.run(
                ["xcrun", "devicectl", "list", "devices", "--json-output", str(_json_out)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and _json_out.exists():
                data = json.loads(_json_out.read_text())
                _json_out.unlink(missing_ok=True)
                for d in data.get("result", {}).get("devices", []):
                    udid = d.get("hardwareProperties", {}).get("udid", "")
                    if udid == self._target_udid:
                        # Prefer tunnelIPAddress (direct), fall back to hostname
                        hostname = (
                            d.get("connectionProperties", {}).get("tunnelIPAddress", "")
                            or d.get("connectionProperties", {}).get("hostname", "")
                        )
                        if hostname:
                            try:
                                ip = socket.gethostbyname(hostname)
                                logger.debug("Resolved %s → %s", hostname, ip)
                                return ip
                            except OSError as exc:
                                logger.warning(
                                    "Could not resolve device hostname %r: %s — trying .local fallback",
                                    hostname,
                                    exc,
                                )
        except Exception as exc:  # noqa: BLE001 — best-effort resolution
            logger.warning("Could not query devicectl for device IP: %s", exc)

        # Bonjour fallback — works on USB-connected devices that broadcast mDNS.
        fallback = f"{self._target_udid}.local"
        logger.warning("Device IP resolution failed — falling back to %s", fallback)
        return fallback

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

        # Resolve Swift source (v14.0.0+ wheel restructure).
        # Search order:
        #   1. runner top-level package via importlib.resources (installed wheel + editable)
        #   2. Legacy specterqa.ios.runner_source sub-package (pre-v14 wheels)
        #   3. Dev-tree fallback: repo root / runner/
        import importlib.resources as _irl

        runner_dir: Path | None = None

        # 1. v14+ layout: runner/ is a top-level Python package
        try:
            _runner_pkg = _irl.files("runner")
            _candidate = Path(str(_runner_pkg))
            if (_candidate / "SpecterQARunner.xcodeproj").exists():
                runner_dir = _candidate
        except (ModuleNotFoundError, TypeError, AttributeError):
            pass

        if runner_dir is None:
            # 2. Legacy wheel layout
            try:
                from specterqa.ios.runner_source import RUNNER_SOURCE_DIR  # noqa: PLC0415

                runner_dir = RUNNER_SOURCE_DIR
            except ImportError:
                pass

        if runner_dir is None:
            # 3. Dev-tree fallback
            try:
                import specterqa.ios as _pkg  # noqa: PLC0415

                pkg_ios_dir = Path(_pkg.__file__).parent
                # session_manager.py lives at src/specterqa/ios/session_manager.py
                # go up: ios → specterqa → src → repo_root
                pkg_root = pkg_ios_dir.parents[2]
                _dev = pkg_root / "runner"
                if (_dev / "SpecterQARunner.xcodeproj").exists():
                    runner_dir = _dev
            except (ImportError, OSError) as exc:
                raise SessionError(f"Cannot locate runner source directory: {exc}") from exc

        if runner_dir is None:
            raise SessionError(
                "Cannot locate runner source directory. "
                "Ensure specterqa-ios is installed from PyPI or run from the repo root."
            )

        xcodeproj = runner_dir / "SpecterQARunner.xcodeproj"
        if not xcodeproj.exists():
            raise SessionError(
                f"Runner Xcode project not found at {xcodeproj}.\n"
                "The specterqa-ios package may be missing runner source files."
            )

        build_dir.mkdir(parents=True, exist_ok=True)

        # Physical devices require the iphoneos SDK and real code signing.
        # Simulators use iphonesimulator SDK with identity=- (no signing).
        is_physical = self.device_type == "physical"
        sdk = "iphoneos" if is_physical else "iphonesimulator"
        destination = "generic/platform=iOS" if is_physical else "generic/platform=iOS Simulator"

        logger.info("Auto-rebuilding runner from %s → %s (sdk=%s)", runner_dir, build_dir, sdk)

        cmd = [
            "xcodebuild",
            "build-for-testing",
            "-project",
            str(xcodeproj),
            "-scheme",
            "SpecterQARunner",
            "-sdk",
            sdk,
            "-destination",
            destination,
            "-derivedDataPath",
            str(build_dir),
        ]

        if not is_physical:
            # Simulator builds don't need real signing.
            cmd += [
                "CODE_SIGN_IDENTITY=-",
                "CODE_SIGNING_REQUIRED=NO",
                "CODE_SIGNING_ALLOWED=YES",
                "DEVELOPMENT_TEAM=",
                "SUPPORTED_PLATFORMS=iphonesimulator",
            ]
        # Physical device builds rely on Xcode's automatic signing configured in the project.

        result = subprocess.run(
            cmd,
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
        """Launch the XCTest runner via RunnerProcess (v14 unified lifecycle owner).

        Delegates all xcodebuild management to RunnerProcess.acquire().
        The port has already been set on self._port by the caller.

        Raises:
            SessionError: Wraps RunnerDeployError for callers that expect SessionError.
        """
        from specterqa.ios.runner_process import RunnerProcess, RunnerDeployError  # noqa: PLC0415

        target_udid = self._target_udid or self._clone_udid or ""
        runner = RunnerProcess.acquire(target_udid, self._port)

        try:
            runner.deploy(self.bundle_id or "")
        except RunnerDeployError as exc:
            raise SessionError(str(exc)) from exc

        # Keep a reference so _teardown() can call runner.stop()
        self._runner = runner
        # Maintain backward compat: self._runner_process still holds the Popen
        # so anything that reads it directly still works in this phase.
        self._runner_process = runner._process

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

        Physical device mode: SIGKILL xcodebuild only — no simctl operations.
        The device state (app running, etc.) is left untouched.

        Simulator direct mode: SIGKILL xcodebuild (not SIGTERM — SIGTERM
        triggers xcodebuild's cleanup which shuts down the sim), then re-boot
        the sim if xcodebuild killed it.

        Simulator clone mode: shutdown and delete the clone.
        """
        target = self._target_udid
        is_physical = self.device_type == "physical"
        is_direct = self._clone_udid is None

        # Kill iproxy if running (physical device port forwarding).
        iproxy = getattr(self, "_iproxy_process", None)
        if iproxy is not None:
            try:
                iproxy.kill()
                iproxy.wait(timeout=3)
            except Exception:
                pass
            self._iproxy_process = None

        # Kill xcodebuild via RunnerProcess if available (v14 path).
        # Fall back to direct process kill for sessions created without RunnerProcess.
        runner = getattr(self, "_runner", None)
        if runner is not None:
            try:
                # Clone: shutdown_sim=True → SIGTERM lets xcodebuild clean up the clone.
                # Physical/direct: shutdown_sim=False → SIGKILL, don't tear down the sim.
                runner.stop(shutdown_sim=(not is_physical and not is_direct))
            except Exception as exc:
                logger.warning("Could not stop RunnerProcess: %s", exc)
            self._runner = None
            self._runner_process = None
        elif self._runner_process is not None:
            # Legacy path — direct Popen management (no RunnerProcess).
            try:
                if is_physical or is_direct:
                    self._runner_process.kill()  # SIGKILL — no cleanup
                else:
                    self._runner_process.terminate()  # SIGTERM — let it clean up clone
                self._runner_process.wait(timeout=5)
            except Exception as exc:
                logger.warning("Could not stop runner process: %s", exc)
            self._runner_process = None

        # Physical device: no further cleanup needed — device is untouched.
        if is_physical:
            self._target_udid = None
            self._device_host = None
            return

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

        # Direct mode: keep the sim in its pre-session Booted state.
        # SIGKILL on xcodebuild triggers CoreSimulator cleanup which shuts the
        # sim down even on SIGKILL. Wait briefly for the shutdown to settle,
        # then boot back up so the sim is ready for the next session without
        # the cold-boot penalty that would otherwise occur on ios_start_session.
        if is_direct and target:
            time.sleep(2)  # Let CoreSimulator finish shutdown sequence
            try:
                _simctl("boot", target, check=False)
                logger.info("Rebooted simulator %s after runner teardown (keeping sim Booted)", target)
            except Exception as exc:
                logger.debug("simctl boot after teardown: %s", exc)

        self._target_udid = None

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"TestSession(source={self.source_udid!r}, clone={self._clone_udid!r}, port={self._port})"
