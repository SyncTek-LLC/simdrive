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
    """Start a real-device session. Touch input is unavailable in v0.2.x; observe + logs only."""
    from . import device  # local import to avoid hard requirement when target=simulator
    if not udid:
        raise errors.no_device({"target": "device", "any_booted": True})
    rd = device.find_device(udid)
    if not rd:
        raise errors.no_device({"target": "device", "udid": udid})
    # Treat the real device as a Device for type compatibility
    d = Device(udid=rd.udid, name=rd.name, os_version=rd.model, state="active")

    if app_bundle_id:
        try:
            device.launch_app(rd.udid, app_bundle_id)
        except device.DeviceError as exc:
            raise errors.no_device({"target": "device", "udid": udid,
                                    "launch_failed": str(exc)})

    sid = secrets.token_urlsafe(8)
    workdir = _workroot() / "sessions" / sid
    workdir.mkdir(parents=True, exist_ok=True)
    s = Session(
        session_id=sid,
        device=d,
        workdir=workdir,
        app_bundle_id=app_bundle_id,
        target="device",
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
