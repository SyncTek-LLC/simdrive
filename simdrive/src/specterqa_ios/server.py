"""SpecterQA for iOS MCP server. (Internal codename: simdrive.)

Exposes the MCP tool surface to any compatible host (Claude, Cline, etc.):

  Lifecycle:  session_start, session_end, session_status
  Observe:    observe
  Act:        tap, swipe, type_text, press_key
  Record:     record_start, record_stop, replay
  Utility:    logs

Run:
    specterqa-ios
    # or (legacy alias, still works)
    simdrive
    # or
    python -m specterqa_ios.server

Add to .mcp.json:
    {
      "mcpServers": {
        "specterqa-ios": { "command": "specterqa-ios" }
      }
    }
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from . import (
    __version__, act, diagnostics, errors, observe, perf, recorder,
    robustness, session, sim, som,
)


def _now() -> float:
    return time.time()


# v0.3.0a3 — module-load timestamp + cached disk-version probe.
# The whole point: catch the case where `pip install --upgrade simdrive`
# refreshes the wheel on disk but the running MCP server is still serving
# the old in-memory code. Every tool response gets a `_simdrive_warning`
# side-channel field when drift is detected.
_LOADED_AT: float = time.time()
_LOADED_VERSION: str = __version__
_DISK_VERSION_CACHE: dict[str, float | str | None] = {"version": None, "checked_at": 0.0}
_DISK_VERSION_TTL_SEC = 5.0


def _disk_version() -> str | None:
    """Read simdrive's on-disk package version, cached for 5s.

    Returns None when importlib.metadata can't find the package (dev installs
    without metadata) — that's not drift, just unknown.
    """
    now = time.time()
    last_at = float(_DISK_VERSION_CACHE.get("checked_at") or 0.0)
    if now - last_at < _DISK_VERSION_TTL_SEC and _DISK_VERSION_CACHE.get("version") is not None:
        return _DISK_VERSION_CACHE["version"]  # type: ignore[return-value]
    try:
        import importlib.metadata as _md
        v = _md.version("specterqa-ios")
    except Exception:
        v = None
    _DISK_VERSION_CACHE["version"] = v
    _DISK_VERSION_CACHE["checked_at"] = now
    return v


def _check_version_drift() -> str | None:
    """Return a warning string when loaded != disk; else None.

    Mounted on every call_tool response so an agent stuck on a stale server
    sees the issue on the very next tool call after `pip install --upgrade`.
    """
    disk = _disk_version()
    if disk is None:
        return None
    if disk == _LOADED_VERSION:
        return None
    return (
        f"Loaded simdrive {_LOADED_VERSION} but disk version is {disk}. "
        "Restart the MCP server (or your agent host) to pick up the upgrade."
    )


# --------------------------- Tool implementations --------------------------- #


def tool_session_start(arguments: dict) -> dict:
    device_name = arguments.get("device") or arguments.get("device_name")
    os_version = arguments.get("os_version")
    udid = arguments.get("udid")
    app_bundle_id = arguments.get("app_bundle_id")
    target = arguments.get("target", "simulator")
    if target not in ("simulator", "device"):
        raise errors.invalid_argument("target", target,
                                       "must be 'simulator' or 'device'")
    s = session.start(
        device_name=device_name, os_version=os_version, udid=udid,
        app_bundle_id=app_bundle_id, target=target,
    )
    return {
        "session_id": s.session_id,
        "udid": s.device.udid,
        "device": s.device.name,
        "os_version": s.device.os_version,
        "app_bundle_id": s.app_bundle_id,
        "state": s.state,
        "target": s.target,
    }


def tool_session_end(arguments: dict) -> dict:
    sid = arguments["session_id"]
    session.end(sid, terminate_app=bool(arguments.get("terminate_app", True)))
    return {"ended": sid}


def tool_session_status(arguments: dict) -> dict:
    from . import act as _act
    sid = arguments.get("session_id")
    if sid:
        s = session.get(sid)
        sessions = [s]
    else:
        sessions = session.all_sessions()
    backend = _act._backend()
    return {
        "sessions": [
            {
                "session_id": s.session_id,
                "udid": s.device.udid,
                "device": s.device.name,
                "os_version": s.device.os_version,
                "state": s.state,
                "app_bundle_id": s.app_bundle_id,
                "last_action_at": s.last_action_at,
                "recording": s.recorder.name if s.recorder else None,
                "last_marks": len(s.last_marks or []),
            }
            for s in sessions
        ],
        "version": __version__,
        "mode": "background" if backend == "hid" else "foreground",
        "mode_note": (
            "Running in background mode — your foreground app keeps focus."
            if backend == "hid"
            else "Simulator will be brought to front on each action."
        ),
    }


def tool_observe(arguments: dict) -> dict:
    sid = arguments["session_id"]
    s = session.get(sid)
    obs = observe.observe(
        s.device.udid,
        s.workdir / "observations",
        annotate=bool(arguments.get("annotate", True)),
        capture_logs=bool(arguments.get("capture_logs", False)),
        log_lines=int(arguments.get("log_lines", 50)),
        log_predicate=arguments.get("log_predicate"),
        target=s.target,
    )
    s.last_screenshot_w = obs.screenshot_w
    s.last_screenshot_h = obs.screenshot_h
    s.last_screenshot_path = obs.screenshot_path
    # Only overwrite the mark cache when this observe actually produced marks.
    # observe(annotate=False) returns marks=[] and used to wipe the cache, breaking
    # subsequent tap text=/mark=/stable_id= calls with "no marks available."
    if obs.marks:
        s.last_marks = obs.marks
    s.last_action_at = _now()
    return obs.to_dict()


def _ensure_screenshot_dims(s) -> tuple[int, int]:
    if s.last_screenshot_w == 0 or s.last_screenshot_h == 0:
        # Auto-observe so the agent can call act tools without first calling observe.
        obs = observe.observe(s.device.udid, s.workdir / "observations")
        s.last_screenshot_w = obs.screenshot_w
        s.last_screenshot_h = obs.screenshot_h
        s.last_screenshot_path = obs.screenshot_path
        s.last_marks = obs.marks
    return s.last_screenshot_w, s.last_screenshot_h


def _resolve_target_xy(s, args: dict) -> tuple[int, int, str, "som.Mark | None"]:
    """Translate {x,y} | {mark} | {text} | {stable_id} into pixel coords + debug 'how' + matched Mark.

    The 4th element is the matched `som.Mark` for mark/stable_id/text resolutions, or
    `None` for raw {x, y} resolutions. Callers that need to record `stable_id` alongside
    pixel coords (so replay can re-resolve against the live screen) read it from there.
    """
    if "x" in args and "y" in args:
        return int(args["x"]), int(args["y"]), "coords", None

    if "mark" in args:
        mark_id = int(args["mark"])
        m = som.find_by_mark_id(s.last_marks or [], mark_id)
        if not m:
            available = [{"id": mk.id, "text": mk.text} for mk in (s.last_marks or [])]
            raise errors.target_not_found("mark", mark_id, available)
        cx, cy = m.center
        return cx, cy, f"mark:{mark_id}({m.text!r})", m

    if "stable_id" in args:
        sid_q = str(args["stable_id"])
        m = som.find_by_stable_id(s.last_marks or [], sid_q)
        if not m:
            available = [{"stable_id": mk.stable_id, "text": mk.text}
                         for mk in (s.last_marks or [])]
            raise errors.target_not_found("stable_id", sid_q, available)
        cx, cy = m.center
        return cx, cy, f"stable_id:{sid_q}({m.text!r})", m

    if "stable_id_loose" in args:
        sid_q = str(args["stable_id_loose"])
        m = som.find_by_stable_id_loose(s.last_marks or [], sid_q)
        if not m:
            available = [{"stable_id_loose": mk.stable_id_loose, "text": mk.text}
                         for mk in (s.last_marks or [])]
            raise errors.target_not_found("stable_id_loose", sid_q, available)
        cx, cy = m.center
        return cx, cy, f"stable_id_loose:{sid_q}({m.text!r})", m

    if "text" in args:
        query = str(args["text"])
        m = som.find_by_text(s.last_marks or [], query)
        if not m:
            available = [mk.text for mk in (s.last_marks or [])]
            raise errors.target_not_found("text", query, available)
        cx, cy = m.center
        return cx, cy, f"text:{query!r}->mark:{m.id}", m

    raise errors.missing_target()


def _record_act_step(s, action: str, args: dict, pre_path: Path) -> int | None:
    if s.recorder is None:
        return None
    # Capture post-screenshot for the recording.
    post_obs = observe.observe(s.device.udid, s.workdir / "observations")
    s.last_screenshot_w = post_obs.screenshot_w
    s.last_screenshot_h = post_obs.screenshot_h
    s.last_screenshot_path = post_obs.screenshot_path
    return s.recorder.add_step(action, args, pre_path, post_obs.screenshot_path)


def tool_tap(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    if s.target == "device":
        raise errors.device_input_unavailable("tap")
    sw, sh = _ensure_screenshot_dims(s)
    x, y, resolved_via, matched_mark = _resolve_target_xy(s, arguments)
    pre_path = s.last_screenshot_path
    sx, sy = act.tap(x, y, sw, sh, udid=s.device.udid)
    s.last_action_at = _now()
    args = {"x": x, "y": y, "screenshot_w": sw, "screenshot_h": sh}
    # Persist stable_id + stable_id_loose + text alongside pixel coords so replay
    # can re-resolve against the live screen (a 1px layout shift no longer silently
    # mistaps; loose covers the >3px shifts that escape the tight 20px bucket).
    if matched_mark is not None:
        args["stable_id"] = matched_mark.stable_id
        args["stable_id_loose"] = matched_mark.stable_id_loose
        args["text"] = matched_mark.text
    step_id = None
    if pre_path:
        step_id = _record_act_step(s, "tap", args, pre_path)
    session.append_action(s, {
        "action": "tap",
        "args": dict(arguments),
        "resolved": {"pixel_x": x, "pixel_y": y, "via": resolved_via},
        "at": _now(),
    })
    response = {
        "ok": True,
        "pixel_x": x,
        "pixel_y": y,
        "screen_x": sx,
        "screen_y": sy,
        "screenshot_size_pixels": [sw, sh],
        "resolved_via": resolved_via,
    }
    if step_id is not None:
        response["step_id"] = step_id
    return response


def tool_swipe(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    if s.target == "device":
        raise errors.device_input_unavailable("swipe")
    duration_ms = int(arguments.get("duration_ms", 300))
    sw, sh = _ensure_screenshot_dims(s)

    # swipe accepts either explicit endpoints {x1,y1,x2,y2} or {from: target, to: target}
    if "x1" in arguments and "y1" in arguments and "x2" in arguments and "y2" in arguments:
        x1, y1 = int(arguments["x1"]), int(arguments["y1"])
        x2, y2 = int(arguments["x2"]), int(arguments["y2"])
        resolved_via = "coords"
    elif "from" in arguments and "to" in arguments:
        x1, y1, _, _ = _resolve_target_xy(s, arguments["from"])
        x2, y2, _, _ = _resolve_target_xy(s, arguments["to"])
        resolved_via = "from/to"
    else:
        raise errors.invalid_argument("swipe", arguments,
                                       "requires {x1,y1,x2,y2} or {from: target, to: target}")

    # Home-indicator guard rail: any swipe ending in the bottom strip is
    # interpreted by iOS as the home-indicator gesture and exits the app.
    warnings: list[str] = []
    home_zone_top = sh - max(80, int(sh * 0.04))
    if y2 >= home_zone_top:
        warnings.append(
            f"swipe end y={y2} is in the home-indicator zone (y >= {home_zone_top}); "
            "iOS will likely interpret this as the home gesture and exit the app. "
            "Suggested: cap y2 at {home_zone_top - 1}."
        )

    pre_path = s.last_screenshot_path
    act.swipe(x1, y1, x2, y2, sw, sh, duration_ms, udid=s.device.udid)
    s.last_action_at = _now()
    args = {
        "x1": x1, "y1": y1, "x2": x2, "y2": y2,
        "screenshot_w": sw, "screenshot_h": sh, "duration_ms": duration_ms,
    }
    step_id = None
    if pre_path:
        step_id = _record_act_step(s, "swipe", args, pre_path)
    session.append_action(s, {
        "action": "swipe",
        "args": dict(arguments),
        "resolved": {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "via": resolved_via},
        "warnings": warnings,
        "at": _now(),
    })
    response: dict = {"ok": True, "resolved_via": resolved_via}
    if warnings:
        response["warnings"] = warnings
    if step_id is not None:
        response["step_id"] = step_id
    return response


def tool_type_text(arguments: dict) -> dict:
    from . import hid_inject
    s = session.get(arguments["session_id"])
    if s.target == "device":
        raise errors.device_input_unavailable("type_text")
    text = str(arguments["text"])
    tap_target = arguments.get("tap_first")  # optional target dict to focus a field first
    clear_first = bool(arguments.get("clear_first", False))

    focused_mark = None  # Mark of the tap_first target if resolved via mark/stable_id/text
    if tap_target:
        sw, sh = _ensure_screenshot_dims(s)
        tx, ty, _, focused_mark = _resolve_target_xy(s, tap_target)
        act.tap(tx, ty, sw, sh, udid=s.device.udid)
        import time as _t
        _t.sleep(0.6)  # give the keyboard a moment to come up

    # v0.3.0a3 — clear_first sends Cmd-A then delete BEFORE typing the new text.
    # Replaces the five-press_key idiom for resetting search fields. Done after
    # the focus tap so the field is the active first responder.
    if clear_first:
        if s.device.udid:
            try:
                hid_inject.chord(s.device.udid, "cmd", "a")
            except Exception:
                pass
            try:
                act.press_key("delete", udid=s.device.udid)
            except Exception:
                pass

    pre_obs = observe.observe(s.device.udid, s.workdir / "observations") if s.recorder else None
    pre_path = pre_obs.screenshot_path if pre_obs else s.last_screenshot_path
    act.type_text(text, udid=s.device.udid)
    dispatch_succeeded = True  # type_text only reaches here if act.type_text didn't raise
    backend_used = act._backend()  # capture which backend actually dispatched
    s.last_action_at = _now()
    step_id = None
    if pre_path:
        step_id = _record_act_step(s, "type_text", {"text": text}, pre_path)
    session.append_action(s, {
        "action": "type_text",
        "args": {"text": text, "tap_first": tap_target, "clear_first": clear_first},
        "at": _now(),
    })

    # Post-type observe so the caller can verify the field accepted focus without
    # having to chain an extra observe() call. Heuristic: keyboard chrome shows
    # well-known key labels OR a row of 1-2 char marks in the bottom 45% of the screen.
    post_obs = observe.observe(s.device.udid, s.workdir / "observations", annotate=True)
    s.last_screenshot_w = post_obs.screenshot_w
    s.last_screenshot_h = post_obs.screenshot_h
    s.last_screenshot_path = post_obs.screenshot_path
    if post_obs.marks:
        s.last_marks = post_obs.marks

    keyboard_chrome_words = {"return", "search", "go", "next", "done", "shift", "delete", "space"}
    keyboard_visible = False
    bottom_threshold = post_obs.screenshot_h * 0.55
    short_marks_in_bottom = 0
    for mk in post_obs.marks:
        t = (mk.text or "").strip().lower()
        if t in keyboard_chrome_words:
            keyboard_visible = True
            break
        if 1 <= len(t) <= 2 and mk.y > bottom_threshold:
            short_marks_in_bottom += 1
    # Two or more short-text marks in the bottom half → likely keyboard key row.
    if not keyboard_visible and short_marks_in_bottom >= 2:
        keyboard_visible = True

    focused_field = focused_mark.stable_id if focused_mark is not None else None

    response = {
        "ok": True,
        "chars": len(text),
        # v0.3.0a3 — `injection_method` and `dispatch_succeeded` are the
        # reliable signals under HID. The legacy `keyboard_visible` /
        # `focused_field` heuristics stay because they're still useful on the
        # cliclick path (where the soft keyboard IS drawn) — but under HID the
        # keystrokes always land even though the soft keyboard isn't visible,
        # so don't trust the heuristic alone.
        "injection_method": backend_used,
        "dispatch_succeeded": dispatch_succeeded,
        "keyboard_visible": keyboard_visible,
        "focused_field": focused_field,
    }
    if step_id is not None:
        response["step_id"] = step_id
    return response


def tool_press_key(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    if s.target == "device":
        raise errors.device_input_unavailable("press_key")
    key = str(arguments["key"])
    pre_obs = observe.observe(s.device.udid, s.workdir / "observations") if s.recorder else None
    pre_path = pre_obs.screenshot_path if pre_obs else s.last_screenshot_path
    act.press_key(key, udid=s.device.udid)
    s.last_action_at = _now()
    step_id = None
    if pre_path:
        step_id = _record_act_step(s, "press_key", {"key": key}, pre_path)
    session.append_action(s, {"action": "press_key", "args": {"key": key}, "at": _now()})
    response = {"ok": True, "key": key}
    if step_id is not None:
        response["step_id"] = step_id
    return response


def tool_record_start(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    name = str(arguments["name"])
    tags = arguments.get("tags") or []
    if not isinstance(tags, list):
        raise errors.invalid_argument("tags", tags, "must be a list of strings")
    rec = recorder.start(s, name, tags=[str(t) for t in tags])
    return {"ok": True, "name": rec.name, "path": str(rec.root), "tags": list(rec.tags)}


def tool_record_stop(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    if s.recorder is None:
        return {"ok": False, "error": "not recording"}
    name = s.recorder.name
    step_count = len(s.recorder.steps)
    yaml_path = recorder.stop(s)
    return {"ok": True, "name": name, "steps": step_count, "yaml_path": str(yaml_path)}


def tool_replay(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    name = str(arguments["name"])
    on_drift = str(arguments.get("on_drift", "halt"))
    threshold = float(arguments.get("drift_threshold", 0.85))
    mask_regions = arguments.get("mask_regions")
    return recorder.replay(name, s, on_drift=on_drift, drift_threshold=threshold,
                           mask_regions=mask_regions)


def tool_list_devices(arguments: dict) -> dict:
    """Enumerate real devices reachable via Apple devicectl + libimobiledevice."""
    from . import device
    ok, missing = device.libimobiledevice_available()
    devs = []
    err: dict | None = None
    try:
        for d in device.list_devices():
            # hid_supported is always False for real devices in v0.2.x — input
            # routes through WDA which is not yet shipped. Sim sessions are
            # the only HID-capable target.
            devs.append({
                "udid": d.udid,
                "name": d.name,
                "model": d.model,
                "transport": d.transport,
                "state": d.state,
                "hid_supported": False,
                "last_seen": d.last_seen,
                "unavailable_reason": d.unavailable_reason,
            })
    except device.DeviceError as exc:
        err = {"code": "discovery_failed", "message": str(exc)}
    return {
        "ok": err is None,
        "devices": devs,
        "libimobiledevice_ready": ok,
        "missing_tools": missing,
        "hid_note": (
            "Real-device HID injection is not yet implemented. Use simulators "
            "for tap/swipe/type_text/press_key. WDA-based real-device input is "
            "on the v0.3 roadmap."
        ),
        "error": err,
    }


def tool_logs(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    lines = int(arguments.get("lines", 200))
    predicate = arguments.get("predicate")
    if s.target == "device":
        from . import device
        text = device.get_log_tail(s.device.udid, lines=lines, predicate=predicate)
    else:
        text = sim.get_log_tail(s.device.udid, lines=lines, predicate=predicate)
    return {"ok": True, "lines": len(text.splitlines()), "logs": text}


# --------------------- Performance / diagnostics / robustness ----------- #


def _resolve_bundle_id(s, arguments: dict) -> str:
    bid = arguments.get("app_bundle_id") or s.app_bundle_id
    if not bid:
        raise errors.invalid_argument(
            "app_bundle_id", None,
            "no bundle_id on session and none provided in arguments",
        )
    return bid


def tool_perf(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    bundle_id = _resolve_bundle_id(s, arguments)
    snap = perf.snapshot(s.device.udid, bundle_id)
    if snap.get("pid") is None:
        raise errors.SimdriveError(
            code="app_not_running",
            message=f"no PID found for {bundle_id} on {s.device.udid}",
            details={"bundle_id": bundle_id, "udid": s.device.udid},
        )
    s.last_action_at = _now()
    return snap


def tool_perf_baseline(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    bundle_id = _resolve_bundle_id(s, arguments)
    label = str(arguments.get("label") or "default")
    snap = perf.snapshot(s.device.udid, bundle_id)
    if snap.get("pid") is None:
        raise errors.SimdriveError(
            code="app_not_running",
            message=f"no PID found for {bundle_id} — cannot capture baseline",
            details={"bundle_id": bundle_id, "udid": s.device.udid},
        )
    record = {"label": label, **snap}
    s.perf_baselines[label] = record
    return record


def tool_perf_compare(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    bundle_id = _resolve_bundle_id(s, arguments)
    label = str(arguments.get("label") or "default")
    baseline = s.perf_baselines.get(label)
    if baseline is None:
        raise errors.SimdriveError(
            code="no_baseline",
            message=f"no baseline labeled {label!r}; call perf_baseline first.",
            details={"label": label, "available": list(s.perf_baselines)},
        )
    current = perf.snapshot(s.device.udid, bundle_id)
    delta = {
        "cpu_pct": round(current["cpu_pct"] - baseline["cpu_pct"], 2),
        "memory_rss_mb": round(current["memory_rss_mb"] - baseline["memory_rss_mb"], 2),
        "threads": current["threads"] - baseline["threads"],
    }
    return {
        "baseline": baseline,
        "current": current,
        "delta": delta,
        "severity": perf.severity(delta),
    }


def tool_memory(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    bundle_id = _resolve_bundle_id(s, arguments)
    return perf.memory_detail(s.device.udid, bundle_id)


def tool_doctor(arguments: dict) -> dict:
    return diagnostics.doctor()


def tool_app_state(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    bundle_id = _resolve_bundle_id(s, arguments)
    return diagnostics.app_state(s.device.udid, bundle_id)


def tool_apps(arguments: dict) -> dict:
    udid = arguments.get("udid")
    if not udid:
        sid = arguments.get("session_id")
        if not sid:
            raise errors.invalid_argument(
                "session_id|udid", None,
                "supply either session_id or a literal udid",
            )
        s = session.get(sid)
        udid = s.device.udid
    return {"apps": diagnostics.list_apps(udid)}


def tool_crashes(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    since = bool(arguments.get("since_session_start", True))
    since_ts = s.started_at if since else 0.0
    bundle_id = arguments.get("app_bundle_id") or s.app_bundle_id  # optional
    max_results = int(arguments.get("max", 10))
    crashes = diagnostics.list_crashes(
        since_ts=since_ts, bundle_id=bundle_id, max_results=max_results,
    )
    return {"crashes": crashes}


def tool_dismiss_first_launch_alerts(arguments: dict) -> dict:
    """Tap permission-alert buttons; re-observe after each tap and retry once.

    Why retry: ~1-in-4 alert taps on iOS 26 fall through to the underlying view
    because SpringBoard hands off alert ownership while the tap is in flight.
    Re-observing 200 ms post-tap and retrying when the alert text persists
    closes that window without inflating the no-alert path.
    """
    import time as _t
    s = session.get(arguments["session_id"])
    if s.target == "device":
        raise errors.device_input_unavailable("dismiss_first_launch_alerts")
    choice = str(arguments.get("choice", "allow"))
    if choice not in ("allow", "deny"):
        raise errors.invalid_argument("choice", choice, "must be 'allow' or 'deny'")
    retries = int(arguments.get("retries", 1))

    dismissed = 0
    attempts = 0
    while True:
        obs = observe.observe(s.device.udid, s.workdir / "observations")
        s.last_screenshot_w = obs.screenshot_w
        s.last_screenshot_h = obs.screenshot_h
        s.last_screenshot_path = obs.screenshot_path
        if obs.marks:
            s.last_marks = obs.marks
        target_mark = robustness.alert_button_match(obs.marks, choice)
        if target_mark is None:
            break
        attempts += 1
        cx, cy = target_mark.center
        try:
            act.tap(cx, cy, obs.screenshot_w, obs.screenshot_h, udid=s.device.udid)
            dismissed += 1
        except Exception:
            pass
        _t.sleep(0.2)
        if attempts > retries:
            break
    s.last_action_at = _now()
    return {"ok": True, "dismissed": dismissed, "attempts": attempts}


def tool_pre_grant_permissions(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    bundle_id = _resolve_bundle_id(s, arguments)
    perms = arguments.get("permissions") or []
    if not isinstance(perms, list) or not perms:
        raise errors.invalid_argument("permissions", perms, "must be a non-empty list")
    return robustness.grant_permissions(s.device.udid, bundle_id, [str(p) for p in perms])


def tool_set_appearance(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    appearance = str(arguments.get("appearance", "light"))
    return robustness.set_appearance(s.device.udid, appearance)


def tool_dismiss_sheet(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    if s.target == "device":
        raise errors.device_input_unavailable("dismiss_sheet")
    sw, sh = _ensure_screenshot_dims(s)
    x_mid = sw // 2
    y_start = int(sh * 0.2)
    y_end = int(sh * 0.7)
    act.swipe(x_mid, y_start, x_mid, y_end, sw, sh, 300, udid=s.device.udid)
    s.last_action_at = _now()
    return {"ok": True}


def tool_list_replays(arguments: dict) -> dict:
    return {"replays": robustness.list_replays(recorder.recordings_root())}


def tool_validate_replay(arguments: dict) -> dict:
    name = str(arguments["name"])
    return robustness.validate_replay(recorder.recordings_root(), name)


# ─── v0.3.0a3 ─────────────────────────────────────────────────────────── #


def tool_version(arguments: dict) -> dict:
    """Report the loaded vs. on-disk simdrive version. Zero-arg.

    `drift=True` means the running MCP server is stale relative to what's on
    disk (after `pip install --upgrade simdrive` without restarting). The
    fix is to restart the agent host / MCP server so the new code is loaded.
    """
    disk = _disk_version()
    return {
        "version": _LOADED_VERSION,
        "loaded_at": _LOADED_AT,
        "disk_version": disk,
        "drift": (disk is not None and disk != _LOADED_VERSION),
    }


def tool_clear_field(arguments: dict) -> dict:
    """Clear a focused text field by sending Cmd-A then delete via HID.

    Useful when the agent wants to reset a search field without immediately
    typing replacement text. If a `target` is given, tap it first to ensure
    the field has first-responder focus before the chord.
    """
    from . import hid_inject
    s = session.get(arguments["session_id"])
    if s.target == "device":
        raise errors.device_input_unavailable("clear_field")
    target = arguments.get("target")
    if target:
        sw, sh = _ensure_screenshot_dims(s)
        tx, ty, _, _ = _resolve_target_xy(s, target)
        act.tap(tx, ty, sw, sh, udid=s.device.udid)
        import time as _t
        _t.sleep(0.5)  # let focus settle before the chord
    cleared = False
    try:
        hid_inject.chord(s.device.udid, "cmd", "a")
        act.press_key("delete", udid=s.device.udid)
        cleared = True
    except Exception:
        cleared = False
    s.last_action_at = _now()
    session.append_action(s, {
        "action": "clear_field",
        "args": {"target": target},
        "at": _now(),
    })
    return {"ok": cleared, "cleared": cleared}


# ----------------------------- MCP wiring ------------------------------- #


# Tool name → (handler, json schema for arguments, description)
_TOOLS: list[dict] = [
    {
        "name": "session_start",
        "description": (
            "Boot/find an iOS simulator (or attach to a real device), optionally "
            "launch an app, and start a simdrive session. Returns session_id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "enum": ["simulator", "device"], "default": "simulator", "description": "'simulator' (default) or 'device' for a real iPhone/iPad. Real-device sessions support observe + logs + app lifecycle; tap/swipe/type_text/press_key require WebDriverAgent (v0.2 roadmap)."},
                "device": {"type": "string", "description": "Device name, e.g. 'iPhone 17 Pro'. Optional if a sim is already booted."},
                "os_version": {"type": "string", "description": "iOS version, e.g. '26.3'. Optional."},
                "udid": {"type": "string", "description": "Simulator UDID, or real-device UDID when target='device'."},
                "app_bundle_id": {"type": "string", "description": "Optional bundle id to launch after boot, e.g. 'com.apple.Preferences'."},
            },
        },
        "handler": tool_session_start,
    },
    {
        "name": "session_end",
        "description": "End a simdrive session. Optionally terminate the launched app. Sim stays booted.",
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string"},
                "terminate_app": {"type": "boolean", "default": True},
            },
        },
        "handler": tool_session_end,
    },
    {
        "name": "session_status",
        "description": "Return state of a session, or all sessions if session_id is omitted.",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
        },
        "handler": tool_session_status,
    },
    {
        "name": "observe",
        "description": (
            "Capture screenshot + numbered marks of all detected text/UI regions. "
            "Returns: screenshot_path (raw PNG), annotated_path (PNG with red numbered "
            "boxes drawn over each mark), marks (id, bbox, center, text). The agent can "
            "either look at the annotated image and tap by mark id/text, or look at the "
            "raw screenshot and tap by pixel coords. Set annotate=false to skip the SoM "
            "pass and get a faster, raw-only observation."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string"},
                "annotate": {"type": "boolean", "default": True, "description": "Run Set-of-Mark detection + annotation."},
                "capture_logs": {"type": "boolean", "default": False, "description": "Include a tail of recent simulator logs."},
                "log_lines": {"type": "integer", "default": 50},
                "log_predicate": {"type": "string", "description": "Optional NSPredicate to filter logs."},
            },
        },
        "handler": tool_observe,
    },
    {
        "name": "tap",
        "description": (
            "Tap a target. Supply ONE of: {x, y} (screenshot pixel coords), "
            "{mark: <id>} (mark id from latest observe — reshuffles per observe), "
            "{stable_id: <hash>} (stable across observes; preferred for replay), "
            "{stable_id_loose: <hash>} (coarser 60px bucket — tolerates layout drift "
            "that escapes the tight 20px stable_id), or "
            "{text: \"...\"} (best-match against the last observe's marks)."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string"},
                "x": {"type": "integer", "description": "Pixel x (paired with y)."},
                "y": {"type": "integer", "description": "Pixel y (paired with x)."},
                "mark": {"type": "integer", "description": "Mark id from the latest observe (reshuffles every observe)."},
                "stable_id": {"type": "string", "description": "Stable mark hash (text + 20px bucketed position) — survives reshuffling."},
                "stable_id_loose": {"type": "string", "description": "Coarser stable hash (text + 60px bucketed position) — tolerates >3px layout drift that breaks the tight stable_id."},
                "text": {"type": "string", "description": "Match a mark by visible text (exact > prefix > substring)."},
            },
        },
        "handler": tool_tap,
    },
    {
        "name": "swipe",
        "description": (
            "Drag between two points. Supply EITHER {x1,y1,x2,y2} pixel coords OR "
            "{from: <target>, to: <target>} where each target is {x,y} | {mark} | {text}."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string"},
                "x1": {"type": "integer"},
                "y1": {"type": "integer"},
                "x2": {"type": "integer"},
                "y2": {"type": "integer"},
                "from": {"type": "object", "description": "Start target: {x,y}, {mark}, or {text}."},
                "to": {"type": "object", "description": "End target: {x,y}, {mark}, or {text}."},
                "duration_ms": {"type": "integer", "default": 300},
            },
        },
        "handler": tool_swipe,
    },
    {
        "name": "type_text",
        "description": (
            "Send text via the keyboard. If tap_first is given (a target dict), tap "
            "that target first to focus a field, then type. If clear_first=true, send "
            "Cmd-A + delete after focusing and before typing — convenient for resetting "
            "search fields. Returns ok, chars, injection_method ('hid' or 'cliclick' — "
            "the backend that actually dispatched), dispatch_succeeded (True when the "
            "keystrokes landed; reliable signal under HID where the soft keyboard isn't "
            "drawn), keyboard_visible (heuristic from a post-type observe; useful on the "
            "cliclick path), and focused_field (the stable_id of the tap_first target "
            "when one was supplied and resolved via mark/stable_id/text, else null)."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["session_id", "text"],
            "properties": {
                "session_id": {"type": "string"},
                "text": {"type": "string"},
                "tap_first": {"type": "object", "description": "Optional focus target: {x,y}, {mark}, or {text}."},
                "clear_first": {
                    "type": "boolean",
                    "default": False,
                    "description": "Send Cmd-A + delete after focusing (and before typing) to clear the field.",
                },
            },
        },
        "handler": tool_type_text,
    },
    {
        "name": "press_key",
        "description": "Press a hardware/special key. Supported: home, lock, shake, siri, return, tab, escape, space, delete, arrow-up/down/left/right.",
        "inputSchema": {
            "type": "object",
            "required": ["session_id", "key"],
            "properties": {
                "session_id": {"type": "string"},
                "key": {"type": "string"},
            },
        },
        "handler": tool_press_key,
    },
    {
        "name": "record_start",
        "description": "Begin recording every act-tool call (with screenshots) under name. Stops via record_stop.",
        "inputSchema": {
            "type": "object",
            "required": ["session_id", "name"],
            "properties": {
                "session_id": {"type": "string"},
                "name": {"type": "string"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of free-form tags persisted into the recording's metadata.",
                },
            },
        },
        "handler": tool_record_start,
    },
    {
        "name": "record_stop",
        "description": "Finalize the active recording and write recording.yaml. Returns yaml_path + step count.",
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {"session_id": {"type": "string"}},
        },
        "handler": tool_record_stop,
    },
    {
        "name": "replay",
        "description": (
            "Replay a recorded session by name. on_drift halt|warn|force; "
            "drift_threshold default 0.85 (SSIM). Pass mask_regions to exclude "
            "noisy areas (e.g. status-bar clock) from the similarity compute. "
            "When omitted, falls back to the recording's own ssim_masks if present."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["session_id", "name"],
            "properties": {
                "session_id": {"type": "string"},
                "name": {"type": "string"},
                "on_drift": {"type": "string", "enum": ["halt", "warn", "force"], "default": "halt"},
                "drift_threshold": {"type": "number", "default": 0.85},
                "mask_regions": {
                    "type": "array",
                    "description": "Rectangles to blank in both screenshots before similarity. Each entry is [x, y, w, h] OR {x, y, w, h}.",
                    "items": {
                        "oneOf": [
                            {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4},
                            {
                                "type": "object",
                                "required": ["x", "y", "w", "h"],
                                "properties": {
                                    "x": {"type": "integer"},
                                    "y": {"type": "integer"},
                                    "w": {"type": "integer"},
                                    "h": {"type": "integer"},
                                    "label": {"type": "string"},
                                },
                            },
                        ]
                    },
                },
            },
        },
        "handler": tool_replay,
    },
    {
        "name": "list_devices",
        "description": (
            "Enumerate real iPhones/iPads paired with this Mac. Use the returned "
            "udid in session_start({target: 'device', udid: ...}) to attach. "
            "Note: real-device sessions support observe + logs + app lifecycle; "
            "tap/swipe/type_text/press_key require WebDriverAgent (v0.2 roadmap)."
        ),
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_list_devices,
    },
    {
        "name": "logs",
        "description": "Tail simulator logs (last 30s window). Use predicate to filter (NSPredicate string).",
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string"},
                "lines": {"type": "integer", "default": 200},
                "predicate": {"type": "string"},
            },
        },
        "handler": tool_logs,
    },
    # ── Performance monitoring ──────────────────────────────────────────
    {
        "name": "perf",
        "description": (
            "Snapshot CPU%, memory RSS (MB), and thread count for the active app. "
            "simctl + ps based — no XCTest bridge required."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string"},
                "app_bundle_id": {"type": "string", "description": "Override the session's bundle id (optional)."},
            },
        },
        "handler": tool_perf,
    },
    {
        "name": "perf_baseline",
        "description": "Capture a labeled perf snapshot on the session for later compare. Default label='default'.",
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string"},
                "label": {"type": "string", "default": "default"},
                "app_bundle_id": {"type": "string"},
            },
        },
        "handler": tool_perf_baseline,
    },
    {
        "name": "perf_compare",
        "description": (
            "Diff a fresh perf snapshot against the stored baseline. Returns "
            "{baseline, current, delta, severity}; severity is 'high' when "
            "memory_rss_mb_delta>50 or threads_delta>10, 'medium' when "
            "cpu_pct_delta>25, else 'low'."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string"},
                "label": {"type": "string", "default": "default"},
                "app_bundle_id": {"type": "string"},
            },
        },
        "handler": tool_perf_compare,
    },
    {
        "name": "memory",
        "description": (
            "Detailed memory breakdown (footprint/dirty/swapped/clean MB) via "
            "macOS `footprint`. Returns {available: false, reason} when the "
            "binary is missing — never raises for that case."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string"},
                "app_bundle_id": {"type": "string"},
            },
        },
        "handler": tool_memory,
    },
    # ── Diagnostics ─────────────────────────────────────────────────────
    {
        "name": "doctor",
        "description": (
            "Environment readiness: Xcode CLT, simctl runtimes, booted devices, "
            "native HID helper presence. Returns {ok, checks: [{name, ok, detail}]}."
        ),
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_doctor,
    },
    {
        "name": "app_state",
        "description": (
            "Heuristic app lifecycle state: foreground / not-running. (background/suspended "
            "are reserved — distinguishing them needs an XCTest bridge.)"
        ),
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string"},
                "app_bundle_id": {"type": "string"},
            },
        },
        "handler": tool_app_state,
    },
    {
        "name": "apps",
        "description": (
            "List installed apps on a sim. Resolve UDID from session_id OR pass udid directly. "
            "Each entry: bundle_id, name, version, path."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "udid": {"type": "string"},
            },
        },
        "handler": tool_apps,
    },
    {
        "name": "crashes",
        "description": (
            "Retrieve `.ips` crash reports from ~/Library/Logs/DiagnosticReports. "
            "Filter by session-start time (default true) and bundle id (optional). "
            "Returns up to `max` reports newest-first."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string"},
                "since_session_start": {"type": "boolean", "default": True},
                "app_bundle_id": {"type": "string"},
                "max": {"type": "integer", "default": 10},
            },
        },
        "handler": tool_crashes,
    },
    # ── Robustness ──────────────────────────────────────────────────────
    {
        "name": "dismiss_first_launch_alerts",
        "description": (
            "Tap Allow/Don't Allow on permission alerts. Re-observes 200 ms post-tap "
            "and retries once when the alert text persists (closes the 1-in-4 "
            "SpringBoard alert-handoff race)."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string"},
                "choice": {"type": "string", "enum": ["allow", "deny"], "default": "allow"},
                "retries": {"type": "integer", "default": 1},
            },
        },
        "handler": tool_dismiss_first_launch_alerts,
    },
    {
        "name": "pre_grant_permissions",
        "description": (
            "Pre-grant permissions via `simctl privacy grant` BEFORE app launch. "
            "Permissions: location, photos, contacts, camera, microphone, calendar, "
            "reminders, motion, health, homekit, siri, speech, medialibrary, all."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["session_id", "permissions"],
            "properties": {
                "session_id": {"type": "string"},
                "permissions": {"type": "array", "items": {"type": "string"}},
                "app_bundle_id": {"type": "string"},
            },
        },
        "handler": tool_pre_grant_permissions,
    },
    {
        "name": "set_appearance",
        "description": "Toggle the simulator's UI appearance: 'light' or 'dark'.",
        "inputSchema": {
            "type": "object",
            "required": ["session_id", "appearance"],
            "properties": {
                "session_id": {"type": "string"},
                "appearance": {"type": "string", "enum": ["light", "dark"]},
            },
        },
        "handler": tool_set_appearance,
    },
    {
        "name": "dismiss_sheet",
        "description": "Dismiss a presented sheet/modal by swiping down (20% → 70% of screen height).",
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {"session_id": {"type": "string"}},
        },
        "handler": tool_dismiss_sheet,
    },
    {
        "name": "list_replays",
        "description": (
            "List saved replay recordings under SIMDRIVE_HOME/recordings. "
            "Each entry: name, path, steps, created_at, modified_at, simdrive_version, tags."
        ),
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_list_replays,
    },
    {
        "name": "validate_replay",
        "description": (
            "Structural validation of a recording YAML without executing. Checks "
            "required fields, step structure, supported actions, screenshot file presence."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        },
        "handler": tool_validate_replay,
    },
    # ── v0.3.0a3: stale-MCP detection + field-clear idiom ──────────────
    {
        "name": "version",
        "description": (
            "Report loaded simdrive version vs. on-disk version. Zero-arg. "
            "Returns {version, loaded_at, disk_version, drift}; drift=true means "
            "the running server is stale (`pip install --upgrade simdrive` without "
            "restarting the agent host). Cheap; safe to call any time."
        ),
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_version,
    },
    {
        "name": "clear_field",
        "description": (
            "Clear a focused text field by sending Cmd-A + delete via HID. "
            "If `target` is given, tap it first so the field is the active "
            "first responder. Returns {ok, cleared}."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string"},
                "target": {
                    "type": "object",
                    "description": "Optional focus target before clearing: {x,y}, {mark}, {text}, {stable_id}, or {stable_id_loose}.",
                },
            },
        },
        "handler": tool_clear_field,
    },
]


def list_tools() -> list[dict]:
    """Return the tool list (sans handlers) suitable for MCP tools/list."""
    return [{k: v for k, v in t.items() if k != "handler"} for t in _TOOLS]


def call_tool(name: str, arguments: dict) -> dict:
    for t in _TOOLS:
        if t["name"] == name:
            result = t["handler"](arguments or {})
            # v0.3.0a3 — inject `_simdrive_warning` side-channel field when the
            # running server is stale relative to the on-disk wheel. Doesn't
            # replace the tool result, just rides along so the agent sees it
            # on every tool call after a `pip install --upgrade`.
            warning = _check_version_drift()
            if warning and isinstance(result, dict) and "_simdrive_warning" not in result:
                result["_simdrive_warning"] = warning
            return result
    raise ValueError(f"unknown tool: {name}")


# ----------------------- async MCP server entry point ------------------ #


async def _serve_async() -> None:
    """Run as an MCP stdio server."""
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    import mcp.types as mtypes

    server: Server = Server("simdrive")

    @server.list_tools()
    async def _list_tools() -> list[mtypes.Tool]:
        return [
            mtypes.Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["inputSchema"],
            )
            for t in _TOOLS
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict | None) -> list[mtypes.TextContent | mtypes.ImageContent]:
        try:
            result = call_tool(name, arguments or {})
        except errors.SimdriveError as exc:
            return [mtypes.TextContent(type="text", text=json.dumps(exc.to_dict()))]
        except Exception as exc:  # last-resort catch-all → wrap as 'internal' code
            envelope = {
                "ok": False,
                "error": {
                    "code": "internal",
                    "message": str(exc),
                    "details": {"exception_type": type(exc).__name__},
                },
            }
            return [mtypes.TextContent(type="text", text=json.dumps(envelope))]
        return [mtypes.TextContent(type="text", text=json.dumps(result))]

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


_HELP_TEXT = """\
specterqa-ios — SpecterQA for iOS MCP server. (codename: simdrive)

Usage: specterqa-ios (no args)  Run as MCP server on stdio.
       specterqa-ios --version
       specterqa-ios --help

Aliases: `simdrive` and `simdrive-mcp` invoke the same server.
"""


def serve() -> None:
    """Console-script entry point. Blocks running the MCP server on stdio."""
    import sys
    args = sys.argv[1:]
    if args:
        flag = args[0]
        if flag in ("--version", "-V"):
            print(f"specterqa-ios {__version__}")
            sys.exit(0)
        if flag in ("--help", "-h"):
            print(_HELP_TEXT, end="")
            sys.exit(0)
    asyncio.run(_serve_async())


if __name__ == "__main__":
    serve()
