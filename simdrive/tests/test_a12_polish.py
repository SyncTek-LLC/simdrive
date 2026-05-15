"""Tests for simdrive a12 polish items.

Covers:
  1. Per-target log-filter API (predicate_kind)
  2. Tool-schema per-target parity markers
  3. SIMDRIVE_HTTP_DEBUG verbose mode
  4. apps includes CFBundleVersion as 'build'
  5. session_start replace_existing flag
  6. dismiss_sheet on device via WDA swipe-down
  7. devicectl noise filter
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_sim_session(tmp_path: Path, udid: str = "SIM-UDID-A12"):
    from simdrive.sim import Device
    device = Device(udid=udid, name="iPhone 17 Pro", os_version="26.0", state="booted")
    return SimpleNamespace(
        session_id="sim-session-a12",
        device=device,
        target="simulator",
        app_bundle_id="com.example.app",
        workdir=tmp_path,
        last_screenshot_w=1290,
        last_screenshot_h=2796,
        last_screenshot_path=None,
        last_marks=[],
        last_action_at=0.0,
        recorder=None,
        wda_client=None,
        pixel_per_point_scale=None,
    )


def _make_device_session(tmp_path: Path, udid: str = "DEV-UDID-A12"):
    from simdrive.sim import Device
    device = Device(udid=udid, name="Moes Max", os_version="26.0", state="active")
    return SimpleNamespace(
        session_id="dev-session-a12",
        device=device,
        target="device",
        app_bundle_id="com.example.app",
        workdir=tmp_path,
        last_screenshot_w=1320,
        last_screenshot_h=2868,
        last_screenshot_path=None,
        last_marks=[],
        last_action_at=0.0,
        recorder=None,
        wda_client=None,
        pixel_per_point_scale=3.0,
    )


# ── Item 1: predicate_kind ────────────────────────────────────────────────────


def test_logs_predicate_kind_regex_sim(tmp_path, monkeypatch):
    """predicate_kind='regex' on sim path filters lines via re.search."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)

    raw_lines = ["ERROR: bad thing", "INFO: all good", "WARNING: check this", "ERROR: second"]
    monkeypatch.setattr(
        "simdrive.sim.get_log_tail",
        lambda udid, lines, predicate: "\n".join(raw_lines),
    )

    result = server_mod.tool_logs({
        "session_id": s.session_id,
        "lines": 50,
        "predicate": "ERROR",
        "predicate_kind": "regex",
    })

    assert result["ok"] is True
    log_lines = [ln for ln in result["logs"].splitlines() if ln]
    assert all("ERROR" in ln for ln in log_lines), (
        f"regex filter should only return ERROR lines, got: {log_lines}"
    )
    assert len(log_lines) == 2, f"Expected 2 ERROR lines, got {len(log_lines)}: {log_lines}"


def test_logs_predicate_kind_substring_device(tmp_path, monkeypatch):
    """predicate_kind='substring' on device filters lines via Python 'in'."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    from simdrive import device as device_mod

    s = _make_device_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)

    raw_lines = ["ERROR: bad", "INFO: fine", "DEBUG: verbose", "ERROR: second"]
    monkeypatch.setattr(device_mod, "_which", lambda name: f"/usr/bin/{name}")

    fake_proc = MagicMock()
    fake_proc.communicate.return_value = ("\n".join(raw_lines) + "\n", "")
    monkeypatch.setattr(device_mod.subprocess, "Popen", lambda *a, **kw: fake_proc)

    result = server_mod.tool_logs({
        "session_id": s.session_id,
        "lines": 50,
        "predicate": "ERROR",
        "predicate_kind": "substring",
    })

    assert result["ok"] is True
    log_lines = [ln for ln in result["logs"].splitlines() if ln]
    assert all("ERROR" in ln for ln in log_lines), (
        f"substring filter should only return ERROR lines: {log_lines}"
    )


def test_logs_nspredicate_device_downgrades_to_substring(tmp_path, monkeypatch, caplog):
    """predicate_kind='nspredicate' on device logs a WARNING and downgrades to substring."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    from simdrive import device as device_mod

    s = _make_device_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)

    raw_lines = ["MATCH: found it", "SKIP: ignore", "MATCH: second"]
    monkeypatch.setattr(device_mod, "_which", lambda name: f"/usr/bin/{name}")

    fake_proc = MagicMock()
    fake_proc.communicate.return_value = ("\n".join(raw_lines) + "\n", "")
    monkeypatch.setattr(device_mod.subprocess, "Popen", lambda *a, **kw: fake_proc)

    with caplog.at_level(logging.WARNING):
        result = server_mod.tool_logs({
            "session_id": s.session_id,
            "lines": 50,
            "predicate": "MATCH",
            "predicate_kind": "nspredicate",
        })

    assert result["ok"] is True
    # WARNING should have been emitted about the downgrade
    assert any("nspredicate" in rec.message and "downgrad" in rec.message.lower()
               for rec in caplog.records), (
        "Expected a WARNING about nspredicate downgrade on device"
    )
    # The filter should still work (substring match)
    log_lines = [ln for ln in result["logs"].splitlines() if ln]
    assert all("MATCH" in ln for ln in log_lines), f"Got unfiltered lines: {log_lines}"


