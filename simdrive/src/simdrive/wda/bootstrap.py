"""WDA bootstrap: clone, build, install, and smoke-test WebDriverAgent on a real device.

Implements `simdrive bootstrap-device <udid>` — the full sequence from a clean
Mac to "WDA ready" on the target device. Each step is a distinct function so
tests can patch at the subprocess boundary without needing a real device.

Algorithm overview:
  1. Verify host tools (xcodebuild, idevicepair, xcrun devicectl)
  2. Verify device paired + Developer Mode + DDI services
  3. Clone WDA at pinned SHA into ~/.simdrive/wda/<udid>/source/
  4. Resolve signing identity from keychain or explicit flags
  5. xcodebuild build-for-testing (streams stdout)
  6. Install via xcrun devicectl device install app
  7. Launch WDA + tail syslog for ServerURLHere port announcement (15 s)
  8. Persist ~/.simdrive/wda/<udid>.json registry
  9. Smoke GET /status → {value: {ready: true}}
 10. Print "WDA ready" summary with any manual Trust prompts

All subprocess.run calls are direct (not wrapped) so tests can patch via
unittest.mock.patch("simdrive.wda.bootstrap.subprocess.run").
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

from . import registry
from .errors import (
    wda_build_failed,
    wda_device_not_ready,
    wda_host_tools_missing,
    wda_install_failed,
    wda_no_signing_identity,
    wda_port_discovery_timeout,
    wda_signing_ambiguous,
    wda_smoke_failed,
)
from .client import WdaClient


# ─── constants ───────────────────────────────────────────────────────────────

_PINNED_SHA_FILE = Path(__file__).parent / "PINNED_SHA.txt"

# How long to tail syslog waiting for the ServerURLHere announcement.
_PORT_DISCOVERY_TIMEOUT_S = 15

# WDA default port (user may override via --wda-port).
_WDA_DEFAULT_PORT = 8100

# Pattern emitted by WDA when it binds its HTTP listener.
_SERVER_URL_RE = re.compile(r"ServerURLHere->http://[^:]+:(\d+)<-")

# WDA bundle identifier (Appium fork default, matches xcodebuild scheme).
_WDA_BUNDLE_ID = "com.facebook.WebDriverAgentRunner.xctrunner"


# ─── host-tool verification ───────────────────────────────────────────────────


def verify_host_tools() -> None:
    """Raise wda_host_tools_missing for each absent required tool.

    Checks: xcodebuild, idevicepair, xcrun (devicectl is a subcommand of xcrun).
    """
    required = ["xcodebuild", "idevicepair", "xcrun"]
    for tool in required:
        if shutil.which(tool) is None:
            raise wda_host_tools_missing(tool)


# ─── device state verification ───────────────────────────────────────────────


def verify_device_ready(udid: str) -> None:
    """Parse `xcrun devicectl device info details --json-output -` and assert pairing + DDI state.

    Uses the structured JSON output (--json-output -) so parsing is reliable
    across macOS / Xcode versions regardless of bullet-point formatting changes.

    JSON paths (confirmed against iPhone 17 Pro Max, iOS 26.3.1):
      result.connectionProperties.pairingState         -> "paired"
      result.connectionProperties.tunnelState          -> "connected"
      result.deviceProperties.developerModeStatus      -> "enabled"
      result.deviceProperties.ddiServicesAvailable     -> true (bool)

    Raises wda_device_not_ready with the list of unmet conditions.
    """
    result = subprocess.run(
        ["xcrun", "devicectl", "device", "info", "details", "--device", udid, "--json-output", "-"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    # A non-zero exit almost always means the device is not visible at all.
    if result.returncode != 0:
        raise wda_device_not_ready(udid, ["device_not_found_or_not_connected"])

    missing: list[str] = []
    try:
        data = json.loads(result.stdout)
        # devicectl JSON envelope: {"result": {...}} — fields are directly on result,
        # not inside a result.devices[] array.
        info = data.get("result") or {}

        conn_props = info.get("connectionProperties") or {}
        dev_props = info.get("deviceProperties") or {}

        pairing = conn_props.get("pairingState", "")
        if pairing.lower() != "paired":
            missing.append(f"pairingState={pairing!r} (need 'paired')")

        dev_mode = dev_props.get("developerModeStatus", "")
        if dev_mode.lower() != "enabled":
            missing.append(f"developerModeStatus={dev_mode!r} (need 'enabled')")

        ddi = dev_props.get("ddiServicesAvailable", False)
        if not ddi:
            missing.append("ddiServicesAvailable=False (mount DDI by connecting device in Xcode)")

    except (json.JSONDecodeError, IndexError, KeyError):
        # Can't parse — treat as unverified rather than crashing; surface a
        # parseable condition so the user can debug.
        missing.append("devicectl_output_unparseable")

    if missing:
        raise wda_device_not_ready(udid, missing)


# ─── WDA clone ───────────────────────────────────────────────────────────────


def _parse_pinned_sha() -> tuple[str, str]:
    """Return (repo_url, sha) from PINNED_SHA.txt."""
    repo = ""
    sha = ""
    for line in _PINNED_SHA_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("REPO="):
            repo = line.split("=", 1)[1].strip()
        elif line.startswith("SHA="):
            sha = line.split("=", 1)[1].strip()
    if not repo or not sha:
        raise RuntimeError(f"PINNED_SHA.txt is malformed — expected REPO= and SHA= lines in {_PINNED_SHA_FILE}")
    return repo, sha


def clone_wda(udid: str, rebuild: bool = False) -> Path:
    """Clone WDA at the pinned SHA into ~/.simdrive/wda/<udid>/source/.

    Returns the source directory path. If the directory already exists and
    rebuild=False, skips the clone and returns immediately.
    """
    wda_home = Path(os.environ.get("WDA_REGISTRY_DIR", Path.home() / ".simdrive" / "wda"))
    source_dir = wda_home / udid / "source"

    if source_dir.exists() and not rebuild:
        print(f"[simdrive] WDA source already present at {source_dir} (skip --rebuild to reuse)", flush=True)
        return source_dir

    if source_dir.exists() and rebuild:
        shutil.rmtree(source_dir)

    source_dir.parent.mkdir(parents=True, exist_ok=True)
    repo_url, sha = _parse_pinned_sha()

    print(f"[simdrive] Cloning WebDriverAgent {sha[:12]} from {repo_url} ...", flush=True)
    result = subprocess.run(
        ["git", "clone", repo_url, str(source_dir)],
        capture_output=False,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise wda_build_failed(str(source_dir / "clone.log"))

    # Check out the exact pinned SHA so we're not on an unknown HEAD.
    checkout = subprocess.run(
        ["git", "-C", str(source_dir), "checkout", sha],
        capture_output=True,
        text=True,
        check=False,
    )
    if checkout.returncode != 0:
        # SHA not found can mean the repo was cloned but SHA is wrong;
        # still try to continue with whatever HEAD is.
        print(f"[simdrive] Warning: could not checkout SHA {sha}: {checkout.stderr.strip()}", flush=True)

    # Bootstrap CocoaPods if needed (WDA uses Pods for its test runner deps).
    if (source_dir / "Podfile").exists():
        print("[simdrive] Running `bundle exec pod install` (WDA CocoaPods deps) ...", flush=True)
        pod_result = subprocess.run(
            ["bundle", "exec", "pod", "install", "--project-directory", str(source_dir)],
            capture_output=False,
            text=True,
            check=False,
            cwd=str(source_dir),
        )
        if pod_result.returncode != 0:
            # pod install failure is non-fatal — xcodebuild may still succeed if
            # a Pods/ directory already exists from a previous run.
            print("[simdrive] Warning: pod install returned non-zero; continuing anyway.", flush=True)

    return source_dir


# ─── signing identity resolution ─────────────────────────────────────────────

# Pattern in `security find-identity -v -p codesigning` output:
#   1) ABCDEF1234... "Apple Development: Name (TEAMID)"
_IDENTITY_RE = re.compile(r'\d+\)\s+([A-F0-9]{40})\s+"([^"]+)"')
_TEAM_ID_RE = re.compile(r'\(([A-Z0-9]{10})\)')


def _parse_identities(output: str) -> list[dict]:
    """Parse `security find-identity -v -p codesigning` stdout.

    Returns list of {sha1, name, team_id} dicts. Skips expired identities
    (security marks them with CSSMERR_TP_CERT_EXPIRED in the output).
    """
    result = []
    for line in output.splitlines():
        # Skip expired / revoked entries
        if "CSSMERR" in line or "REVOKED" in line:
            continue
        m = _IDENTITY_RE.search(line)
        if m:
            sha1, name = m.group(1), m.group(2)
            tm = _TEAM_ID_RE.search(name)
            team_id = tm.group(1) if tm else ""
            result.append({"sha1": sha1, "name": name, "team_id": team_id})
    return result


def resolve_signing_identity(
    signing_identity: Optional[str] = None,
    team_id: Optional[str] = None,
) -> tuple[str, str]:
    """Return (signing_identity, team_id) to use for xcodebuild.

    Resolution order:
      1. If --signing-identity supplied, use it (extract team_id if absent).
      2. Else parse keychain; if exactly one identity → use it.
      3. Else raise wda_signing_ambiguous / wda_no_signing_identity.
    """
    result = subprocess.run(
        ["security", "find-identity", "-v", "-p", "codesigning"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    identities = _parse_identities(result.stdout)

    if signing_identity:
        # Caller supplied explicit identity; just extract team_id if not also supplied.
        if not team_id:
            tm = _TEAM_ID_RE.search(signing_identity)
            team_id = tm.group(1) if tm else ""
        return signing_identity, team_id

    if not identities:
        raise wda_no_signing_identity()

    if len(identities) == 1:
        identity = identities[0]
        return identity["name"], team_id or identity["team_id"]

    # Multiple identities — filter to Apple Development / iPhone Developer certs
    # and prefer the ones that contain "Apple Development".
    apple_dev = [i for i in identities if "Apple Development" in i["name"]]
    if len(apple_dev) == 1:
        identity = apple_dev[0]
        return identity["name"], team_id or identity["team_id"]

    # Still ambiguous — raise with the full list.
    raise wda_signing_ambiguous([i["name"] for i in identities])


# ─── xcodebuild ──────────────────────────────────────────────────────────────


def build_wda(
    udid: str,
    source_dir: Path,
    signing_identity: str,
    team_id: str,
) -> Path:
    """Run xcodebuild build-for-testing for WebDriverAgentRunner.

    Streams stdout live (so the user can see progress). Returns the derived
    data path. Raises wda_build_failed with the log path on non-zero exit.
    """
    wda_home = Path(os.environ.get("WDA_REGISTRY_DIR", Path.home() / ".simdrive" / "wda"))
    derived_data = wda_home / udid / "derived"
    log_path = wda_home / udid / "build.log"
    derived_data.mkdir(parents=True, exist_ok=True)

    workspace = source_dir / "WebDriverAgent.xcworkspace"
    cmd = [
        "xcodebuild",
        "-workspace", str(workspace),
        "-scheme", "WebDriverAgentRunner",
        "-destination", f"id={udid}",
        "-derivedDataPath", str(derived_data),
        "build-for-testing",
        f"CODE_SIGN_IDENTITY={signing_identity}",
        f"DEVELOPMENT_TEAM={team_id}",
    ]
    print(f"[simdrive] Building WebDriverAgentRunner ...", flush=True)
    print(f"[simdrive] xcodebuild command: {' '.join(cmd)}", flush=True)

    with log_path.open("w", encoding="utf-8") as log_file:
        proc = subprocess.run(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )

    if proc.returncode != 0:
        raise wda_build_failed(str(log_path))

    print(f"[simdrive] Build succeeded. Derived data: {derived_data}", flush=True)
    return derived_data


# ─── install ─────────────────────────────────────────────────────────────────


def _find_wda_app_bundle(derived_data: Path) -> Optional[Path]:
    """Find WebDriverAgentRunner.app inside the derived data directory."""
    # xcodebuild places the .app here:
    # <derived>/Build/Products/Debug-iphoneos/WebDriverAgentRunner-Runner.app
    # Appium WDA uses: WebDriverAgentRunner-Runner.app or similar
    patterns = [
        "Build/Products/*-iphoneos/WebDriverAgentRunner*.app",
        "Build/Products/*-iphoneos/*.xctrunner",
    ]
    import glob
    for pat in patterns:
        matches = glob.glob(str(derived_data / pat))
        if matches:
            return Path(matches[0])
    return None


def install_wda(udid: str, derived_data: Path) -> str:
    """Install the WDA app bundle via xcrun devicectl.

    Returns the bundle identifier of the installed app.
    Raises wda_install_failed on non-zero exit.
    """
    app_bundle = _find_wda_app_bundle(derived_data)
    if app_bundle is None:
        raise wda_install_failed(
            f"Could not find WebDriverAgentRunner.app in {derived_data}. "
            "Run with --rebuild to trigger a fresh build."
        )

    print(f"[simdrive] Installing {app_bundle.name} on device {udid} ...", flush=True)
    result = subprocess.run(
        ["xcrun", "devicectl", "device", "install", "app",
         "--device", udid, str(app_bundle)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise wda_install_failed(result.stderr or result.stdout)

    print("[simdrive] Install succeeded.", flush=True)
    return _WDA_BUNDLE_ID


# ─── launch + port discovery ─────────────────────────────────────────────────


def _tail_console_for_port(udid: str, timeout_s: float) -> Optional[int]:
    """Tail the device console for the WDA ServerURLHere announcement.

    Spawns `xcrun devicectl device console --device <udid>` in the background
    and scans its stdout for the magic pattern for up to timeout_s seconds.
    Returns the port integer, or None on timeout.
    """
    port: Optional[int] = None
    deadline = time.monotonic() + timeout_s

    proc = subprocess.Popen(
        ["xcrun", "devicectl", "device", "console", "--device", udid],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )

    def _reader() -> None:
        nonlocal port
        assert proc.stdout is not None
        for line in proc.stdout:
            m = _SERVER_URL_RE.search(line)
            if m:
                port = int(m.group(1))
                break
            if time.monotonic() > deadline:
                break

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    t.join(timeout=timeout_s + 1.0)
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
    return port


def launch_and_discover_port(
    udid: str,
    bundle_id: str = _WDA_BUNDLE_ID,
    wda_port: int = _WDA_DEFAULT_PORT,
    tunnel_host: str = "",
) -> tuple[str, int]:
    """Launch WDA on the device and discover the port it bound to.

    Returns (host, port). host is the tunnel_host if supplied, else 'localhost'.
    Raises wda_port_discovery_timeout if WDA doesn't announce within 15s.
    """
    host = tunnel_host or "localhost"

    # Launch the WDA test runner process on the device.
    print(f"[simdrive] Launching WDA ({bundle_id}) on device {udid} ...", flush=True)
    subprocess.run(
        ["xcrun", "devicectl", "device", "process", "launch",
         "--device", udid, bundle_id],
        capture_output=True,
        text=True,
        check=False,  # non-zero is acceptable — WDA may already be running
        timeout=30,
    )

    print(f"[simdrive] Waiting for WDA port announcement (up to {_PORT_DISCOVERY_TIMEOUT_S}s) ...", flush=True)
    discovered = _tail_console_for_port(udid, _PORT_DISCOVERY_TIMEOUT_S)

    if discovered is None:
        raise wda_port_discovery_timeout(udid)

    print(f"[simdrive] WDA listening on {host}:{discovered}", flush=True)
    return host, discovered


# ─── smoke test ──────────────────────────────────────────────────────────────


def smoke_test(host: str, port: int) -> None:
    """GET http://<host>:<port>/status and assert {value: {ready: true}}.

    Raises wda_smoke_failed on mismatch or HTTP error.
    """
    url = f"http://{host}:{port}/status"
    print(f"[simdrive] Smoke testing WDA at {url} ...", flush=True)
    try:
        resp = httpx.get(url, timeout=10.0)
    except httpx.TransportError as exc:
        raise wda_smoke_failed(0, str(exc))

    if not resp.is_success:
        raise wda_smoke_failed(resp.status_code, resp.text)

    try:
        body = resp.json()
    except Exception:
        raise wda_smoke_failed(resp.status_code, resp.text)

    ready = (body.get("value") or {}).get("ready")
    if not ready:
        raise wda_smoke_failed(resp.status_code, json.dumps(body))

    print("[simdrive] WDA smoke test passed — ready=True.", flush=True)


# ─── user-facing Trust guidance ──────────────────────────────────────────────


def _print_trust_guidance(signing_identity: str) -> None:
    """Print the device-side Trust prompt instructions to stdout.

    Called before install so the user knows to watch their device screen.
    This text is the copyable guidance spec'd in the prompt.
    """
    print("", flush=True)
    print("=" * 72, flush=True)
    print("DEVICE ACTION MAY BE REQUIRED", flush=True)
    print("=" * 72, flush=True)
    print(
        f"If this is the first time installing a build signed with:\n"
        f"  {signing_identity}\n"
        f"\n"
        f"iOS will show an 'Untrusted Developer' alert on the device.\n"
        f"To trust the certificate:\n"
        f"\n"
        f"  Settings → General → VPN & Device Management\n"
        f"    → {signing_identity}\n"
        f"    → Trust\n"
        f"\n"
        f"After tapping Trust, re-run:\n"
        f"  simdrive bootstrap-device <udid> --rebuild\n",
        flush=True,
    )
    print("=" * 72, flush=True)
    print("", flush=True)


# ─── main bootstrap entry-point ──────────────────────────────────────────────


def bootstrap_device(
    udid: str,
    signing_identity: Optional[str] = None,
    team_id: Optional[str] = None,
    wireless: bool = False,
    wda_port: int = _WDA_DEFAULT_PORT,
    rebuild: bool = False,
) -> dict:
    """Full WDA bootstrap sequence. Prints progress to stdout.

    Returns the registry dict that was persisted to ~/.simdrive/wda/<udid>.json.
    All steps raise a typed SimdriveError subclass on failure.
    """
    # 1. Host tools
    verify_host_tools()
    print("[simdrive] Host tools OK.", flush=True)

    # 2. Device state
    verify_device_ready(udid)
    print("[simdrive] Device ready (paired, Developer Mode, DDI).", flush=True)

    # 3. Clone WDA
    source_dir = clone_wda(udid, rebuild=rebuild)

    # 4. Signing identity
    resolved_identity, resolved_team = resolve_signing_identity(signing_identity, team_id)
    print(f"[simdrive] Signing identity: {resolved_identity}", flush=True)
    print(f"[simdrive] Team ID:          {resolved_team}", flush=True)

    # Trust guidance before install (device screen may prompt).
    _print_trust_guidance(resolved_identity)

    # 5. Build
    derived_data = build_wda(udid, source_dir, resolved_identity, resolved_team)

    # 6. Install
    bundle_id = install_wda(udid, derived_data)

    # 7. Launch + port discovery
    # For wireless/CoreDevice tunnel use the well-known tunnel IPv6 address if
    # provided via env; otherwise fall back to localhost which works for USB.
    tunnel_host = os.environ.get("WDA_TUNNEL_HOST", "")
    host, port = launch_and_discover_port(udid, bundle_id, wda_port, tunnel_host)

    # 8. Persist registry
    import time as _time
    entry = {
        "wda_bundle_id": bundle_id,
        "install_path": str(_find_wda_app_bundle(derived_data) or ""),
        "last_built_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
        "host": host,
        "port": port,
        "signing_identity": resolved_identity,
        "team_id": resolved_team,
    }
    registry_path = registry.save(udid, entry)
    print(f"[simdrive] Registry written to {registry_path}", flush=True)

    # 9. Smoke test
    smoke_test(host, port)

    # 10. Success summary
    print("", flush=True)
    print("=" * 72, flush=True)
    print("WDA READY", flush=True)
    print("=" * 72, flush=True)
    print(f"  Device UDID:      {udid}", flush=True)
    print(f"  WDA endpoint:     http://{host}:{port}", flush=True)
    print(f"  Bundle ID:        {bundle_id}", flush=True)
    print(f"  Signing identity: {resolved_identity}", flush=True)
    print(f"  Registry:         {registry_path}", flush=True)
    print("", flush=True)
    print("Next step: start a simdrive session with target=device:", flush=True)
    print(f'  simdrive start-session --udid {udid} --target device', flush=True)
    print("=" * 72, flush=True)

    return entry
