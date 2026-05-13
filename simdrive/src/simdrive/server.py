"""simdrive — MCP-native iOS simulator driver, MCP server.

Exposes the MCP tool surface to any compatible host (Claude, Cline, etc.):

  Lifecycle:  session_start, session_end, session_status
  Observe:    observe
  Act:        tap, swipe, type_text, press_key
  Record:     record_start, record_stop, replay
  Utility:    logs

Run:
    simdrive
    # or
    python -m simdrive.server

Add to .mcp.json:
    {
      "mcpServers": {
        "simdrive": { "command": "simdrive" }
      }
    }
"""
from __future__ import annotations

import asyncio
import base64
import inspect
import json
import time
from pathlib import Path
from typing import Optional

from . import (
    __version__, act, diagnostics, errors, observe, perf, recorder,
    robustness, session, sim, som,
)
from .observability.logger import get_logger


# ── MCP session holder (INIT-2026-544) ──────────────────────────────────────
# Populated by _serve_async when the MCP server starts so that async tool
# handlers (e.g. tool_run_journey) can retrieve the active ServerSession.
_MCP_SERVER: Optional[object] = None


def _get_current_mcp_session():
    """Return the active MCP ServerSession or None.

    Safe to call from any tool handler. Returns None when there is no live
    MCP context (e.g. unit tests or CLI calls).
    """
    if _MCP_SERVER is None:
        return None
    try:
        return _MCP_SERVER.request_context.session  # type: ignore[union-attr]
    except (LookupError, AttributeError):
        return None

_log = get_logger(__name__)


def _now() -> float:
    return time.time()


# ── WDA device-input helper ─────────────────────────────────────────────────


