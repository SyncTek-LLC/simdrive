"""Tests for simdrive.wda.bootstrap.

Hardware paths (xcodebuild, install, syslog tail) are mocked via
unittest.mock.patch("simdrive.wda.bootstrap.subprocess.run") and
unittest.mock.patch("simdrive.wda.bootstrap.subprocess.Popen").

Coverage target: ≥90% (real device interactions are intentionally unmockable
without full XCTest environment; those are guarded by integration test labels).
"""
from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolate_registry(tmp_path, monkeypatch):
    """Redirect all registry I/O to a temp dir."""
    monkeypatch.setenv("WDA_REGISTRY_DIR", str(tmp_path))


@pytest.fixture()
def mock_run_ok():
    """subprocess.run that always returns returncode=0."""
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.returncode = 0
    m.stdout = ""
    m.stderr = ""
    return m


# ── verify_host_tools ─────────────────────────────────────────────────────────


def test_verify_host_tools_ok(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda t: f"/usr/bin/{t}")
    from simdrive.wda.bootstrap import verify_host_tools
    verify_host_tools()  # should not raise


def test_verify_host_tools_missing_xcodebuild(monkeypatch):
    import shutil
    from simdrive.errors import SimdriveError
    monkeypatch.setattr(shutil, "which", lambda t: None if t == "xcodebuild" else f"/usr/bin/{t}")
    from simdrive.wda.bootstrap import verify_host_tools
    with pytest.raises(SimdriveError) as exc:
        verify_host_tools()
    assert exc.value.code == "wda_host_tools_missing"
    assert exc.value.details["tool"] == "xcodebuild"
    assert "Recovery:" in exc.value.message


def test_verify_host_tools_missing_idevicepair(monkeypatch):
    import shutil
    from simdrive.errors import SimdriveError
    monkeypatch.setattr(shutil, "which", lambda t: None if t == "idevicepair" else f"/usr/bin/{t}")
    from simdrive.wda.bootstrap import verify_host_tools
    with pytest.raises(SimdriveError) as exc:
        verify_host_tools()
    assert exc.value.code == "wda_host_tools_missing"
    assert exc.value.details["tool"] == "idevicepair"


def test_verify_host_tools_missing_xcrun(monkeypatch):
    import shutil
    from simdrive.errors import SimdriveError
    monkeypatch.setattr(shutil, "which", lambda t: None if t == "xcrun" else f"/usr/bin/{t}")
    from simdrive.wda.bootstrap import verify_host_tools
    with pytest.raises(SimdriveError) as exc:
        verify_host_tools()
    assert exc.value.code == "wda_host_tools_missing"


# ── verify_device_ready ───────────────────────────────────────────────────────


def _devicectl_json(paired: str = "paired", dev_mode: str = "enabled", ddi: bool = True) -> str:
    # Matches the real `xcrun devicectl device info details --json-output -` structure:
    # result.connectionProperties.pairingState
    # result.deviceProperties.{developerModeStatus, ddiServicesAvailable}
    return json.dumps({
        "result": {
            "connectionProperties": {
                "pairingState": paired,
                "tunnelState": "connected",
            },
            "deviceProperties": {
                "developerModeStatus": dev_mode,
                "ddiServicesAvailable": ddi,
                "osVersionNumber": "26.3.1",
            },
            "hardwareProperties": {
                "marketingName": "iPhone 17 Pro Max",
            },
        }
    })


def test_verify_device_ready_ok():
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=_devicectl_json())
        from simdrive.wda.bootstrap import verify_device_ready
        verify_device_ready("TESTUDID")


def test_verify_device_not_paired():
    from simdrive.errors import SimdriveError
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=_devicectl_json(paired="unpaired"))
        from simdrive.wda.bootstrap import verify_device_ready
        with pytest.raises(SimdriveError) as exc:
            verify_device_ready("TESTUDID")
    assert exc.value.code == "wda_device_not_ready"
    assert any("pairingState" in m for m in exc.value.details["missing"])
    assert "Recovery:" in exc.value.message


def test_verify_device_developer_mode_off():
    from simdrive.errors import SimdriveError
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=_devicectl_json(dev_mode="disabled"))
        from simdrive.wda.bootstrap import verify_device_ready
        with pytest.raises(SimdriveError) as exc:
            verify_device_ready("TESTUDID")
    assert "developerModeStatus" in str(exc.value.details["missing"])


def test_verify_device_ddi_unavailable():
    from simdrive.errors import SimdriveError
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=_devicectl_json(ddi=False))
        from simdrive.wda.bootstrap import verify_device_ready
        with pytest.raises(SimdriveError) as exc:
            verify_device_ready("TESTUDID")
    assert any("ddiServicesAvailable" in m for m in exc.value.details["missing"])


def test_verify_device_devicectl_nonzero():
    from simdrive.errors import SimdriveError
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="no device")
        from simdrive.wda.bootstrap import verify_device_ready
        with pytest.raises(SimdriveError) as exc:
            verify_device_ready("TESTUDID")
    assert exc.value.code == "wda_device_not_ready"


def test_verify_device_unparseable_json():
    from simdrive.errors import SimdriveError
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="not-json-{{")
        from simdrive.wda.bootstrap import verify_device_ready
        with pytest.raises(SimdriveError) as exc:
            verify_device_ready("TESTUDID")
    assert "devicectl_output_unparseable" in exc.value.details["missing"]


# ── _parse_identities ─────────────────────────────────────────────────────────


def test_parse_identities_happy_path():
    from simdrive.wda.bootstrap import _parse_identities
    output = (
        "1) ABCDEF1234567890ABCDEF1234567890ABCDEF12 \"Apple Development: Alice (E52N8732YT)\"\n"
        "2) 1234567890ABCDEF1234567890ABCDEF12345678 \"Apple Development: Bob (36Z53T97PH)\"\n"
        "    2 valid identities found\n"
    )
    result = _parse_identities(output)
    assert len(result) == 2
    assert result[0]["name"] == "Apple Development: Alice (E52N8732YT)"
    assert result[0]["team_id"] == "E52N8732YT"
    assert result[1]["team_id"] == "36Z53T97PH"


