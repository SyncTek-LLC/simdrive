"""Unit tests for physical device opt-in gate (v14.0.3).

All tests are hermetic — no live simulator required.
Tests cover:
  - device_type="physical" without env var → opt-in error
  - device_type="physical" WITH SPECTERQA_ALLOW_PHYSICAL_DEVICE=1 → no opt-in error
  - device_type="simulator" unaffected regardless of env var
  - env var set to falsy values (0, false) → still blocked
  - ios_get_capabilities returns physical with available=True, default=False, opt_in_env set
"""
from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_license_valid():
    """Return a mock LicenseValidator that always passes."""
    lv = MagicMock()
    lv.return_value.validate.return_value = {"valid": True}
    return lv


def _call_handle_start_session(arguments: dict, env_override: dict | None = None) -> dict:
    """Call handle_start_session with optional env overrides, fully mocked."""
    import specterqa.ios.mcp.server as srv

    license_cls = _mock_license_valid()
    session_mgr = MagicMock()
    session_mgr._find_xctestrun.return_value = "/fake/runner.xctestrun"
    session_mgr._needs_rebuild.return_value = False
    session_mgr._DEFAULT_RUNNER_BUILD_DIR = "/fake/build"
    session_mgr.write_version_marker = MagicMock()

    selector_module = MagicMock()
    chosen = MagicMock()
    chosen.__class__.__name__ = "XCTestBackend"
    selector_module.BackendSelector.return_value.choose.return_value = chosen

    from specterqa.ios.runner_process import RunnerState
    runner_process_module = MagicMock()
    runner_mock = MagicMock()
    runner_mock._port = 8222
    runner_mock._udid = "FAKE-UDID"
    runner_mock.state = RunnerState.RUNNING
    runner_process_module.RunnerProcess.acquire.return_value = runner_mock
    runner_process_module.RunnerDeployError = type("RunnerDeployError", (Exception,), {})
    runner_process_module.RunnerState = RunnerState

    patches = {
        "specterqa.ios.license.validator": MagicMock(LicenseValidator=license_cls),
        "specterqa.ios.runner_process": runner_process_module,
        "specterqa.ios.backends.selector": selector_module,
        "specterqa.ios.som_annotator": MagicMock(SoMAnnotator=MagicMock()),
        "specterqa.ios.backends.xctest_client": MagicMock(XCTestBackend=MagicMock()),
        "specterqa.ios.session_manager": session_mgr,
        "specterqa.ios.replay": MagicMock(ReplayRecorder=MagicMock()),
        "specterqa.ios.drivers.simulator.console": MagicMock(ConsoleMonitor=MagicMock()),
        "specterqa.ios.drivers.simulator.crash": MagicMock(CrashDetector=MagicMock()),
        "specterqa.ios.drivers.simulator.perf": MagicMock(PerfProfiler=MagicMock()),
        "specterqa.ios.drivers.simulator.network": MagicMock(NetworkInspector=MagicMock()),
    }

    saved = {k: os.environ.get(k) for k in (env_override or {})}
    # Determine if any env var in env_override explicitly enables physical opt-in.
    # If not, ensure config file doesn't interfere by patching _read_physical_opt_in.
    env_enables = any(
        v in ("1", "true", "yes")
        for v in (env_override or {}).values()
        if v is not None
    )
    try:
        for k, v in (env_override or {}).items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

        config_mock = MagicMock(return_value=False)  # isolate from ~/.specterqa/config.toml
        with (
            patch.dict("sys.modules", patches),
            patch("specterqa.ios.mcp.server._circuit_breaker"),
            patch("specterqa.ios.config._read_physical_opt_in", config_mock),
            patch("specterqa.ios.config._read_keychain_opt_in", MagicMock(return_value=False)),
        ):
            result = srv.handle_start_session(arguments)
    finally:
        # Restore env
        for k, original in saved.items():
            if original is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = original
        # Clean up module globals
        srv._session = None
        srv._mcp_runner_ref = None
        srv._backend = None
        srv._session_state = "idle"

    return result


# ---------------------------------------------------------------------------
# Tests: physical device opt-in gate
# ---------------------------------------------------------------------------

