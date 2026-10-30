"""Real-device backend — screenshot + logs + app lifecycle for connected iPhones / iPads.

Internal module. Mirrors the surface of ``sim.py`` but for physical devices
reachable via Apple's ``devicectl`` and libimobiledevice. Touch input is NOT
implemented here; that's a v0.2.x follow-up that needs WebDriverAgent.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ----------------------- devicectl noise filter ----------------------- #

_DEVICECTL_NOISE = "No provider was found for this descriptor"


def _filter_devicectl_stderr(stderr: str, returncode: int) -> str:
    """Strip the noisy 'No provider was found' warning from devicectl stderr.

    When the command succeeds (returncode == 0), the warning is cosmetic noise
    emitted on every call regardless of outcome. We strip it to keep logs clean.
    Non-zero exits preserve stderr intact so callers can diagnose failures.
    """
    if returncode != 0:
        return stderr
    lines = [ln for ln in stderr.splitlines() if _DEVICECTL_NOISE not in ln]
    return "\n".join(lines)


class DeviceError(RuntimeError):
    """Raised when a real-device operation fails."""


@dataclass(frozen=True)
class RealDevice:
    udid: str
    name: str
    model: str
    transport: Optional[str]  # "wired" | "localNetwork" | None
    state: str  # "available" | "unavailable"
    last_seen: Optional[str] = None  # ISO-8601 from devicectl lastConnectionDate
    unavailable_reason: Optional[str] = None  # human reason when state="unavailable"
    coredevice_id: Optional[str] = None  # devicectl `identifier` (CoreDevice UUID)
    tunnel_state: Optional[str] = None  # devicectl connectionProperties.tunnelState

    @property
    def is_available(self) -> bool:
        return self.state == "available"


# ----------------------- Tool path resolution ----------------------- #


def _which(name: str) -> Optional[str]:
    p = shutil.which(name)
    if p:
        return p
    # Common Homebrew locations
    for cand in (f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"):
        if Path(cand).exists():
            return cand
    return None


def libimobiledevice_available() -> tuple[bool, list[str]]:
    """Returns (ok, missing_tools)."""
    needed = ["idevice_id", "idevicescreenshot", "idevicesyslog", "ideviceimagemounter"]
    missing = [n for n in needed if not _which(n)]
    return (not missing, missing)


def devicectl_available() -> bool:
    return _which("xcrun") is not None  # devicectl ships with Xcode


# ----------------------- Device discovery ----------------------- #


def list_devices() -> list[RealDevice]:
    """Enumerate all paired devices (Apple Silicon + libimobiledevice path)."""
    if not devicectl_available():
        raise DeviceError("xcrun (Xcode) not found")

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        json_out = tf.name
    try:
        res = subprocess.run(
            ["xcrun", "devicectl", "list", "devices",
             "--json-output", json_out, "--quiet"],
            capture_output=True, text=True, timeout=10.0, check=False,
        )
        if res.returncode != 0:
            raise DeviceError(f"devicectl list failed: {_filter_devicectl_stderr(res.stderr, res.returncode).strip()}")
        try:
            with open(json_out) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise DeviceError(f"devicectl list JSON unreadable: {exc}") from exc
    finally:
        try: os.unlink(json_out)
        except OSError: pass

    out: list[RealDevice] = []
    for d in data.get("result", {}).get("devices", []):
        hw = d.get("hardwareProperties", {}) or {}
        dp = d.get("deviceProperties", {}) or {}
        cp = d.get("connectionProperties", {}) or {}
        transport = cp.get("transportType")
        state = "available" if transport else "unavailable"
        out.append(RealDevice(
            udid=hw.get("udid", ""),
            name=dp.get("name", "<unknown>"),
            model=hw.get("marketingName") or hw.get("productType") or "<unknown>",
            transport=transport,
            state=state,
            last_seen=cp.get("lastConnectionDate"),
            unavailable_reason=_unavailable_reason(state, cp, dp),
            coredevice_id=d.get("identifier"),
            tunnel_state=cp.get("tunnelState"),
        ))
    return out


def probe_reachable(udid: str, timeout_s: float = 4.0) -> bool:
    """Return True when devicectl can talk to the device right now.

    `state="available"` only says devicectl knows a transport for the device;
    a phone that is asleep, out of Wi-Fi range or has a dead tunnel still
    reports one. ``devicectl device info lockState`` is the cheapest call that
    needs a live connection. Bounded by ``timeout_s``; any failure is False.
    """
    try:
        res = subprocess.run(
            ["xcrun", "devicectl", "device", "info", "lockState",
             "--device", udid, "--timeout", str(max(1, int(timeout_s))), "--quiet"],
            capture_output=True, text=True, timeout=timeout_s + 2.0, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0


def _unavailable_reason(state: str, cp: dict, dp: dict) -> Optional[str]:
    """Compose a one-line reason from devicectl JSON when state='unavailable'.

    devicectl JSON has no single 'reason' field; the diagnosis lives across
    pairingState, tunnelState, transportType, developerModeStatus.
    """
    if state == "available":
        return None
    parts: list[str] = []
    if cp.get("pairingState") == "unpaired":
        parts.append("not paired")
    if cp.get("tunnelState") == "disconnected":
        parts.append("tunnel disconnected")
    if not cp.get("transportType"):
        parts.append("no transport")
    if dp.get("developerModeStatus") == "disabled":
        parts.append("developer mode disabled")
    return "; ".join(parts) if parts else "device offline"


def find_device(udid: str) -> Optional[RealDevice]:
    """Find a device by hardware UDID or CoreDevice UUID."""
    want = udid.replace("-", "").upper()
    for d in list_devices():
        if d.udid.replace("-", "").upper() == want:
            return d
        if d.coredevice_id and d.coredevice_id.replace("-", "").upper() == want:
            return d
    return None


# CoreDevice UUIDs are RFC-4122 shaped (8-4-4-4-12). Hardware UDIDs are either
# 8-16 hex (iPhone XS and later) or 40 hex (older devices), so they never match.
_COREDEVICE_UUID_RE = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)


def resolve_hardware_udid(udid: str) -> str:
    """Map a CoreDevice UUID to the hardware UDID that libimobiledevice needs.

    devicectl accepts either identifier; ``idevicesyslog -u`` and
    ``idevicescreenshot -u`` only accept the hardware UDID. Anything that is
    not CoreDevice-UUID shaped is returned unchanged. Uses ``devicectl list
    devices`` (local, does not contact the device); falls back to ``udid``.
    """
    if not _COREDEVICE_UUID_RE.match(udid or ""):
        return udid
    try:
        d = find_device(udid)
    except DeviceError:
        return udid
    return d.udid if d and d.udid else udid


_NO_DEVICE_MARKERS = ("No device found", "ERROR: Device", "not found")


def _run_imobile(cmd_base: list[str], udid: str, rest: list[str], timeout: float,
                 network: Optional[bool]) -> subprocess.CompletedProcess:
    """Run a libimobiledevice tool, retrying with ``-n`` (network) when the
    USB lookup reports no device and ``network`` was not pinned."""
    modes = [network] if network is not None else [False, True]
    res: Optional[subprocess.CompletedProcess] = None
    for net in modes:
        cmd = list(cmd_base) + ["-u", udid] + (["-n"] if net else []) + list(rest)
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        if res.returncode == 0:
            return res
        msg = (res.stderr or "") + (res.stdout or "")
        if not any(m in msg for m in _NO_DEVICE_MARKERS):
            return res
    assert res is not None
    return res


# ----------------------- Screenshot ----------------------- #


def screenshot(udid: str, dest_path: Path, network: Optional[bool] = None) -> Path:
    """Capture a PNG screenshot to dest_path.

    ``udid`` may be a CoreDevice UUID; it is mapped to the hardware UDID.
    ``network=None`` tries USB first, then ``-n`` (Wi-Fi) if USB has no device.

    `idevicescreenshot` writes TIFF by default; pass an explicit `.png`
    output path and it will encode as PNG.
    """
    bin_path = _which("idevicescreenshot")
    if not bin_path:
        raise DeviceError(
            "idevicescreenshot not found. Install with: brew install libimobiledevice"
        )
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    res = _run_imobile([bin_path], resolve_hardware_udid(udid), [str(dest_path)],
                       timeout=15.0, network=network)
    if res.returncode != 0:
        msg = res.stderr.strip() or res.stdout.strip()
        if "Developer disk image" in msg or "Invalid service" in msg:
            raise DeviceError(
                f"screenshot service unavailable (Developer Disk Image not mounted). "
                f"Run: ideviceimagemounter -u {udid} <path-to-DDI>. Original error: {msg}"
            )
        raise DeviceError(f"idevicescreenshot failed: {msg}")
    if not dest_path.exists():
        raise DeviceError(f"screenshot reported success but file missing: {dest_path}")
    return dest_path


# ----------------------- Logs ----------------------- #


def get_log_tail(
    udid: str,
    lines: int = 50,
    predicate: Optional[str] = None,
    predicate_kind: str = "substring",
    timeout_s: float = 5.0,
    bundle_id: Optional[str] = None,
    network: Optional[bool] = None,
    process: Optional[str] = None,
) -> str:
    """Capture a live tail of syslog from the device via idevicesyslog.

    ``udid`` may be a CoreDevice UUID; it is mapped to the hardware UDID that
    idevicesyslog needs. ``network=None`` tries USB first and retries with
    ``-n`` when idevicesyslog reports no device. ``process`` passes ``-p`` (the
    process name, e.g. "Palace"). If idevicesyslog exits with an error before
    the timeout, DeviceError is raised with its stderr.

    idevicesyslog streams indefinitely; we spawn it with communicate(timeout=...)
    to capture up to `timeout_s` seconds of output, then apply filtering.

    Args:
        udid: The device UDID (hardware UDID, not CoreDevice UUID).
        lines: Maximum number of lines to return (default 50). The most
            recent ``lines`` entries are returned after filtering.
        predicate: Optional filter string. Interpretation depends on predicate_kind.
            If the filter causes zero matches, a warning line is returned
            so the caller is not silently empty.
        predicate_kind: How to apply ``predicate``. One of:
            - "substring" (default for device): Python ``predicate in line``.
            - "regex": Python ``re.search(predicate, line)`` post-capture.
            - "nspredicate": Not natively supported by idevicesyslog. A simple
              CONTAINS/equality predicate body is used as-is for substring match.
              A WARNING is logged once on downgrade.
        timeout_s: Wall-clock timeout in seconds (default 5). idevicesyslog
            is killed after this time whether or not output has finished.
        bundle_id: Optional app bundle identifier to pass as a process/bundle
            filter to idevicesyslog (--match or -p flag). When set, only
            log lines from that process are captured, reducing noise.

    Returns:
        A newline-joined string of the captured (and optionally filtered) log
        lines, trimmed to the most recent `lines` entries.

    Raises:
        DeviceError with code context "device_logs_unavailable" when
        idevicesyslog is not on PATH.
    """
    import logging as _logging
    import re as _re
    _device_log = _logging.getLogger(__name__)

    bin_path = _which("idevicesyslog")
    if not bin_path:
        raise DeviceError(
            "device_logs_unavailable: idevicesyslog not installed. "
            "Recovery: brew install libimobiledevice"
        )

    # nspredicate is not supported on device; downgrade to substring with a warning.
    effective_kind = predicate_kind
    if predicate_kind == "nspredicate" and predicate:
        _device_log.warning(
            "predicate_kind='nspredicate' is not supported on device (idevicesyslog "
            "does not speak NSPredicate). Downgrading to predicate_kind='substring' "
            "using the predicate body as-is. For reliable device filtering, use "
            "predicate_kind='substring' or 'regex'."
        )
        effective_kind = "substring"

    hw_udid = resolve_hardware_udid(udid)
    modes = [network] if network is not None else [False, True]
    raw_output = ""
    err_output = ""
    for net in modes:
        cmd = [bin_path, "-u", hw_udid]
        if net:
            cmd.append("-n")
        if process:
            cmd.extend(["-p", process])
        if bundle_id:
            # --match filters on a substring of the line (libimobiledevice >= 1.3).
            cmd.extend(["--match", bundle_id])
        raw_output, err_output, exited_rc = _capture_stream(cmd, timeout_s)
        if exited_rc is None or exited_rc == 0 or raw_output.strip():
            break
        msg = (err_output or "").strip()
        if net is False and len(modes) > 1 and any(m in msg for m in _NO_DEVICE_MARKERS):
            continue  # USB lookup failed; retry over the network.
        raise DeviceError(
            f"idevicesyslog exited {exited_rc} for device {hw_udid}"
            + (" (network)" if net else "")
            + f": {msg or '<no stderr>'}"
        )

    all_lines = [ln for ln in raw_output.splitlines() if ln]
    if not all_lines:
        hint = (err_output or "").strip()
        return (
            f"[simdrive] idevicesyslog captured no lines from {hw_udid} within "
            f"{timeout_s:.0f}s. Check that the device is unlocked and reachable "
            "(USB, or Wi-Fi pairing for -n)."
            + (f" stderr: {hint}" if hint else "")
        )

    # Apply predicate filter based on effective_kind.
    if predicate:
        if effective_kind == "regex":
            try:
                pattern = _re.compile(predicate)
                filtered = [ln for ln in all_lines if pattern.search(ln)]
            except _re.error as exc:
                return (
                    f"[simdrive] idevicesyslog: invalid regex predicate={predicate!r}: {exc}. "
                    "Fix the regex or use predicate_kind='substring'."
                )
        else:
            # "substring" or downgraded "nspredicate" — plain Python substring match.
            filtered = [ln for ln in all_lines if predicate in ln]

        if not filtered:
            # Surface a warning line rather than an opaque empty string.
            return (
                f"[simdrive] idevicesyslog: no lines matched predicate={predicate!r} "
                f"(kind={effective_kind!r}) within {timeout_s:.0f}s. "
                "The app may not be logging or the filter is too narrow. "
                "Retry without predicate to see raw syslog."
            )
        all_lines = filtered

    # Trim to the most recent `lines` entries.
    return "\n".join(all_lines[-lines:])


def _capture_stream(cmd: list[str], timeout_s: float) -> tuple[str, str, Optional[int]]:
    """Run a streaming command for up to ``timeout_s`` seconds.

    Returns (stdout, stderr, returncode). returncode is None when the command
    was still running at the timeout (the normal case for idevicesyslog) and
    was killed; output captured up to that point is returned.
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout_s)
        rc = proc.returncode
        return out or "", err or "", rc if isinstance(rc, int) else 0
    except subprocess.TimeoutExpired:
        proc.kill()
        # communicate() buffers what it has read so far; a second call after
        # kill() returns all of it. Reading proc.stdout directly would only
        # return the unconsumed remainder.
        try:
            out, err = proc.communicate(timeout=2.0)
            return out or "", err or "", None
        except Exception:
            try:
                out = proc.stdout.read() if proc.stdout else ""
            except Exception:
                out = ""
            return out or "", "", None
    except Exception:
        proc.kill()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass
        return "", "", None


