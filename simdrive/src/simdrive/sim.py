"""simctl wrappers — boot, screenshot, install, logs, list."""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class SimError(RuntimeError):
    """Raised when simctl returns a non-zero status or unexpected output."""


@dataclass(frozen=True)
class Device:
    udid: str
    name: str
    os_version: str
    state: str  # "Booted" | "Shutdown" | ...

    @property
    def is_booted(self) -> bool:
        return self.state == "Booted"


def _simctl(*args: str, timeout: float = 30.0, capture: bool = True) -> subprocess.CompletedProcess:
    cmd = ["xcrun", "simctl", *args]
    return subprocess.run(cmd, capture_output=capture, text=True, timeout=timeout, check=False)


def list_devices() -> list[Device]:
    """All known simulator devices, with current state."""
    res = _simctl("list", "devices", "--json")
    if res.returncode != 0:
        raise SimError(f"simctl list failed: {res.stderr.strip()}")
    payload = json.loads(res.stdout)
    out: list[Device] = []
    for runtime, devs in payload.get("devices", {}).items():
        # runtime looks like "com.apple.CoreSimulator.SimRuntime.iOS-26-3"
        os_version = runtime.split(".")[-1].replace("iOS-", "").replace("-", ".")
        for d in devs:
            if not d.get("isAvailable", True):
                continue
            out.append(Device(udid=d["udid"], name=d["name"], os_version=os_version, state=d.get("state", "")))
    return out


def get_app_version(udid: str, bundle_id: str) -> Optional[str]:
    """Return the installed app's CFBundleShortVersionString (or CFBundleVersion fallback).

    Returns None if the bundle is not installed or simctl listapps can't be parsed.
    Used by recorder.finalize() to stamp the recording with the app's version
    so a stale replay against a newer build is diagnosable.
    """
    res = _simctl("listapps", udid, timeout=15.0)
    if res.returncode != 0:
        return None
    body = res.stdout or ""
    if not body.strip():
        return None
    data = _parse_listapps(body)
    info = data.get(bundle_id) if isinstance(data, dict) else None
    if not isinstance(info, dict):
        return None
    return info.get("CFBundleShortVersionString") or info.get("CFBundleVersion")


def _parse_listapps(body: str) -> dict:
    """Parse simctl listapps output. It's OpenStep ASCII plist on Xcode 16+;
    plistlib can't read that format, so route through `plutil -convert json`.
    """
    import plistlib
    try:
        return plistlib.loads(body.encode("utf-8"))
    except Exception:
        pass
    try:
        return json.loads(body)
    except Exception:
        pass
    try:
        conv = subprocess.run(
            ["plutil", "-convert", "json", "-o", "-", "-"],
            input=body, capture_output=True, text=True,
            timeout=10.0, check=False,
        )
        if conv.returncode == 0 and conv.stdout.strip():
            return json.loads(conv.stdout)
    except Exception:
        pass
    return {}


def get_app_container(udid: str, bundle_id: str, kind: str = "data") -> Path:
    """Resolve an installed app's on-disk container.

    kind: "app" (the bundle), "data" (the sandbox — where Library/Preferences
    lives), "groups", or a specific app-group identifier.

    Raises SimError when the app is not installed on that device, which is the
    only way simctl distinguishes "no such bundle" from "no such container".
    """
    res = _simctl("get_app_container", udid, bundle_id, kind, timeout=15.0)
    if res.returncode != 0:
        raise SimError(
            f"no {kind} container for {bundle_id!r} on {udid}: "
            f"{(res.stderr or res.stdout).strip()[:300]}"
        )
    path = res.stdout.strip()
    if not path:
        raise SimError(f"simctl returned an empty {kind} container path for {bundle_id!r}")
    return Path(path)


def find_device(name: str | None = None, os_version: str | None = None, udid: str | None = None) -> Device | None:
    """Look up a device by udid (exact), or name (+ optional os_version)."""
    devices = list_devices()
    if udid:
        for d in devices:
            if d.udid == udid:
                return d
        return None
    if name:
        candidates = [d for d in devices if d.name == name]
        if os_version:
            candidates = [d for d in candidates if d.os_version == os_version]
        if not candidates:
            return None
        # Prefer already-booted candidate
        booted = [d for d in candidates if d.is_booted]
        return booted[0] if booted else candidates[0]
    # No filter — return first booted device if any
    booted = [d for d in devices if d.is_booted]
    return booted[0] if booted else None


def first_booted() -> Device | None:
    for d in list_devices():
        if d.is_booted:
            return d
    return None


def boot(udid: str, timeout: float = 60.0) -> None:
    """Boot a sim if not already booted; wait for state=Booted."""
    res = _simctl("bootstatus", udid, "-b", timeout=timeout + 5.0)
    # bootstatus returns 0 once booted; -b means "boot if not running"
    if res.returncode != 0:
        # Might already be booted; check
        d = find_device(udid=udid)
        if d and d.is_booted:
            return
        raise SimError(f"simctl boot failed for {udid}: {res.stderr.strip() or res.stdout.strip()}")


