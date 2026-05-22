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
    pixel_per_point_scale: Optional[float] = None  # cached px/pt scale for WDA coord conversion (F-002/F-006)
    # F#2 — populated when session_start launches an app and verify_launch
    # detects it never reached foreground (i.e. crashed during settle). The
    # tool surface lifts these into the session_start response so the agent
    # sees the failure on the first roundtrip instead of after multiple taps.
    launch_verification: Optional[dict] = None


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


# F#2 — launch-verification settle parameters.
#
# Default total budget: ~3000 ms (10×300 ms). Real iOS apps on fresh sims with
# SwiftUI cold start + first-launch onboarding routinely take 2-4s before
# `launchctl list` shows the bundle. The previous 1500 ms budget produced
# false `launched_then_exited` verdicts on legitimately slow cold-starts.
#
# Configurable via env var ``SIMDRIVE_VERIFY_LAUNCH_BUDGET_MS`` (default 3000,
# clamped to [500, 15000]). Per-poll sleep is fixed at 300 ms; attempts is
# derived as ``ceil(budget_ms / 300)``.
_VERIFY_LAUNCH_SLEEP_S = 0.3
_VERIFY_LAUNCH_BUDGET_DEFAULT_MS = 3000
_VERIFY_LAUNCH_BUDGET_MIN_MS = 500
_VERIFY_LAUNCH_BUDGET_MAX_MS = 15000
# F#2 — crash-report flush race: `ReportCrash` writes .ips asynchronously.
# After we conclude "never reached foreground", give the filesystem one short
# retry before reporting crash_report_path=None.
_CRASH_REPORT_FLUSH_RETRY_S = 0.25


def _verify_launch_attempts() -> int:
    """Read ``SIMDRIVE_VERIFY_LAUNCH_BUDGET_MS`` (clamped) and return the
    number of 300 ms attempts that fit in the budget."""
    raw = os.environ.get("SIMDRIVE_VERIFY_LAUNCH_BUDGET_MS")
    try:
        budget_ms = int(raw) if raw is not None else _VERIFY_LAUNCH_BUDGET_DEFAULT_MS
    except (TypeError, ValueError):
        budget_ms = _VERIFY_LAUNCH_BUDGET_DEFAULT_MS
    budget_ms = max(
        _VERIFY_LAUNCH_BUDGET_MIN_MS,
        min(_VERIFY_LAUNCH_BUDGET_MAX_MS, budget_ms),
    )
    # Per-poll sleep is 300 ms; ceil-divide so the budget is a lower bound.
    attempts = (budget_ms + 299) // 300
    return max(1, int(attempts))


# Back-compat constant (tests / external callers may import it). Reflects the
# default budget, not the env-overridden value.
_VERIFY_LAUNCH_ATTEMPTS = (
    _VERIFY_LAUNCH_BUDGET_DEFAULT_MS + 299
) // 300


def start(
    device_name: Optional[str] = None,
    os_version: Optional[str] = None,
    udid: Optional[str] = None,
    app_bundle_id: Optional[str] = None,
    target: str = "simulator",
    verify_launch: bool = True,
) -> Session:
    """Find or boot a sim/device, start a session.

    target="simulator" (default): existing simulator behavior.
    target="device": resolve a connected real device by udid.

    verify_launch (F#2): when True (default) and ``app_bundle_id`` is
    provided, poll ``diagnostics.app_state`` (sim) or
    ``diagnostics.app_state_device`` (device) up to ~3000 ms after launch.
    Settle budget is env-tunable via ``SIMDRIVE_VERIFY_LAUNCH_BUDGET_MS``
    (default 3000, clamped to [500, 15000]).  Crash declaration requires
    TWO consecutive ``not-running`` polls — single launchctl flakes never
    trip the verdict, and per-poll exceptions are caught and retried.
    The Session is created either way, but its ``state`` will be set to
    ``"launched_then_exited"`` (with a ``crash_report_path`` populated
    from the most recent .ips for the bundle — simulator path only) when
    the app never reaches foreground. On device, when devicectl's process
    list is unavailable, falls back to ``state="active"`` with a
    verification-unavailable warning rather than mis-declaring a crash.
    Pass False to keep the legacy fire-and-forget behaviour.
    """
    if target == "device":
        return _start_device(
            udid=udid,
            app_bundle_id=app_bundle_id,
            verify_launch=verify_launch,
        )

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

    launch_ts = time.time()
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

    # F#2 — verify the launched app actually reached foreground. Without
    # this, an app that crashes within ~500 ms of launch (e.g. missing
    # entitlement) yielded state="active" and burned multiple agent
    # roundtrips before the failure was visible.
    if app_bundle_id and verify_launch:
        result = _verify_launch(d.udid, app_bundle_id, launch_ts)
        if result is not None:
            s.state = result["state"]
            s.launch_verification = result  # type: ignore[attr-defined]

    _SESSIONS[sid] = s
    return s


