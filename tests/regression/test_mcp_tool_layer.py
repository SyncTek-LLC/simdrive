"""MCP tool layer tests — handler return shapes and argument validation.

Uses a real Protocol-conforming StubBackend (no MagicMock/patch).
Per feedback_no_mock_tests_specterqa: real behavior, real class implementing IOSBackend.

Tests invoke handle_* functions directly to validate:
  - Return shape matches docstring expectations
  - Bad args return clear error shapes (not bare TypeError)
  - Session guard works correctly (error, not exception)

Run:
    pytest tests/regression/test_mcp_tool_layer.py -v --tb=short
"""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Stub IOSBackend — a real class implementing the Protocol, not MagicMock
# ---------------------------------------------------------------------------


class StubBackend:
    """Minimal real implementation of IOSBackend Protocol for testing.

    Returns canned responses — no network, no simulator required.
    """

    @classmethod
    def is_available(cls) -> bool:
        return True

    def start(self, device_udid: str, bundle_id: str, **kwargs) -> None:
        pass

    def stop(self) -> None:
        pass

    def health(self) -> dict:
        return {"status": "ok", "pid": 12345}

    def app_state(self) -> str:
        return "foreground"

    def tap(self, x=None, y=None, label=None, identifier=None, element_index=None) -> dict:
        return {"success": True, "tapped": label or identifier or f"({x},{y})"}

    def swipe(self, direction: str = "up", duration_s: float = 0.3) -> dict:
        return {"success": True, "direction": direction}

    def type_text(self, text: str, label=None, identifier=None, element_index=None) -> dict:
        return {"success": True, "typed": text}

    def press_key(self, key: str) -> dict:
        return {"success": True, "key": key}

    def get_elements(self, max_elements: int = 0) -> dict:
        return {"elements": [], "count": 0}

    def find_element(self, **criteria) -> dict | None:
        return None

    def screenshot(self, quality: str = "standard") -> dict:
        # Return the dict format the server expects (same as XCTestBackend)
        return {"base64": "", "result": {"data": ""}}

    def source(self) -> dict:
        return {"xml": "<hierarchy></hierarchy>"}

    # XCTestBackend-specific attributes used by server internals
    _runner_url: str = "http://localhost:8100"
    _port: int = 8100

    def _get(self, path: str) -> dict:
        return {}

    def swipe_back(self) -> dict:
        return {"success": True}

    def long_press(self, x: float, y: float, duration: float = 1.0) -> dict:
        return {"success": True}


# ---------------------------------------------------------------------------
# Fixtures: inject StubBackend into module globals
# ---------------------------------------------------------------------------

import specterqa.ios.mcp.server as _srv


@pytest.fixture()
def stub_session():
    """Install a StubBackend as the active session; restore after test."""
    original_backend = _srv._backend
    original_state = _srv._session_state
    original_annotator = _srv._annotator

    stub = StubBackend()
    _srv._backend = stub
    _srv._session_state = "running"
    _srv._annotator = None  # annotator is not needed for handler-level tests

    yield stub

    _srv._backend = original_backend
    _srv._session_state = original_state
    _srv._annotator = original_annotator


@pytest.fixture()
def no_session():
    """Ensure no session is active."""
    original_backend = _srv._backend
    original_state = _srv._session_state
    _srv._backend = None
    _srv._session_state = "idle"
    yield
    _srv._backend = original_backend
    _srv._session_state = original_state


# ---------------------------------------------------------------------------
# Import handlers under test
# ---------------------------------------------------------------------------

from specterqa.ios.mcp.server import (
    handle_doctor,
    handle_devices,
    handle_apps,
    handle_license_status,
    handle_list_replays,
    handle_validate_replay,
    handle_replay,
)


# ===========================================================================
# Session guard — no active session returns error dict
# ===========================================================================


class TestSessionGuardShape:
    """Handlers requiring a session must return {"error": ...} when none active."""

    def test_replay_no_session_returns_error_dict(self, no_session):
        from specterqa.ios.mcp.server import handle_replay
        result = handle_replay({"name": "smoke_login"})
        assert isinstance(result, dict), "Must return dict, not raise"
        assert "error" in result

    def test_replay_error_mentions_start_session(self, no_session):
        from specterqa.ios.mcp.server import handle_replay
        result = handle_replay({"name": "smoke_login"})
        hint = result.get("hint", "") + result.get("error", "")
        assert "ios_start_session" in hint


# ===========================================================================
# handle_validate_replay — argument validation
# ===========================================================================