def _wda_client_for(udid: str):
    """Return a WdaClient for the given device UDID.

    Loads the per-UDID registry from ~/.simdrive/wda/<udid>.json (written by
    `simdrive bootstrap-device`). Raises SimdriveError with a clear recovery
    message if the registry is missing (i.e., WDA has not been bootstrapped).
    """
    from .wda import registry as wda_registry
    from .wda.client import WdaClient

    entry = wda_registry.load(udid)
    if entry is None:
        raise errors.SimdriveError(
            code="wda_not_bootstrapped",
            message=(
                f"No WDA registry found for device {udid}. "
                "Recovery: run `simdrive bootstrap-device {udid}` to install and "
                "start WebDriverAgent on the device before using input tools with target=device."
            ),
            details={"udid": udid},
        )
    host = entry.get("host", "localhost")
    port = int(entry.get("port", 8100))
    return WdaClient(host=host, port=port)


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
        v = _md.version("simdrive")
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
    # Accept both "udid" (schema name) and "device_udid" (common alias used by live callers).
    udid = arguments.get("udid") or arguments.get("device_udid")
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

    if s.target == "device":
        # Route through WDA /screenshot (no session required) instead of
        # idevicescreenshot — CoreDevice UUIDs are not recognized by
        # idevicescreenshot, causing "No device found" errors.
        # Matches the target=device routing pattern used by tool_tap/tool_swipe/etc.
        from PIL import Image
        import io

        wda = _wda_client_for(s.device.udid)
        png_bytes = wda.screenshot_any()

        obs_dir = s.workdir / "observations"
        obs_dir.mkdir(parents=True, exist_ok=True)
        ts = int(_now() * 1000)
        screenshot_path = obs_dir / f"observe-{ts}.png"
        screenshot_path.write_bytes(png_bytes)

        with Image.open(io.BytesIO(png_bytes)) as im:
            w, h = im.size

        s.last_screenshot_w = w
        s.last_screenshot_h = h
        s.last_screenshot_path = screenshot_path
        s.last_action_at = _now()

        marks: list = []
        annotated_path = None
        if bool(arguments.get("annotate", True)):
            from .wda.som_device import annotate_device_screenshot
            wda_annotate = s.wda_client or wda
            point_scale: float = float(getattr(s, "pixel_per_point_scale", None) or 1.0)
            marks, annotated_path = annotate_device_screenshot(
                screenshot_path, (w, h), wda_annotate, point_scale=point_scale,
            )
            if marks:
                s.last_marks = marks

        result = {
            "screenshot_path": str(screenshot_path),
            "annotated_path": str(annotated_path) if annotated_path else None,
            "screenshot_size_pixels": [w, h],
            "window_bounds_macos": None,
            "captured_at": _now(),
            "marks": marks,
            "recent_logs": None,
            "target": "device",
        }
        # screenshot_b64 is opt-in: a 101k-char inline payload overflows the
        # MCP token budget for typical screens. Callers that need raw bytes
        # read screenshot_path from disk.
        if bool(arguments.get("include_screenshot_b64", False)):
            result["screenshot_b64"] = base64.b64encode(png_bytes).decode("ascii")
        return result

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
    sw, sh = _ensure_screenshot_dims(s)
    x, y, resolved_via, matched_mark = _resolve_target_xy(s, arguments)
    pre_path = s.last_screenshot_path

    if s.target == "device":
        wda = _wda_client_for(s.device.udid)
        wda.tap(float(x), float(y))
        s.last_action_at = _now()
        session.append_action(s, {
            "action": "tap",
            "args": dict(arguments),
            "resolved": {"pixel_x": x, "pixel_y": y, "via": resolved_via},
            "backend": "wda",
            "at": _now(),
        })
        resp: dict = {
            "ok": True,
            "pixel_x": x,
            "pixel_y": y,
            "screen_x": 0,
            "screen_y": 0,
            "screenshot_size_pixels": [sw, sh],
            "resolved_via": resolved_via,
        }
        if matched_mark is not None and pre_path:
            step_id = _record_act_step(s, "tap", {
                "x": x, "y": y, "screenshot_w": sw, "screenshot_h": sh,
                "stable_id": matched_mark.stable_id,
                "stable_id_loose": matched_mark.stable_id_loose,
                "text": matched_mark.text,
            }, pre_path)
            if step_id is not None:
                resp["step_id"] = step_id
        return resp

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
    if s.target == "device":
        wda = _wda_client_for(s.device.udid)
        wda.swipe(float(x1), float(y1), float(x2), float(y2), duration_ms)
    else:
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
    text = str(arguments["text"])
    tap_target = arguments.get("tap_first")  # optional target dict to focus a field first
    clear_first = bool(arguments.get("clear_first", False))
    focused_mark = None  # Mark of the tap_first target if resolved via mark/stable_id/text

    if s.target == "device":
        wda = _wda_client_for(s.device.udid)
        if tap_target:
            sw, sh = _ensure_screenshot_dims(s)
            tx, ty, _, focused_mark = _resolve_target_xy(s, tap_target)
            wda.tap(float(tx), float(ty))
            import time as _t
            _t.sleep(0.6)
        if clear_first:
            wda.clear_field()
        pre_obs = observe.observe(s.device.udid, s.workdir / "observations") if s.recorder else None
        pre_path = pre_obs.screenshot_path if pre_obs else s.last_screenshot_path
        wda.type_text(text)
        s.last_action_at = _now()
        step_id = None
        if pre_path:
            step_id = _record_act_step(s, "type_text", {"text": text}, pre_path)
        session.append_action(s, {
            "action": "type_text",
            "args": {"text": text, "tap_first": tap_target, "clear_first": clear_first},
            "backend": "wda",
            "at": _now(),
        })
        post_obs = observe.observe(s.device.udid, s.workdir / "observations", annotate=True)
        s.last_screenshot_w = post_obs.screenshot_w
        s.last_screenshot_h = post_obs.screenshot_h
        s.last_screenshot_path = post_obs.screenshot_path
        if post_obs.marks:
            s.last_marks = post_obs.marks
        focused_field = focused_mark.stable_id if focused_mark is not None else None
        resp: dict = {
            "ok": True,
            "chars": len(text),
            "injection_method": "wda",
            "dispatch_succeeded": True,
            "keyboard_visible": False,
            "focused_field": focused_field,
        }
        if step_id is not None:
            resp["step_id"] = step_id
        return resp

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
    key = str(arguments["key"])
    pre_obs = observe.observe(s.device.udid, s.workdir / "observations") if s.recorder else None
    pre_path = pre_obs.screenshot_path if pre_obs else s.last_screenshot_path
    if s.target == "device":
        wda = _wda_client_for(s.device.udid)
        wda.press_key(key)
    else:
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
    halt_on_state_mismatch = bool(arguments.get("halt_on_state_mismatch", True))
    return recorder.replay(name, s, on_drift=on_drift, drift_threshold=threshold,
                           mask_regions=mask_regions,
                           halt_on_state_mismatch=halt_on_state_mismatch)


def tool_list_devices(arguments: dict) -> dict:
    """Enumerate real devices reachable via Apple devicectl + libimobiledevice."""
    from . import device
    from .wda import registry as wda_registry
    ok, missing = device.libimobiledevice_available()
    devs = []
    err: dict | None = None
    try:
        for d in device.list_devices():
            # hid_supported: True when a WDA registry entry exists for this UDID
            # (i.e. `simdrive bootstrap-device` has been run and WDA is ready).
            # tap/swipe/type_text/press_key all route through WDA on real devices.
            reg = wda_registry.load(d.udid)
            devs.append({
                "udid": d.udid,
                "name": d.name,
                "model": d.model,
                "transport": d.transport,
                "state": d.state,
                "hid_supported": reg is not None,
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
            "Real-device tap/swipe/type require a bootstrapped WebDriverAgent "
            "(run `simdrive bootstrap-device` once per device). "
            "Devices showing hid_supported=true are ready to drive."
        ),
        "error": err,
    }