class TestPhysicalDeviceOptIn:

    def test_physical_without_env_var_returns_optin_error(self):
        """device_type='physical' without opt-in env var → must contain SPECTERQA_ALLOW_PHYSICAL_DEVICE."""
        result = _call_handle_start_session(
            {"bundle_id": "com.example.app", "device_type": "physical"},
            env_override={"SPECTERQA_ALLOW_PHYSICAL_DEVICE": None},
        )
        assert "error" in result, f"Expected error key, got: {result}"
        assert "SPECTERQA_ALLOW_PHYSICAL_DEVICE" in result["error"], (
            f"Expected opt-in env var name in error, got: {result['error']}"
        )

    def test_physical_with_env_var_1_passes_gate(self):
        """device_type='physical' WITH SPECTERQA_ALLOW_PHYSICAL_DEVICE=1 must NOT return opt-in error."""
        result = _call_handle_start_session(
            {"bundle_id": "com.example.app", "device_type": "physical"},
            env_override={"SPECTERQA_ALLOW_PHYSICAL_DEVICE": "1"},
        )
        # Should not be the opt-in error (may succeed or fail for other reasons)
        if "error" in result:
            assert "SPECTERQA_ALLOW_PHYSICAL_DEVICE" not in result["error"], (
                "Should have passed opt-in gate but got opt-in error: " + result["error"]
            )

    def test_simulator_unaffected_without_env_var(self):
        """device_type='simulator' must work regardless of env var."""
        result = _call_handle_start_session(
            {"bundle_id": "com.example.app", "device_type": "simulator"},
            env_override={"SPECTERQA_ALLOW_PHYSICAL_DEVICE": None},
        )
        if "error" in result:
            assert "SPECTERQA_ALLOW_PHYSICAL_DEVICE" not in result["error"], (
                "Simulator path should never return opt-in error: " + result["error"]
            )

    def test_simulator_unaffected_with_env_var(self):
        """device_type='simulator' must work whether env var is set or not."""
        result = _call_handle_start_session(
            {"bundle_id": "com.example.app", "device_type": "simulator"},
            env_override={"SPECTERQA_ALLOW_PHYSICAL_DEVICE": "1"},
        )
        if "error" in result:
            assert "SPECTERQA_ALLOW_PHYSICAL_DEVICE" not in result["error"], (
                "Simulator path should never return opt-in error: " + result["error"]
            )

    def test_physical_env_var_false_is_blocked(self):
        """SPECTERQA_ALLOW_PHYSICAL_DEVICE=false must still block (falsy string check)."""
        result = _call_handle_start_session(
            {"bundle_id": "com.example.app", "device_type": "physical"},
            env_override={"SPECTERQA_ALLOW_PHYSICAL_DEVICE": "false"},
        )
        assert "error" in result
        assert "SPECTERQA_ALLOW_PHYSICAL_DEVICE" in result["error"]

    def test_physical_env_var_zero_is_blocked(self):
        """SPECTERQA_ALLOW_PHYSICAL_DEVICE=0 must still block."""
        result = _call_handle_start_session(
            {"bundle_id": "com.example.app", "device_type": "physical"},
            env_override={"SPECTERQA_ALLOW_PHYSICAL_DEVICE": "0"},
        )
        assert "error" in result
        assert "SPECTERQA_ALLOW_PHYSICAL_DEVICE" in result["error"]

    def test_physical_env_var_yes_is_allowed(self):
        """SPECTERQA_ALLOW_PHYSICAL_DEVICE=yes must pass the gate."""
        result = _call_handle_start_session(
            {"bundle_id": "com.example.app", "device_type": "physical"},
            env_override={"SPECTERQA_ALLOW_PHYSICAL_DEVICE": "yes"},
        )
        if "error" in result:
            assert "SPECTERQA_ALLOW_PHYSICAL_DEVICE" not in result["error"]

    def test_physical_env_var_true_is_allowed(self):
        """SPECTERQA_ALLOW_PHYSICAL_DEVICE=true must pass the gate."""
        result = _call_handle_start_session(
            {"bundle_id": "com.example.app", "device_type": "physical"},
            env_override={"SPECTERQA_ALLOW_PHYSICAL_DEVICE": "true"},
        )
        if "error" in result:
            assert "SPECTERQA_ALLOW_PHYSICAL_DEVICE" not in result["error"]

    def test_physical_env_var_with_leading_trailing_whitespace_is_allowed(self):
        """SPECTERQA_ALLOW_PHYSICAL_DEVICE=' 1 ' (whitespace-padded) must pass after strip()."""
        result = _call_handle_start_session(
            {"bundle_id": "com.example.app", "device_type": "physical"},
            env_override={"SPECTERQA_ALLOW_PHYSICAL_DEVICE": "  1  "},
        )
        if "error" in result:
            assert "SPECTERQA_ALLOW_PHYSICAL_DEVICE" not in result["error"], (
                "Whitespace-padded '  1  ' should be stripped and accepted: " + result["error"]
            )

    def test_physical_env_var_case_insensitive_TRUE(self):
        """SPECTERQA_ALLOW_PHYSICAL_DEVICE=TRUE (uppercase) must pass (lowercased before compare)."""
        result = _call_handle_start_session(
            {"bundle_id": "com.example.app", "device_type": "physical"},
            env_override={"SPECTERQA_ALLOW_PHYSICAL_DEVICE": "TRUE"},
        )
        if "error" in result:
            assert "SPECTERQA_ALLOW_PHYSICAL_DEVICE" not in result["error"], (
                "Uppercase 'TRUE' should be accepted: " + result["error"]
            )

    def test_physical_device_type_uppercase_is_blocked(self):
        """device_type='PHYSICAL' (uppercase) must be blocked — device_type is normalized with .lower()."""
        result = _call_handle_start_session(
            {"bundle_id": "com.example.app", "device_type": "PHYSICAL"},
            env_override={"SPECTERQA_ALLOW_PHYSICAL_DEVICE": None},
        )
        assert "error" in result, f"Expected opt-in error for 'PHYSICAL', got: {result}"
        assert "SPECTERQA_ALLOW_PHYSICAL_DEVICE" in result["error"], (
            "Uppercase 'PHYSICAL' should hit the opt-in gate after .lower() normalization: "
            + result["error"]
        )

    def test_physical_device_type_with_whitespace_is_blocked(self):
        """device_type=' physical' (leading space) must be blocked — device_type is normalized with .strip()."""
        result = _call_handle_start_session(
            {"bundle_id": "com.example.app", "device_type": " physical"},
            env_override={"SPECTERQA_ALLOW_PHYSICAL_DEVICE": None},
        )
        assert "error" in result, f"Expected opt-in error for ' physical', got: {result}"
        assert "SPECTERQA_ALLOW_PHYSICAL_DEVICE" in result["error"], (
            "Whitespace-padded ' physical' should hit the opt-in gate after .strip() normalization: "
            + result["error"]
        )


