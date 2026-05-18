"""Tests for D4 — device-launch failure surfaces a devicectl-shaped error.

When `_start_device` is called with an `app_bundle_id` and the underlying
`devicectl process launch` fails, the raised SimdriveError must:

  1. Have code `device_launch_failed` (not `no_device`, which is simctl-shaped).
  2. Have a recovery hint that references `xcrun devicectl`, not `xcrun simctl`.

This is the agent contract — the recovery hint goes straight back to the
caller, and `simctl boot <udid>` against a CoreDevice UUID is wrong advice.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


FAKE_UDID = "31471BBD-6889-5DAC-9497-TESTLAUNCHFAIL"
FAKE_REGISTRY = {
    "host": "192.168.1.99",
    "ip": "192.168.1.99",
    "port": 8100,
    "hardware_udid": "00008150-001400000000001C",
    "coredevice_uuid": FAKE_UDID,
    "team_id": "AAAAAAAAAA",
    "device_name": "Moes Max",
}


@pytest.fixture(autouse=True)
def _isolate_sessions():
    from simdrive import session as session_mod
    before = set(session_mod._SESSIONS.keys())
    yield
    for k in list(session_mod._SESSIONS.keys()):
        if k not in before:
            session_mod._SESSIONS.pop(k, None)


@pytest.fixture()
def wda_registry_dir(tmp_path, monkeypatch):
    wda_dir = tmp_path / ".simdrive" / "wda"
    wda_dir.mkdir(parents=True)
    monkeypatch.setenv("WDA_REGISTRY_DIR", str(wda_dir))
    path = wda_dir / f"{FAKE_UDID}.json"
    path.write_text(json.dumps(FAKE_REGISTRY))
    return wda_dir


def test_device_launch_failure_raises_device_launch_failed(wda_registry_dir):
    """When devicectl launch fails, _start_device must raise device_launch_failed."""
    from simdrive import errors as _errors

    mock_wda = MagicMock()
    mock_wda.status.return_value = {"value": {"ready": True}}

    def _fail_launch(udid, bundle_id):
        from simdrive.device import DeviceError
        raise DeviceError("devicectl launch failed: boom")

    with patch("simdrive.wda.client.WdaClient", return_value=mock_wda), \
         patch("simdrive.device.launch_app", side_effect=_fail_launch):
        from simdrive import server
        with pytest.raises(_errors.SimdriveError) as exc_info:
            server.tool_session_start({
                "udid": FAKE_UDID,
                "target": "device",
                "app_bundle_id": "io.synctek.atlas-portal",
            })

    assert exc_info.value.code == "device_launch_failed", (
        f"expected device_launch_failed, got {exc_info.value.code}"
    )


def test_device_launch_failed_recovery_hint_references_devicectl(wda_registry_dir):
    """Recovery hint must reference devicectl, not simctl."""
    from simdrive import errors as _errors

    mock_wda = MagicMock()
    mock_wda.status.return_value = {"value": {"ready": True}}

    def _fail_launch(udid, bundle_id):
        from simdrive.device import DeviceError
        raise DeviceError("devicectl launch failed: boom")

    with patch("simdrive.wda.client.WdaClient", return_value=mock_wda), \
         patch("simdrive.device.launch_app", side_effect=_fail_launch):
        from simdrive import server
        with pytest.raises(_errors.SimdriveError) as exc_info:
            server.tool_session_start({
                "udid": FAKE_UDID,
                "target": "device",
                "app_bundle_id": "io.synctek.atlas-portal",
            })

    msg = exc_info.value.message
    assert "devicectl" in msg, f"recovery hint missing 'devicectl': {msg!r}"
    assert "simctl" not in msg, (
        f"recovery hint must not reference simctl on a real-device error: {msg!r}"
    )
    assert "Recovery:" in msg


def test_device_launch_failed_constructor_shape():
    """device_launch_failed builder must match the existing error-builder shape."""
    from simdrive import errors as _errors

    err = _errors.device_launch_failed(
        udid=FAKE_UDID,
        bundle_id="io.synctek.atlas-portal",
        reason="boom",
    )
    assert err.code == "device_launch_failed"
    assert err.details["udid"] == FAKE_UDID
    assert err.details["bundle_id"] == "io.synctek.atlas-portal"
    assert err.details["reason"] == "boom"
    assert "Recovery:" in err.message
    assert "devicectl" in err.message