# ----------------------- App lifecycle ----------------------- #


def install_app(udid: str, app_path: Path) -> None:
    if not Path(app_path).exists():
        raise DeviceError(f"app bundle not found: {app_path}")
    res = subprocess.run(
        ["xcrun", "devicectl", "device", "install", "app",
         "--device", udid, str(app_path)],
        capture_output=True, text=True, timeout=120.0, check=False,
    )
    if res.returncode != 0:
        raise DeviceError(f"devicectl install failed: {_filter_devicectl_stderr(res.stderr, res.returncode).strip() or res.stdout.strip()}")


def launch_app(udid: str, bundle_id: str) -> int:
    res = subprocess.run(
        ["xcrun", "devicectl", "device", "process", "launch",
         "--device", udid, bundle_id,
         "--json-output", "/dev/stdout"],
        capture_output=True, text=True, timeout=30.0, check=False,
    )
    if res.returncode != 0:
        raise DeviceError(f"devicectl launch failed: {_filter_devicectl_stderr(res.stderr, res.returncode).strip() or res.stdout.strip()}")
    try:
        data = json.loads(res.stdout)
        return int(data.get("result", {}).get("process", {}).get("processIdentifier", 0))
    except (json.JSONDecodeError, ValueError, TypeError):
        return 0


def terminate_app(udid: str, bundle_id: str) -> bool:
    """Terminate ``bundle_id`` on the device. Returns True if a process was
    terminated, False if it was not running.

    ``devicectl device process terminate`` (and ``signal``) only accept
    ``--pid``; there is no bundle-id form. The PID comes from the devicectl
    process list via ``diagnostics.app_state_device``.
    Raises DeviceError when the process list or the terminate call fails.
    """
    from . import diagnostics as _diag
    try:
        info = _diag.app_state_device(udid, bundle_id)
    except Exception as exc:
        raise DeviceError(f"devicectl process list failed: {exc}") from exc
    pid = info.get("pid")
    if info.get("state") != "running" or not pid:
        return False
    res = subprocess.run(
        ["xcrun", "devicectl", "device", "process", "terminate",
         "--device", udid, "--pid", str(int(pid))],
        capture_output=True, text=True, timeout=30.0, check=False,
    )
    if res.returncode != 0:
        raise DeviceError(
            "devicectl terminate failed: "
            f"{_filter_devicectl_stderr(res.stderr, res.returncode).strip() or res.stdout.strip()}"
        )
    return True


# ----------------------- Developer Disk Image ----------------------- #


def is_developer_disk_mounted(udid: str) -> bool:
    """Best-effort check via idevicescreenshot — if it succeeds, DDI is mounted."""
    bin_path = _which("idevicescreenshot")
    if not bin_path:
        return False
    # `-l` lists capability without writing; not all idevicescreenshot builds
    # support it. Fall back to a probe: try a quick -h, see if service errors.
    res = subprocess.run(
        [bin_path, "-u", udid, "/dev/null"],
        capture_output=True, text=True, timeout=5.0, check=False,
    )
    return "Invalid service" not in (res.stderr + res.stdout)
