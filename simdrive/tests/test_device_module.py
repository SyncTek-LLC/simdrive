"""Unit tests for ``simdrive.device`` — real-device backend with mocked subprocess.

The device module wraps devicectl / libimobiledevice CLI tools. These tests
stub ``subprocess.run`` / ``shutil.which`` so we exercise the parsing and
error paths without a connected device.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from simdrive import device
from simdrive.device import DeviceError, RealDevice


# ── _filter_devicectl_stderr ────────────────────────────────────────────────


def test_filter_devicectl_stderr_strips_noise_on_success():
    noise = "No provider was found for this descriptor\nuseful line"
    out = device._filter_devicectl_stderr(noise, returncode=0)
    assert "No provider" not in out
    assert "useful line" in out


def test_filter_devicectl_stderr_preserves_on_failure():
    noise = "No provider was found for this descriptor\nactual failure"
    out = device._filter_devicectl_stderr(noise, returncode=1)
    assert "No provider" in out


# ── _which ──────────────────────────────────────────────────────────────────


def test_which_uses_shutil_first():
    with patch("simdrive.device.shutil.which", return_value="/usr/local/bin/idevice_id"):
        out = device._which("idevice_id")
    assert out == "/usr/local/bin/idevice_id"


def test_which_falls_back_to_homebrew_paths():
    inst = MagicMock()
    inst.exists.return_value = True
    with patch("simdrive.device.shutil.which", return_value=None), \
         patch("simdrive.device.Path") as MockPath:
        MockPath.return_value = inst
        out = device._which("idevice_id")
    # Should be /opt/homebrew/bin or /usr/local/bin form
    assert out is not None
    assert "idevice_id" in out


def test_which_returns_none_when_missing():
    inst = MagicMock()
    inst.exists.return_value = False
    with patch("simdrive.device.shutil.which", return_value=None), \
         patch("simdrive.device.Path") as MockPath:
        MockPath.return_value = inst
        out = device._which("idevice_id")
    assert out is None


# ── libimobiledevice_available + devicectl_available ────────────────────────


def test_libimobiledevice_available_when_all_present():
    with patch("simdrive.device._which", return_value="/opt/homebrew/bin/anything"):
        ok, missing = device.libimobiledevice_available()
    assert ok is True
    assert missing == []


def test_libimobiledevice_available_returns_missing():
    with patch("simdrive.device._which", return_value=None):
        ok, missing = device.libimobiledevice_available()
    assert ok is False
    assert "idevice_id" in missing


def test_devicectl_available_truthy():
    with patch("simdrive.device._which", return_value="/usr/bin/xcrun"):
        assert device.devicectl_available() is True


# ── _unavailable_reason ─────────────────────────────────────────────────────


def test_unavailable_reason_available_state():
    assert device._unavailable_reason("available", {}, {}) is None


def test_unavailable_reason_unpaired():
    out = device._unavailable_reason("unavailable", {"pairingState": "unpaired"}, {})
    assert "not paired" in out


def test_unavailable_reason_tunnel_disconnected():
    out = device._unavailable_reason("unavailable",
                                     {"tunnelState": "disconnected"}, {})
    assert "tunnel" in out


def test_unavailable_reason_no_transport():
    out = device._unavailable_reason("unavailable", {"transportType": None}, {})
    assert "no transport" in out


def test_unavailable_reason_developer_mode_disabled():
    out = device._unavailable_reason("unavailable",
                                     {}, {"developerModeStatus": "disabled"})
    assert "developer mode" in out


def test_unavailable_reason_default_offline():
    """When state is unavailable but nothing specific matches, returns 'device offline'."""
    out = device._unavailable_reason("unavailable",
                                     {"transportType": "wired"}, {})
    assert out == "device offline"


# ── list_devices ────────────────────────────────────────────────────────────


def _ok(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=stderr)


def _fail(stderr: str = "boom") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)


def test_list_devices_raises_without_xcrun():
    with patch("simdrive.device.devicectl_available", return_value=False):
        with pytest.raises(DeviceError) as exc:
            device.list_devices()
    assert "xcrun" in str(exc.value)


def test_list_devices_parses_json_payload(tmp_path):
    payload = {
        "result": {
            "devices": [
                {
                    "hardwareProperties": {
                        "udid": "00008150-001400000000001C",
                        "marketingName": "iPhone 17 Pro Max",
                        "productType": "iPhone17,1",
                    },
                    "deviceProperties": {"name": "Test Phone"},
                    "connectionProperties": {
                        "transportType": "wired",
                        "lastConnectionDate": "2026-05-19T00:00:00Z",
                    },
                },
                {
                    "hardwareProperties": {"udid": "OFFLINE"},
                    "deviceProperties": {"name": "Offline Phone"},
                    "connectionProperties": {},  # no transport
                },
            ],
        },
    }

    def fake_run(args, **kwargs):
        # The JSON output file is the 4th arg or so. The implementation writes
        # to whichever path it passed via --json-output. Inspect args.
        for i, a in enumerate(args):
            if a == "--json-output":
                Path(args[i + 1]).write_text(json.dumps(payload))
                break
        return _ok()

    with patch("simdrive.device.devicectl_available", return_value=True), \
         patch("simdrive.device.subprocess.run", side_effect=fake_run):
        out = device.list_devices()

    assert len(out) == 2
    assert out[0].is_available is True
    assert out[0].model == "iPhone 17 Pro Max"
    assert out[1].state == "unavailable"


def test_list_devices_raises_when_devicectl_fails():
    with patch("simdrive.device.devicectl_available", return_value=True), \
         patch("simdrive.device.subprocess.run", return_value=_fail("nope")):
        with pytest.raises(DeviceError) as exc:
            device.list_devices()
    assert "devicectl list failed" in str(exc.value)


def test_list_devices_raises_on_bad_json():
    def fake_run(args, **kwargs):
        for i, a in enumerate(args):
            if a == "--json-output":
                Path(args[i + 1]).write_text("{not json")
        return _ok()

    with patch("simdrive.device.devicectl_available", return_value=True), \
         patch("simdrive.device.subprocess.run", side_effect=fake_run):
        with pytest.raises(DeviceError) as exc:
            device.list_devices()
    assert "JSON unreadable" in str(exc.value)


# ── find_device ─────────────────────────────────────────────────────────────


def test_find_device_returns_match():
    dev = RealDevice(udid="ABCD-1234", name="iPad", model="iPad Pro",
                     transport="wired", state="available")
    with patch("simdrive.device.list_devices", return_value=[dev]):
        out = device.find_device("ABCD-1234")
    assert out is dev


def test_find_device_normalises_hyphens():
    dev = RealDevice(udid="ABCD-1234", name="iPad", model="iPad Pro",
                     transport="wired", state="available")
    with patch("simdrive.device.list_devices", return_value=[dev]):
        # Match even if caller drops the hyphen
        out = device.find_device("ABCD1234")
    assert out is dev


def test_find_device_no_match_returns_none():
    with patch("simdrive.device.list_devices", return_value=[]):
        assert device.find_device("NOPE") is None


# ── screenshot ──────────────────────────────────────────────────────────────


def test_screenshot_raises_when_binary_missing(tmp_path):
    with patch("simdrive.device._which", return_value=None):
        with pytest.raises(DeviceError) as exc:
            device.screenshot("UDID", tmp_path / "out.png")
    assert "idevicescreenshot" in str(exc.value)


def test_screenshot_succeeds(tmp_path):
    dest = tmp_path / "out.png"

    def fake_run(args, **kwargs):
        dest.write_bytes(b"\x89PNG")
        return _ok()

    with patch("simdrive.device._which", return_value="/opt/homebrew/bin/idevicescreenshot"), \
         patch("simdrive.device.subprocess.run", side_effect=fake_run):
        out = device.screenshot("UDID", dest)
    assert out == dest


def test_screenshot_raises_developer_disk_image_hint(tmp_path):
    dest = tmp_path / "out.png"
    fail = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="",
        stderr="Could not start service: Developer disk image not mounted",
    )
    with patch("simdrive.device._which", return_value="/opt/homebrew/bin/idevicescreenshot"), \
         patch("simdrive.device.subprocess.run", return_value=fail):
        with pytest.raises(DeviceError) as exc:
            device.screenshot("UDID", dest)
    assert "Developer Disk Image" in str(exc.value)
    assert "ideviceimagemounter" in str(exc.value)


def test_screenshot_raises_generic_failure(tmp_path):
    dest = tmp_path / "out.png"
    fail = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="something else broke",
    )
    with patch("simdrive.device._which", return_value="/opt/homebrew/bin/idevicescreenshot"), \
         patch("simdrive.device.subprocess.run", return_value=fail):
        with pytest.raises(DeviceError) as exc:
            device.screenshot("UDID", dest)
    assert "idevicescreenshot failed" in str(exc.value)


def test_screenshot_raises_when_file_missing(tmp_path):
    dest = tmp_path / "out.png"
    # _which finds the binary, subprocess says OK, but file never appears.
    with patch("simdrive.device._which", return_value="/opt/homebrew/bin/idevicescreenshot"), \
         patch("simdrive.device.subprocess.run", return_value=_ok()):
        with pytest.raises(DeviceError) as exc:
            device.screenshot("UDID", dest)
    assert "file missing" in str(exc.value)


# ── get_log_tail ────────────────────────────────────────────────────────────


def test_get_log_tail_raises_when_binary_missing():
    with patch("simdrive.device._which", return_value=None):
        with pytest.raises(DeviceError) as exc:
            device.get_log_tail("UDID")
    assert "device_logs_unavailable" in str(exc.value)


def _fake_popen(stdout: str, raise_timeout: bool = False):
    proc = MagicMock()
    proc.stdout = MagicMock()
    if raise_timeout:
        proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=1)
    else:
        proc.communicate.return_value = (stdout, "")
    proc.stdout.read.return_value = ""
    return proc


def test_get_log_tail_returns_raw_output():
    multiline = "\n".join(f"line-{i}" for i in range(60))
    with patch("simdrive.device._which", return_value="/opt/homebrew/bin/idevicesyslog"), \
         patch("simdrive.device.subprocess.Popen", return_value=_fake_popen(multiline)):
        out = device.get_log_tail("UDID", lines=10)
    out_lines = out.splitlines()
    assert len(out_lines) == 10
    assert out_lines[-1] == "line-59"


def test_get_log_tail_timeout_drains_partial_output():
    with patch("simdrive.device._which", return_value="/opt/homebrew/bin/idevicesyslog"), \
         patch("simdrive.device.subprocess.Popen", return_value=_fake_popen("", raise_timeout=True)):
        out = device.get_log_tail("UDID")
    # Empty string on timeout with no partial.
    assert out == ""


def test_get_log_tail_substring_predicate():
    multiline = "error line\ninfo line\nerror x\n"
    with patch("simdrive.device._which", return_value="/opt/homebrew/bin/idevicesyslog"), \
         patch("simdrive.device.subprocess.Popen", return_value=_fake_popen(multiline)):
        out = device.get_log_tail("UDID", predicate="error")
    assert "info line" not in out
    assert "error line" in out


def test_get_log_tail_regex_predicate():
    multiline = "error 123\ninfo 456\nERROR 789\n"
    with patch("simdrive.device._which", return_value="/opt/homebrew/bin/idevicesyslog"), \
         patch("simdrive.device.subprocess.Popen", return_value=_fake_popen(multiline)):
        out = device.get_log_tail("UDID", predicate=r"\d{3}", predicate_kind="regex")
    assert "error 123" in out


def test_get_log_tail_regex_invalid_returns_message():
    with patch("simdrive.device._which", return_value="/opt/homebrew/bin/idevicesyslog"), \
         patch("simdrive.device.subprocess.Popen", return_value=_fake_popen("anything")):
        out = device.get_log_tail("UDID", predicate="[invalid", predicate_kind="regex")
    assert "invalid regex" in out


def test_get_log_tail_predicate_no_match_returns_warning():
    multiline = "line one\nline two\n"
    with patch("simdrive.device._which", return_value="/opt/homebrew/bin/idevicesyslog"), \
         patch("simdrive.device.subprocess.Popen", return_value=_fake_popen(multiline)):
        out = device.get_log_tail("UDID", predicate="not-present")
    assert "no lines matched" in out


def test_get_log_tail_nspredicate_downgrades_to_substring():
    multiline = "alpha line\nbeta line"
    with patch("simdrive.device._which", return_value="/opt/homebrew/bin/idevicesyslog"), \
         patch("simdrive.device.subprocess.Popen", return_value=_fake_popen(multiline)):
        out = device.get_log_tail("UDID", predicate="alpha", predicate_kind="nspredicate")
    assert "alpha line" in out


def test_get_log_tail_with_bundle_id_appends_flag():
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_popen("ok line")

    with patch("simdrive.device._which", return_value="/opt/homebrew/bin/idevicesyslog"), \
         patch("simdrive.device.subprocess.Popen", side_effect=fake_popen):
        device.get_log_tail("UDID", bundle_id="com.example.App")
    assert "--match" in captured["cmd"]
    assert "com.example.App" in captured["cmd"]


# ── install_app / launch_app / terminate_app ────────────────────────────────


def test_install_app_missing_bundle_raises(tmp_path):
    with pytest.raises(DeviceError) as exc:
        device.install_app("UDID", tmp_path / "missing.app")
    assert "not found" in str(exc.value)


def test_install_app_succeeds(tmp_path):
    app_path = tmp_path / "fake.app"
    app_path.mkdir()
    with patch("simdrive.device.subprocess.run", return_value=_ok()):
        device.install_app("UDID", app_path)  # No raise.


def test_install_app_raises_on_failure(tmp_path):
    app_path = tmp_path / "fake.app"
    app_path.mkdir()
    with patch("simdrive.device.subprocess.run", return_value=_fail("nope")):
        with pytest.raises(DeviceError) as exc:
            device.install_app("UDID", app_path)
    assert "devicectl install failed" in str(exc.value)


def test_launch_app_parses_pid_from_json():
    payload = {"result": {"process": {"processIdentifier": 12345}}}
    with patch("simdrive.device.subprocess.run",
               return_value=_ok(json.dumps(payload))):
        pid = device.launch_app("UDID", "com.example.App")
    assert pid == 12345


def test_launch_app_returns_zero_on_bad_json():
    with patch("simdrive.device.subprocess.run", return_value=_ok("not json")):
        pid = device.launch_app("UDID", "com.example.App")
    assert pid == 0


def test_launch_app_raises_on_failure():
    with patch("simdrive.device.subprocess.run", return_value=_fail("boom")):
        with pytest.raises(DeviceError) as exc:
            device.launch_app("UDID", "com.example.App")
    assert "devicectl launch failed" in str(exc.value)


def test_terminate_app_does_not_raise():
    with patch("simdrive.device.subprocess.run", return_value=_ok()):
        device.terminate_app("UDID", "com.example.App")  # No raise.


# ── is_developer_disk_mounted ───────────────────────────────────────────────


def test_is_developer_disk_mounted_no_binary():
    with patch("simdrive.device._which", return_value=None):
        assert device.is_developer_disk_mounted("UDID") is False


def test_is_developer_disk_mounted_yes():
    with patch("simdrive.device._which", return_value="/opt/homebrew/bin/idevicescreenshot"), \
         patch("simdrive.device.subprocess.run", return_value=_ok()):
        assert device.is_developer_disk_mounted("UDID") is True


def test_is_developer_disk_mounted_no_on_invalid_service():
    fail = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="Invalid service",
    )
    with patch("simdrive.device._which", return_value="/opt/homebrew/bin/idevicescreenshot"), \
         patch("simdrive.device.subprocess.run", return_value=fail):
        assert device.is_developer_disk_mounted("UDID") is False
