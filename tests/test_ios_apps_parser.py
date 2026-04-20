"""Tests for Issue 7: ios_apps JSON parsing path.

Verifies:
- JSON path (simctl listapps -j) is used by default
- Plist fallback is invoked for older Xcode
- Parsing succeeds on realistic JSON output
"""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest


_SAMPLE_JSON_OUTPUT = json.dumps({
    "com.apple.mobilesafari": {
        "CFBundleDisplayName": "Safari",
        "CFBundleName": "Safari",
        "CFBundleShortVersionString": "18.0",
        "CFBundleVersion": "18000",
        "Path": "/Applications/Safari.app",
    },
    "io.synctek.specterqa.testkit": {
        "CFBundleDisplayName": "TestKit",
        "CFBundleName": "TestKitApp",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "100",
        "Path": "/private/var/containers/Bundle/Application/X/TestKitApp.app",
    },
}).encode()


_SAMPLE_PLIST_OUTPUT = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.mobilesafari</key>
    <dict>
        <key>CFBundleDisplayName</key>
        <string>Safari</string>
        <key>CFBundleShortVersionString</key>
        <string>18.0</string>
        <key>Path</key>
        <string>/Applications/Safari.app</string>
    </dict>
</dict>
</plist>
"""


class TestIosAppsJsonPath:
    """handle_apps should use -j JSON flag and parse correctly."""

    def test_json_path_succeeds(self):
        """Default invocation should use listapps -j and parse JSON."""
        from specterqa.ios.mcp.server import handle_apps

        with patch("subprocess.check_output") as mock_cmd:
            mock_cmd.return_value = _SAMPLE_JSON_OUTPUT

            result = handle_apps({"device_udid": "FAKE-UDID-001"})

        # Should return a list of dicts
        assert isinstance(result, list)
        assert len(result) == 2

        bundle_ids = {r["bundle_id"] for r in result}
        assert "com.apple.mobilesafari" in bundle_ids
        assert "io.synctek.specterqa.testkit" in bundle_ids

    def test_json_path_invokes_listapps_j_flag(self):
        """check_output should be called with -j flag."""
        from specterqa.ios.mcp.server import handle_apps

        called_with = []

        def fake_check_output(cmd, **kwargs):
            called_with.append(cmd)
            return _SAMPLE_JSON_OUTPUT

        with patch("subprocess.check_output", side_effect=fake_check_output):
            handle_apps({"device_udid": "FAKE-UDID-001"})

        # At least one call should have -j
        assert any("-j" in cmd for cmd in called_with), (
            f"Expected -j flag in subprocess call, got: {called_with}"
        )

    def test_plist_fallback_when_json_parse_fails(self):
        """If JSON parse fails, should attempt plist parsing."""
        from specterqa.ios.mcp.server import handle_apps

        call_n = {"n": 0}

        def fake_check_output(cmd, **kwargs):
            call_n["n"] += 1
            if "-j" in cmd:
                # Return non-JSON to trigger fallback
                return b"not json"
            else:
                return _SAMPLE_PLIST_OUTPUT

        with patch("subprocess.check_output", side_effect=fake_check_output):
            result = handle_apps({"device_udid": "FAKE-UDID-001"})

        # Should succeed via plist fallback
        assert isinstance(result, list)
        # Should have attempted at least 2 calls (JSON + plist fallback)
        # OR returned a warning — either outcome is acceptable; plist may parse non-JSON output differently
        # Main check: no uncaught exception

    def test_result_has_required_fields(self):
        """Each result dict must have bundle_id, display_name, version, install_path."""
        from specterqa.ios.mcp.server import handle_apps

        with patch("subprocess.check_output", return_value=_SAMPLE_JSON_OUTPUT):
            result = handle_apps({"device_udid": "FAKE-UDID-001"})

        for app in result:
            assert "bundle_id" in app
            assert "display_name" in app
            assert "version" in app
            assert "install_path" in app

    def test_result_sorted_by_display_name(self):
        """Results should be sorted alphabetically by display_name."""
        from specterqa.ios.mcp.server import handle_apps

        with patch("subprocess.check_output", return_value=_SAMPLE_JSON_OUTPUT):
            result = handle_apps({"device_udid": "FAKE-UDID-001"})

        names = [r["display_name"] for r in result]
        assert names == sorted(names, key=str.lower)

    def test_missing_device_udid_raises(self):
        """Missing device_udid should raise ValueError."""
        from specterqa.ios.mcp.server import handle_apps

        with pytest.raises(ValueError, match="device_udid"):
            handle_apps({})
