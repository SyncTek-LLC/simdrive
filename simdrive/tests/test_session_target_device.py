"""Tests for tool_session_start when target=device — routes through WDA registry.

Verifies that _start_device:
  1. Loads the WDA registry from ~/.simdrive/wda/<udid>.json
  2. Creates a Session with target="device" and a WdaClient attached
  3. Returns the standard {session_id, udid, target, ...} shape
  4. Raises wda_not_bootstrapped (with "bootstrap" in the message) when
     the registry file is absent
  5. Accepts both "udid" and "device_udid" as argument names
"""
from __future__ import annotations

import json
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


FAKE_UDID = "31471BBD-6889-5DAC-9497-TESTDEADBEEF"
FAKE_REGISTRY = {
    "host": "192.168.1.99",
    "ip": "192.168.1.99",
    "port": 8100,
    "hardware_udid": "00008150-001400000000001C",
    "coredevice_uuid": FAKE_UDID,
    "team_id": "AAAAAAAAAA",
}


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_sessions():
    """Remove any sessions created by these tests from the global registry."""
    from simdrive import session as session_mod
    before = set(session_mod._SESSIONS.keys())
    yield
    for k in list(session_mod._SESSIONS.keys()):
        if k not in before:
            session_mod._SESSIONS.pop(k, None)


@pytest.fixture()
def wda_registry_dir(tmp_path, monkeypatch):
    """Stage a WDA registry dir under tmp_path and point the module at it."""
    wda_dir = tmp_path / ".simdrive" / "wda"
    wda_dir.mkdir(parents=True)
    monkeypatch.setenv("WDA_REGISTRY_DIR", str(wda_dir))
    return wda_dir


def _write_registry(wda_dir: Path, udid: str = FAKE_UDID, entry: dict | None = None) -> Path:
    path = wda_dir / f"{udid}.json"
    path.write_text(json.dumps(entry or FAKE_REGISTRY))
    return path


# ── happy path ────────────────────────────────────────────────────────────────


def test_start_device_session_loads_wda_registry(wda_registry_dir):
    """Starting a target=device session reads the WDA registry and creates
    a Session with target='device'."""
    _write_registry(wda_registry_dir)

    mock_wda = MagicMock()
    mock_wda.status.return_value = {"value": {"ready": True}}

    with patch("simdrive.wda.client.WdaClient", return_value=mock_wda) as mock_cls:
        from simdrive import server
        result = server.tool_session_start({
            "udid": FAKE_UDID,
            "target": "device",
        })

    assert result["session_id"], "session_id must be non-empty"
    assert result["target"] == "device"
    assert result["udid"] == FAKE_UDID
    # WdaClient must have been constructed with the registry's host:port
    mock_cls.assert_called_once_with(host="192.168.1.99", port=8100)


def test_start_device_session_accepts_device_udid_alias(wda_registry_dir):
    """'device_udid' key must be accepted as an alias for 'udid'."""
    _write_registry(wda_registry_dir)

    mock_wda = MagicMock()
    with patch("simdrive.wda.client.WdaClient", return_value=mock_wda):
        from simdrive import server
        result = server.tool_session_start({
            "device_udid": FAKE_UDID,
            "target": "device",
        })

    assert result["session_id"]
    assert result["target"] == "device"


def test_start_device_session_stored_in_session_dict(wda_registry_dir):
    """After tool_session_start, the Session must be findable via session.get()."""
    _write_registry(wda_registry_dir)

    mock_wda = MagicMock()
    with patch("simdrive.wda.client.WdaClient", return_value=mock_wda):
        from simdrive import server, session as session_mod
        result = server.tool_session_start({
            "udid": FAKE_UDID,
            "target": "device",
        })

    sid = result["session_id"]
    s = session_mod.get(sid)
    assert s.target == "device"
    assert s.device.udid == FAKE_UDID
    # wda_client must be set on the session
    assert s.wda_client is not None


def test_start_device_session_return_shape(wda_registry_dir):
    """Return dict must contain the same keys as the simulator path."""
    _write_registry(wda_registry_dir)

    mock_wda = MagicMock()
    with patch("simdrive.wda.client.WdaClient", return_value=mock_wda):
        from simdrive import server
        result = server.tool_session_start({
            "udid": FAKE_UDID,
            "target": "device",
        })

    for key in ("session_id", "udid", "target", "state", "app_bundle_id"):
        assert key in result, f"Missing key {key!r} in tool_session_start response"


def test_start_device_session_ip_fallback(wda_registry_dir):
    """Registry with only 'ip' (no 'host') must still produce a WdaClient."""
    entry = {k: v for k, v in FAKE_REGISTRY.items() if k != "host"}
    entry["ip"] = "10.0.0.55"
    _write_registry(wda_registry_dir, entry=entry)

    mock_wda = MagicMock()
    with patch("simdrive.wda.client.WdaClient", return_value=mock_wda) as mock_cls:
        from simdrive import server
        server.tool_session_start({"udid": FAKE_UDID, "target": "device"})

    mock_cls.assert_called_once_with(host="10.0.0.55", port=8100)


# ── error path: registry missing ──────────────────────────────────────────────


def test_start_device_session_raises_when_registry_missing(wda_registry_dir):
    """When ~/.simdrive/wda/<udid>.json doesn't exist, raise wda_not_bootstrapped
    with a message that tells the user to run bootstrap-device."""
    from simdrive import server, errors

    with pytest.raises(errors.SimdriveError) as exc_info:
        server.tool_session_start({
            "udid": "NEVER-BOOTSTRAPPED-UDID",
            "target": "device",
        })

    err = exc_info.value
    assert err.code == "wda_not_bootstrapped"
    assert "bootstrap" in err.message.lower() or "registry" in err.message.lower(), (
        f"Expected 'bootstrap' or 'registry' in error message, got: {err.message!r}"
    )
    assert "NEVER-BOOTSTRAPPED-UDID" in err.message or "NEVER-BOOTSTRAPPED-UDID" in str(err.details)


def test_start_device_session_raises_when_no_udid():
    """Calling with target=device but no udid must raise no_device."""
    from simdrive import server, errors

    with pytest.raises(errors.SimdriveError) as exc_info:
        server.tool_session_start({"target": "device"})

    assert exc_info.value.code == "no_device"
