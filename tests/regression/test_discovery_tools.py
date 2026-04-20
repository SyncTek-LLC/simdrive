"""Regression tests for ios_doctor, ios_devices, ios_apps, ios_license_status handlers.

Tests exercise the handler functions directly — real behavior per feedback_no_mock_tests_specterqa.
ios_apps raises ValueError for bad UDIDs, ios_devices returns empty list if no sims booted.

Run:
    pytest tests/regression/test_discovery_tools.py -v --tb=short
"""
from __future__ import annotations

import pytest

from specterqa.ios.mcp.server import (
    handle_apps,
    handle_devices,
    handle_doctor,
    handle_license_status,
)


# ===========================================================================
# handle_doctor
# ===========================================================================


class TestHandleDoctor:
    """ios_doctor returns structured health checks with pass/fail + fix keys."""

    def test_returns_dict_with_checks_and_overall(self):
        result = handle_doctor({})
        assert isinstance(result, dict)
        assert "checks" in result
        assert "overall" in result

    def test_checks_has_required_keys(self):
        result = handle_doctor({})
        checks = result["checks"]
        required = {"xcode_present", "simulators_available", "runner_built"}
        for key in required:
            assert key in checks, f"Missing check: {key}"

    def test_each_check_has_pass_detail_fix(self):
        result = handle_doctor({})
        for name, check in result["checks"].items():
            assert "pass" in check, f"Check '{name}' missing 'pass'"
            assert "detail" in check, f"Check '{name}' missing 'detail'"
            assert "fix" in check, f"Check '{name}' missing 'fix'"

    def test_pass_values_are_bool(self):
        result = handle_doctor({})
        for name, check in result["checks"].items():
            assert isinstance(check["pass"], bool), f"Check '{name}'.pass must be bool"

    def test_overall_is_valid_value(self):
        result = handle_doctor({})
        assert result["overall"] in ("ok", "degraded", "fail"), (
            f"overall must be ok/degraded/fail, got {result['overall']!r}"
        )

    def test_fix_is_none_or_string(self):
        result = handle_doctor({})
        for name, check in result["checks"].items():
            fix = check["fix"]
            assert fix is None or isinstance(fix, str), (
                f"Check '{name}'.fix must be None or str, got {type(fix)}"
            )

    def test_xcode_check_present_on_mac(self):
        """xcode_present check should run on macOS (where Xcode may or may not be installed)."""
        result = handle_doctor({})
        xcode = result["checks"]["xcode_present"]
        # On CI or dev Mac with Xcode installed this should pass
        # On any Mac it should at least return a valid structure
        assert isinstance(xcode["pass"], bool)
        assert isinstance(xcode["detail"], str)

    def test_session_active_check_reflects_no_session(self):
        """When no session is active, session_active check should report fail."""
        import specterqa.ios.mcp.server as srv
        original = srv._backend
        try:
            srv._backend = None
            result = handle_doctor({})
            session_check = result["checks"].get("session_active", {})
            assert session_check.get("pass") is False
        finally:
            srv._backend = original


# ===========================================================================
# handle_devices
# ===========================================================================


class TestHandleDevices:
    """ios_devices returns booted simulator list — never crashes on empty result."""

    def test_returns_list(self):
        result = handle_devices({})
        assert isinstance(result, list), f"Expected list, got {type(result)}"

    def test_does_not_raise_when_no_sims(self):
        """Even with no booted sims, should return an empty list (not raise)."""
        # This is a pure behavior test — if sims are booted we get entries,
        # if not we get []. Both are valid.
        try:
            result = handle_devices({})
            assert isinstance(result, list)
        except Exception as exc:
            pytest.fail(f"handle_devices raised unexpectedly: {exc}")

    def test_each_device_has_required_keys(self):
        result = handle_devices({})
        for device in result:
            assert "udid" in device, f"Missing 'udid' in {device}"
            assert "name" in device, f"Missing 'name' in {device}"
            assert "runtime" in device, f"Missing 'runtime' in {device}"
            assert "state" in device, f"Missing 'state' in {device}"

    def test_udid_is_string(self):
        result = handle_devices({})
        for device in result:
            assert isinstance(device["udid"], str), "udid must be a string"

    def test_state_is_booted(self):
        """All returned devices should be in Booted state (that's what we query)."""
        result = handle_devices({})
        for device in result:
            assert device["state"].lower() == "booted", (
                f"Expected 'Booted', got {device['state']!r}"
            )


# ===========================================================================
# handle_apps
# ===========================================================================