def tool_logs(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    lines = int(arguments.get("lines", 200))
    predicate = arguments.get("predicate")
    if s.target == "device":
        from . import device
        try:
            text = device.get_log_tail(s.device.udid, lines=lines, predicate=predicate)
        except device.DeviceError as exc:
            # F-003: surface missing idevicesyslog as a structured error rather
            # than an unhandled exception, so the MCP caller gets a clean code.
            msg = str(exc)
            if "device_logs_unavailable" in msg:
                return {
                    "ok": False,
                    "error": {
                        "code": "device_logs_unavailable",
                        "message": (
                            "idevicesyslog not installed. "
                            "Recovery: brew install libimobiledevice"
                        ),
                    },
                }
            raise
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
    if s.target == "device":
        return diagnostics.app_state_device(s.device.udid, bundle_id)
    return diagnostics.app_state(s.device.udid, bundle_id)


def tool_apps(arguments: dict) -> dict:
    udid = arguments.get("udid")
    target = "simulator"
    if not udid:
        sid = arguments.get("session_id")
        if not sid:
            raise errors.invalid_argument(
                "session_id|udid", None,
                "supply either session_id or a literal udid",
            )
        s = session.get(sid)
        udid = s.device.udid
        target = s.target
    if target == "device":
        return {"apps": diagnostics.list_apps_device(udid)}
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


def tool_lint_recordings(arguments: dict) -> dict:
    """Lint every recording under `path` (or recordings root). a9.1."""
    path_arg = arguments.get("path")
    target = Path(path_arg) if path_arg else recorder.recordings_root()
    results = recorder.lint_recordings(target)
    fail_count = sum(1 for r in results if r.status == "fail")
    return {
        "results": [r.to_dict() for r in results],
        "ok": len(results) - fail_count,
        "fail": fail_count,
    }


def tool_migrate_recording(arguments: dict) -> dict:
    """Backfill a `requires:` block onto an existing recording. a9.1."""
    name = str(arguments["name"])
    force = bool(arguments.get("force", False))
    dry_run = bool(arguments.get("dry_run", False))
    try:
        result = recorder.migrate_recording(name, force=force, dry_run=dry_run)
    except recorder.MigrationError as exc:
        return {"migrated": False, "error": str(exc)}
    return {
        "migrated": result.migrated,
        "reason": result.reason,
        "dry_run": result.dry_run,
        "text_mark_count": result.text_mark_count,
        "primary_button_label": result.primary_button_label,
        "backup_path": str(result.backup_path) if result.backup_path else None,
    }


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
    """Clear a focused text field.

    On simulator: Cmd-A then delete via HID.
    On device: WDA active-element clear.

    If a `target` is given, tap it first to ensure the field has first-responder
    focus before the clear operation.
    """
    from . import hid_inject
    s = session.get(arguments["session_id"])
    target = arguments.get("target")

    if s.target == "device":
        wda = _wda_client_for(s.device.udid)
        if target:
            sw, sh = _ensure_screenshot_dims(s)
            tx, ty, _, _ = _resolve_target_xy(s, target)
            wda.tap(float(tx), float(ty))
            import time as _t
            _t.sleep(0.5)
        wda.clear_field()
        s.last_action_at = _now()
        session.append_action(s, {
            "action": "clear_field",
            "args": {"target": target},
            "backend": "wda",
            "at": _now(),
        })
        return {"ok": True, "cleared": True}

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


# ─── SimDrive 1.0 — Journey runner MCP tool ──────────────────────────── #


async def tool_run_journey(arguments: dict) -> dict:
    """Execute a YAML journey against a running session via MCP sampling.

    Requires a valid license (calls check_entitlement() — raises LicenseError
    when the license is absent/expired/invalid).

    Uses MCPSamplingLLMClient — the connected MCP client (Claude Code, Cline,
    etc.) provides the LLM and credentials via session.create_message().  The
    anthropic package is NOT required for this code path.

    Parameters
    ----------
    session_id:    Active session to drive.
    journey_path:  Absolute or relative path to the journey YAML file.
    persona_path:  Absolute or relative path to the persona YAML file.
    budget_override: Optional dict with any subset of {max_steps, max_seconds,
                     max_llm_calls} to override the journey's default budget.

    Returns RunResult.to_dict().
    """
    from simdrive.license.entitlement import check_entitlement
    from simdrive.journey.schema import load_journey
    from simdrive.journey.persona import load_persona
    from simdrive.journey.runner import run_journey
    from simdrive.journey.mcp_sampling_client import MCPSamplingLLMClient

    # License gate — raises LicenseError on expiry / invalid / not found.
    check_entitlement()

    # Acquire the MCP session for sampling — required on the MCP path.
    mcp_session = _get_current_mcp_session()
    if mcp_session is None:
        raise errors.SimdriveError(
            code="mcp_sampling_unavailable",
            message=(
                "tool_run_journey via MCP requires a connected MCP client that "
                "supports sampling (e.g. Claude Code). For standalone use, run "
                "`simdrive run path/to/journey.yaml` after "
                "`pip install simdrive[claude]`."
            ),
            details={},
        )

    session_id = arguments["session_id"]
    s = session.get(session_id)

    journey_path = arguments["journey_path"]
    persona_path = arguments["persona_path"]
    budget_override = arguments.get("budget_override")

    journey = load_journey(journey_path)

    # Apply any budget overrides before running.
    if budget_override:
        for key in ("max_steps", "max_seconds", "max_llm_calls"):
            if key in budget_override:
                setattr(journey.budget, key, int(budget_override[key]))

    persona = load_persona(persona_path)
    llm_client = MCPSamplingLLMClient(mcp_session)

    result = await run_journey(
        journey=journey,
        persona=persona,
        session=s,
        llm_client=llm_client,
    )
    return result.to_dict()


def tool_load_journey(arguments: dict) -> dict:
    """Load and parse a journey YAML, return its data so the agent can drive primitives directly.

    Use case: the agent in your MCP host reads the journey definition, then
    drives each step using existing primitives (observe / tap / type_text / etc.)
    — no LLM call inside simdrive, no API key needed, no MCP sampling required.

    This replaces the former `run_journey` MCP tool which required
    `sampling/createMessage` support (not implemented in Claude Code and most
    MCP clients). The agent-first workflow is now:
      1. tool_load_journey → get journey goals + success_criteria + budget
      2. tool_session_start → get session_id
      3. tool_observe → see the screen
      4. tool_tap / tool_type_text / tool_swipe → interact
      5. Repeat until success_criteria are met or budget exhausted

    Parameters
    ----------
    path:         Absolute path to the journey YAML file.
    persona_path: Optional absolute path to the persona YAML file.

    Returns dict with:
      ok:       True
      journey:  name, goals, success_criteria (as dicts), budget, target, tags
      persona:  slug, name, role, technical_comfort, patience, goals (or null)
    """
    from simdrive.journey.schema import load_journey

    path = arguments["path"]
    persona_path = arguments.get("persona_path")

    journey = load_journey(Path(path))

    journey_data: dict = {
        "name": journey.name,
        "persona": journey.persona,
        "target": journey.target,
        "goals": journey.goals,
        "success_criteria": [sc.model_dump() for sc in journey.success_criteria],
        "budget": journey.budget.model_dump(),
        "tags": journey.tags,
        "app_bundle_id": journey.app_bundle_id,
        "replay_id": journey.replay_id,
    }

    persona_data = None
    if persona_path:
        from simdrive.journey.persona import load_persona
        persona = load_persona(Path(persona_path))
        persona_data = {
            "slug": persona.slug,
            "name": persona.name,
            "role": persona.role,
            "technical_comfort": persona.technical_comfort,
            "patience": persona.patience,
            "goals": persona.goals,
            "frustrations": persona.frustrations,
            "locale": persona.locale,
        }

    return {
        "ok": True,
        "journey": journey_data,
        "persona": persona_data,
    }


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
                "target": {"type": "string", "enum": ["simulator", "device"], "default": "simulator", "description": "'simulator' (default) or 'device' for a real iPhone/iPad. Real-device sessions support observe, logs, app lifecycle, and (after `simdrive bootstrap-device`) tap/swipe/type_text/press_key via WebDriverAgent."},
                "device": {"type": "string", "description": "Device name, e.g. 'iPhone 17 Pro'. Optional if a sim is already booted."},
                "os_version": {"type": "string", "description": "iOS version, e.g. '26.3'. Optional."},
                "udid": {"type": "string", "description": "Simulator UDID, or coredevice UUID when target='device'. Alias: 'device_udid'."},
                "device_udid": {"type": "string", "description": "Alias for 'udid'. Coredevice UUID for real-device sessions (target='device')."},
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
                "include_screenshot_b64": {"type": "boolean", "default": False, "description": "Inline the PNG as base64 in the response. Off by default — the payload overflows the MCP token budget. Read screenshot_path from disk instead."},
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
            "When omitted, falls back to the recording's own ssim_masks if present. "
            "halt_on_state_mismatch (a9.0, default true) verifies the recorded "
            "requires: block before step 1 and halts with halt_reason="
            "'state_contract_mismatch' on failure. Set false to proceed with a warning."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["session_id", "name"],
            "properties": {
                "session_id": {"type": "string"},
                "name": {"type": "string"},
                "on_drift": {"type": "string", "enum": ["halt", "warn", "force"], "default": "halt"},
                "drift_threshold": {"type": "number", "default": 0.85},
                "halt_on_state_mismatch": {"type": "boolean", "default": True},
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
            "Devices with hid_supported=true have a bootstrapped WebDriverAgent "
            "and support tap/swipe/type_text/press_key. Run "
            "`simdrive bootstrap-device <udid>` once to enable HID on a device."
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
    # ── SimDrive 1.0 — Journey loader (agent-first, no API key required) ──
    {
        "name": "load_journey",
        "description": (
            "Load and parse a journey YAML, returning its goals, success criteria, "
            "budget, and persona data as structured JSON. "
            "\n\n"
            "AGENT-FIRST WORKFLOW (no API key needed, no MCP sampling required):\n"
            "  1. load_journey → get journey goals + success_criteria + budget\n"
            "  2. session_start → start a simulator or device session\n"
            "  3. observe → see the current screen\n"
            "  4. tap / type_text / swipe → interact with the app\n"
            "  5. Repeat observe → act until success_criteria are met\n"
            "\n"
            "The agent in your MCP host (Claude Code, Cline, etc.) drives the loop "
            "using simdrive primitives. simdrive does not make any LLM calls.\n"
            "\n"
            "Note: tool_run_journey is available as a standalone CLI command "
            "(`simdrive run path/to/journey.yaml`) for hosts that support MCP sampling, "
            "but it is not exposed as an MCP tool because most MCP clients (including "
            "Claude Code) do not implement sampling/createMessage."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the journey YAML file.",
                },
                "persona_path": {
                    "type": "string",
                    "description": "Optional absolute path to the persona YAML file.",
                },
            },
        },
        "handler": tool_load_journey,
    },
    # ── SimDrive a9.1 — recording lint + migrate ────────────────────────
    {
        "name": "lint_recordings",
        "description": (
            "Walk a directory tree and lint every recording.yaml for state-contract "
            "(`requires:`) presence + shape. Returns one result per recording: "
            "ok / fail with reason. Use to find recordings that pre-date a9.0 "
            "and still need `migrate_recording` to backfill their state contract."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Optional directory to scan; defaults to the simdrive recordings root.",
                },
            },
        },
        "handler": tool_lint_recordings,
    },
    {
        "name": "migrate_recording",
        "description": (
            "Backfill a `requires:` state contract onto an existing recording by "
            "OCR'ing its step-0 pre_screenshot. Idempotent: no-op when the recording "
            "already has `requires:` (use force=true to overwrite). Writes a "
            ".pre-migrate.bak sibling before mutating so a botched migration is recoverable."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "description": "Recording name (directory under recordings root)."},
                "force": {"type": "boolean", "description": "Re-migrate even if `requires:` already present."},
                "dry_run": {"type": "boolean", "description": "Compute the would-be result without writing."},
            },
        },
        "handler": tool_migrate_recording,
    },
]


