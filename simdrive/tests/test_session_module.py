"""Unit tests for ``simdrive.session`` — start/end/get + audit logging."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from simdrive import errors, session
from simdrive.sim import Device, SimError


# ── append_action ───────────────────────────────────────────────────────────


def test_append_action_writes_jsonl(tmp_path):
    s = session.Session(
        session_id="t",
        device=Device(udid="U", name="iPhone", os_version="26.3", state="Booted"),
        workdir=tmp_path,
    )
    session.append_action(s, {"action": "tap", "x": 1})
    session.append_action(s, {"action": "tap", "x": 2})
    lines = (tmp_path / "actions.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["action"] == "tap"


def test_append_action_swallows_errors(tmp_path):
    """If the workdir is a file (not a dir), append_action must not raise."""
    bad_dir = tmp_path / "not-a-dir"
    bad_dir.write_text("oops")  # file, not dir
    s = session.Session(
        session_id="t",
        device=Device(udid="U", name="iPhone", os_version="26.3", state="Booted"),
        workdir=bad_dir,
    )
    # Should not raise even though the path isn't a directory
    session.append_action(s, {"a": 1})


# ── start (simulator) ───────────────────────────────────────────────────────


def test_start_no_udid_no_name_uses_first_booted(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    session._SESSIONS.clear()
    booted = Device(udid="U1", name="iPhone", os_version="26.3", state="Booted")
    with patch("simdrive.sim.first_booted", return_value=booted):
        s = session.start()
    assert s.device.udid == "U1"
    assert s.target == "simulator"
    assert s.workdir.exists()
    session._SESSIONS.clear()


def test_start_no_booted_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    session._SESSIONS.clear()
    with patch("simdrive.sim.first_booted", return_value=None):
        with pytest.raises(errors.SimdriveError) as exc:
            session.start()
    assert exc.value.code == "no_device"


def test_start_by_udid_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    session._SESSIONS.clear()
    with patch("simdrive.sim.find_device", return_value=None):
        with pytest.raises(errors.SimdriveError) as exc:
            session.start(udid="UNKNOWN")
    assert exc.value.code == "no_device"


def test_start_by_name_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    session._SESSIONS.clear()
    with patch("simdrive.sim.find_device", return_value=None):
        with pytest.raises(errors.SimdriveError) as exc:
            session.start(device_name="iPhone Nonexistent")
    assert exc.value.code == "no_device"


def test_start_boots_when_shutdown(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    session._SESSIONS.clear()
    shutdown = Device(udid="U1", name="iPhone", os_version="26.3", state="Shutdown")
    booted = Device(udid="U1", name="iPhone", os_version="26.3", state="Booted")
    with patch("simdrive.sim.find_device", side_effect=[shutdown, booted]), \
         patch("simdrive.sim.boot") as mock_boot:
        s = session.start(udid="U1")
    assert mock_boot.called
    assert s.device.is_booted
    session._SESSIONS.clear()


def test_start_with_app_launches(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    session._SESSIONS.clear()
    booted = Device(udid="U1", name="iPhone", os_version="26.3", state="Booted")
    with patch("simdrive.sim.find_device", return_value=booted), \
         patch("simdrive.sim.launch_app") as mock_launch:
        s = session.start(udid="U1", app_bundle_id="com.example.App")
    mock_launch.assert_called_with("U1", "com.example.App")
    assert s.app_bundle_id == "com.example.App"
    session._SESSIONS.clear()


def test_start_app_launch_failure_wraps_simerror(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    session._SESSIONS.clear()
    booted = Device(udid="U1", name="iPhone", os_version="26.3", state="Booted")
    with patch("simdrive.sim.find_device", return_value=booted), \
         patch("simdrive.sim.launch_app", side_effect=SimError("launch boom")):
        with pytest.raises(SimError) as exc:
            session.start(udid="U1", app_bundle_id="com.example.App")
    assert "launch_app" in str(exc.value)


# ── get / end / all_sessions ────────────────────────────────────────────────


def test_get_missing_raises():
    session._SESSIONS.clear()
    with pytest.raises(errors.SimdriveError) as exc:
        session.get("nope")
    assert exc.value.code == "no_session"


def test_end_unknown_session_is_noop():
    session._SESSIONS.clear()
    # Should not raise
    session.end("never-existed")


def test_end_terminates_app_by_default(tmp_path):
    session._SESSIONS.clear()
    s = session.Session(
        session_id="abc",
        device=Device(udid="U1", name="iPhone", os_version="26.3", state="Booted"),
        workdir=tmp_path,
        app_bundle_id="com.example.App",
    )
    session._SESSIONS["abc"] = s
    with patch("simdrive.sim.terminate_app") as mock_term:
        session.end("abc")
    mock_term.assert_called_with("U1", "com.example.App")
    assert "abc" not in session._SESSIONS


def test_end_terminate_swallowed_on_error(tmp_path):
    session._SESSIONS.clear()
    s = session.Session(
        session_id="abc",
        device=Device(udid="U1", name="iPhone", os_version="26.3", state="Booted"),
        workdir=tmp_path,
        app_bundle_id="com.example.App",
    )
    session._SESSIONS["abc"] = s
    with patch("simdrive.sim.terminate_app", side_effect=RuntimeError("boom")):
        # Must not raise — terminate is best-effort.
        session.end("abc")
    assert "abc" not in session._SESSIONS


def test_end_skip_terminate_when_disabled(tmp_path):
    session._SESSIONS.clear()
    s = session.Session(
        session_id="abc",
        device=Device(udid="U1", name="iPhone", os_version="26.3", state="Booted"),
        workdir=tmp_path,
        app_bundle_id="com.example.App",
    )
    session._SESSIONS["abc"] = s
    with patch("simdrive.sim.terminate_app") as mock_term:
        session.end("abc", terminate_app=False)
    assert not mock_term.called


def test_all_sessions_lists_active(tmp_path):
    session._SESSIONS.clear()
    s1 = session.Session(
        session_id="a", device=Device(udid="U1", name="i", os_version="26.3", state="Booted"),
        workdir=tmp_path / "a",
    )
    s2 = session.Session(
        session_id="b", device=Device(udid="U2", name="i", os_version="26.3", state="Booted"),
        workdir=tmp_path / "b",
    )
    session._SESSIONS["a"] = s1
    session._SESSIONS["b"] = s2
    out = session.all_sessions()
    assert len(out) == 2
    assert {x.session_id for x in out} == {"a", "b"}
    session._SESSIONS.clear()


# ── _start_device ───────────────────────────────────────────────────────────


def test_start_device_no_udid_raises():
    session._SESSIONS.clear()
    with pytest.raises(errors.SimdriveError) as exc:
        session._start_device(udid=None, app_bundle_id=None)
    assert exc.value.code == "no_device"


def test_start_device_no_registry_raises():
    session._SESSIONS.clear()
    with patch("simdrive.wda.registry.load", return_value=None):
        with pytest.raises(errors.SimdriveError) as exc:
            session._start_device(udid="UDID-X", app_bundle_id=None)
    # wda_not_bootstrapped is a SimdriveError subclass
    assert "bootstrap" in (exc.value.message or "").lower() or exc.value.code == "wda_not_bootstrapped"


def test_workroot_default_uses_home(monkeypatch):
    monkeypatch.delenv("SIMDRIVE_HOME", raising=False)
    root = session._workroot()
    assert root.name == ".simdrive"


def test_workroot_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path / "alt"))
    root = session._workroot()
    assert root == Path(str(tmp_path / "alt"))