def test_logs_invalid_predicate_kind_raises(tmp_path, monkeypatch):
    """Invalid predicate_kind returns an error (invalid_argument)."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    from simdrive import errors

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)

    with pytest.raises(errors.SimdriveError) as exc_info:
        server_mod.tool_logs({
            "session_id": s.session_id,
            "predicate_kind": "bogus_kind",
        })
    assert "predicate_kind" in str(exc_info.value) or "bogus_kind" in str(exc_info.value)


# ── Item 2: target parity markers ────────────────────────────────────────────


def test_all_mcp_tools_have_target_marker():
    """Every MCP tool description must start with (sim only), (sim + device), or (device only)."""
    from simdrive.server import _TOOLS

    missing = []
    for tool in _TOOLS:
        desc = tool.get("description", "")
        if not (desc.startswith("(sim") or desc.startswith("(device")):
            missing.append(tool["name"])

    assert not missing, (
        f"These tools have no target marker in their description: {missing}. "
        "Every tool description must start with '(sim only)', '(sim + device)', "
        "or '(device only)'."
    )


def test_record_start_is_sim_plus_device():
    """a13: record_start ships on device — must be marked (sim + device)."""
    from simdrive.server import _TOOLS
    t = next(t for t in _TOOLS if t["name"] == "record_start")
    assert t["description"].startswith("(sim + device)"), (
        f"record_start description must start with '(sim + device)' after a13, "
        f"got: {t['description'][:60]}"
    )


def test_dismiss_sheet_is_sim_plus_device():
    """dismiss_sheet now ships on device and must be marked (sim + device)."""
    from simdrive.server import _TOOLS
    t = next(t for t in _TOOLS if t["name"] == "dismiss_sheet")
    assert t["description"].startswith("(sim + device)"), (
        f"dismiss_sheet description must start with '(sim + device)', got: {t['description'][:60]}"
    )


def test_list_devices_is_device_only():
    """list_devices should be marked (device only)."""
    from simdrive.server import _TOOLS
    t = next(t for t in _TOOLS if t["name"] == "list_devices")
    assert t["description"].startswith("(device only)"), (
        f"list_devices description must start with '(device only)', got: {t['description'][:60]}"
    )


def test_logs_schema_has_predicate_kind():
    """logs tool schema must declare predicate_kind with enum."""
    from simdrive.server import _TOOLS
    t = next(t for t in _TOOLS if t["name"] == "logs")
    props = t["inputSchema"]["properties"]
    assert "predicate_kind" in props, "logs inputSchema missing predicate_kind"
    assert "enum" in props["predicate_kind"], "predicate_kind must have enum"
    assert set(props["predicate_kind"]["enum"]) == {"nspredicate", "regex", "substring"}


# ── Item 3: SIMDRIVE_HTTP_DEBUG ───────────────────────────────────────────────


def test_wda_http_debug_logs_request(monkeypatch, caplog):
    """With SIMDRIVE_HTTP_DEBUG set, _request logs method + path at DEBUG level."""
    import importlib
    import simdrive.wda.client as wda_client_mod

    # Patch env + reload module-level flag.
    monkeypatch.setenv("SIMDRIVE_HTTP_DEBUG", "1")
    monkeypatch.setattr(wda_client_mod, "_HTTP_DEBUG", True)

    import httpx
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.is_success = True
    mock_resp.json.return_value = {"value": {"sessionId": "abc"}}
    mock_resp.text = '{"value": {"sessionId": "abc"}}'
    mock_resp.status_code = 200

    client = wda_client_mod.WdaClient(host="localhost", port=8100)
    client._client = MagicMock()
    client._client.request.return_value = mock_resp

    with caplog.at_level(logging.DEBUG, logger="simdrive.wda.client"):
        client._request("GET", "/status")

    # Logs are emitted at INFO level (so they appear even without SIMDRIVE_DEBUG=1).
    wda_msgs = [r.message for r in caplog.records
                if r.name.startswith("simdrive.wda.client") and "[WDA]" in r.message]
    assert any("GET" in m and "/status" in m for m in wda_msgs), (
        f"Expected [WDA] log with 'GET /status', got: {wda_msgs}"
    )


def test_wda_http_debug_off_no_logs(monkeypatch, caplog):
    """Without SIMDRIVE_HTTP_DEBUG, _request emits no debug logs."""
    import simdrive.wda.client as wda_client_mod

    monkeypatch.setattr(wda_client_mod, "_HTTP_DEBUG", False)
    monkeypatch.delenv("SIMDRIVE_HTTP_DEBUG", raising=False)

    import httpx
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.is_success = True
    mock_resp.json.return_value = {}
    mock_resp.text = "{}"
    mock_resp.status_code = 200

    client = wda_client_mod.WdaClient(host="localhost", port=8100)
    client._client = MagicMock()
    client._client.request.return_value = mock_resp

    with caplog.at_level(logging.DEBUG, logger="simdrive.wda.client"):
        client._request("GET", "/status")

    debug_msgs = [r.message for r in caplog.records if "[WDA]" in r.message]
    assert not debug_msgs, (
        f"With _HTTP_DEBUG=False, no [WDA] logs expected, got: {debug_msgs}"
    )


# ── Item 4: apps build field ──────────────────────────────────────────────────


def test_apps_sim_has_build_field(monkeypatch):
    """list_apps (sim) returns entries with a 'build' key (CFBundleVersion)."""
    import plistlib
    from simdrive import diagnostics

    fake_plist_data = {
        "com.example.app": {
            "CFBundleDisplayName": "MyApp",
            "CFBundleShortVersionString": "2.1.0",
            "CFBundleVersion": "20100",
            "Path": "/private/var/containers/Bundle/MyApp.app",
        }
    }
    fake_plist_bytes = plistlib.dumps(fake_plist_data)

    fake_result = SimpleNamespace(
        returncode=0,
        stdout=fake_plist_bytes.decode("utf-8"),
        stderr="",
    )
    monkeypatch.setattr(diagnostics, "_run", lambda *a, **kw: fake_result)

    apps = diagnostics.list_apps("FAKE-SIM-UDID")
    assert len(apps) == 1
    app = apps[0]
    assert "build" in app, f"'build' key missing from sim app entry: {app}"
    assert app["build"] == "20100", f"Expected build='20100', got: {app['build']}"
    assert app["version"] == "2.1.0", f"Expected version='2.1.0', got: {app['version']}"


def test_apps_device_has_build_field(monkeypatch):
    """list_apps_device returns entries with a 'build' key (CFBundleVersion)."""
    from simdrive import diagnostics

    fake_data = {
        "result": {
            "apps": [
                {
                    "bundleIdentifier": "com.example.app",
                    "name": "MyApp",
                    "version": "2.1.0",
                    "buildVersion": "20100",
                    "url": "file:///var/containers/Bundle/MyApp.app",
                }
            ]
        }
    }
    monkeypatch.setattr(diagnostics, "_devicectl_info_json", lambda *a, **kw: fake_data)

    apps = diagnostics.list_apps_device("FAKE-DEV-UDID")
    assert len(apps) == 1
    app = apps[0]
    assert "build" in app, f"'build' key missing from device app entry: {app}"
    assert app["build"] == "20100", f"Expected build='20100', got: {app['build']}"


# ── Item 5: session_start replace_existing ────────────────────────────────────


def test_session_start_replace_existing_ends_old_session(tmp_path, monkeypatch):
    """replace_existing=True ends the existing session for the same UDID."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    from simdrive.sim import Device

    udid = "DEV-REPLACE-UDID"

    # Inject an existing session for the same UDID.
    existing_device = Device(udid=udid, name="Old Device", os_version="26.0", state="active")
    existing_session = SimpleNamespace(
        session_id="old-session-id",
        device=existing_device,
        target="device",
        app_bundle_id=None,
        workdir=tmp_path,
        last_screenshot_w=0,
        last_screenshot_h=0,
        last_screenshot_path=None,
        last_marks=[],
        last_action_at=0.0,
        recorder=None,
        wda_client=None,
        pixel_per_point_scale=None,
    )
    monkeypatch.setitem(session_mod._SESSIONS, "old-session-id", existing_session)

    ended_sids: list[str] = []

    def _fake_end(sid, terminate_app=True):
        ended_sids.append(sid)
        session_mod._SESSIONS.pop(sid, None)

    monkeypatch.setattr(session_mod, "end", _fake_end)

    # Mock session.start to avoid real device calls.
    new_device = Device(udid=udid, name="New Device", os_version="26.0", state="active")
    new_session = SimpleNamespace(
        session_id="new-session-id",
        device=new_device,
        target="device",
        app_bundle_id=None,
        state="active",
    )
    monkeypatch.setattr(session_mod, "start", lambda **kw: new_session)

    result = server_mod.tool_session_start({
        "udid": udid,
        "target": "device",
        "replace_existing": True,
    })

    assert "old-session-id" in ended_sids, (
        f"Expected old-session-id to be ended, ended: {ended_sids}"
    )
    assert result["session_id"] == "new-session-id"