def test_parse_identities_skips_expired():
    from simdrive.wda.bootstrap import _parse_identities
    output = (
        "1) ABCDEF1234567890ABCDEF1234567890ABCDEF12 \"iPhone Developer: Old (ZZZZZZZZZZ)\" (CSSMERR_TP_CERT_EXPIRED)\n"
        "2) 1234567890ABCDEF1234567890ABCDEF12345678 \"Apple Development: Valid (AAABBBCCCD)\"\n"
    )
    result = _parse_identities(output)
    assert len(result) == 1
    assert result[0]["team_id"] == "AAABBBCCCD"


def test_parse_identities_no_identities():
    from simdrive.wda.bootstrap import _parse_identities
    assert _parse_identities("   0 valid identities found\n") == []


def test_parse_identities_extracts_sha1():
    from simdrive.wda.bootstrap import _parse_identities
    output = '1) AABBCCDDEEFF00112233445566778899AABBCCDD "Apple Development: Test (TEAM0001XX)"\n'
    result = _parse_identities(output)
    assert result[0]["sha1"] == "AABBCCDDEEFF00112233445566778899AABBCCDD"


# ── resolve_signing_identity ──────────────────────────────────────────────────


SINGLE_IDENTITY_OUTPUT = (
    '1) AABBCCDDEEFF00112233445566778899AABBCCDD "Apple Development: Maurice (E52N8732YT)"\n'
    "    1 valid identities found\n"
)

TWO_IDENTITY_OUTPUT = (
    '1) AABBCCDDEEFF00112233445566778899AABBCCDD "Apple Development: Maurice (E52N8732YT)"\n'
    '2) 1122334455667788990011223344556677889900 "Apple Development: Personal (36Z53T97PH)"\n'
    "    2 valid identities found\n"
)


def test_resolve_uses_explicit_identity():
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=SINGLE_IDENTITY_OUTPUT)
        from simdrive.wda.bootstrap import resolve_signing_identity
        name, team = resolve_signing_identity(signing_identity="Apple Development: Custom (XY1234567Z)")
    assert name == "Apple Development: Custom (XY1234567Z)"
    assert team == "XY1234567Z"


def test_resolve_uses_explicit_team_id():
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=SINGLE_IDENTITY_OUTPUT)
        from simdrive.wda.bootstrap import resolve_signing_identity
        name, team = resolve_signing_identity(
            signing_identity="Apple Development: Custom (E52N8732YT)",
            team_id="OVERRIDE01T",
        )
    assert team == "OVERRIDE01T"


def test_resolve_auto_selects_single_identity():
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=SINGLE_IDENTITY_OUTPUT)
        from simdrive.wda.bootstrap import resolve_signing_identity
        name, team = resolve_signing_identity()
    assert "Maurice" in name
    assert team == "E52N8732YT"


def test_resolve_raises_no_identity():
    from simdrive.errors import SimdriveError
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="   0 valid identities found\n")
        from simdrive.wda.bootstrap import resolve_signing_identity
        with pytest.raises(SimdriveError) as exc:
            resolve_signing_identity()
    assert exc.value.code == "wda_no_signing_identity"
    assert "Recovery:" in exc.value.message


def test_resolve_raises_ambiguous():
    from simdrive.errors import SimdriveError
    # TWO_IDENTITY_OUTPUT has two distinct Apple Development certs
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=TWO_IDENTITY_OUTPUT)
        from simdrive.wda.bootstrap import resolve_signing_identity
        with pytest.raises(SimdriveError) as exc:
            resolve_signing_identity()
    assert exc.value.code == "wda_signing_ambiguous"
    assert "Recovery:" in exc.value.message
    assert len(exc.value.details["identities"]) == 2


# Bug 1 fix: when team_id supplied and multiple Apple Dev certs exist, filter by team_id.
def test_resolve_signing_identity_filters_by_team_id():
    """When 2 Apple Development certs exist + team_id supplied, returns the matching one."""
    from simdrive.wda.bootstrap import resolve_signing_identity
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=TWO_IDENTITY_OUTPUT)
        name, team = resolve_signing_identity(team_id="E52N8732YT")
    assert "Maurice" in name
    assert team == "E52N8732YT"


def test_resolve_signing_identity_filters_by_team_id_second():
    """When 2 Apple Development certs exist + team_id for the second, returns the second."""
    from simdrive.wda.bootstrap import resolve_signing_identity
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=TWO_IDENTITY_OUTPUT)
        name, team = resolve_signing_identity(team_id="36Z53T97PH")
    assert "Personal" in name
    assert team == "36Z53T97PH"


def test_resolve_auto_selects_when_one_apple_dev_among_many():
    """If there are mixed cert types but exactly one 'Apple Development', auto-select it."""
    mixed = (
        '1) AABB112233445566778899AABBCCDDEEFF0011 "iPhone Distribution: OldCert (DIST000001)"\n'
        '2) AABBCCDDEEFF00112233445566778899AABBCCDD "Apple Development: Maurice (E52N8732YT)"\n'
        "    2 valid identities found\n"
    )
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=mixed)
        from simdrive.wda.bootstrap import resolve_signing_identity
        name, team = resolve_signing_identity()
    assert "Maurice" in name
    assert team == "E52N8732YT"


# Personal Team fix: when team_id has no matching cert, return (None, team_id).
def test_resolve_signing_identity_returns_none_for_personal_team():
    """When team_id has no matching cert in keychain (Apple Personal Team case),
    return (None, team_id) so xcodebuild can use -allowProvisioningUpdates to
    fetch a cert at build time."""
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=TWO_IDENTITY_OUTPUT)
        from simdrive.wda.bootstrap import resolve_signing_identity
        # B3HE38966G is not in TWO_IDENTITY_OUTPUT (which has E52N8732YT and 36Z53T97PH)
        result_identity, result_team = resolve_signing_identity(team_id="B3HE38966G")
    assert result_identity is None
    assert result_team == "B3HE38966G"


