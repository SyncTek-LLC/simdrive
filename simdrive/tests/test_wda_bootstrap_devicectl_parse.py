"""Unit tests for the devicectl JSON parser inside verify_device_ready.

Exercises the `xcrun devicectl device info details --json-output -` JSON paths
confirmed against iPhone 17 Pro Max (Moes Max, iOS 26.3.1):

  result.connectionProperties.pairingState       -> "paired"
  result.connectionProperties.tunnelState        -> "connected"
  result.deviceProperties.developerModeStatus    -> "enabled"
  result.deviceProperties.ddiServicesAvailable   -> true (bool)
  result.hardwareProperties.marketingName        -> "iPhone 17 Pro Max"
  result.deviceProperties.osVersionNumber        -> "26.3.1 (a)"
  result.deviceProperties.osBuildUpdate          -> "23D771330a"

All subprocess.run calls are mocked — no real device required.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


# ── minimal JSON fixture ───────────────────────────────────────────────────────

def _make_devicectl_json(
    pairing_state: str = "paired",
    tunnel_state: str = "connected",
    developer_mode: str = "enabled",
    ddi_available: bool = True,
    marketing_name: str = "iPhone 17 Pro Max",
    os_version: str = "26.3.1 (a)",
    os_build: str = "23D771330a",
) -> str:
    """Build a minimal but structurally accurate devicectl JSON blob."""
    return json.dumps({
        "result": {
            "connectionProperties": {
                "pairingState": pairing_state,
                "tunnelState": tunnel_state,
            },
            "deviceProperties": {
                "developerModeStatus": developer_mode,
                "ddiServicesAvailable": ddi_available,
                "osVersionNumber": os_version,
                "osBuildUpdate": os_build,
            },
            "hardwareProperties": {
                "marketingName": marketing_name,
            },
        }
    })


_FAKE_UDID = "00008150-000A1B2C3D4E001A"


def _mock_run(json_str: str, returncode: int = 0) -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = json_str
    m.stderr = ""
    return m


# ── happy path ────────────────────────────────────────────────────────────────


def test_happy_path_paired_devmode_ddi():
    """Fully-ready device: paired + developer mode on + DDI available → no exception."""
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = _mock_run(_make_devicectl_json())
        from simdrive.wda.bootstrap import verify_device_ready
        # Should complete without raising
        verify_device_ready(_FAKE_UDID)
    mock_run.assert_called_once()
    # Confirm the --json-output flag is present in the call
    cmd = mock_run.call_args[0][0]
    assert "--json-output" in cmd
    assert "-" in cmd


# ── not paired ────────────────────────────────────────────────────────────────


def test_unpaired_raises_wda_device_not_ready():
    """pairingState='unpaired' → raises wda_device_not_ready listing pairingState."""
    from simdrive.errors import SimdriveError

    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = _mock_run(
            _make_devicectl_json(pairing_state="unpaired")
        )
        from simdrive.wda.bootstrap import verify_device_ready
        with pytest.raises(SimdriveError) as exc:
            verify_device_ready(_FAKE_UDID)

    assert exc.value.code == "wda_device_not_ready"
    missing = exc.value.details["missing"]
    assert any("pairingState" in m for m in missing), f"pairingState not in missing: {missing}"
    assert "Recovery:" in exc.value.message


# ── developer mode off ────────────────────────────────────────────────────────


def test_developer_mode_disabled_raises():
    """developerModeStatus='disabled' → raises listing developerModeStatus."""
    from simdrive.errors import SimdriveError

    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = _mock_run(
            _make_devicectl_json(developer_mode="disabled")
        )
        from simdrive.wda.bootstrap import verify_device_ready
        with pytest.raises(SimdriveError) as exc:
            verify_device_ready(_FAKE_UDID)

    assert exc.value.code == "wda_device_not_ready"
    missing = exc.value.details["missing"]
    assert any("developerModeStatus" in m for m in missing), f"developerModeStatus not in missing: {missing}"


# ── DDI unavailable ───────────────────────────────────────────────────────────


def test_ddi_unavailable_raises():
    """ddiServicesAvailable=false → raises listing ddiServicesAvailable."""
    from simdrive.errors import SimdriveError

    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = _mock_run(
            _make_devicectl_json(ddi_available=False)
        )
        from simdrive.wda.bootstrap import verify_device_ready
        with pytest.raises(SimdriveError) as exc:
            verify_device_ready(_FAKE_UDID)

    assert exc.value.code == "wda_device_not_ready"
    missing = exc.value.details["missing"]
    assert any("ddiServicesAvailable" in m for m in missing), f"ddiServicesAvailable not in missing: {missing}"


# ── all-three-conditions-failed ───────────────────────────────────────────────


def test_all_conditions_fail_all_reported():
    """All three checks fail → all three appear in the missing list."""
    from simdrive.errors import SimdriveError

    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = _mock_run(
            _make_devicectl_json(
                pairing_state="unpaired",
                developer_mode="disabled",
                ddi_available=False,
            )
        )
        from simdrive.wda.bootstrap import verify_device_ready
        with pytest.raises(SimdriveError) as exc:
            verify_device_ready(_FAKE_UDID)

    missing = exc.value.details["missing"]
    assert len(missing) == 3
    assert any("pairingState" in m for m in missing)
    assert any("developerModeStatus" in m for m in missing)
    assert any("ddiServicesAvailable" in m for m in missing)


# ── nonzero returncode ────────────────────────────────────────────────────────


def test_devicectl_nonzero_exit_raises_not_found():
    """Non-zero exit from devicectl → device_not_found_or_not_connected."""
    from simdrive.errors import SimdriveError

    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = _mock_run("", returncode=1)
        from simdrive.wda.bootstrap import verify_device_ready
        with pytest.raises(SimdriveError) as exc:
            verify_device_ready(_FAKE_UDID)

    assert exc.value.code == "wda_device_not_ready"
    assert "device_not_found_or_not_connected" in exc.value.details["missing"]


# ── malformed JSON ────────────────────────────────────────────────────────────


def test_malformed_json_raises_unparseable():
    """Non-JSON output → devicectl_output_unparseable in missing list."""
    from simdrive.errors import SimdriveError

    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = _mock_run("• pairingState: paired\n▿ developerMode: enabled\n")
        from simdrive.wda.bootstrap import verify_device_ready
        with pytest.raises(SimdriveError) as exc:
            verify_device_ready(_FAKE_UDID)

    assert exc.value.code == "wda_device_not_ready"
    assert "devicectl_output_unparseable" in exc.value.details["missing"]


# ── JSON paths validated ───────────────────────────────────────────────────────


def test_json_paths_are_nested_not_flat():
    """Confirm the parser reads from connectionProperties/deviceProperties sub-objects,
    NOT from a flat devices[] array (the old broken structure)."""
    # Build JSON with the correct nested structure but wrong values
    broken_flat_json = json.dumps({
        "result": {
            # Old broken structure — fields at result level, not nested
            "pairingState": "paired",
            "developerModeStatus": "enabled",
            "ddiServicesAvailable": True,
            # Correct nested structure intentionally missing → should fail
        }
    })
    from simdrive.errors import SimdriveError

    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = _mock_run(broken_flat_json)
        from simdrive.wda.bootstrap import verify_device_ready
        with pytest.raises(SimdriveError) as exc:
            verify_device_ready(_FAKE_UDID)

    # The flat-structure JSON should fail all checks because the parser looks
    # at connectionProperties / deviceProperties sub-objects, not the root keys.
    missing = exc.value.details["missing"]
    assert any("pairingState" in m for m in missing), (
        "Parser should NOT find pairingState at result root — it must read "
        "result.connectionProperties.pairingState"
    )
