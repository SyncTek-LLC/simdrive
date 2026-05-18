"""Tests for D5 + D7 — apps + app_state route to devicectl on target=device.

simctl is simulator-only. On a CoreDevice UUID, `xcrun simctl listapps` and
`xcrun simctl spawn launchctl list` either return empty results or leak
"Invalid device" detail strings. The fix is to branch on session.target and
call the devicectl equivalents:

  - apps      → `xcrun devicectl device info apps --device <udid>`
  - app_state → `xcrun devicectl device info processes --device <udid>`
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── session factory ───────────────────────────────────────────────────────────


def _make_device_session(
    tmp_path: Path,
    sid: str = "diag-dev",
    udid: str = "31471BBD-6889-5DAC-9497-DIAGSESSION",
    bundle_id: str = "io.synctek.atlas-portal",
    hardware_udid: str = "00008150-001400000000001C",
):
    from simdrive import session as session_mod
    from simdrive.sim import Device

    d = Device(udid=udid, name="Moes Max", os_version="26.3.1", state="active")
    workdir = tmp_path / "sessions" / sid
    workdir.mkdir(parents=True, exist_ok=True)
    s = session_mod.Session(
        session_id=sid,
        device=d,
        workdir=workdir,
        target="device",
        app_bundle_id=bundle_id,
    )
    # Stash hardware_udid so the device path can use it for devicectl --device.
    # The session API doesn't expose this directly; the server reads it from
    # the WDA registry by udid. For the test, mock wda.registry.load to return it.
    session_mod._SESSIONS[sid] = s
    return s


@pytest.fixture(autouse=True)
def _isolate_sessions():
    from simdrive import session as session_mod
    before = set(session_mod._SESSIONS.keys())
    yield
    for k in list(session_mod._SESSIONS.keys()):
        if k not in before:
            session_mod._SESSIONS.pop(k, None)


def _fake_apps_json(*entries) -> dict:
    return {"result": {"apps": list(entries)}}


def _fake_processes_json(*entries) -> dict:
    return {"result": {"runningProcesses": list(entries)}}


# Sample devicectl output shapes — mirror what `xcrun devicectl device info` writes.
_APP_ATLAS = {
    "appClip": False,
    "builtByDeveloper": True,
    "bundleIdentifier": "io.synctek.atlas-portal",
    "bundleVersion": "1",
    "defaultApp": False,
    "name": "Atlas Portal",
    "removable": True,
    "url": "file:///private/var/containers/Bundle/Application/AAAA/Atlas%20Portal.app/",
    "version": "1.0",
}
_APP_SPLASH = {
    "appClip": False,
    "builtByDeveloper": True,
    "bundleIdentifier": "com.synctek.SplashMate",
    "bundleVersion": "42",
    "name": "SplashMate",
    "removable": True,
    "url": "file:///private/var/containers/Bundle/Application/BBBB/SplashMate.app/",
    "version": "1.1.0",
}


# ── D5: apps ──────────────────────────────────────────────────────────────────


def test_apps_target_device_calls_devicectl_not_simctl(tmp_path):
    """tool_apps with target=device must invoke `devicectl device info apps`."""
    _make_device_session(tmp_path)

    seen_argvs: list[list[str]] = []

    def _fake_run(argv, *args, **kwargs):
        seen_argvs.append(argv)
        json_path = None
        for i, tok in enumerate(argv):
            if tok in ("--json-output", "-j") and i + 1 < len(argv):
                json_path = argv[i + 1]
        if json_path and json_path != "-":
            Path(json_path).write_text(json.dumps(_fake_apps_json(_APP_ATLAS, _APP_SPLASH)))
        return subprocess.CompletedProcess(argv, 0, "", "")

    with patch("subprocess.run", side_effect=_fake_run):
        from simdrive import server
        result = server.tool_apps({"session_id": "diag-dev"})

    assert any("devicectl" in argv for argv in seen_argvs), (
        f"expected devicectl call, got: {seen_argvs!r}"
    )
    assert not any("simctl" in argv for argv in seen_argvs), (
        f"simctl must not be invoked for target=device, got: {seen_argvs!r}"
    )
    bundles = {a["bundle_id"] for a in result["apps"]}
    assert bundles == {"io.synctek.atlas-portal", "com.synctek.SplashMate"}


def test_apps_target_device_argv_uses_device_flag(tmp_path):
    """The devicectl call must pass `--device <udid>` and `--json-output`."""
    _make_device_session(tmp_path, udid="UDID-DEV-A")

    seen_argvs: list[list[str]] = []

    def _fake_run(argv, *args, **kwargs):
        seen_argvs.append(argv)
        for i, tok in enumerate(argv):
            if tok in ("--json-output", "-j") and i + 1 < len(argv):
                p = argv[i + 1]
                if p != "-":
                    Path(p).write_text(json.dumps(_fake_apps_json(_APP_ATLAS)))
        return subprocess.CompletedProcess(argv, 0, "", "")

    with patch("subprocess.run", side_effect=_fake_run):
        from simdrive import server
        server.tool_apps({"session_id": "diag-dev"})

    devicectl_argv = next(a for a in seen_argvs if "devicectl" in a)
    # subcommand path: xcrun devicectl device info apps
    assert devicectl_argv[0] == "xcrun"
    assert devicectl_argv[1] == "devicectl"
    assert "info" in devicectl_argv
    assert "apps" in devicectl_argv
    # --device <udid>
    idx = devicectl_argv.index("--device")
    assert devicectl_argv[idx + 1] == "UDID-DEV-A"
    # --json-output present
    assert any(t in devicectl_argv for t in ("--json-output", "-j"))


def test_apps_target_device_normalizes_to_existing_schema(tmp_path):
    """Each returned app must include bundle_id, name, version, path — the
    same keys the simulator path emits — populated from devicectl JSON."""
    _make_device_session(tmp_path)

    def _fake_run(argv, *args, **kwargs):
        for i, tok in enumerate(argv):
            if tok in ("--json-output", "-j") and i + 1 < len(argv):
                p = argv[i + 1]
                if p != "-":
                    Path(p).write_text(json.dumps(_fake_apps_json(_APP_ATLAS)))
        return subprocess.CompletedProcess(argv, 0, "", "")

    with patch("subprocess.run", side_effect=_fake_run):
        from simdrive import server
        result = server.tool_apps({"session_id": "diag-dev"})

    assert result["apps"][0]["bundle_id"] == "io.synctek.atlas-portal"
    assert result["apps"][0]["name"] == "Atlas Portal"
    assert result["apps"][0]["version"] == "1.0"
    # path: the devicectl URL gets translated to a filesystem path (file:// stripped, %20 decoded)
    assert "Atlas Portal.app" in result["apps"][0]["path"]
    assert "file://" not in result["apps"][0]["path"]


def test_apps_target_simulator_still_uses_simctl(tmp_path, monkeypatch):
    """Regression guard: simulator-target sessions must not regress to devicectl."""
    from simdrive import session as session_mod, diagnostics
    from simdrive.sim import Device
    import plistlib

    sid = "sim-sess"
    s = session_mod.Session(
        session_id=sid,
        device=Device(udid="SIM-UDID", name="iPhone 16", os_version="26.3", state="Booted"),
        workdir=tmp_path / "wd",
        target="simulator",
    )
    s.workdir.mkdir(parents=True, exist_ok=True)
    session_mod._SESSIONS[sid] = s

    plist_payload = plistlib.dumps({
        "com.example.App": {
            "CFBundleDisplayName": "Example",
            "CFBundleShortVersionString": "1.2.3",
            "Path": "/Applications/Example.app",
        },
    })

    def fake_run(cmd, timeout=15.0):
        return subprocess.CompletedProcess(cmd, 0, plist_payload.decode("utf-8"), "")

    monkeypatch.setattr(diagnostics, "_run", fake_run)

    from simdrive import server
    result = server.tool_apps({"session_id": sid})
    bundles = {a["bundle_id"] for a in result["apps"]}
    assert bundles == {"com.example.App"}


# ── D7: app_state ─────────────────────────────────────────────────────────────


def test_app_state_target_device_running(tmp_path):
    """app_state must return state=running with pid when devicectl shows the bundle's
    executable URL in runningProcesses."""
    _make_device_session(tmp_path, bundle_id="io.synctek.atlas-portal")

    apps_json = _fake_apps_json(_APP_ATLAS)
    procs_json = _fake_processes_json(
        {"executable": "file:///sbin/launchd", "processIdentifier": 1},
        {
            "executable": "file:///private/var/containers/Bundle/Application/AAAA/Atlas%20Portal.app/Atlas%20Portal",
            "processIdentifier": 4242,
        },
    )

    def _fake_run(argv, *args, **kwargs):
        for i, tok in enumerate(argv):
            if tok in ("--json-output", "-j") and i + 1 < len(argv):
                p = argv[i + 1]
                if p == "-":
                    continue
                if "apps" in argv:
                    Path(p).write_text(json.dumps(apps_json))
                elif "processes" in argv:
                    Path(p).write_text(json.dumps(procs_json))
        return subprocess.CompletedProcess(argv, 0, "", "")

    with patch("subprocess.run", side_effect=_fake_run):
        from simdrive import server
        result = server.tool_app_state({"session_id": "diag-dev"})

    assert result["state"] == "running", f"expected running, got {result!r}"
    assert result["bundle_id"] == "io.synctek.atlas-portal"
    assert result["pid"] == 4242


def test_app_state_target_device_not_running(tmp_path):
    """When the bundle's executable URL is absent from runningProcesses, state=not-running."""
    _make_device_session(tmp_path, bundle_id="io.synctek.atlas-portal")

    apps_json = _fake_apps_json(_APP_ATLAS)
    procs_json = _fake_processes_json(
        {"executable": "file:///sbin/launchd", "processIdentifier": 1},
        # No Atlas Portal process running.
    )

    def _fake_run(argv, *args, **kwargs):
        for i, tok in enumerate(argv):
            if tok in ("--json-output", "-j") and i + 1 < len(argv):
                p = argv[i + 1]
                if p == "-":
                    continue
                if "apps" in argv:
                    Path(p).write_text(json.dumps(apps_json))
                elif "processes" in argv:
                    Path(p).write_text(json.dumps(procs_json))
        return subprocess.CompletedProcess(argv, 0, "", "")

    with patch("subprocess.run", side_effect=_fake_run):
        from simdrive import server
        result = server.tool_app_state({"session_id": "diag-dev"})

    assert result["state"] == "not-running"
    assert result["bundle_id"] == "io.synctek.atlas-portal"
    assert result["pid"] is None


