"""simdrive MCP server.

Exposes 12 tools to any MCP-compatible host (Claude, Cline, etc.):

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
import json
import time
from pathlib import Path
from typing import Any

from . import __version__, act, errors, observe, recorder, session, sim, som


def _now() -> float:
    return time.time()


# --------------------------- Tool implementations --------------------------- #


def tool_session_start(arguments: dict) -> dict:
    device_name = arguments.get("device") or arguments.get("device_name")
    os_version = arguments.get("os_version")
    udid = arguments.get("udid")
    app_bundle_id = arguments.get("app_bundle_id")
    s = session.start(device_name=device_name, os_version=os_version, udid=udid, app_bundle_id=app_bundle_id)
    return {
        "session_id": s.session_id,
        "udid": s.device.udid,
        "device": s.device.name,
        "os_version": s.device.os_version,
        "app_bundle_id": s.app_bundle_id,
        "state": s.state,
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
    )
    s.last_screenshot_w = obs.screenshot_w
    s.last_screenshot_h = obs.screenshot_h
    s.last_screenshot_path = obs.screenshot_path
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


def _resolve_target_xy(s, args: dict) -> tuple[int, int, str]:
    """Translate {x,y} | {mark} | {text} into pixel coords + a debug 'how' string."""
    if "x" in args and "y" in args:
        return int(args["x"]), int(args["y"]), "coords"

    if "mark" in args:
        mark_id = int(args["mark"])
        m = som.find_by_mark_id(s.last_marks or [], mark_id)
        if not m:
            available = [{"id": mk.id, "text": mk.text} for mk in (s.last_marks or [])]
            raise errors.target_not_found("mark", mark_id, available)
        cx, cy = m.center
        return cx, cy, f"mark:{mark_id}({m.text!r})"

    if "text" in args:
        query = str(args["text"])
        m = som.find_by_text(s.last_marks or [], query)
        if not m:
            available = [mk.text for mk in (s.last_marks or [])]
            raise errors.target_not_found("text", query, available)
        cx, cy = m.center
        return cx, cy, f"text:{query!r}->mark:{m.id}"

    raise errors.missing_target()


def _record_act_step(s, action: str, args: dict, pre_path: Path) -> None:
    if s.recorder is None:
        return
    # Capture post-screenshot for the recording.
    post_obs = observe.observe(s.device.udid, s.workdir / "observations")
    s.last_screenshot_w = post_obs.screenshot_w
    s.last_screenshot_h = post_obs.screenshot_h
    s.last_screenshot_path = post_obs.screenshot_path
    s.recorder.add_step(action, args, pre_path, post_obs.screenshot_path)


def tool_tap(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    sw, sh = _ensure_screenshot_dims(s)
    x, y, resolved_via = _resolve_target_xy(s, arguments)
    pre_path = s.last_screenshot_path
    sx, sy = act.tap(x, y, sw, sh, udid=s.device.udid)
    s.last_action_at = _now()
    args = {"x": x, "y": y, "screenshot_w": sw, "screenshot_h": sh}
    if pre_path:
        _record_act_step(s, "tap", args, pre_path)
    return {
        "ok": True,
        "pixel_x": x,
        "pixel_y": y,
        "screen_x": sx,
        "screen_y": sy,
        "screenshot_size_pixels": [sw, sh],
        "resolved_via": resolved_via,
    }


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
        x1, y1, _ = _resolve_target_xy(s, arguments["from"])
        x2, y2, _ = _resolve_target_xy(s, arguments["to"])
        resolved_via = "from/to"
    else:
        raise ValueError("swipe requires {x1,y1,x2,y2} or {from: target, to: target}")

    pre_path = s.last_screenshot_path
    act.swipe(x1, y1, x2, y2, sw, sh, duration_ms, udid=s.device.udid)
    s.last_action_at = _now()
    args = {
        "x1": x1, "y1": y1, "x2": x2, "y2": y2,
        "screenshot_w": sw, "screenshot_h": sh, "duration_ms": duration_ms,
    }
    if pre_path:
        _record_act_step(s, "swipe", args, pre_path)
    return {"ok": True, "resolved_via": resolved_via}


def tool_type_text(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    text = str(arguments["text"])
    tap_target = arguments.get("tap_first")  # optional target dict to focus a field first

    if tap_target:
        sw, sh = _ensure_screenshot_dims(s)
        tx, ty, _ = _resolve_target_xy(s, tap_target)
        act.tap(tx, ty, sw, sh, udid=s.device.udid)
        import time as _t
        _t.sleep(0.6)  # give the keyboard a moment to come up

    pre_obs = observe.observe(s.device.udid, s.workdir / "observations") if s.recorder else None
    pre_path = pre_obs.screenshot_path if pre_obs else s.last_screenshot_path
    act.type_text(text, udid=s.device.udid)
    s.last_action_at = _now()
    if pre_path:
        _record_act_step(s, "type_text", {"text": text}, pre_path)
    return {"ok": True, "chars": len(text)}


def tool_press_key(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    key = str(arguments["key"])
    pre_obs = observe.observe(s.device.udid, s.workdir / "observations") if s.recorder else None
    pre_path = pre_obs.screenshot_path if pre_obs else s.last_screenshot_path
    act.press_key(key, udid=s.device.udid)
    s.last_action_at = _now()
    if pre_path:
        _record_act_step(s, "press_key", {"key": key}, pre_path)
    return {"ok": True, "key": key}


def tool_record_start(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    name = str(arguments["name"])
    rec = recorder.start(s, name)
    return {"ok": True, "name": rec.name, "path": str(rec.root)}


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
    return recorder.replay(name, s, on_drift=on_drift, drift_threshold=threshold)


def tool_logs(arguments: dict) -> dict:
    s = session.get(arguments["session_id"])
    lines = int(arguments.get("lines", 200))
    predicate = arguments.get("predicate")
    text = sim.get_log_tail(s.device.udid, lines=lines, predicate=predicate)
    return {"ok": True, "lines": len(text.splitlines()), "logs": text}


# ----------------------------- MCP wiring ------------------------------- #


# Tool name → (handler, json schema for arguments, description)
_TOOLS: list[dict] = [
    {
        "name": "session_start",
        "description": "Boot/find an iOS simulator, optionally launch an app, and start a simdrive session. Returns session_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "Device name, e.g. 'iPhone 17 Pro'. Optional if a sim is already booted."},
                "os_version": {"type": "string", "description": "iOS version, e.g. '26.3'. Optional."},
                "udid": {"type": "string", "description": "Specific simulator UDID. Overrides device/os_version."},
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
            "{mark: <id>} (a mark id from the most recent observe), or "
            "{text: \"...\"} (best-match against the last observe's marks)."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string"},
                "x": {"type": "integer", "description": "Pixel x (paired with y)."},
                "y": {"type": "integer", "description": "Pixel y (paired with x)."},
                "mark": {"type": "integer", "description": "Mark id from the latest observe."},
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
            "that target first to focus a field, then type."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["session_id", "text"],
            "properties": {
                "session_id": {"type": "string"},
                "text": {"type": "string"},
                "tap_first": {"type": "object", "description": "Optional focus target: {x,y}, {mark}, or {text}."},
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
        "description": "Replay a recorded session by name. on_drift halt|warn|force; drift_threshold default 0.85 (SSIM).",
        "inputSchema": {
            "type": "object",
            "required": ["session_id", "name"],
            "properties": {
                "session_id": {"type": "string"},
                "name": {"type": "string"},
                "on_drift": {"type": "string", "enum": ["halt", "warn", "force"], "default": "halt"},
                "drift_threshold": {"type": "number", "default": 0.85},
            },
        },
        "handler": tool_replay,
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
]


def list_tools() -> list[dict]:
    """Return the tool list (sans handlers) suitable for MCP tools/list."""
    return [{k: v for k, v in t.items() if k != "handler"} for t in _TOOLS]


def call_tool(name: str, arguments: dict) -> dict:
    for t in _TOOLS:
        if t["name"] == name:
            return t["handler"](arguments or {})
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


def serve() -> None:
    """Console-script entry point. Blocks running the MCP server on stdio."""
    asyncio.run(_serve_async())


if __name__ == "__main__":
    serve()