def list_tools() -> list[dict]:
    """Return the tool list (sans handlers) suitable for MCP tools/list."""
    return [{k: v for k, v in t.items() if k != "handler"} for t in _TOOLS]


def call_tool(name: str, arguments: dict) -> dict:
    """Sync tool dispatcher — for non-MCP callers (CLI smokes, direct test calls).

    Does NOT support async handlers. Use call_tool_async inside the MCP event loop.
    """
    for t in _TOOLS:
        if t["name"] == name:
            handler = t["handler"]
            if inspect.iscoroutinefunction(handler):
                raise RuntimeError(
                    f"Tool '{name}' has an async handler — use call_tool_async "
                    "inside an async context (MCP server) instead of call_tool."
                )
            result = handler(arguments or {})
            # v0.3.0a3 — inject `_simdrive_warning` side-channel field when the
            # running server is stale relative to the on-disk wheel. Doesn't
            # replace the tool result, just rides along so the agent sees it
            # on every tool call after a `pip install --upgrade`.
            warning = _check_version_drift()
            if warning and isinstance(result, dict) and "_simdrive_warning" not in result:
                result["_simdrive_warning"] = warning
            return result
    raise ValueError(f"unknown tool: {name}")


async def call_tool_async(name: str, arguments: dict) -> dict:
    """Async-aware tool dispatcher — supports both sync and coroutine handlers.

    Used by the MCP server's _call_tool handler so async tools (like
    tool_run_journey after INIT-2026-544) are properly awaited.
    """
    for t in _TOOLS:
        if t["name"] == name:
            handler = t["handler"]
            if inspect.iscoroutinefunction(handler):
                result = await handler(arguments or {})
            else:
                result = handler(arguments or {})
            warning = _check_version_drift()
            if warning and isinstance(result, dict) and "_simdrive_warning" not in result:
                result["_simdrive_warning"] = warning
            return result
    raise ValueError(f"unknown tool: {name}")