class TestValidateReplayArgValidation:

    def test_empty_name_returns_error_list(self):
        result = handle_validate_replay({})
        assert result["valid"] is False
        assert len(result["issues"]) > 0

    def test_nonexistent_name_returns_invalid(self):
        result = handle_validate_replay({"name": "totally_not_real_replay_xyz"})
        assert result["valid"] is False

    def test_valid_fixture_returns_valid_true(self):
        from pathlib import Path
        fixture = Path(__file__).parent.parent / "fixtures" / "replays" / "smoke_login.yaml"
        result = handle_validate_replay({"name": str(fixture)})
        assert result["valid"] is True
        assert result["step_count"] == 3

    def test_return_shape_for_found_replay(self):
        """Validate return shape has all documented keys when replay is found."""
        from pathlib import Path
        fixture = Path(__file__).parent.parent / "fixtures" / "replays" / "smoke_login.yaml"
        result = handle_validate_replay({"name": str(fixture)})
        for key in ("valid", "step_count", "issues", "name", "bundle_id"):
            assert key in result, f"Missing key: {key}"

    def test_return_shape_for_missing_replay(self):
        """Validate return shape when replay is not found (minimal keys required)."""
        result = handle_validate_replay({"name": "nonexistent"})
        # Must always have valid and issues
        assert "valid" in result
        assert "issues" in result
        assert result["valid"] is False


# ===========================================================================
# handle_list_replays — return shape
# ===========================================================================


class TestListReplaysReturnShape:

    def test_returns_list(self):
        result = handle_list_replays({"replay_dir": "/nonexistent"})
        assert isinstance(result, list)

    def test_returns_list_from_fixture(self):
        from pathlib import Path
        fixtures = Path(__file__).parent.parent / "fixtures" / "replays"
        result = handle_list_replays({"replay_dir": str(fixtures)})
        assert isinstance(result, list)
        assert len(result) >= 1


# ===========================================================================
# handle_doctor — return shape
# ===========================================================================


class TestDoctorReturnShape:

    def test_has_checks_and_overall(self):
        result = handle_doctor({})
        assert "checks" in result
        assert "overall" in result
        assert result["overall"] in ("ok", "degraded", "fail")

    def test_checks_are_dicts_with_pass(self):
        result = handle_doctor({})
        for name, check in result["checks"].items():
            assert isinstance(check.get("pass"), bool), f"Check '{name}'.pass must be bool"


# ===========================================================================
# handle_devices — return shape
# ===========================================================================


class TestDevicesReturnShape:

    def test_returns_list(self):
        result = handle_devices({})
        assert isinstance(result, list)

    def test_does_not_raise(self):
        try:
            handle_devices({})
        except Exception as exc:
            pytest.fail(f"handle_devices must not raise: {exc}")


# ===========================================================================
# handle_apps — argument validation
# ===========================================================================


class TestAppsArgValidation:

    def test_raises_value_error_without_udid(self):
        with pytest.raises(ValueError, match="device_udid"):
            handle_apps({})

    def test_raises_value_error_with_empty_udid(self):
        with pytest.raises(ValueError, match="device_udid"):
            handle_apps({"device_udid": "   "})

    def test_raises_value_error_with_invalid_udid(self):
        with pytest.raises(ValueError):
            handle_apps({"device_udid": "NOT-A-REAL-UDID"})

    def test_error_message_is_actionable(self):
        """The ValueError message should tell the user how to fix it."""
        with pytest.raises(ValueError) as exc_info:
            handle_apps({"device_udid": "INVALID-UDID"})
        msg = str(exc_info.value).lower()
        assert "simctl" in msg or "udid" in msg or "simulator" in msg


# ===========================================================================
# handle_license_status — return shape regardless of env
# ===========================================================================


class TestLicenseStatusReturnShape:

    def test_returns_dict(self):
        result = handle_license_status({})
        assert isinstance(result, dict)

    def test_has_all_required_keys(self):
        result = handle_license_status({})
        for key in ("tier", "valid", "entitlements", "expiry"):
            assert key in result, f"Missing key: {key}"

    def test_tier_is_non_empty_string(self):
        result = handle_license_status({})
        assert isinstance(result["tier"], str) and result["tier"]

    def test_entitlements_dict(self):
        result = handle_license_status({})
        ents = result["entitlements"]
        assert isinstance(ents, dict)
        for k in ("browserstack", "indigo_hid", "ci_replay"):
            assert k in ents
