"""Session lifecycle: pick a sim, boot it, optionally launch an app, track state.

Sessions are in-process state — one MCP server, one process, dict of sessions
keyed by session_id. Sims persist across sessions; we don't shut them down.
"""
from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import errors, sim
from .sim import Device, SimError


_SESSIONS: dict[str, "Session"] = {}


@dataclass
class Session:
    session_id: str
    device: Device
    workdir: Path
    target: str = "simulator"  # "simulator" | "device"
    app_bundle_id: Optional[str] = None
    last_screenshot_w: int = 0
    last_screenshot_h: int = 0
    last_screenshot_path: Optional[Path] = None
    last_marks: list = field(default_factory=list)  # list[som.Mark]
    last_action_at: float = field(default_factory=time.time)
    state: str = "active"  # "active" | "degraded"
    recorder: Optional["Recorder"] = None  # set by recorder.py to avoid import cycle
    perf_baselines: dict = field(default_factory=dict)  # label -> snapshot dict (for perf_compare)
    started_at: float = field(default_factory=time.time)  # used by `crashes` to filter .ips by mtime
    wda_client: Optional[object] = None  # WdaClient instance for target="device" sessions


def _workroot() -> Path:
    base = os.environ.get("SIMDRIVE_HOME") or str(Path.home() / ".simdrive")
    return Path(base)


def append_action(s: "Session", record: dict) -> None:
    """Append an action entry to the session's actions.jsonl audit log.

    One JSON object per line. Records every act-tool call (with args, resolved
    target, screenshot paths, timestamp) so a session directory is a complete
    artifact — no need to call record_start to capture a flow.
    """
    import json as _json
    log_path = s.workdir / "actions.jsonl"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as f:
            f.write(_json.dumps(record) + "\n")
    except Exception:
        pass  # never fail the act tool because of audit logging


def start(
    device_name: Optional[str] = None,
    os_version: Optional[str] = None,
    udid: Optional[str] = None,
    app_bundle_id: Optional[str] = None,
    target: str = "simulator",
) -> Session:
    """Find or boot a sim/device, start a session.

    target="simulator" (default): existing simulator behavior.
    target="device": resolve a connected real device by udid.
    """
    if target == "device":
        return _start_device(udid=udid, app_bundle_id=app_bundle_id)

    if udid:
        d = sim.find_device(udid=udid)
        if not d:
            raise errors.no_device({"udid": udid})
    elif device_name:
        d = sim.find_device(name=device_name, os_version=os_version)
        if not d:
            raise errors.no_device({"device_name": device_name, "os_version": os_version})
    else:
        d = sim.first_booted()
        if not d:
            raise errors.no_device({"any_booted": True})

    if not d.is_booted:
        sim.boot(d.udid)
        # Refresh state
        refreshed = sim.find_device(udid=d.udid)
        if refreshed:
            d = refreshed

    if app_bundle_id:
        try:
            sim.launch_app(d.udid, app_bundle_id)
        except SimError as exc:
            raise SimError(f"sim booted but launch_app({app_bundle_id}) failed: {exc}")

    sid = secrets.token_urlsafe(8)
    workdir = _workroot() / "sessions" / sid
    workdir.mkdir(parents=True, exist_ok=True)
    s = Session(
        session_id=sid,
        device=d,
        workdir=workdir,
        app_bundle_id=app_bundle_id,
        target="simulator",
    )
    _SESSIONS[sid] = s
    return s


def _start_device(udid: Optional[str], app_bundle_id: Optional[str]) -> Session:
    """Start a real-device WDA session.

    Reads the WDA registry entry written by ``simdrive bootstrap-device`` to
    discover the host:port where WebDriverAgent is serving, then creates a
    Session pointing at that endpoint.  Never calls ``devicectl list`` — the
    registry is the single source of truth for device connectivity once WDA has
    been bootstrapped.
    """
    from .wda import registry as wda_registry
    from .wda.client import WdaClient
    from .wda.errors import wda_not_bootstrapped

    if not udid:
        raise errors.no_device({"target": "device", "any_booted": True})

    entry = wda_registry.load(udid)
    if entry is None:
        raise wda_not_bootstrapped(udid)

    host = entry.get("host") or entry.get("ip") or "localhost"
    port = int(entry.get("port", 8100))
    hardware_udid = entry.get("hardware_udid") or udid
    device_name = entry.get("device_name", "Real Device")

    wda = WdaClient(host=host, port=port)

    # Confirm WDA is reachable before we open a session — surfaces tunnel
    # issues with a clear wda_unreachable error instead of a confusing
    # session-open failure downstream.
    wda.status()

    # Build a Device stub that is compatible with the Session dataclass.
    # os_version is not stored in the registry; use a sentinel so the shape
    # is correct (primitives only read device.udid).
    d = Device(
        udid=udid,           # coredevice UUID — matches the registry filename
        name=device_name,
        os_version=entry.get("os_version", ""),
        state="active",
    )

    if app_bundle_id:
        try:
            from . import device as _device_mod
            _device_mod.launch_app(hardware_udid, app_bundle_id)
        except Exception as exc:
            raise errors.device_launch_failed(
                udid=udid,
                bundle_id=app_bundle_id,
                reason=str(exc),
            )

    # Open a default WDA session so the input verbs (tap/swipe/type_text/
    # press_key) work immediately. Without this, every input call returns
    # wda_session_not_open. When app_bundle_id is provided the session is
    # scoped to that app; otherwise WDA returns a session that targets the
    # current foreground app / home screen.
    wda.open_session(app_bundle_id)

    sid = secrets.token_urlsafe(8)
    workdir = _workroot() / "sessions" / sid
    workdir.mkdir(parents=True, exist_ok=True)
    s = Session(
        session_id=sid,
        device=d,
        workdir=workdir,
        app_bundle_id=app_bundle_id,
        target="device",
        wda_client=wda,
    )
    _SESSIONS[sid] = s
    return s


def get(session_id: str) -> Session:
    s = _SESSIONS.get(session_id)
    if not s:
        raise errors.no_session(session_id)
    return s


def end(session_id: str, terminate_app: bool = True) -> None:
    s = _SESSIONS.pop(session_id, None)
    if not s:
        return
    if terminate_app and s.app_bundle_id:
        try:
            sim.terminate_app(s.device.udid, s.app_bundle_id)
        except Exception:
            pass


def all_sessions() -> list[Session]:
    return list(_SESSIONS.values())