# ----------------------- async MCP server entry point ------------------ #


async def _serve_async() -> None:
    """Run as an MCP stdio server."""
    global _MCP_SERVER

    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    import mcp.types as mtypes

    server: Server = Server("simdrive")

    # Populate the module-level holder so tool_run_journey can acquire the
    # active ServerSession via _get_current_mcp_session().
    _MCP_SERVER = server

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
            result = await call_tool_async(name, arguments or {})
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
simdrive — MCP-native iOS simulator driver

Usage: simdrive (no args)   Run as MCP server on stdio.
       simdrive --version
       simdrive --help

SimDrive journey subcommands:
  simdrive run  --session-id <id> --journey <path> [--persona-override <path>]
                [--budget-override max_steps=N,max_seconds=N,max_llm_calls=N]
  simdrive ci   --session-id <id> [--journeys-dir <path>] [--tag <tag>...]

Recording maintenance:
  simdrive lint-recordings    [--path <dir>] [--quiet] [--json]
  simdrive migrate-recording  <name> [--force] [--dry-run]

Trial / license subcommands:
  simdrive trial start --email <you@example.com>
  simdrive trial start --email <you@example.com> --offline-dev
  simdrive license show
  simdrive license path
"""


def _parse_budget_override(value: str) -> dict:
    """Parse ``max_steps=30,max_seconds=120`` into a dict."""
    result = {}
    for part in value.split(","):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            result[k.strip()] = int(v.strip())
    return result


def _cmd_run(args: list[str]) -> None:
    """Handle `specterqa-ios run ...` CLI subcommand.

    Requires a valid license. Instantiates ClaudeLLMClient and calls
    run_journey(). Prints the RunResult summary to stdout and exits with
    0 on pass, 1 on any other outcome.
    """
    import argparse
    import json as _json
    from simdrive.license.entitlement import check_entitlement
    from simdrive.license.errors import LicenseError
    from simdrive.journey.schema import load_journey
    from simdrive.journey.persona import load_persona
    from simdrive.journey.runner import run_journey
    try:
        from simdrive.journey.claude_client import ClaudeLLMClient
    except ModuleNotFoundError as exc:
        if "anthropic" in str(exc):
            import sys as _sys
            print(
                "ERROR: `simdrive run` requires the [claude] optional extra.\n"
                "Install with: pip install simdrive[claude]\n"
                "Or use the MCP server: run `simdrive` (no args) and let your MCP "
                "client (Claude Code, etc.) drive run_journey via sampling.",
                file=_sys.stderr,
            )
            _sys.exit(2)
        raise

    parser = argparse.ArgumentParser(prog="specterqa-ios run")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--journey", required=True, metavar="PATH")
    parser.add_argument("--persona-override", metavar="PATH")
    parser.add_argument("--budget-override", metavar="KEY=VAL[,...]", default="")
    ns = parser.parse_args(args)

    # License gate
    try:
        check_entitlement()
    except LicenseError as exc:
        _log.error("LICENSE ERROR: %s", exc)
        import sys; sys.exit(2)

    journey = load_journey(ns.journey)

    if ns.budget_override:
        overrides = _parse_budget_override(ns.budget_override)
        for k in ("max_steps", "max_seconds", "max_llm_calls"):
            if k in overrides:
                setattr(journey.budget, k, overrides[k])

    # Load persona — use persona_override if supplied, otherwise look in same
    # dir as the journey file with the journey's persona slug.
    if ns.persona_override:
        persona = load_persona(ns.persona_override)
    else:
        import pathlib
        journey_dir = pathlib.Path(ns.journey).parent
        persona_path = journey_dir / ".." / "personas" / f"{journey.persona}.yaml"
        persona_path = persona_path.resolve()
        if not persona_path.exists():
            _log.error("persona file not found at %s — use --persona-override <path>", persona_path)
            import sys; sys.exit(2)
        persona = load_persona(persona_path)

    # Need an active session object — look it up from the in-memory registry.
    s = session.get(ns.session_id)

    llm_client = ClaudeLLMClient()
    result = asyncio.run(run_journey(journey=journey, persona=persona, session=s, llm_client=llm_client))

    print(_json.dumps(result.to_dict(), indent=2))
    import sys
    sys.exit(0 if result.passed else 1)


def _cmd_ci(args: list[str]) -> None:
    """Handle `specterqa-ios ci ...` CLI subcommand.

    Requires a valid license. Discovers journeys in --journeys-dir (default:
    .simdrive/journeys), runs them all, and prints a JUnit XML summary.
    """
    import argparse
    import json as _json
    from simdrive.license.entitlement import check_entitlement
    from simdrive.license.errors import LicenseError
    from simdrive.journey.ci import run_ci

    parser = argparse.ArgumentParser(prog="specterqa-ios ci")
    parser.add_argument("--session-id", required=False, help="(unused — CI manages sessions internally)")
    parser.add_argument("--journeys-dir", default=".simdrive/journeys")
    parser.add_argument("--tag", action="append", default=[])
    ns = parser.parse_args(args)

    # License gate
    try:
        check_entitlement()
    except LicenseError as exc:
        _log.error("LICENSE ERROR: %s", exc)
        import sys; sys.exit(2)

    from simdrive.journey.ci import CIRunOptions

    options = CIRunOptions(
        journeys_dir=ns.journeys_dir,
        tag_filter=ns.tag or [],
    )
    ci_result = run_ci(options)
    print(_json.dumps(ci_result.to_dict(), indent=2))
    import sys; sys.exit(ci_result.exit_code)


def _cmd_bootstrap_device(args: list[str]) -> None:
    """Handle `simdrive bootstrap-device <udid> [flags]` CLI subcommand."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="simdrive bootstrap-device",
        description=(
            "Bootstrap WebDriverAgent on a paired real iOS device.\n\n"
            "Steps:\n"
            "  1. Verify host tools (xcodebuild, idevicepair, xcrun devicectl)\n"
            "  2. Verify device paired + Developer Mode enabled\n"
            "  3. Clone WDA at pinned SHA\n"
            "  4. Resolve codesigning identity\n"
            "  5. xcodebuild build-for-testing\n"
            "  6. Install via xcrun devicectl\n"
            "  7. Launch WDA + discover HTTP port from syslog\n"
            "  8. Persist registry to ~/.simdrive/wda/<udid>.json\n"
            "  9. Smoke-test GET /status -> {ready: true}\n\n"
            "DEVICE TRUST: On first install trust the cert:\n"
            "  Settings > General > VPN & Device Management > [Identity] > Trust"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("udid", help="Device UDID (e.g. 00008150-00142D540A87801C)")
    parser.add_argument("--team-id", default=None, help="Apple Developer Team ID.")
    parser.add_argument("--signing-identity", default=None,
                        help="Full signing identity string.")
    parser.add_argument("--wireless", action="store_true", default=False,
                        help="Use CoreDevice wireless tunnel.")
    parser.add_argument("--wda-port", type=int, default=8100,
                        help="Override WDA HTTP port (default: 8100).")
    parser.add_argument("--rebuild", action="store_true", default=False,
                        help="Force a fresh WDA clone and build.")

    ns = parser.parse_args(args)
    from .wda.bootstrap import bootstrap_device

    try:
        bootstrap_device(
            udid=ns.udid,
            signing_identity=ns.signing_identity,
            team_id=ns.team_id,
            wireless=ns.wireless,
            wda_port=ns.wda_port,
            rebuild=ns.rebuild,
        )
    except Exception as exc:
        _log.error("bootstrap-device failed: %s", exc)
        sys.exit(1)


def _cmd_wda_up(args: list[str]) -> None:
    """Handle `simdrive wda-up <udid>` — re-launch a bootstrapped WDA daemon."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="simdrive wda-up",
        description=(
            "Re-launch a previously-bootstrapped WDA daemon without rebuilding.\n"
            "Reads ~/.simdrive/wda/<udid>.json for the cached xctestrun path."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("udid", help="Device UDID previously passed to bootstrap-device.")

    ns = parser.parse_args(args)
    from .wda.bootstrap import wda_up

    try:
        wda_up(ns.udid)
    except Exception as exc:
        _log.error("wda-up failed: %s", exc)
        sys.exit(1)


def _cmd_lint_recordings(args: list[str]) -> None:
    """Handle `simdrive lint-recordings [--path] [--quiet] [--json]` CLI subcommand."""
    import argparse
    import json as _json
    import sys

    parser = argparse.ArgumentParser(
        prog="simdrive lint-recordings",
        description=(
            "Walk a directory tree and lint every recording.yaml found.\n"
            "Reports OK / FAIL per recording with the failure reason.\n"
            "Exits non-zero if any recording fails the lint."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--path", default=None,
                        help="Directory to scan (default: simdrive recordings root).")
    parser.add_argument("--quiet", action="store_true", default=False,
                        help="Suppress [OK] lines; print only [FAIL] lines.")
    parser.add_argument("--json", action="store_true", default=False,
                        dest="json_out",
                        help="Emit a single JSON object instead of per-line records.")
    ns = parser.parse_args(args)

    target = Path(ns.path) if ns.path else recorder.recordings_root()
    results = recorder.lint_recordings(target)

    fail_count = sum(1 for r in results if r.status == "fail")
    ok_count = len(results) - fail_count

    if ns.json_out:
        print(_json.dumps({
            "results": [r.to_dict() for r in results],
            "ok": ok_count,
            "fail": fail_count,
        }))
    else:
        for r in results:
            if r.status == "ok":
                if ns.quiet:
                    continue
                meta = (f"{r.text_mark_count} text marks, "
                        f"requires app={r.app_bundle_id}, sim={r.sim_device}")
                print(f"[OK]   {r.path}  ({meta})")
            else:
                print(f"[FAIL] {r.path}  {r.reason}")

    sys.exit(1 if fail_count else 0)


def _cmd_migrate_recording(args: list[str]) -> None:
    """Handle `simdrive migrate-recording <name> [--force] [--dry-run]` CLI subcommand."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="simdrive migrate-recording",
        description=(
            "Backfill a `requires:` state contract onto an old recording.\n"
            "Re-OCRs the step-0 pre_screenshot, builds a RequiresBlock, and\n"
            "writes the YAML back in place (with a .pre-migrate.bak sibling)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("name", help="Recording name (directory under recordings root).")
    parser.add_argument("--force", action="store_true", default=False,
                        help="Re-migrate even if the recording already has `requires:`.")
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="Print what would be written; do not modify the file.")
    ns = parser.parse_args(args)

    try:
        result = recorder.migrate_recording(ns.name, force=ns.force, dry_run=ns.dry_run)
    except recorder.MigrationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not result.migrated:
        print(result.reason)
        sys.exit(0)

    suffix = " (dry-run)" if result.dry_run else ""
    backup_note = (f" Backup at {result.backup_path}."
                   if result.backup_path else "")
    print(
        f"Migrated {result.name}{suffix}: {result.text_mark_count} text marks"
        f" (primary button: {result.primary_button_label!r}).{backup_note}"
    )
    sys.exit(0)


def _cmd_wda_down(args: list[str]) -> None:
    """Handle `simdrive wda-down <udid>` — SIGTERM the running WDA daemon."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="simdrive wda-down",
        description=(
            "SIGTERM the WDA daemon for the given UDID (PID from pidfile).\n"
            "Use after a session, or before re-running bootstrap-device."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("udid", help="Device UDID previously passed to bootstrap-device.")

    ns = parser.parse_args(args)
    from .wda.bootstrap import wda_down

    try:
        wda_down(ns.udid)
    except Exception as exc:
        _log.error("wda-down failed: %s", exc)
        sys.exit(1)


def _cmd_trial(args: list[str]) -> None:
    """Handle `simdrive trial <subcommand> ...` CLI subcommand."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="simdrive trial")
    sub = parser.add_subparsers(dest="subcmd")

    start_p = sub.add_parser("start", help="Activate a 14-day free trial.")
    start_p.add_argument("--email", required=True, help="Your email address.")
    start_p.add_argument(
        "--offline-dev",
        action="store_true",
        default=False,
        help=(
            "Self-issue a local dev trial (no network required). "
            "The license is signed with the embedded dev key and valid for 14 days."
        ),
    )
    start_p.add_argument(
        "--license-path",
        default=None,
        help="Override the license.json path (default: ~/.simdrive/license.json).",
    )

    ns = parser.parse_args(args)
    if ns.subcmd is None:
        parser.print_help()
        sys.exit(1)

    from pathlib import Path as _Path
    from simdrive.license.cli import cmd_trial_start, _DEFAULT_LICENSE_PATH
    from simdrive.license.errors import LicenseError

    license_path = _Path(ns.license_path) if ns.license_path else _DEFAULT_LICENSE_PATH

    try:
        result = cmd_trial_start(
            ns.email,
            offline_dev=ns.offline_dev,
            license_path=license_path,
        )
        print(result["message"])
        sys.exit(0)
    except LicenseError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_license(args: list[str]) -> None:
    """Handle `simdrive license <subcommand> ...` CLI subcommand."""
    import argparse
    import sys
    import time as _time

    parser = argparse.ArgumentParser(prog="simdrive license")
    sub = parser.add_subparsers(dest="subcmd")
    sub.add_parser("show", help="Show current license details.")
    sub.add_parser("path", help="Print the resolved license.json path.")

    ns = parser.parse_args(args)
    if ns.subcmd is None:
        parser.print_help()
        sys.exit(1)

    from simdrive.license.entitlement import check_entitlement, _DEFAULT_LICENSE_PATH
    from simdrive.license.errors import LicenseError

    if ns.subcmd == "path":
        print(str(_DEFAULT_LICENSE_PATH))
        sys.exit(0)

    # show
    try:
        ent = check_entitlement()
        now = int(_time.time())
        days_left = max(0, (ent.expires_at - now) // 86400)
        print(
            f"subject:    {ent.customer_email}\n"
            f"tier:       {ent.tier}\n"
            f"expires_at: {ent.expires_at}\n"
            f"days_left:  {days_left}"
        )
        sys.exit(0)
    except LicenseError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        sys.exit(1)


# Subcommand dispatch registry — maps the first CLI argument to its handler.
_SUBCOMMANDS: dict = {
    "run": _cmd_run,
    "ci": _cmd_ci,
    "bootstrap-device": _cmd_bootstrap_device,
    "wda-up": _cmd_wda_up,
    "wda-down": _cmd_wda_down,
    "trial": _cmd_trial,
    "license": _cmd_license,
    "lint-recordings": _cmd_lint_recordings,
    "migrate-recording": _cmd_migrate_recording,
}


def serve() -> None:
    """Console-script entry point. Blocks running the MCP server on stdio.

    Subcommand dispatch via _SUBCOMMANDS registry:
      "run"              → _cmd_run
      "ci"               → _cmd_ci
      "bootstrap-device" → _cmd_bootstrap_device
      "wda-up"           → _cmd_wda_up
      "wda-down"         → _cmd_wda_down
      "trial"            → _cmd_trial
      "license"          → _cmd_license
    """
    import sys
    args = sys.argv[1:]
    if args:
        flag = args[0]
        if flag in ("--version", "-V"):
            print(f"simdrive {__version__}")
            sys.exit(0)
        if flag in ("--help", "-h"):
            print(_HELP_TEXT, end="")
            sys.exit(0)
        handler = _SUBCOMMANDS.get(flag)
        if handler is not None:
            handler(args[1:])
            return
    asyncio.run(_serve_async())


if __name__ == "__main__":
    serve()