def test_session_start_replace_existing_false_raises_when_active(tmp_path, monkeypatch):
    """replace_existing=False (default) raises SimdriveError when a session already exists for the same UDID.

    a12 spec: conflict detection — a second session for the same device must not
    silently start. The caller must explicitly pass replace_existing=True.
    """
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    from simdrive.errors import SimdriveError
    from simdrive.sim import Device

    udid = "DEV-NO-REPLACE-UDID"

    existing_device = Device(udid=udid, name="Old", os_version="26.0", state="active")
    existing_session = SimpleNamespace(
        session_id="existing-id",
        device=existing_device,
        target="device",
        app_bundle_id=None,
        workdir=tmp_path,
    )
    monkeypatch.setitem(session_mod._SESSIONS, "existing-id", existing_session)

    ended_sids: list[str] = []

    def _fake_end(sid, terminate_app=True):
        ended_sids.append(sid)

    monkeypatch.setattr(session_mod, "end", _fake_end)

    with pytest.raises(SimdriveError) as exc_info:
        server_mod.tool_session_start({
            "udid": udid,
            "target": "device",
            "replace_existing": False,
        })

    assert exc_info.value.code == "session_already_active", (
        f"Expected code='session_already_active', got: {exc_info.value.code!r}"
    )
    assert not ended_sids, (
        f"replace_existing=False must not end existing sessions, ended: {ended_sids}"
    )


