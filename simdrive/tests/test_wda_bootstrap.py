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


# ── Bug 5+6: xcodebuild test-without-building launch ─────────────────────────


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
        import io
        mock_proc = MagicMock()
        mock_proc.stdout = io.StringIO(wda_output)
        mock_popen.return_value = mock_proc

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
        import io
        mock_proc = MagicMock()
        mock_proc.stdout = io.StringIO(wda_output)
        mock_popen.return_value = mock_proc

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
    ]
    for err in errors:
        assert "Recovery:" in err.message, f"{err.code} missing 'Recovery:' in message"