# ---------------------------------------------------------------------------
# Tests: ios_get_capabilities discovery tool
# ---------------------------------------------------------------------------

class TestIosGetCapabilities:

    def _call_capabilities(self, env_override: dict | None = None) -> dict:
        """Call ios_get_capabilities via create_server().

        Config file and keychain are mocked to isolate from the test environment.
        Config file returns True only when SPECTERQA_ALLOW_PHYSICAL_DEVICE is set.
        """
        import specterqa.ios.mcp.server as srv

        saved = {k: os.environ.get(k) for k in (env_override or {})}
        # Config file should NOT interfere — mock it to return False unless env var is set.
        env_enables = any(
            v in ("1", "true", "yes")
            for v in (env_override or {}).values()
            if v is not None
        )
        try:
            for k, v in (env_override or {}).items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

            server = srv.create_server()

            async def _run():
                with patch("specterqa.ios.config._read_physical_opt_in", return_value=False):
                    with patch("specterqa.ios.config._read_keychain_opt_in", return_value=False):
                        result = await server.call_tool("ios_get_capabilities", {})
                # result is list of content items
                raw = result[0][0].text if (result and result[0]) else result[0].text
                return json.loads(raw)

            return asyncio.run(_run())
        finally:
            for k, original in saved.items():
                if original is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = original

    def test_physical_available_true(self):
        """ios_get_capabilities must report physical device with available=True."""
        caps = self._call_capabilities(env_override={"SPECTERQA_ALLOW_PHYSICAL_DEVICE": None})
        device_types = {d["type"]: d for d in caps.get("device_types", [])}
        assert "physical" in device_types, f"physical not in device_types: {caps}"
        assert device_types["physical"]["available"] is True

    def test_physical_default_false(self):
        """ios_get_capabilities must report physical device with default=False."""
        caps = self._call_capabilities(env_override={"SPECTERQA_ALLOW_PHYSICAL_DEVICE": None})
        device_types = {d["type"]: d for d in caps.get("device_types", [])}
        assert device_types["physical"]["default"] is False

    def test_physical_opt_in_env_present(self):
        """ios_get_capabilities must include opt_in_env pointing to SPECTERQA_ALLOW_PHYSICAL_DEVICE."""
        caps = self._call_capabilities(env_override={"SPECTERQA_ALLOW_PHYSICAL_DEVICE": None})
        device_types = {d["type"]: d for d in caps.get("device_types", [])}
        assert device_types["physical"].get("opt_in_env") == "SPECTERQA_ALLOW_PHYSICAL_DEVICE"

    def test_simulator_available_and_default(self):
        """Simulator must be available=True and default=True."""
        caps = self._call_capabilities()
        device_types = {d["type"]: d for d in caps.get("device_types", [])}
        assert "simulator" in device_types
        assert device_types["simulator"]["available"] is True
        assert device_types["simulator"]["default"] is True

    def test_opt_in_active_reflects_env(self):
        """opt_in_active field must reflect whether the env var is actually set."""
        caps_off = self._call_capabilities(env_override={"SPECTERQA_ALLOW_PHYSICAL_DEVICE": None})
        device_types_off = {d["type"]: d for d in caps_off.get("device_types", [])}
        assert device_types_off["physical"].get("opt_in_active") is False

        caps_on = self._call_capabilities(env_override={"SPECTERQA_ALLOW_PHYSICAL_DEVICE": "1"})
        device_types_on = {d["type"]: d for d in caps_on.get("device_types", [])}
        assert device_types_on["physical"].get("opt_in_active") is True

    def test_tool_count_matches_registered_count(self):
        """ios_get_capabilities.tool_count must equal the actual @mcp.tool registration count."""
        import re
        import specterqa.ios.mcp.server as srv_mod
        from pathlib import Path

        server_src = Path(srv_mod.__file__).read_text(encoding="utf-8")
        actual_count = len(set(re.findall(r'@mcp\.tool\(\s*\n?\s*name="([^"]+)"', server_src)))
        caps = self._call_capabilities()
        reported_count = caps.get("tool_count")
        assert reported_count == actual_count, (
            f"ios_get_capabilities reports tool_count={reported_count} "
            f"but actual registered count is {actual_count}"
        )

    def test_safe_to_call_without_active_session(self):
        """ios_get_capabilities must succeed without a running session (no session state needed)."""
        import specterqa.ios.mcp.server as srv
        # Ensure no session is active
        srv._session = None
        srv._mcp_runner_ref = None
        srv._backend = None
        srv._session_state = "idle"

        try:
            caps = self._call_capabilities(env_override={"SPECTERQA_ALLOW_PHYSICAL_DEVICE": None})
            assert "version" in caps, "Must return version without an active session"
            assert "backends" in caps
            assert "device_types" in caps
        finally:
            srv._session = None
            srv._mcp_runner_ref = None
            srv._backend = None
            srv._session_state = "idle"

    def test_returns_valid_json_dict(self):
        """ios_get_capabilities must return a dict with required top-level keys."""
        caps = self._call_capabilities()
        assert isinstance(caps, dict), f"Expected dict, got {type(caps)}"
        for key in ("version", "backends", "device_types", "tool_count"):
            assert key in caps, f"Missing required key: {key}"
        assert isinstance(caps["backends"], list)
        assert isinstance(caps["device_types"], list)
        assert len(caps["device_types"]) >= 2, "Must list at least simulator and physical"