# ── Item 6: dismiss_sheet on device ──────────────────────────────────────────


def test_dismiss_sheet_device_calls_wda_swipe(tmp_path, monkeypatch):
    """dismiss_sheet on target=device calls wda.swipe with F-006 scale applied."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    udid = "DEV-DISMISS-UDID"
    s = _make_device_session(tmp_path, udid=udid)
    s.last_screenshot_w = 1320
    s.last_screenshot_h = 2868
    s.pixel_per_point_scale = 3.0  # 3x device
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)

    mock_wda = MagicMock()
    s.wda_client = mock_wda

    # _ensure_screenshot_dims will use cached values since last_screenshot_w > 0
    # _session_scale should return 3.0

    # Suppress _session_scale to return fixed scale so test is deterministic.
    monkeypatch.setattr(server_mod, "_session_scale", lambda sess, wda=None: 3.0)

    result = server_mod.tool_dismiss_sheet({"session_id": s.session_id})

    assert result["ok"] is True
    assert mock_wda.swipe.called, "wda.swipe should have been called on device"
    call_args = mock_wda.swipe.call_args
    # Verify the coordinates are scaled: pixel / 3.0
    # x_mid = 1320//2 = 660, /3 = 220.0
    # y_start = 2868*0.2 = 573.6, /3 = 191.2
    # y_end = 2868*0.7 = 2007.6, /3 = 669.2
    from_x, from_y, to_x, to_y, duration = call_args.args
    assert abs(from_x - 660 / 3.0) < 1.0, f"Expected from_x≈{660/3:.1f}, got {from_x}"
    assert duration == 300


def test_dismiss_sheet_sim_uses_act_swipe(tmp_path, monkeypatch):
    """dismiss_sheet on target=simulator still routes through act.swipe (unchanged)."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    from simdrive import act

    s = _make_sim_session(tmp_path)
    s.last_screenshot_w = 1290
    s.last_screenshot_h = 2796
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)

    swipe_calls: list = []
    monkeypatch.setattr(act, "swipe", lambda *a, **kw: swipe_calls.append((a, kw)))

    result = server_mod.tool_dismiss_sheet({"session_id": s.session_id})

    assert result["ok"] is True
    assert swipe_calls, "act.swipe should have been called on simulator"