@pytest.mark.live
class TestHandleApps:
    """ios_apps handles bad UDIDs gracefully with ValueError.

    Marked @pytest.mark.live: these exercise the real `simctl listapps`
    subprocess against booted simulators. Earlier suite tests may shut down
    the sim (via session teardown paths), so they can't run hermetically
    in full-suite mode. Run with `pytest -m live` when a sim is booted.
    """

    def test_missing_device_udid_raises_value_error(self):
        with pytest.raises(ValueError, match="device_udid is required"):
            handle_apps({})

    def test_empty_device_udid_raises_value_error(self):
        with pytest.raises(ValueError, match="device_udid is required"):
            handle_apps({"device_udid": ""})

    def test_invalid_udid_raises_value_error(self):
        """A nonsense UDID that simctl doesn't recognize raises ValueError with a clear message."""
        with pytest.raises(ValueError) as exc_info:
            handle_apps({"device_udid": "NOT-A-REAL-UDID-00000000"})
        msg = str(exc_info.value)
        # Should mention the UDID or simctl in the error
        assert "simctl" in msg.lower() or "udid" in msg.lower() or "NOT-A-REAL-UDID" in msg

    def test_result_has_required_keys(self):
        """If a booted sim is available, returned entries have the correct shape."""
        devices = handle_devices({})
        if not devices:
            pytest.skip("No booted simulators — skipping shape check")

        udid = devices[0]["udid"]
        result = handle_apps({"device_udid": udid})
        assert isinstance(result, list)
        for entry in result:
            assert "bundle_id" in entry, f"Missing bundle_id: {entry}"
            assert "display_name" in entry, f"Missing display_name: {entry}"
            assert "version" in entry, f"Missing version: {entry}"
            assert "install_path" in entry, f"Missing install_path: {entry}"

    def test_returns_at_least_one_app_for_booted_sim(self):
        """A real booted simulator should have at least a few apps installed."""
        devices = handle_devices({})
        if not devices:
            pytest.skip("No booted simulators")

        udid = devices[0]["udid"]
        result = handle_apps({"device_udid": udid})
        # Filter out any warning entries
        apps = [a for a in result if not a.get("warning")]
        assert len(apps) > 0, "Expected at least one app on a booted simulator"

    def test_sorted_alphabetically(self):
        """Results should be sorted by display_name alphabetically."""
        devices = handle_devices({})
        if not devices:
            pytest.skip("No booted simulators")

        udid = devices[0]["udid"]
        result = handle_apps({"device_udid": udid})
        apps = [a for a in result if not a.get("warning")]
        names = [a["display_name"].lower() for a in apps]
        assert names == sorted(names), "Apps should be sorted alphabetically by display_name"


# ===========================================================================
# handle_license_status
# ===========================================================================


class TestHandleLicenseStatus:
    """ios_license_status reports tier + entitlements regardless of env state."""

    def test_returns_dict(self):
        result = handle_license_status({})
        assert isinstance(result, dict)

    def test_has_required_keys(self):
        result = handle_license_status({})
        for key in ("tier", "valid", "entitlements", "expiry"):
            assert key in result, f"Missing key: {key}"

    def test_tier_is_string(self):
        result = handle_license_status({})
        assert isinstance(result["tier"], str), "tier must be a string"
        assert len(result["tier"]) > 0, "tier must be non-empty"

    def test_entitlements_is_dict(self):
        result = handle_license_status({})
        assert isinstance(result["entitlements"], dict), "entitlements must be a dict"

    def test_entitlements_has_known_keys(self):
        result = handle_license_status({})
        ents = result["entitlements"]
        for key in ("browserstack", "indigo_hid", "multi_sim", "ci_replay"):
            assert key in ents, f"Missing entitlement: {key}"

    def test_expiry_is_none_or_string(self):
        result = handle_license_status({})
        expiry = result["expiry"]
        assert expiry is None or isinstance(expiry, str), (
            f"expiry must be None or str, got {type(expiry)}"
        )

    def test_founder_tier_when_env_set(self, monkeypatch):
        """When SPECTERQA_IOS_LICENSE=founder, tier should be 'founder'."""
        monkeypatch.setenv("SPECTERQA_IOS_LICENSE", "founder")
        result = handle_license_status({})
        assert result["tier"] == "founder", f"Expected founder, got {result['tier']}"
        assert result["valid"] is True
        assert result["entitlements"]["browserstack"] is True
        assert result["entitlements"]["indigo_hid"] is True

    def test_trial_tier_without_license(self, monkeypatch):
        """Without any license key, tier should be 'trial'."""
        monkeypatch.delenv("SPECTERQA_IOS_LICENSE", raising=False)
        monkeypatch.delenv("SPECTERQA_LICENSE_KEY", raising=False)
        result = handle_license_status({})
        assert result["tier"] in ("trial", "unknown"), (
            f"Expected trial without license, got {result['tier']}"
        )