def _verify_launch(udid: str, bundle_id: str, launch_ts: float) -> Optional[dict]:
    """Poll app_state to confirm the launched app reached foreground.

    Returns None on success (caller leaves state="active" alone). Returns
    a dict ``{"state": "launched_then_exited", "crash_report_path": ...,
    "recovery": "..."}`` when the app never reached foreground within
    the settle budget — the caller stamps these onto the Session and the
    tool surface so the agent sees the failure on the first roundtrip.

    Crash declaration rules (review feedback, PR #144):
      * `app_state` is a presence-based heuristic over `launchctl list`. A
        single transient `not-running` (launchctl flake, race with the
        forking guest process) is NOT enough to declare a crash.
      * Require TWO CONSECUTIVE `not-running` polls before concluding
        ``launched_then_exited``. Any `foreground` resets the streak.
      * Per-poll exceptions are caught and retried — never propagate the
        first `app_state` failure.

    Imports are local so this stays cheap for callers that pass
    ``verify_launch=False``.
    """
    from . import diagnostics as _diag

    attempts = _verify_launch_attempts()
    last_state = "unknown"
    consecutive_not_running = 0
    crashed = False
    for attempt in range(attempts):
        try:
            info = _diag.app_state(udid, bundle_id)
            last_state = info.get("state", "not-running")
        except Exception:
            # Soft-fail on the underlying helper — DO NOT propagate.
            # Treat as "unknown" so it neither confirms foreground nor
            # advances the not-running streak; the next poll decides.
            last_state = "unknown"

        if last_state == "foreground":
            return None
        if last_state == "not-running":
            consecutive_not_running += 1
            if consecutive_not_running >= 2:
                crashed = True
                break
        else:
            # Any non-foreground, non-not-running result (e.g. "unknown" from
            # a transient exception) does NOT advance the crash streak.
            consecutive_not_running = 0

        # Don't sleep after the final poll — we're about to return.
        if attempt < attempts - 1:
            time.sleep(_VERIFY_LAUNCH_SLEEP_S)

    if not crashed:
        # Budget exhausted without two consecutive not-running polls. The app
        # is slow but apparently alive (or app_state is flaky). Don't declare
        # a crash — leave the session active so the agent can proceed.
        return None

    # Two consecutive not-running polls = real crash. Look for a crash report
    # newer than launch. ReportCrash writes asynchronously, so on a fresh
    # miss retry once after a short flush window.
    crash_path = _find_crash_report(_diag, launch_ts, bundle_id)
    if crash_path is None:
        time.sleep(_CRASH_REPORT_FLUSH_RETRY_S)
        crash_path = _find_crash_report(_diag, launch_ts, bundle_id)

    budget_ms = int(attempts * _VERIFY_LAUNCH_SLEEP_S * 1000)
    recovery = (
        f"App crashed within {budget_ms}ms of launch. "
        "See crash_report_path for stack."
    )
    return {
        "state": "launched_then_exited",
        "crash_report_path": crash_path,
        "recovery": recovery,
        "last_observed_state": last_state,
    }


