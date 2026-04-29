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


def start(
    device_name: Optional[str] = None,
    os_version: Optional[str] = None,
    udid: Optional[str] = None,
    app_bundle_id: Optional[str] = None,
) -> Session:
    """Find or boot a sim, start a session.

    Resolution order:
      1. udid given → use it
      2. device_name given → first match (preferring booted)
      3. neither → first booted sim
    """
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