def test_app_state_target_device_does_not_call_simctl(tmp_path):
    """No simctl spawn / launchctl call may happen for target=device."""
    _make_device_session(tmp_path)

    seen_argvs: list[list[str]] = []

    def _fake_run(argv, *args, **kwargs):
        seen_argvs.append(argv)
        for i, tok in enumerate(argv):
            if tok in ("--json-output", "-j") and i + 1 < len(argv):
                p = argv[i + 1]
                if p == "-":
                    continue
                if "apps" in argv:
                    Path(p).write_text(json.dumps(_fake_apps_json(_APP_ATLAS)))
                elif "processes" in argv:
                    Path(p).write_text(json.dumps(_fake_processes_json()))
        return subprocess.CompletedProcess(argv, 0, "", "")

    with patch("subprocess.run", side_effect=_fake_run):
        from simdrive import server
        server.tool_app_state({"session_id": "diag-dev"})

    assert not any("simctl" in argv for argv in seen_argvs), (
        f"simctl must not be called for target=device app_state: {seen_argvs!r}"
    )
    assert not any("launchctl" in argv for argv in seen_argvs), (
        f"launchctl must not be called for target=device app_state: {seen_argvs!r}"
    )


def test_app_state_target_simulator_still_uses_simctl(tmp_path, monkeypatch):
    """Regression guard: simulator-target sessions keep the simctl path."""
    from simdrive import session as session_mod, diagnostics
    from simdrive.sim import Device

    sid = "sim-sess-state"
    s = session_mod.Session(
        session_id=sid,
        device=Device(udid="SIM-UDID", name="iPhone 16", os_version="26.3", state="Booted"),
        workdir=tmp_path / "wd",
        app_bundle_id="com.example.App",
        target="simulator",
    )
    s.workdir.mkdir(parents=True, exist_ok=True)
    session_mod._SESSIONS[sid] = s

    def fake_run(cmd, timeout=10.0):
        return subprocess.CompletedProcess(
            cmd, 0,
            "1234\t0\tUIKitApplication:com.example.App[uuid]\n",
            "",
        )

    monkeypatch.setattr(diagnostics, "_run", fake_run)

    from simdrive import server
    result = server.tool_app_state({"session_id": sid})
    assert result["state"] == "foreground"
    assert result["pid"] == 1234