def test_resolve_signing_identity_returns_none_for_personal_team_empty_keychain():
    """When team_id supplied but keychain has zero certs at all, also return (None, team_id)."""
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="   0 valid identities found\n")
        from simdrive.wda.bootstrap import resolve_signing_identity
        result_identity, result_team = resolve_signing_identity(team_id="B3HE38966G")
    assert result_identity is None
    assert result_team == "B3HE38966G"


def test_resolve_signing_identity_picks_newest_when_team_has_multiple_certs(monkeypatch):
    """B2: When team_id matches multiple certs, pick the most-recently-issued one
    instead of raising ambiguous. All matches share team_id so they are
    equivalent for codesigning; the older cert is typically expired or revoked.
    """
    two_same_team = (
        '1) AABBCCDDEEFF00112233445566778899AABBCCDD "Apple Development: alice@example.com (AAAAAAAAAA)"\n'
        '2) 1122334455667788990011223344556677889900 "Apple Development: alice-old@example.com (AAAAAAAAAA)"\n'
        "    2 valid identities found\n"
    )

    def _stub_not_before(name: str):
        # Newer cert wins; older entry's name has "alice-old" and is from 2024.
        if "alice-old" in name:
            return "2024-01-15T00:00:00"
        return "2026-04-30T00:00:00"

    monkeypatch.setattr("simdrive.wda.bootstrap._cert_not_before", _stub_not_before)

    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=two_same_team)
        from simdrive.wda.bootstrap import resolve_signing_identity
        name, team = resolve_signing_identity(team_id="AAAAAAAAAA")

    assert "alice@example.com" in name and "alice-old" not in name
    assert team == "AAAAAAAAAA"


def test_pick_newest_identity_falls_back_when_no_dates(monkeypatch):
    """B2: If openssl/security can't resolve dates for any cert, fall back to
    the first entry (deterministic, matches pre-B2 behaviour)."""
    monkeypatch.setattr("simdrive.wda.bootstrap._cert_not_before", lambda _: None)

    from simdrive.wda.bootstrap import _pick_newest_identity
    a = {"sha1": "AAA", "name": "Apple Development: A (TEAM000001)", "team_id": "TEAM000001"}
    b = {"sha1": "BBB", "name": "Apple Development: B (TEAM000001)", "team_id": "TEAM000001"}
    assert _pick_newest_identity([a, b]) is a


# ── Bug 2: hardware UDID resolution ──────────────────────────────────────────


_DEVICECTL_HW_JSON = json.dumps({
    "result": {
        "hardwareProperties": {
            "udid": "00008130-001A2B3C4D5E6F70",
            "marketingName": "iPhone 17 Pro Max",
        }
    }
})


def test_bootstrap_resolves_hardware_udid_via_devicectl():
    """resolve_hardware_udid calls devicectl device info details --json-output - and parses hardwareProperties.udid."""
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=_DEVICECTL_HW_JSON)
        from simdrive.wda.bootstrap import resolve_hardware_udid
        hw_udid = resolve_hardware_udid("31471BBD-6889-5DAC-9497-BCD565AB1CD6")
    assert hw_udid == "00008130-001A2B3C4D5E6F70"
    # Verify the command included --json-output -
    call_args = mock_run.call_args[0][0]
    assert "--json-output" in call_args
    assert "-" in call_args


def test_resolve_hardware_udid_falls_back_on_nonzero():
    """When devicectl exits non-zero, falls back to the supplied coredevice UUID."""
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="device not found")
        from simdrive.wda.bootstrap import resolve_hardware_udid
        result = resolve_hardware_udid("FALLBACK-UUID")
    assert result == "FALLBACK-UUID"


def test_resolve_hardware_udid_falls_back_on_missing_field():
    """When JSON lacks hardwareProperties.udid, falls back to the supplied UUID."""
    empty_json = json.dumps({"result": {"hardwareProperties": {}}})
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=empty_json)
        from simdrive.wda.bootstrap import resolve_hardware_udid
        result = resolve_hardware_udid("FALLBACK-UUID-2")
    assert result == "FALLBACK-UUID-2"


# ── Bug 3: correct signing flags ──────────────────────────────────────────────


def test_build_wda_uses_correct_signing_flags(tmp_path):
    """build_wda uses CODE_SIGN_IDENTITY=Apple Development + CODE_SIGN_STYLE=Automatic + -allowProvisioningUpdates + OTHER_CFLAGS."""
    source_dir = tmp_path / "source"
    (source_dir / "WebDriverAgent.xcodeproj").mkdir(parents=True)

    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        from simdrive.wda.bootstrap import build_wda
        build_wda(
            coredevice_uuid="TEST-UUID",
            source_dir=source_dir,
            team_id="E52N8732YT",
            hardware_udid="00008130-001A2B3C4D5E6F70",
        )

    call_args = mock_run.call_args[0][0]
    cmd_str = " ".join(call_args)
    # Bug 3 fix assertions
    assert "CODE_SIGN_IDENTITY=Apple Development" in cmd_str
    assert "CODE_SIGN_STYLE=Automatic" in cmd_str
    assert "DEVELOPMENT_TEAM=E52N8732YT" in cmd_str
    assert "-allowProvisioningUpdates" in cmd_str
    # Bug 4 fix assertion
    assert "OTHER_CFLAGS=-Wno-reserved-identifier" in cmd_str
    # Bug 2 fix: xcodebuild uses hardware UDID, not coredevice UUID
    assert "id=00008130-001A2B3C4D5E6F70" in cmd_str


# ── D8: device + os metadata extraction ─────────────────────────────────────


def test_extract_device_metadata_from_status_real_device():
    """D8: pulls os.version from a real-device WDA /status payload."""
    from simdrive.wda.bootstrap import extract_device_metadata_from_status

    status = {
        "value": {
            "build": {"productBundleIdentifier": "com.facebook.WebDriverAgentRunner"},
            "ios": {"ip": "192.168.1.26"},
            "os": {"name": "iOS", "version": "26.3.1", "sdkVersion": "26.3"},
            "ready": True,
            "sessionId": None,
        }
    }
    meta = extract_device_metadata_from_status(status)
    assert meta == {"os_version": "26.3.1"}


