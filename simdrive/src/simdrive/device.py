"""Real-device backend — screenshot + logs + app lifecycle for connected iPhones / iPads.

Internal module. Mirrors the surface of ``sim.py`` but for physical devices
reachable via Apple's ``devicectl`` and libimobiledevice. Touch input is NOT
implemented here; that's a v0.2.x follow-up that needs WebDriverAgent.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


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
            raise DeviceError(f"devicectl list failed: {res.stderr.strip()}")
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
        ))
    return out


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
    for d in list_devices():
        if d.udid == udid or d.udid.replace("-", "") == udid.replace("-", ""):
            return d
    return None


# ----------------------- Screenshot ----------------------- #


def screenshot(udid: str, dest_path: Path) -> Path:
    """Capture a PNG screenshot to dest_path.

    `idevicescreenshot` writes TIFF by default; pass an explicit `.png`
    output path and it will encode as PNG.
    """
    bin_path = _which("idevicescreenshot")
    if not bin_path:
        raise DeviceError(
            "idevicescreenshot not found. Install with: brew install libimobiledevice"
        )
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(
        [bin_path, "-u", udid, str(dest_path)],
        capture_output=True, text=True, timeout=15.0, check=False,
    )
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


def get_log_tail(udid: str, lines: int = 50, predicate: Optional[str] = None) -> str:
    """Capture a short live tail of syslog from the device.

    `idevicesyslog` streams indefinitely, so we read for ~1 second and trim
    to the most recent `lines` lines. `predicate`, when given, is applied as
    a simple substring filter (NSPredicate on real device requires log
    framework, not idevicesyslog).
    """
    bin_path = _which("idevicesyslog")
    if not bin_path:
        raise DeviceError(
            "idevicesyslog not found. Install with: brew install libimobiledevice"
        )

    proc = subprocess.Popen(
        [bin_path, "-u", udid],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        time.sleep(1.0)
        proc.terminate()
        out, _ = proc.communicate(timeout=2.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        out = ""

    out_lines = (out or "").splitlines()
    if predicate:
        out_lines = [ln for ln in out_lines if predicate in ln]
    return "\n".join(out_lines[-lines:])


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
        raise DeviceError(f"devicectl install failed: {res.stderr.strip() or res.stdout.strip()}")


def launch_app(udid: str, bundle_id: str) -> int:
    res = subprocess.run(
        ["xcrun", "devicectl", "device", "process", "launch",
         "--device", udid, bundle_id,
         "--json-output", "/dev/stdout"],
        capture_output=True, text=True, timeout=30.0, check=False,
    )
    if res.returncode != 0:
        raise DeviceError(f"devicectl launch failed: {res.stderr.strip() or res.stdout.strip()}")
    try:
        data = json.loads(res.stdout)
        return int(data.get("result", {}).get("process", {}).get("processIdentifier", 0))
    except (json.JSONDecodeError, ValueError, TypeError):
        return 0


def terminate_app(udid: str, bundle_id: str) -> None:
    # devicectl process kill needs PID; we don't track PIDs persistently, so
    # do a best-effort signal via terminate-by-bundle (Xcode 26+ supports this).
    subprocess.run(
        ["xcrun", "devicectl", "device", "process", "signal",
         "--device", udid, "--signal", "SIGTERM", "--bundle-id", bundle_id],
        capture_output=True, timeout=10.0, check=False,
    )


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
