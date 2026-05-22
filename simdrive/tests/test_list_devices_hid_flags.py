"""Tests for tool_list_devices hid_supported / hid_note behaviour.

These tests MUST fail on feat/v17-claude-native HEAD (hid_supported is always
False regardless of registry, hid_note mentions 'v0.3 roadmap') and PASS after
feat/simdrive-a10-zero-config-bootstrap is merged.

Strategy: patch ``simdrive.server.device.list_devices`` to return a canned
device list, and redirect the WDA registry dir (WDA_REGISTRY_DIR) to a
tmp_path so we can control which UDIDs appear as bootstrapped without touching
the real filesystem.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_real_device(udid: str, name: str = "Test iPhone") -> MagicMock:
    """Build a minimal RealDevice-shaped mock."""
    d = MagicMock()
    d.udid = udid
    d.name = name
    d.model = "iPhone17,1"
    d.transport = "localNetwork"
    d.state = "available"
    d.last_seen = "2026-05-11T00:00:00"
    d.unavailable_reason = None
    return d


def _write_registry(registry_dir: Path, udid: str) -> None:
    """Write a minimal WDA registry file for the given UDID."""
    reg_file = registry_dir / f"{udid}.json"
    reg_file.parent.mkdir(parents=True, exist_ok=True)
    reg_file.write_text(
        json.dumps({"host": "192.168.1.100", "port": 8100}),
        encoding="utf-8",
    )


# Fixture: redirect WDA registry to temp dir + patch libimobiledevice_available
@pytest.fixture()
def registry_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WDA_REGISTRY_DIR", str(tmp_path))
    return tmp_path


# ── tests ─────────────────────────────────────────────────────────────────────


def test_registered_device_hid_supported_true(registry_dir):
    """A UDID that has a WDA registry file → hid_supported: True."""
    udid = "00008150-REGTEST001"
    _write_registry(registry_dir, udid)

    device_mock = _make_real_device(udid)

    # tool_list_devices lazily imports `device` inside the function body
    # (`from . import device`), so we patch at the simdrive.device module level.
    with (
        patch("simdrive.device.list_devices", return_value=[device_mock]),
        patch("simdrive.device.libimobiledevice_available", return_value=(True, [])),
    ):
        from simdrive.server import tool_list_devices
        result = tool_list_devices({})

    assert result["ok"] is True
    assert len(result["devices"]) == 1
    dev = result["devices"][0]
    assert dev["udid"] == udid
    assert dev["hid_supported"] is True, (
        f"Expected hid_supported=True for registered UDID, got {dev['hid_supported']!r}"
    )


def test_unregistered_device_hid_supported_false_while_registered_is_true(registry_dir):
    """Unregistered UDID → hid_supported: False; proves the check is per-device, not blanket.

    This test ensures the implementation actually consults the registry (not just
    always-False). We supply TWO devices: one registered, one not. On HEAD
    (always-False), the registered device still returns False → assertion fails.
    Post-merge the registered device returns True → both assertions pass.
    """
    registered_udid = "00008150-REGTEST001"
    unregistered_udid = "00008150-NOTREG002"
    _write_registry(registry_dir, registered_udid)
    # unregistered_udid intentionally has no registry file

    devices = [
        _make_real_device(registered_udid, "Registered iPhone"),
        _make_real_device(unregistered_udid, "Unregistered iPhone"),
    ]

    with (
        patch("simdrive.device.list_devices", return_value=devices),
        patch("simdrive.device.libimobiledevice_available", return_value=(True, [])),
    ):
        from simdrive.server import tool_list_devices
        result = tool_list_devices({})

    assert result["ok"] is True
    assert len(result["devices"]) == 2
    by_udid = {d["udid"]: d for d in result["devices"]}

    # The registered device must be True (fails on HEAD which hard-codes False)
    assert by_udid[registered_udid]["hid_supported"] is True, (
        f"Expected hid_supported=True for registered UDID, got "
        f"{by_udid[registered_udid]['hid_supported']!r}"
    )
    # The unregistered device must remain False
    assert by_udid[unregistered_udid]["hid_supported"] is False, (
        f"Expected hid_supported=False for unregistered UDID, got "
        f"{by_udid[unregistered_udid]['hid_supported']!r}"
    )


def test_mixed_registered_and_unregistered(registry_dir):
    """Mix of registered and unregistered UDIDs returns correct hid_supported per device."""
    registered_udid = "00008150-REGTEST001"
    unregistered_udid = "00008150-NOTREG002"
    _write_registry(registry_dir, registered_udid)

    devices = [
        _make_real_device(registered_udid, "Registered iPhone"),
        _make_real_device(unregistered_udid, "Unregistered iPhone"),
    ]

    with (
        patch("simdrive.device.list_devices", return_value=devices),
        patch("simdrive.device.libimobiledevice_available", return_value=(True, [])),
    ):
        from simdrive.server import tool_list_devices
        result = tool_list_devices({})

    assert len(result["devices"]) == 2
    by_udid = {d["udid"]: d for d in result["devices"]}

    assert by_udid[registered_udid]["hid_supported"] is True
    assert by_udid[unregistered_udid]["hid_supported"] is False


def test_hid_note_does_not_mention_v03_roadmap(registry_dir):
    """hid_note must NOT contain 'v0.3' or 'not yet implemented'."""
    device_mock = _make_real_device("00008150-HIDNOTE01")

    with (
        patch("simdrive.device.list_devices", return_value=[device_mock]),
        patch("simdrive.device.libimobiledevice_available", return_value=(True, [])),
    ):
        from simdrive.server import tool_list_devices
        result = tool_list_devices({})

    hid_note = result.get("hid_note", "")
    assert "v0.3" not in hid_note, (
        f"hid_note still mentions 'v0.3' (stale roadmap text): {hid_note!r}"
    )
    assert "not yet implemented" not in hid_note.lower(), (
        f"hid_note still contains 'not yet implemented': {hid_note!r}"
    )


def test_hid_note_mentions_bootstrap_device(registry_dir):
    """hid_note should guide the user toward running bootstrap-device."""
    device_mock = _make_real_device("00008150-HIDNOTE02")

    with (
        patch("simdrive.device.list_devices", return_value=[device_mock]),
        patch("simdrive.device.libimobiledevice_available", return_value=(True, [])),
    ):
        from simdrive.server import tool_list_devices
        result = tool_list_devices({})

    hid_note = result.get("hid_note", "")
    assert "bootstrap" in hid_note.lower() or "bootstrap-device" in hid_note, (
        f"hid_note does not mention 'bootstrap' or 'bootstrap-device': {hid_note!r}"
    )


# ── compact mode ────────────────────────────────────────────────────────────


def test_list_devices_compact_slims_per_device_entries(registry_dir):
    """compact=true must drop diagnostic per-device fields (model, transport,
    last_seen, unavailable_reason), keeping only {udid, name, state, hid_supported}.
    """
    udid = "00008150-COMPACT01"
    _write_registry(registry_dir, udid)
    device_mock = _make_real_device(udid)

    with (
        patch("simdrive.device.list_devices", return_value=[device_mock]),
        patch("simdrive.device.libimobiledevice_available", return_value=(True, [])),
    ):
        from simdrive.server import tool_list_devices
        result = tool_list_devices({"compact": True})

    dev = result["devices"][0]
    assert set(dev.keys()) == {"udid", "name", "state", "hid_supported"}
    # Top-level diagnostic fields also dropped in compact mode.
    assert "libimobiledevice_ready" not in result
    assert "missing_tools" not in result
    assert "hid_note" not in result
    # Functional fields preserved.
    assert result["ok"] is True
    assert dev["udid"] == udid
    assert dev["hid_supported"] is True


def test_list_devices_default_keeps_full_payload(registry_dir):
    """compact=false (default) must return the legacy shape unchanged."""
    udid = "00008150-FULLDEF01"
    _write_registry(registry_dir, udid)
    device_mock = _make_real_device(udid)

    with (
        patch("simdrive.device.list_devices", return_value=[device_mock]),
        patch("simdrive.device.libimobiledevice_available", return_value=(True, [])),
    ):
        from simdrive.server import tool_list_devices
        result = tool_list_devices({})

    dev = result["devices"][0]
    # Full key set present.
    assert {"udid", "name", "model", "transport", "state",
            "hid_supported", "last_seen", "unavailable_reason"} <= set(dev.keys())
    assert "hid_note" in result
    assert "libimobiledevice_ready" in result
