"""Device logs use the hardware UDID and report idevicesyslog failures.

idevicesyslog only accepts the hardware UDID; a CoreDevice UUID made it exit
with "No device found", and the old code discarded stderr and returned "".
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from simdrive import device
from simdrive.device import DeviceError, RealDevice

COREDEVICE = "DD191FC5-F9E3-56DA-A978-0E15F8A56EDD"
HARDWARE = "00008030-001E0C901410402E"


def _phone(**kw):
    base = dict(udid=HARDWARE, name="Phone", model="iPhone", transport="wired",
                state="available", coredevice_id=COREDEVICE)
    base.update(kw)
    return RealDevice(**base)


def _popen_factory(results):
    """results: list of (stdout, stderr, returncode-or-None-for-timeout)."""
    calls = []

    def fake_popen(cmd, *a, **kw):
        calls.append(cmd)
        out, err, rc = results[len(calls) - 1]
        proc = MagicMock()
        if rc is None:
            # First communicate times out, second (after kill) returns the buffer.
            proc.communicate.side_effect = [
                subprocess.TimeoutExpired(cmd="idevicesyslog", timeout=1), (out, err)]
        else:
            proc.communicate.return_value = (out, err)
            proc.returncode = rc
        return proc
    return fake_popen, calls


@pytest.fixture
def syslog_bin(monkeypatch):
    monkeypatch.setattr(device, "_which", lambda n: f"/opt/homebrew/bin/{n}")


def test_resolve_hardware_udid_maps_coredevice_uuid():
    with patch.object(device, "list_devices", return_value=[_phone()]):
        assert device.resolve_hardware_udid(COREDEVICE) == HARDWARE


def test_resolve_hardware_udid_leaves_hardware_udid_untouched():
    with patch.object(device, "list_devices") as ld:
        assert device.resolve_hardware_udid(HARDWARE) == HARDWARE
    ld.assert_not_called()


def test_resolve_hardware_udid_falls_back_when_devicectl_fails():
    with patch.object(device, "list_devices", side_effect=DeviceError("x")):
        assert device.resolve_hardware_udid(COREDEVICE) == COREDEVICE


def test_list_devices_exposes_coredevice_id_and_tunnel_state():
    payload = {"result": {"devices": [{
        "identifier": COREDEVICE,
        "hardwareProperties": {"udid": HARDWARE},
        "deviceProperties": {"name": "Phone"},
        "connectionProperties": {"transportType": "localNetwork",
                                 "tunnelState": "disconnected"},
    }]}}

    def fake_run(args, **kw):
        Path(args[args.index("--json-output") + 1]).write_text(json.dumps(payload))
        return subprocess.CompletedProcess(args, 0, "", "")

    with patch.object(device, "devicectl_available", return_value=True), \
         patch.object(device.subprocess, "run", side_effect=fake_run):
        [d] = device.list_devices()
    assert d.coredevice_id == COREDEVICE
    assert d.tunnel_state == "disconnected"


def test_get_log_tail_passes_hardware_udid_and_process(syslog_bin):
    fake, calls = _popen_factory([("Palace[1]: hello\n", "", None)])
    with patch.object(device, "list_devices", return_value=[_phone()]), \
         patch.object(device.subprocess, "Popen", side_effect=fake):
        out = device.get_log_tail(COREDEVICE, process="Palace")
    assert calls[0][1:3] == ["-u", HARDWARE]
    assert calls[0][calls[0].index("-p") + 1] == "Palace"
    assert "-n" not in calls[0]
    assert out == "Palace[1]: hello"


def test_get_log_tail_keeps_output_buffered_before_timeout(syslog_bin):
    """Lines read by communicate() before the timeout are returned after kill()."""
    fake, _ = _popen_factory([("a\nb\nc\n", "", None)])
    with patch.object(device.subprocess, "Popen", side_effect=fake):
        assert device.get_log_tail(HARDWARE) == "a\nb\nc"


def test_get_log_tail_retries_over_network_when_usb_has_no_device(syslog_bin):
    fake, calls = _popen_factory([
        ("", f"ERROR: Device {HARDWARE} not found!\n", 255),
        ("net line\n", "", None),
    ])
    with patch.object(device.subprocess, "Popen", side_effect=fake):
        out = device.get_log_tail(HARDWARE)
    assert "-n" not in calls[0]
    assert "-n" in calls[1]
    assert out == "net line"


def test_get_log_tail_raises_with_stderr_when_idevicesyslog_fails(syslog_bin):
    fake, _ = _popen_factory([("", "ERROR: Could not connect to lockdownd: -18\n", 1)])
    with patch.object(device.subprocess, "Popen", side_effect=fake):
        with pytest.raises(DeviceError) as exc:
            device.get_log_tail(HARDWARE)
    assert "lockdownd" in str(exc.value)
    assert "exited 1" in str(exc.value)


def test_server_logs_on_device_surfaces_failure(tmp_path, monkeypatch, syslog_bin):
    from types import SimpleNamespace
    from simdrive import server, session
    from simdrive.sim import Device
    s = SimpleNamespace(session_id="sid", target="device", workdir=tmp_path,
                        device=Device(udid=HARDWARE, name="P", os_version="26", state="active"),
                        app_bundle_id=None, hardware_udid=HARDWARE)
    monkeypatch.setattr(session, "get", lambda sid: s)
    monkeypatch.setattr(server, "_entitlement_gate", lambda: None)
    fake, _ = _popen_factory([("", "ERROR: Could not connect to lockdownd: -18\n", 1)])
    monkeypatch.setattr(device.subprocess, "Popen", fake)
    out = server.tool_logs({"session_id": "sid", "predicate_kind": "substring"})
    assert out["ok"] is False
    assert out["error"]["code"] == "device_logs_failed"
    assert "lockdownd" in out["error"]["message"]