def screenshot(udid: str, dest_path: Path) -> Path:
    """Capture a PNG screenshot to dest_path."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    res = _simctl("io", udid, "screenshot", str(dest_path), timeout=15.0)
    if res.returncode != 0:
        raise SimError(f"simctl screenshot failed: {res.stderr.strip()}")
    if not dest_path.exists():
        raise SimError(f"screenshot reported success but file missing: {dest_path}")
    return dest_path


def launch_app(udid: str, bundle_id: str) -> int:
    """Launch an installed app; returns the launched PID."""
    res = _simctl("launch", udid, bundle_id, timeout=15.0)
    if res.returncode != 0:
        raise SimError(f"simctl launch {bundle_id} failed: {res.stderr.strip()}")
    # stdout looks like "com.example.App: 12345"
    line = res.stdout.strip()
    if ":" in line:
        try:
            return int(line.rsplit(":", 1)[1].strip())
        except ValueError:
            pass
    return 0


def terminate_app(udid: str, bundle_id: str) -> None:
    _simctl("terminate", udid, bundle_id, timeout=10.0)


def set_pasteboard(udid: str, text: str) -> None:
    """Push UTF-8 text onto the simulator's pasteboard.

    Used as the fallback for non-ASCII characters in type_text since the HID
    keyboard only emits US-ASCII keycodes.
    """
    res = subprocess.run(
        ["xcrun", "simctl", "pbcopy", udid],
        input=text,
        text=True,
        capture_output=True,
        timeout=5.0,
    )
    if res.returncode != 0:
        raise SimError(f"simctl pbcopy failed: {res.stderr.strip()}")


# ── log capture ─────────────────────────────────────────────────────────────
#
# `log show` reports only default-level records unless told otherwise: `.info`
# and `.debug` are dropped on the floor. Most apps narrate at `.info`, so the
# pre-1.0.0b13 capture (no flags) made an app's own logging structurally
# invisible to the tool built to read it. LOG_LEVELS maps our three-valued
# `level` onto the flags that actually widen the stream.
#
# "debug" deliberately carries --info too: --debug alone is documented as
# enabling debug-level records, not as implying info-level, and the union is
# what a caller asking for "everything" means.
LOG_LEVELS: dict[str, tuple[str, ...]] = {
    "default": (),
    "info": ("--info",),
    "debug": ("--info", "--debug"),
}

# `log show --last` accepts a bare integer (seconds) or an integer plus a unit
# suffix. We validate rather than pass through: the value lands in an argv we
# construct, and a permissive parse is how a duration string turns into an
# argument-injection surface.
_LOG_WINDOW_UNIT_SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}

_LOG_TIMEOUT_MIN_SEC = 10.0
_LOG_TIMEOUT_MAX_SEC = 120.0


def _parse_log_window(last: str) -> int:
    """Validate a `log show --last` duration and return it in seconds.

    Accepts ``"30s"``, ``"5m"``, ``"2h"``, ``"1d"`` and a bare integer
    (interpreted as seconds, matching `log show`). Raises SimError on anything
    else so a malformed value fails loudly at the call site instead of
    reaching the shell.
    """
    raw = (last or "").strip()
    if not raw:
        raise SimError("log window `last` must be non-empty, e.g. '30s' or '5m'")
    unit = ""
    digits = raw
    if raw[-1].isalpha():
        unit = raw[-1].lower()
        digits = raw[:-1]
    if not digits.isdigit() or unit not in _LOG_WINDOW_UNIT_SECONDS:
        raise SimError(
            f"invalid log window {last!r}: expected an integer plus an optional "
            "s/m/h/d suffix, e.g. '30s', '5m', '2h'"
        )
    return int(digits) * _LOG_WINDOW_UNIT_SECONDS[unit]


def _log_timeout(window_seconds: int) -> float:
    """Scale the subprocess budget with the requested window.

    A 5-minute `--info` window emits far more than a 30-second default-level
    one; the old fixed 10s budget would time the capture out and hand back an
    empty result that reads like "the app logged nothing".
    """
    return max(_LOG_TIMEOUT_MIN_SEC, min(_LOG_TIMEOUT_MAX_SEC, window_seconds * 0.5))


def get_log_tail(
    udid: str,
    lines: int = 50,
    predicate: str | None = None,
    level: str = "info",
    last: str = "30s",
) -> str:
    """Capture a one-shot tail of recent simulator logs.

    Uses `log show --last <duration>` which doesn't stream — bounded latency.

    level: "default" (default-level records only, the quietest stream),
           "info" (adds `--info` — what an app's own narration usually uses),
           "debug" (adds `--info --debug`; very high volume).
    last:  capture window, e.g. "30s", "5m", "2h". `lines` slices *after*
           capture, so this is what actually bounds how far back a tail reaches.
    """
    if level not in LOG_LEVELS:
        raise SimError(
            f"invalid log level {level!r}: expected one of {sorted(LOG_LEVELS)}"
        )
    window_seconds = _parse_log_window(last)
    args = ["spawn", udid, "log", "show", "--last", last, "--style", "compact"]
    args += list(LOG_LEVELS[level])
    if predicate:
        args += ["--predicate", predicate]
    res = _simctl(*args, timeout=_log_timeout(window_seconds))
    if res.returncode != 0:
        # Don't raise — logs are best-effort; just return stderr noise.
        return res.stderr.strip()[:2000]
    out_lines = res.stdout.strip().splitlines()
    return "\n".join(out_lines[-lines:])


def shutdown(udid: str) -> None:
    _simctl("shutdown", udid, timeout=30.0)


def cliclick_path() -> str:
    """Locate cliclick binary; raise if missing."""
    p = shutil.which("cliclick") or "/opt/homebrew/bin/cliclick"
    if not Path(p).exists():
        raise SimError(
            "cliclick not found. Install with: brew install cliclick"
        )
    return p
