"""Device sessions without WebDriverAgent, registry lookup by hardware UDID,
and app termination on session_end."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from simdrive import device, session
from simdrive.sim import Device

COREDEVICE = "DD191FC5-F9E3-56DA-A978-0E15F8A56EDD"
HARDWARE = "00008030-001E0C901410402E"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("WDA_REGISTRY_DIR", str(tmp_path / "wda"))
    session._SESSIONS.clear()
    yield
    session._SESSIONS.clear()


def _phone():
    return device.RealDevice(udid=HARDWARE, name="Maurice's iPhone", model="iPhone",
                             transport="wired", state="available", coredevice_id=COREDEVICE)


def test_wda_less_session_launches_app_by_hardware_udid():
    with patch.object(device, "list_devices", return_value=[_phone()]), \
         patch.object(device, "launch_app", return_value=99) as launch, \
         patch("simdrive.diagnostics.app_state_device",
               return_value={"state": "running", "pid": 99}):
        s = session.start(udid=COREDEVICE, app_bundle_id="org.thepalaceproject.palace",
                          target="device")
    launch.assert_called_once_with(HARDWARE, "org.thepalaceproject.palace")
    assert s.wda_client is None
    assert s.hardware_udid == HARDWARE
    assert s.device.udid == COREDEVICE
    assert s.device.name == "Maurice's iPhone"
    assert s.state == "active"


def test_registry_keyed_by_hardware_udid_is_found_from_coredevice_uuid(tmp_path):
    reg = tmp_path / "wda"
    reg.mkdir()
    (reg / f"{HARDWARE}.json").write_text(json.dumps(
        {"host": "fd00::1", "port": 8100, "hardware_udid": HARDWARE, "device_name": "Phone"}))
    wda = MagicMock()
    with patch.object(device, "list_devices", return_value=[_phone()]), \
         patch("simdrive.wda.client.WdaClient", return_value=wda):
        s = session.start(udid=COREDEVICE, target="device", verify_launch=False)
    assert s.wda_client is wda
    assert s.hardware_udid == HARDWARE
    wda.status.assert_called_once()


def test_end_device_session_terminates_with_devicectl_not_simctl():
    s = session.Session(session_id="d1", device=Device(udid=COREDEVICE, name="P", os_version="", state="active"),
                        workdir=session._workroot(), target="device", app_bundle_id="com.x",
                        hardware_udid=HARDWARE)
    session._SESSIONS["d1"] = s
    with patch.object(device, "terminate_app") as dev_term, \
         patch("simdrive.sim.terminate_app") as sim_term:
        session.end("d1")
    dev_term.assert_called_once_with(HARDWARE, "com.x")
    sim_term.assert_not_called()


def test_end_device_session_survives_terminate_failure():
    s = session.Session(session_id="d2", device=Device(udid=HARDWARE, name="P", os_version="", state="active"),
                        workdir=session._workroot(), target="device", app_bundle_id="com.x")
    session._SESSIONS["d2"] = s
    with patch.object(device, "terminate_app", side_effect=device.DeviceError("gone")) as dev_term:
        session.end("d2")
    dev_term.assert_called_once_with(HARDWARE, "com.x")
    assert "d2" not in session._SESSIONS