def _find_crash_report(
    diag_mod, launch_ts: float, bundle_id: str,
) -> Optional[str]:
    """Best-effort lookup of the most recent crash .ips for ``bundle_id`` since
    ``launch_ts``. Returns the path string or None. Never raises."""
    try:
        crashes = diag_mod.list_crashes(
            since_ts=launch_ts,
            bundle_id=bundle_id,
            max_results=1,
        )
    except Exception:
        return None
    if crashes:
        return crashes[0].get("path")
    return None


def _start_device(
    udid: Optional[str],
    app_bundle_id: Optional[str],
    verify_launch: bool = True,
) -> Session:
    """Start a real-device WDA session.

    Reads the WDA registry entry written by ``simdrive bootstrap-device`` to
    discover the host:port where WebDriverAgent is serving, then creates a
    Session pointing at that endpoint.  Never calls ``devicectl list`` — the
    registry is the single source of truth for device connectivity once WDA has
    been bootstrapped.

    ``verify_launch`` (F#2, review feedback PR #144): when True and a bundle
    is provided, poll ``diagnostics.app_state_device`` after the launch using
    the same 2-consecutive-not-running rule as the simulator path. If WDA /
    devicectl can't answer (e.g. process list unavailable), gracefully fall
    back to ``state="active"`` with a verification-unavailable warning rather
    than misreport a crash.
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

    launch_ts = time.time()
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

    # F#2 — device launch verification using app_state_device (devicectl
    # process list). If the helper can't answer (no DDI / older Xcode), we
    # surface a warning rather than mis-declaring a crash.
    if app_bundle_id and verify_launch:
        result = _verify_launch_device(
            hardware_udid, app_bundle_id, launch_ts,
        )
        if result is not None:
            s.state = result["state"]
            s.launch_verification = result  # type: ignore[attr-defined]

    _SESSIONS[sid] = s
    return s


def _verify_launch_device(
    hardware_udid: str, bundle_id: str, launch_ts: float,
) -> Optional[dict]:
    """Device counterpart to ``_verify_launch``.

    Polls ``diagnostics.app_state_device`` (which scans devicectl's
    process list). The success state for the device path is ``"running"``
    rather than ``"foreground"`` — devicectl can't distinguish fg/bg.

    Same 2-consecutive-not-running rule as the simulator path. If every
    poll raises (e.g. DDI not mounted), returns a verification-unavailable
    result with ``state="active"`` — we do NOT mis-declare a crash when
    we simply can't see the process list.
    """
    from . import diagnostics as _diag

    attempts = _verify_launch_attempts()
    last_state = "unknown"
    consecutive_not_running = 0
    crashed = False
    exception_streak = 0
    for attempt in range(attempts):
        try:
            info = _diag.app_state_device(hardware_udid, bundle_id)
            last_state = info.get("state", "not-running")
            exception_streak = 0
        except Exception:
            last_state = "unknown"
            exception_streak += 1

        # Device helper returns "running" for foreground/background indistinct.
        if last_state == "running":
            return None
        if last_state == "not-running":
            consecutive_not_running += 1
            if consecutive_not_running >= 2:
                crashed = True
                break
        else:
            consecutive_not_running = 0

        if attempt < attempts - 1:
            time.sleep(_VERIFY_LAUNCH_SLEEP_S)

    # If every poll raised we have no real signal — fall back to active with
    # a warning rather than claim a crash we never observed.
    if exception_streak >= attempts:
        return {
            "state": "active",
            "crash_report_path": None,
            "recovery": (
                "Launch verification unavailable on this device "
                "(devicectl process list could not be queried). "
                "Session is active but the launch was not verified."
            ),
            "last_observed_state": last_state,
            "verification_available": False,
        }

    if not crashed:
        return None

    budget_ms = int(attempts * _VERIFY_LAUNCH_SLEEP_S * 1000)
    recovery = (
        f"App appears to have exited within {budget_ms}ms of launch on the "
        "real device. Pull a sysdiagnose / crash log via Xcode → Devices."
    )
    return {
        "state": "launched_then_exited",
        "crash_report_path": None,  # devicectl doesn't surface .ips on-device
        "recovery": recovery,
        "last_observed_state": last_state,
    }


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
