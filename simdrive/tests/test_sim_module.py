"""Unit tests for ``simdrive.sim`` — wraps simctl invocations under mocks.

These tests exercise the simctl wrapper module without spinning up a real
simulator. All ``subprocess.run`` calls are mocked at the module boundary
(``simdrive.sim.subprocess.run``) so we can assert the wrappers parse the
expected outputs and raise the expected errors.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from simdrive import sim
from simdrive.sim import Device, SimError


# ── helpers ─────────────────────────────────────────────────────────────────


def _ok(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=stderr)


def _fail(stderr: str = "boom") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)


# ── list_devices ────────────────────────────────────────────────────────────


def test_list_devices_parses_runtime_and_devices():
    payload = {
        "devices": {
            "com.apple.CoreSimulator.SimRuntime.iOS-26-3": [
                {"udid": "U1", "name": "iPhone 17", "state": "Booted", "isAvailable": True},
                {"udid": "U2", "name": "iPhone 16", "state": "Shutdown", "isAvailable": True},
            ],
        }
    }
    with patch("simdrive.sim._simctl", return_value=_ok(json.dumps(payload))):
        devices = sim.list_devices()
    assert len(devices) == 2
    assert devices[0].udid == "U1"
    assert devices[0].os_version == "26.3"
    assert devices[0].is_booted is True
    assert devices[1].is_booted is False


def test_list_devices_skips_unavailable():
    payload = {
        "devices": {
            "com.apple.CoreSimulator.SimRuntime.iOS-26-3": [
                {"udid": "U1", "name": "iPhone X", "state": "Booted", "isAvailable": False},
                {"udid": "U2", "name": "iPhone Y", "state": "Shutdown", "isAvailable": True},
            ],
        }
    }
    with patch("simdrive.sim._simctl", return_value=_ok(json.dumps(payload))):
        devices = sim.list_devices()
    assert [d.udid for d in devices] == ["U2"]


def test_list_devices_raises_on_simctl_failure():
    with patch("simdrive.sim._simctl", return_value=_fail("simctl exploded")):
        with pytest.raises(SimError) as exc:
            sim.list_devices()
    assert "simctl list failed" in str(exc.value)


# ── find_device ─────────────────────────────────────────────────────────────


def _device_list():
    return [
        Device(udid="A1", name="iPhone 17", os_version="26.3", state="Shutdown"),
        Device(udid="A2", name="iPhone 17", os_version="26.4", state="Booted"),
        Device(udid="B1", name="iPhone 16", os_version="26.3", state="Booted"),
    ]


def test_find_device_by_udid_exact():
    with patch("simdrive.sim.list_devices", return_value=_device_list()):
        d = sim.find_device(udid="A2")
    assert d is not None
    assert d.udid == "A2"


def test_find_device_by_udid_missing_returns_none():
    with patch("simdrive.sim.list_devices", return_value=_device_list()):
        d = sim.find_device(udid="NOPE")
    assert d is None


def test_find_device_by_name_prefers_booted():
    with patch("simdrive.sim.list_devices", return_value=_device_list()):
        d = sim.find_device(name="iPhone 17")
    assert d is not None
    # Booted iPhone 17 should be preferred even though there's also a shutdown one.
    assert d.udid == "A2"


def test_find_device_by_name_filters_os_version():
    with patch("simdrive.sim.list_devices", return_value=_device_list()):
        d = sim.find_device(name="iPhone 17", os_version="26.3")
    assert d is not None
    assert d.udid == "A1"  # only candidate with os_version 26.3


def test_find_device_name_no_match():
    with patch("simdrive.sim.list_devices", return_value=_device_list()):
        d = sim.find_device(name="iPhone Made-Up")
    assert d is None


def test_find_device_no_filter_returns_first_booted():
    with patch("simdrive.sim.list_devices", return_value=_device_list()):
        d = sim.find_device()
    assert d is not None
    assert d.is_booted


def test_find_device_no_filter_no_booted_returns_none():
    only_shutdown = [Device(udid="X", name="iPhone X", os_version="26.3", state="Shutdown")]
    with patch("simdrive.sim.list_devices", return_value=only_shutdown):
        d = sim.find_device()
    assert d is None


# ── first_booted ────────────────────────────────────────────────────────────


def test_first_booted_returns_first_booted_device():
    with patch("simdrive.sim.list_devices", return_value=_device_list()):
        d = sim.first_booted()
    assert d is not None
    assert d.is_booted


def test_first_booted_returns_none_when_none_booted():
    none_booted = [Device(udid="X", name="iPhone X", os_version="26.3", state="Shutdown")]
    with patch("simdrive.sim.list_devices", return_value=none_booted):
        d = sim.first_booted()
    assert d is None


# ── boot ────────────────────────────────────────────────────────────────────


def test_boot_succeeds_when_simctl_returns_zero():
    with patch("simdrive.sim._simctl", return_value=_ok()):
        sim.boot("U1")  # No raise == success


def test_boot_fallback_recognises_already_booted():
    # bootstatus fails, but find_device shows it as booted.
    booted = Device(udid="U1", name="iPhone X", os_version="26.3", state="Booted")
    with patch("simdrive.sim._simctl", return_value=_fail("already booted")), \
         patch("simdrive.sim.find_device", return_value=booted):
        sim.boot("U1")  # No raise — already-booted is success.


def test_boot_raises_when_bootstatus_fails_and_not_booted():
    with patch("simdrive.sim._simctl", return_value=_fail("nope")), \
         patch("simdrive.sim.find_device", return_value=None):
        with pytest.raises(SimError) as exc:
            sim.boot("U1")
    assert "simctl boot failed" in str(exc.value)


# ── screenshot ──────────────────────────────────────────────────────────────


def test_screenshot_writes_file_and_returns_path(tmp_path: Path):
    dest = tmp_path / "out.png"

    def fake_simctl(*args, **kwargs):
        # Emulate simctl writing the file
        dest.write_bytes(b"\x89PNG\r\n\x1a\n")
        return _ok()

    with patch("simdrive.sim._simctl", side_effect=fake_simctl):
        out = sim.screenshot("U1", dest)
    assert out == dest
    assert dest.exists()


def test_screenshot_raises_on_simctl_failure(tmp_path: Path):
    dest = tmp_path / "out.png"
    with patch("simdrive.sim._simctl", return_value=_fail("io failed")):
        with pytest.raises(SimError) as exc:
            sim.screenshot("U1", dest)
    assert "simctl screenshot failed" in str(exc.value)


def test_screenshot_raises_when_file_missing_after_ok(tmp_path: Path):
    dest = tmp_path / "out.png"
    # simctl returns success but never writes the file
    with patch("simdrive.sim._simctl", return_value=_ok()):
        with pytest.raises(SimError) as exc:
            sim.screenshot("U1", dest)
    assert "file missing" in str(exc.value)


# ── launch_app ──────────────────────────────────────────────────────────────


def test_launch_app_parses_pid_from_stdout():
    with patch("simdrive.sim._simctl", return_value=_ok("com.example.App: 12345")):
        pid = sim.launch_app("U1", "com.example.App")
    assert pid == 12345


def test_launch_app_returns_zero_when_pid_unparseable():
    with patch("simdrive.sim._simctl", return_value=_ok("totally garbage")):
        pid = sim.launch_app("U1", "com.example.App")
    assert pid == 0


def test_launch_app_returns_zero_when_pid_not_int():
    with patch("simdrive.sim._simctl", return_value=_ok("com.example.App: not-a-pid")):
        pid = sim.launch_app("U1", "com.example.App")
    assert pid == 0


def test_launch_app_raises_on_failure():
    with patch("simdrive.sim._simctl", return_value=_fail("nope")):
        with pytest.raises(SimError) as exc:
            sim.launch_app("U1", "com.example.App")
    assert "launch" in str(exc.value)


# ── terminate_app + shutdown ────────────────────────────────────────────────


def test_terminate_app_invokes_simctl():
    with patch("simdrive.sim._simctl", return_value=_ok()) as m:
        sim.terminate_app("U1", "com.example.App")
    assert m.called


def test_shutdown_invokes_simctl():
    with patch("simdrive.sim._simctl", return_value=_ok()) as m:
        sim.shutdown("U1")
    assert m.called


# ── set_pasteboard ──────────────────────────────────────────────────────────


def test_set_pasteboard_success():
    with patch("simdrive.sim.subprocess.run", return_value=_ok()) as m:
        sim.set_pasteboard("U1", "hello")
    assert m.called
    args, kwargs = m.call_args
    # input should be passed via kwarg
    assert kwargs.get("input") == "hello"


def test_set_pasteboard_failure_raises():
    with patch("simdrive.sim.subprocess.run", return_value=_fail("pbcopy fail")):
        with pytest.raises(SimError) as exc:
            sim.set_pasteboard("U1", "hello")
    assert "pbcopy failed" in str(exc.value)


# ── get_log_tail ────────────────────────────────────────────────────────────


def test_get_log_tail_returns_lines_from_stdout():
    multiline = "\n".join(f"line-{i}" for i in range(60))
    with patch("simdrive.sim._simctl", return_value=_ok(multiline)):
        out = sim.get_log_tail("U1", lines=10)
    out_lines = out.splitlines()
    assert len(out_lines) == 10
    assert out_lines[-1] == "line-59"


def test_get_log_tail_passes_predicate():
    with patch("simdrive.sim._simctl", return_value=_ok("ok line")) as m:
        sim.get_log_tail("U1", predicate="subsystem == 'com.app'")
    args = m.call_args.args
    # The predicate value must be passed somewhere in the args list.
    assert any("subsystem" in str(a) for a in args)


def test_get_log_tail_returns_stderr_on_failure_truncated():
    long_stderr = "X" * 4000
    with patch("simdrive.sim._simctl", return_value=_fail(long_stderr)):
        out = sim.get_log_tail("U1")
    # Truncated to 2000 chars on the failure path.
    assert len(out) <= 2000


# ── get_app_version ─────────────────────────────────────────────────────────


def test_get_app_version_returns_none_on_simctl_failure():
    with patch("simdrive.sim._simctl", return_value=_fail("listapps blew up")):
        v = sim.get_app_version("U1", "com.example.App")
    assert v is None


def test_get_app_version_returns_none_on_empty_body():
    with patch("simdrive.sim._simctl", return_value=_ok("   \n  ")):
        v = sim.get_app_version("U1", "com.example.App")
    assert v is None


def test_get_app_version_parses_json_body():
    body = json.dumps({"com.example.App": {"CFBundleShortVersionString": "1.2.3"}})
    with patch("simdrive.sim._simctl", return_value=_ok(body)):
        v = sim.get_app_version("U1", "com.example.App")
    assert v == "1.2.3"


def test_get_app_version_falls_back_to_cfbundleversion():
    body = json.dumps({"com.example.App": {"CFBundleVersion": "987"}})
    with patch("simdrive.sim._simctl", return_value=_ok(body)):
        v = sim.get_app_version("U1", "com.example.App")
    assert v == "987"


def test_get_app_version_missing_bundle_returns_none():
    body = json.dumps({"com.other.App": {"CFBundleShortVersionString": "2"}})
    with patch("simdrive.sim._simctl", return_value=_ok(body)):
        v = sim.get_app_version("U1", "com.example.App")
    assert v is None


def test_get_app_version_unparseable_body_returns_none():
    # Garbage that can't be parsed as plist, json, or via plutil.
    with patch("simdrive.sim._simctl", return_value=_ok("totally garbage")), \
         patch("simdrive.sim.subprocess.run", return_value=_fail("plutil fail")):
        v = sim.get_app_version("U1", "com.example.App")
    assert v is None


def test_parse_listapps_uses_plutil_fallback():
    """When plistlib + json both fail, plutil should be invoked and its output parsed."""
    body = "/* OpenStep-style plist that plistlib can't read */"
    plutil_out = json.dumps({"com.example.App": {"CFBundleShortVersionString": "9.9"}})
    with patch("simdrive.sim.subprocess.run",
               return_value=_ok(plutil_out)):
        result = sim._parse_listapps(body)
    assert result["com.example.App"]["CFBundleShortVersionString"] == "9.9"


def test_parse_listapps_returns_empty_on_total_failure():
    """If everything fails — plist, json, plutil — return an empty dict, not raise."""
    with patch("simdrive.sim.subprocess.run", side_effect=OSError("plutil missing")):
        result = sim._parse_listapps("garbage")
    assert result == {}


# ── cliclick_path ───────────────────────────────────────────────────────────


def test_cliclick_path_returns_when_found():
    with patch("simdrive.sim.shutil.which", return_value="/usr/local/bin/cliclick"), \
         patch("simdrive.sim.Path") as MockPath:
        # Path("/usr/local/bin/cliclick").exists() -> True
        inst = MagicMock()
        inst.exists.return_value = True
        MockPath.return_value = inst
        out = sim.cliclick_path()
    assert out == "/usr/local/bin/cliclick"


def test_cliclick_path_raises_when_missing():
    with patch("simdrive.sim.shutil.which", return_value=None), \
         patch("simdrive.sim.Path") as MockPath:
        inst = MagicMock()
        inst.exists.return_value = False
        MockPath.return_value = inst
        with pytest.raises(SimError) as exc:
            sim.cliclick_path()
    assert "cliclick" in str(exc.value)