def test_extract_device_metadata_from_status_handles_missing_os():
    """D8: missing fields default to empty string (no KeyError)."""
    from simdrive.wda.bootstrap import extract_device_metadata_from_status

    assert extract_device_metadata_from_status({}) == {"os_version": ""}
    assert extract_device_metadata_from_status({"value": {"ready": True}}) == {"os_version": ""}


def test_fetch_device_name_via_devicectl_returns_name():
    """D8: parses result.deviceProperties.name from devicectl JSON."""
    from simdrive.wda.bootstrap import fetch_device_name_via_devicectl
    payload = json.dumps({
        "result": {
            "deviceProperties": {"name": "Moes Max", "osVersionNumber": "26.3.1"},
            "hardwareProperties": {"marketingName": "iPhone 17 Pro Max"},
        }
    })
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=payload)
        assert fetch_device_name_via_devicectl("UDID-X") == "Moes Max"


def test_fetch_device_name_via_devicectl_returns_empty_on_failure():
    """D8: returns "" on any devicectl error so callers can fall back cleanly."""
    from simdrive.wda.bootstrap import fetch_device_name_via_devicectl
    with patch("simdrive.wda.bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not found")
        assert fetch_device_name_via_devicectl("UDID-X") == ""


def test_smoke_test_returns_status_body_for_metadata_capture():
    """D8: smoke_test must return the parsed body so bootstrap_device can pull
    os_version out of /status without re-fetching."""
    import httpx as _httpx
    body = {"value": {"ready": True, "os": {"version": "26.3.1"}}}
    with patch("simdrive.wda.bootstrap.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = body
        mock_httpx.get.return_value = mock_resp
        mock_httpx.TransportError = _httpx.TransportError

        from simdrive.wda.bootstrap import smoke_test
        returned = smoke_test("192.168.1.26", 8100)

    assert returned == body


# ── B4: FAILED-before-SUCCEEDED retry classification ─────────────────────────


def test_classify_build_log_emits_info_on_recoverable_retry(caplog):
    """B4: when the build log contains FAILED then SUCCEEDED, emit one INFO line
    explaining the recoverable -allowProvisioningUpdates round-trip and keep
    the FAILED token at DEBUG (so log scrapers don't panic).
    """
    log_text = (
        "=== BUILD TARGET WebDriverAgentRunner ===\n"
        "** BUILD FAILED **\n"
        "Provisioning profile not found, fetching ...\n"
        "** BUILD SUCCEEDED **\n"
    )
    import logging
    from simdrive.wda.bootstrap import _classify_build_log

    with caplog.at_level(logging.DEBUG, logger="simdrive.wda.bootstrap"):
        _classify_build_log(log_text)

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records == [], f"unexpected ERROR-level records: {error_records}"

    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(info_records) == 1, f"expected exactly one INFO record, got {info_records}"
    assert "provisioning" in info_records[0].getMessage().lower()
    assert "expected" in info_records[0].getMessage().lower()


def test_classify_build_log_silent_on_clean_success(caplog):
    """B4: a log with only SUCCEEDED (no FAILED) emits nothing — we never want
    to spam INFO when there was no retry to explain."""
    log_text = (
        "=== BUILD TARGET WebDriverAgentRunner ===\n"
        "** BUILD SUCCEEDED **\n"
    )
    import logging
    from simdrive.wda.bootstrap import _classify_build_log

    with caplog.at_level(logging.DEBUG, logger="simdrive.wda.bootstrap"):
        _classify_build_log(log_text)

    assert caplog.records == []


def test_classify_build_log_silent_when_failed_after_succeeded(caplog):
    """B4: SUCCEEDED then FAILED later (e.g. a follow-up phase) is NOT the
    recoverable retry pattern — don't emit the calming INFO."""
    log_text = (
        "** BUILD SUCCEEDED **\n"
        "Some later phase\n"
        "** BUILD FAILED **\n"
    )
    import logging
    from simdrive.wda.bootstrap import _classify_build_log

    with caplog.at_level(logging.DEBUG, logger="simdrive.wda.bootstrap"):
        _classify_build_log(log_text)

    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert info_records == []


# ── Bug 5+6: xcodebuild test-without-building launch ─────────────────────────


def _fake_popen(udid: str, wda_output: str):
    """Build a Popen side_effect that writes wda_output into the per-UDID log file
    (where the daemonized launch_and_discover_port tails for the ServerURLHere line)
    and returns a mock process.
    """
    from simdrive.wda.bootstrap import _log_path

    def _side_effect(cmd, *args, **kwargs):  # noqa: ARG001
        log_path = _log_path(udid)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(wda_output, encoding="utf-8")
        proc = MagicMock()
        proc.pid = 4242
        proc.poll.return_value = None
        return proc

    return _side_effect


def test_launch_uses_xcodebuild_test_without_building(tmp_path):
    """launch_and_discover_port spawns xcodebuild test-without-building, NOT devicectl."""
    # Create a fake xctestrun file
    products_dir = tmp_path / "Build" / "Products"
    products_dir.mkdir(parents=True)
    xctestrun = products_dir / "WebDriverAgentRunner_iphoneos26.3.xctestrun"
    xctestrun.write_text("<dict/>")

    wda_output = (
        "Test Suite started\n"
        "ServerURLHere->http://192.168.1.26:8100<-ServerURLHere\n"
    )

    with patch("simdrive.wda.bootstrap.subprocess.Popen") as mock_popen:
        mock_popen.side_effect = _fake_popen("TEST-UUID", wda_output)

        from simdrive.wda.bootstrap import launch_and_discover_port
        host, port = launch_and_discover_port(
            coredevice_uuid="TEST-UUID",
            derived_data=tmp_path,
            hardware_udid="HW-UDID-123",
        )

    assert host == "192.168.1.26"
    assert port == 8100

    # Verify xcodebuild test-without-building was called (NOT devicectl)
    popen_cmd = mock_popen.call_args[0][0]
    assert "xcodebuild" in popen_cmd
    assert "test-without-building" in popen_cmd
    assert "-xctestrun" in popen_cmd
    assert "devicectl" not in " ".join(popen_cmd)


def test_port_discovery_parses_serverurlhere_from_xcodebuild_stdout(tmp_path):
    """Port discovery correctly parses the IP and port from xcodebuild stdout."""
    products_dir = tmp_path / "Build" / "Products"
    products_dir.mkdir(parents=True)
    xctestrun = products_dir / "WebDriverAgentRunner_iphoneos26.3.xctestrun"
    xctestrun.write_text("<dict/>")

    # Test with a different IP/port combination
    wda_output = "ServerURLHere->http://10.0.0.5:9200<-ServerURLHere\n"

    with patch("simdrive.wda.bootstrap.subprocess.Popen") as mock_popen:
        mock_popen.side_effect = _fake_popen("TEST-UUID", wda_output)

        from simdrive.wda.bootstrap import launch_and_discover_port
        host, port = launch_and_discover_port(
            coredevice_uuid="TEST-UUID",
            derived_data=tmp_path,
            hardware_udid="HW-UDID-456",
        )

    assert host == "10.0.0.5"
    assert port == 9200


def test_server_url_regex_captures_host_and_port():
    """_SERVER_URL_RE captures both host (group 1) and port (group 2)."""
    from simdrive.wda.bootstrap import _SERVER_URL_RE

    line = "ServerURLHere->http://192.168.1.26:8100<-ServerURLHere"
    m = _SERVER_URL_RE.search(line)
    assert m is not None
    assert m.group(1) == "192.168.1.26"
    assert m.group(2) == "8100"


def test_server_url_regex_captures_localhost():
    """_SERVER_URL_RE works with localhost too."""
    from simdrive.wda.bootstrap import _SERVER_URL_RE

    line = "2026-05-02 12:00:01 ServerURLHere->http://localhost:8100<-"
    m = _SERVER_URL_RE.search(line)
    assert m is not None
    assert m.group(1) == "localhost"
    assert m.group(2) == "8100"


# ── _parse_pinned_sha ─────────────────────────────────────────────────────────


def test_parse_pinned_sha_returns_repo_and_sha():
    from simdrive.wda.bootstrap import _parse_pinned_sha
    repo, sha = _parse_pinned_sha()
    assert "appium/WebDriverAgent" in repo
    assert len(sha) > 10


# ── port discovery regex ──────────────────────────────────────────────────────


def test_server_url_regex_matches():
    from simdrive.wda.bootstrap import _SERVER_URL_RE
    line = "2026-05-02 12:00:01 ServerURLHere->http://localhost:8100<-"
    m = _SERVER_URL_RE.search(line)
    assert m is not None
    # group(1) = host, group(2) = port
    assert m.group(1) == "localhost"
    assert m.group(2) == "8100"


def test_server_url_regex_no_match():
    from simdrive.wda.bootstrap import _SERVER_URL_RE
    assert _SERVER_URL_RE.search("nothing here") is None


def test_server_url_regex_with_ip():
    from simdrive.wda.bootstrap import _SERVER_URL_RE
    line = "ServerURLHere->http://192.168.1.5:9100<-"
    m = _SERVER_URL_RE.search(line)
    assert m is not None
    assert m.group(1) == "192.168.1.5"
    assert m.group(2) == "9100"


# ── smoke_test ────────────────────────────────────────────────────────────────


def test_smoke_test_passes():
    import httpx

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": {"ready": True}})

    with patch("simdrive.wda.bootstrap.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"value": {"ready": True}}
        mock_httpx.get.return_value = mock_resp
        mock_httpx.TransportError = httpx.TransportError

        from simdrive.wda.bootstrap import smoke_test
        smoke_test("localhost", 8100)


def test_smoke_test_fails_on_non_200():
    from simdrive.errors import SimdriveError
    import httpx as _httpx

    with patch("simdrive.wda.bootstrap.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.is_success = False
        mock_resp.status_code = 503
        mock_resp.text = "Service Unavailable"
        mock_httpx.get.return_value = mock_resp
        mock_httpx.TransportError = _httpx.TransportError

        from simdrive.wda.bootstrap import smoke_test
        with pytest.raises(SimdriveError) as exc:
            smoke_test("localhost", 8100)
    assert exc.value.code == "wda_smoke_failed"
    assert "Recovery:" in exc.value.message


def test_smoke_test_fails_when_ready_false():
    from simdrive.errors import SimdriveError
    import httpx as _httpx

    with patch("simdrive.wda.bootstrap.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"value": {"ready": False}}
        mock_httpx.get.return_value = mock_resp
        mock_httpx.TransportError = _httpx.TransportError

        from simdrive.wda.bootstrap import smoke_test
        with pytest.raises(SimdriveError) as exc:
            smoke_test("localhost", 8100)
    assert exc.value.code == "wda_smoke_failed"


def test_smoke_test_raises_on_transport_error():
    from simdrive.errors import SimdriveError
    import httpx as _httpx

    with patch("simdrive.wda.bootstrap.httpx") as mock_httpx:
        mock_httpx.get.side_effect = _httpx.ConnectError("refused")
        mock_httpx.TransportError = _httpx.TransportError

        from simdrive.wda.bootstrap import smoke_test
        with pytest.raises(SimdriveError) as exc:
            smoke_test("localhost", 8100)
    assert exc.value.code == "wda_smoke_failed"


# ── wda error constructors (Recovery: required) ───────────────────────────────


# ── verify_xcode_account_for_team ────────────────────────────────────────────


def test_verify_xcode_account_raises_when_defaults_key_missing(monkeypatch):
    """When `defaults read` returns non-zero (key doesn't exist), raise."""
    def fake_run(args, **kwargs):
        from subprocess import CompletedProcess
        return CompletedProcess(args=args, returncode=1, stdout="", stderr="The domain/default pair does not exist")
    monkeypatch.setattr("simdrive.wda.bootstrap.subprocess.run", fake_run)

    from simdrive.errors import SimdriveError
    from simdrive.wda.bootstrap import verify_xcode_account_for_team
    with pytest.raises(SimdriveError) as exc_info:
        verify_xcode_account_for_team("E52N8732YT")
    assert exc_info.value.code == "wda_xcode_account_not_authenticated"
    assert "E52N8732YT" in exc_info.value.message
    assert "Xcode" in exc_info.value.message
    assert "Accounts" in exc_info.value.message


def test_verify_xcode_account_raises_when_account_list_empty(monkeypatch):
    """When defaults returns a list with no identifiers, raise."""
    def fake_run(args, **kwargs):
        from subprocess import CompletedProcess
        return CompletedProcess(args=args, returncode=0, stdout="{\n}\n", stderr="")
    monkeypatch.setattr("simdrive.wda.bootstrap.subprocess.run", fake_run)

    from simdrive.errors import SimdriveError
    from simdrive.wda.bootstrap import verify_xcode_account_for_team
    with pytest.raises(SimdriveError) as exc_info:
        verify_xcode_account_for_team("E52N8732YT")
    assert exc_info.value.code == "wda_xcode_account_not_authenticated"


def test_verify_xcode_account_passes_when_team_bound(monkeypatch):
    """B1: When defaults output names the requested team_id, pass without raising.

    Real-world shape — Xcode persists each Apple ID account with its team
    bindings serialised under a `teamIDs` array (or `teamID` key for the
    primary team). The team id we require must appear inside that structure.
    """
    def fake_run(args, **kwargs):
        from subprocess import CompletedProcess
        stdout = """{
    "IDE.Identifiers.Prod" =     (
                {
            identifier = "5AB0A02E-3F17-4098-932D-7F19CDBF16FA";
            teamIDs = (
                "E52N8732YT"
            );
        }
    );
}
"""
        return CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")
    monkeypatch.setattr("simdrive.wda.bootstrap.subprocess.run", fake_run)

    from simdrive.wda.bootstrap import verify_xcode_account_for_team
    verify_xcode_account_for_team("E52N8732YT")


def test_verify_xcode_account_raises_when_only_other_teams_signed_in(monkeypatch, capsys):
    """B1+: Account signed in for a *different* team now defers rather than
    raising — the plist has an `identifier` entry so at least one Apple ID is
    signed in, and xcodebuild is trusted to fail with a meaningful error if the
    requested team isn't accessible. The function must return normally and print
    the deferral message.
    """
    def fake_run(args, **kwargs):
        from subprocess import CompletedProcess
        stdout = """{
    "IDE.Identifiers.Prod" =     (
                {
            identifier = "5AB0A02E-3F17-4098-932D-7F19CDBF16FA";
            teamIDs = (
                "OTHERTEAM1"
            );
        }
    );
}
"""
        return CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")
    monkeypatch.setattr("simdrive.wda.bootstrap.subprocess.run", fake_run)

    from simdrive.wda.bootstrap import verify_xcode_account_for_team
    # B1+ relax: should NOT raise when any Apple ID is signed in
    verify_xcode_account_for_team("E52N8732YT")

    captured = capsys.readouterr()
    assert "E52N8732YT" in captured.out
    assert "deferring final team check to xcodebuild" in captured.out


def test_verify_xcode_account_raises_when_no_accounts_signed_in(monkeypatch):
    """B1+: When the plist has NO `identifier` entries (no Apple ID signed in
    at all), verify_xcode_account_for_team must still raise
    wda_xcode_account_not_authenticated — that is the only remaining hard failure.
    """
    def fake_run(args, **kwargs):
        from subprocess import CompletedProcess
        # Plist with no identifier entries — not even a different team
        stdout = """{
    "IDE.Identifiers.Prod" =     (
    );
}
"""
        return CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")
    monkeypatch.setattr("simdrive.wda.bootstrap.subprocess.run", fake_run)

    from simdrive.errors import SimdriveError
    from simdrive.wda.bootstrap import verify_xcode_account_for_team
    with pytest.raises(SimdriveError) as exc_info:
        verify_xcode_account_for_team("E52N8732YT")
    assert exc_info.value.code == "wda_xcode_account_not_authenticated"


def test_verify_xcode_account_passes_with_paid_team_kv_form(monkeypatch):
    """B1: paid Developer Program accounts persist the team via `teamID = "..."`.
    Match that form too, not just the array form.
    """
    def fake_run(args, **kwargs):
        from subprocess import CompletedProcess
        stdout = """{
    "IDE.Identifiers.Prod" =     (
                {
            identifier = "5AB0A02E-3F17-4098-932D-7F19CDBF16FA";
            DVTDeveloperAccountTeamID = "PAIDTEAM12";
        }
    );
}
"""
        return CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")
    monkeypatch.setattr("simdrive.wda.bootstrap.subprocess.run", fake_run)

    from simdrive.wda.bootstrap import verify_xcode_account_for_team
    verify_xcode_account_for_team("PAIDTEAM12")


def test_all_wda_errors_have_recovery():
    from simdrive.wda.errors import (
        wda_host_tools_missing,
        wda_device_not_ready,
        wda_no_signing_identity,
        wda_signing_ambiguous,
        wda_build_failed,
        wda_install_failed,
        wda_port_discovery_timeout,
        wda_smoke_failed,
        wda_session_lost,
        wda_xcode_account_not_authenticated,
        wda_device_locked,
    )
    errors = [
        wda_host_tools_missing("xcodebuild"),
        wda_device_not_ready("UDID", ["something"]),
        wda_no_signing_identity(),
        wda_signing_ambiguous(["Apple Development: A", "Apple Development: B"]),
        wda_build_failed("/path/build.log"),
        wda_install_failed("stderr text"),
        wda_port_discovery_timeout("UDID"),
        wda_smoke_failed(503, "body text"),
        wda_session_lost("UDID"),
        wda_session_lost("UDID", last_seen_at=1234567890.0),
        wda_xcode_account_not_authenticated("E52N8732YT"),
        wda_device_locked("UDID"),
    ]
    for err in errors:
        assert "Recovery" in err.message, f"{err.code} missing 'Recovery' in message"


# ── port discovery timeout constant ──────────────────────────────────────────


def test_port_discovery_timeout_constant_is_60():
    """Guard against accidental regression of the timeout back below 60s."""
    from simdrive.wda.bootstrap import _PORT_DISCOVERY_TIMEOUT_S
    assert _PORT_DISCOVERY_TIMEOUT_S == 60, (
        f"_PORT_DISCOVERY_TIMEOUT_S should be 60 (real-device xcodebuild preflight "
        f"takes 20-40s on first launch); got {_PORT_DISCOVERY_TIMEOUT_S}"
    )


# ── locked-device detection ───────────────────────────────────────────────────


def test_port_discovery_raises_device_locked_on_unlock_message(tmp_path):
    """When xcodebuild stdout contains 'Unlock <name> to Continue', raise wda_device_locked."""
    products_dir = tmp_path / "Build" / "Products"
    products_dir.mkdir(parents=True)
    xctestrun = products_dir / "WebDriverAgentRunner_iphoneos26.3.xctestrun"
    xctestrun.write_text("<dict/>")

    fake_lines = (
        "Command line invocation:\n"
        "    xcodebuild test-without-building ...\n"
        "[MT] Run Destination Preflight: The destination is not ready.\n"
        'Error Domain=com.apple.dt.deviceprep Code=-3 "Unlock Moes Max to Continue" UserInfo=...\n'
        "[MT] Run Destination Preflight: Waiting for the destination to become ready.\n"
    )

    from simdrive.errors import SimdriveError
    with patch("simdrive.wda.bootstrap.subprocess.Popen") as mock_popen:
        mock_popen.side_effect = _fake_popen("TEST-LOCKED-UUID", fake_lines)

        from simdrive.wda.bootstrap import launch_and_discover_port
        with pytest.raises(SimdriveError) as exc_info:
            launch_and_discover_port(
                coredevice_uuid="TEST-LOCKED-UUID",
                derived_data=tmp_path,
                hardware_udid="HW-UDID-LOCKED",
            )

    assert exc_info.value.code == "wda_device_locked"
    assert "Unlock" in exc_info.value.message
    assert "passcode" in exc_info.value.message.lower()
    assert exc_info.value.details["udid"] == "TEST-LOCKED-UUID"


def test_port_discovery_raises_device_locked_on_device_is_locked_phrase(tmp_path):
    """When xcodebuild stdout contains 'device is locked', raise wda_device_locked."""
    products_dir = tmp_path / "Build" / "Products"
    products_dir.mkdir(parents=True)
    xctestrun = products_dir / "WebDriverAgentRunner_iphoneos26.3.xctestrun"
    xctestrun.write_text("<dict/>")

    fake_lines = (
        "Xcode cannot launch WebDriverAgentRunner on Moes Max because the device is locked.\n"
    )

    from simdrive.errors import SimdriveError
    with patch("simdrive.wda.bootstrap.subprocess.Popen") as mock_popen:
        mock_popen.side_effect = _fake_popen("TEST-LOCKED-UUID-2", fake_lines)

        from simdrive.wda.bootstrap import launch_and_discover_port
        with pytest.raises(SimdriveError) as exc_info:
            launch_and_discover_port(
                coredevice_uuid="TEST-LOCKED-UUID-2",
                derived_data=tmp_path,
                hardware_udid="HW-UDID-LOCKED-2",
            )

    assert exc_info.value.code == "wda_device_locked"


def test_locked_device_regex_matches_unlock_message():
    """_LOCKED_DEVICE_RE matches xcodebuild's 'Unlock <device> to Continue' pattern."""
    from simdrive.wda.bootstrap import _LOCKED_DEVICE_RE

    assert _LOCKED_DEVICE_RE.search('Error Domain=com.apple.dt.deviceprep Code=-3 "Unlock Moes Max to Continue"') is not None
    assert _LOCKED_DEVICE_RE.search("the device is locked.") is not None
    assert _LOCKED_DEVICE_RE.search("DEVICE IS LOCKED") is not None  # case-insensitive
    assert _LOCKED_DEVICE_RE.search("ServerURLHere->http://192.168.1.1:8100<-") is None  # no match on success


# ── B3: daemonization, wda-up, wda-down ──────────────────────────────────────


def test_launch_passes_start_new_session_true(tmp_path):
    """B3: Popen must be invoked with start_new_session=True so the xcodebuild
    subprocess is detached from the bootstrap CLI's process group and survives
    the CLI exiting (otherwise SIGHUP cascade kills WDA)."""
    products_dir = tmp_path / "Build" / "Products"
    products_dir.mkdir(parents=True)
    (products_dir / "WebDriverAgentRunner_iphoneos26.3.xctestrun").write_text("<dict/>")

    wda_output = "ServerURLHere->http://192.168.1.50:8100<-ServerURLHere\n"

    with patch("simdrive.wda.bootstrap.subprocess.Popen") as mock_popen:
        mock_popen.side_effect = _fake_popen("TEST-DAEMON-UUID", wda_output)

        from simdrive.wda.bootstrap import launch_and_discover_port
        launch_and_discover_port(
            coredevice_uuid="TEST-DAEMON-UUID",
            derived_data=tmp_path,
            hardware_udid="HW-DAEMON",
        )

    kwargs = mock_popen.call_args.kwargs
    assert kwargs.get("start_new_session") is True


def test_launch_writes_pidfile_and_log(tmp_path):
    """B3: launch_and_discover_port writes a pidfile + log next to the registry."""
    products_dir = tmp_path / "Build" / "Products"
    products_dir.mkdir(parents=True)
    (products_dir / "WebDriverAgentRunner_iphoneos26.3.xctestrun").write_text("<dict/>")

    wda_output = "ServerURLHere->http://192.168.1.50:8100<-ServerURLHere\n"

    with patch("simdrive.wda.bootstrap.subprocess.Popen") as mock_popen:
        mock_popen.side_effect = _fake_popen("TEST-PIDFILE-UUID", wda_output)

        from simdrive.wda.bootstrap import launch_and_discover_port, _pid_path, _log_path
        launch_and_discover_port(
            coredevice_uuid="TEST-PIDFILE-UUID",
            derived_data=tmp_path,
            hardware_udid="HW-PIDFILE",
        )

    assert _pid_path("TEST-PIDFILE-UUID").exists()
    assert _pid_path("TEST-PIDFILE-UUID").read_text().strip() == "4242"
    assert _log_path("TEST-PIDFILE-UUID").exists()


def test_wda_up_relaunches_from_registry_without_rebuild(tmp_path):
    """wda_up reads the registry entry and re-launches WDA without rebuilding."""
    products_dir = tmp_path / "Build" / "Products"
    products_dir.mkdir(parents=True)
    xctestrun = products_dir / "WebDriverAgentRunner_iphoneos26.3.xctestrun"
    xctestrun.write_text("<dict/>")

    from simdrive.wda import registry
    registry.save("TEST-UP-UUID", {
        "wda_bundle_id": "com.facebook.WebDriverAgentRunner.xctrunner",
        "derived_data": str(tmp_path),
        "xctestrun_path": str(xctestrun),
        "hardware_udid": "HW-UP",
        "host": "192.168.1.99",
        "ip": "192.168.1.99",
        "port": 8100,
        "team_id": "TEAMUP",
    })

    wda_output = "ServerURLHere->http://192.168.1.99:8100<-ServerURLHere\n"

    with patch("simdrive.wda.bootstrap.subprocess.Popen") as mock_popen, \
         patch("simdrive.wda.bootstrap.smoke_test") as mock_smoke, \
         patch("simdrive.wda.bootstrap.build_wda") as mock_build, \
         patch("simdrive.wda.bootstrap.fetch_device_name_via_devicectl", return_value=""):
        mock_popen.side_effect = _fake_popen("TEST-UP-UUID", wda_output)
        mock_smoke.return_value = {"value": {"ready": True, "os": {"version": "26.3.1"}}}

        from simdrive.wda.bootstrap import wda_up
        entry = wda_up("TEST-UP-UUID")

    # Should NOT have called build_wda — that's the whole point of wda-up.
    mock_build.assert_not_called()
    mock_smoke.assert_called_once()
    assert entry["host"] == "192.168.1.99"
    assert entry["port"] == 8100
    # D8: wda-up refreshes os_version from /status when present.
    assert entry["os_version"] == "26.3.1"


def test_wda_up_writes_device_name_when_devicectl_succeeds(tmp_path):
    """D8: when devicectl returns the device name, wda_up persists it to the
    registry so the next session.start() picks it up automatically."""
    products_dir = tmp_path / "Build" / "Products"
    products_dir.mkdir(parents=True)
    xctestrun = products_dir / "WebDriverAgentRunner_iphoneos26.3.xctestrun"
    xctestrun.write_text("<dict/>")

    from simdrive.wda import registry
    registry.save("TEST-D8-UUID", {
        "wda_bundle_id": "com.facebook.WebDriverAgentRunner.xctrunner",
        "derived_data": str(tmp_path),
        "xctestrun_path": str(xctestrun),
        "hardware_udid": "HW-D8",
        "host": "192.168.1.50",
        "port": 8100,
        "team_id": "TEAMD8",
    })

    wda_output = "ServerURLHere->http://192.168.1.50:8100<-ServerURLHere\n"

    with patch("simdrive.wda.bootstrap.subprocess.Popen") as mock_popen, \
         patch("simdrive.wda.bootstrap.smoke_test") as mock_smoke, \
         patch("simdrive.wda.bootstrap.fetch_device_name_via_devicectl", return_value="Moes Max"):
        mock_popen.side_effect = _fake_popen("TEST-D8-UUID", wda_output)
        mock_smoke.return_value = {"value": {"ready": True, "os": {"version": "26.3.1"}}}

        from simdrive.wda.bootstrap import wda_up
        wda_up("TEST-D8-UUID")

    persisted = registry.load("TEST-D8-UUID")
    assert persisted["device_name"] == "Moes Max"
    assert persisted["os_version"] == "26.3.1"


def test_wda_up_raises_when_no_registry_entry(tmp_path):
    """wda_up raises wda_not_bootstrapped if no registry entry exists."""
    from simdrive.errors import SimdriveError
    from simdrive.wda.bootstrap import wda_up

    with pytest.raises(SimdriveError) as exc_info:
        wda_up("UNKNOWN-UUID")
    assert exc_info.value.code == "wda_not_bootstrapped"


def test_wda_down_kills_process_via_pidfile(tmp_path):
    """wda_down reads the pidfile and SIGTERMs the recorded PID."""
    from simdrive.wda.bootstrap import _pid_path, wda_down

    pid_file = _pid_path("TEST-DOWN-UUID")
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text("9999", encoding="utf-8")

    with patch("simdrive.wda.bootstrap.os.kill") as mock_kill:
        result = wda_down("TEST-DOWN-UUID")

    import signal
    mock_kill.assert_called_once_with(9999, signal.SIGTERM)
    assert result is True
    assert not pid_file.exists()  # pidfile cleaned up


def test_wda_down_returns_false_when_no_pidfile(tmp_path):
    """wda_down is a no-op (returns False) when the pidfile is missing."""
    from simdrive.wda.bootstrap import wda_down

    assert wda_down("MISSING-UUID") is False


def test_wda_down_handles_already_dead_process(tmp_path):
    """wda_down survives ProcessLookupError when the PID is stale."""
    from simdrive.wda.bootstrap import _pid_path, wda_down

    pid_file = _pid_path("TEST-STALE-UUID")
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text("12345", encoding="utf-8")

    with patch("simdrive.wda.bootstrap.os.kill", side_effect=ProcessLookupError()):
        result = wda_down("TEST-STALE-UUID")

    assert result is False
    assert not pid_file.exists()  # pidfile still cleaned up


# Real-device coverage: the daemonization survives a real `simdrive bootstrap-device`
# CLI exit. Covered by the dogfood script against Moes Max (see
# simdrive/docs/DOGFOOD_FEEDBACK_*.md).