# ── Item 7: devicectl noise filter ───────────────────────────────────────────


def test_devicectl_noise_filter_strips_warning_on_success():
    """_filter_devicectl_stderr strips the noisy warning when rc=0."""
    from simdrive.device import _filter_devicectl_stderr

    noisy_stderr = (
        "No provider was found for this descriptor\n"
        "Useful diagnostic line\n"
        "No provider was found for this descriptor\n"
    )
    result = _filter_devicectl_stderr(noisy_stderr, returncode=0)
    assert "No provider was found" not in result, (
        f"Noise should be stripped on rc=0, got: {result!r}"
    )
    assert "Useful diagnostic line" in result, (
        "Real stderr lines should be preserved"
    )


def test_devicectl_noise_filter_keeps_stderr_on_failure():
    """_filter_devicectl_stderr preserves all stderr on non-zero exit."""
    from simdrive.device import _filter_devicectl_stderr

    noisy_stderr = "No provider was found for this descriptor\nActual error: device not found\n"
    result = _filter_devicectl_stderr(noisy_stderr, returncode=1)
    assert "No provider was found" in result, (
        f"On rc=1, all stderr should be preserved for diagnosis, got: {result!r}"
    )
    assert "Actual error: device not found" in result


def test_filter_applied_to_list_devices(monkeypatch):
    """list_devices strips the noise warning from stderr when command succeeds."""
    import json as _json
    import tempfile
    from simdrive import device as device_mod

    noisy_stderr = "No provider was found for this descriptor\n"
    fake_data = {"result": {"devices": []}}

    def _fake_run(cmd, **kwargs):
        # Write the JSON to the temp file the code creates.
        for i, arg in enumerate(cmd):
            if arg == "--json-output" and i + 1 < len(cmd):
                with open(cmd[i + 1], "w") as f:
                    _json.dump(fake_data, f)
                break
        return SimpleNamespace(returncode=0, stderr=noisy_stderr, stdout="")

    monkeypatch.setattr(device_mod.subprocess, "run", _fake_run)

    # Should not raise — noise is filtered, empty device list returned
    devices = device_mod.list_devices()
    assert devices == [], f"Expected empty device list, got: {devices}"
