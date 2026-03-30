"""Tests for IOSMCPServer (M17a) — INIT-2026-492.

TDD Phase — tests written BEFORE implementation exists.
These tests are importable even when the implementation module is absent.

This file tests the iOS-specific MCP server, which is distinct from the
general SpecterQA MCP server (specterqa.mcp.server).

Module under test (to be created by CodeAtlas):
  specterqa/ios/mcp/server.py  — IOSMCPServer
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.mcp.server import IOSMCPServer  # type: ignore[import]
    _SERVER_AVAILABLE = True
except ImportError:
    _SERVER_AVAILABLE = False
    IOSMCPServer = None  # type: ignore[assignment,misc]

needs_server = pytest.mark.skipif(
    not _SERVER_AVAILABLE,
    reason="specterqa.ios.mcp.server not yet implemented",
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SIMCTL_DEVICES_OUTPUT = json.dumps({
    "devices": {
        "com.apple.CoreSimulator.SimRuntime.iOS-17-0": [
            {
                "name": "iPhone 15",
                "udid": "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
                "state": "Booted",
                "isAvailable": True,
            },
            {
                "name": "iPhone SE (3rd generation)",
                "udid": "FFFFFFFF-0000-1111-2222-333333333333",
                "state": "Shutdown",
                "isAvailable": True,
            },
        ]
    }
})


def _make_pool(udid: str = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE") -> MagicMock:
    """Build a mock SimulatorPool."""
    pool = MagicMock()
    sim = MagicMock()
    sim.udid = udid
    # Support both explicit acquire/release and context-manager style.
    pool.acquire = MagicMock(return_value=sim)
    pool.release = MagicMock()
    pool.__enter__ = MagicMock(return_value=sim)
    pool.__exit__ = MagicMock(return_value=False)
    return pool


def _make_step_runner_factory() -> MagicMock:
    """Build a mock factory that produces a step runner."""
    factory = MagicMock()
    runner = MagicMock()
    runner.run_step = MagicMock(return_value={
        "success": True,
        "finding": None,
        "covered_area": "home",
        "cost": 0.01,
        "critical": False,
    })
    factory.return_value = runner
    return factory


def _make_scenario() -> dict:
    return {
        "id": "sc-001",
        "name": "Login flow",
        "steps": [
            {"action": "tap", "target": "login_button"},
            {"action": "fill", "target": "email_field", "value": "user@example.com"},
        ],
    }


# ===========================================================================
# M17a: IOSMCPServer — 10 tests
# ===========================================================================


@needs_server
class TestRegisterTools:
    """_register_tools() returns a well-formed MCP tool manifest."""

    def test_register_tools_returns_list(self):
        server = IOSMCPServer()
        tools = server._register_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0

    def test_register_tools_each_entry_has_name_description_parameters(self):
        server = IOSMCPServer()
        tools = server._register_tools()
        for tool in tools:
            assert "name" in tool, f"Tool entry missing 'name': {tool}"
            assert "description" in tool, f"Tool entry missing 'description': {tool}"
            assert "parameters" in tool, f"Tool entry missing 'parameters': {tool}"


@needs_server
class TestRunTest:
    """run_test() simulator lifecycle and return shape."""

    def test_run_test_acquires_simulator_from_pool(self):
        pool = _make_pool()
        factory = _make_step_runner_factory()
        server = IOSMCPServer(pool=pool, step_runner_factory=factory)
        server.run_test(scenario=_make_scenario())
        pool.acquire.assert_called_once()

    def test_run_test_releases_simulator_after_completion(self):
        pool = _make_pool()
        factory = _make_step_runner_factory()
        server = IOSMCPServer(pool=pool, step_runner_factory=factory)
        server.run_test(scenario=_make_scenario())
        pool.release.assert_called_once()

    def test_run_test_returns_dict_with_run_id_results_device_id(self):
        pool = _make_pool()
        factory = _make_step_runner_factory()
        server = IOSMCPServer(pool=pool, step_runner_factory=factory)
        result = server.run_test(scenario=_make_scenario())
        assert isinstance(result, dict)
        assert "run_id" in result, "'run_id' missing from run_test result"
        assert "results" in result, "'results' missing from run_test result"
        assert "device_id" in result, "'device_id' missing from run_test result"

    def test_run_test_pool_release_called_even_on_error(self):
        pool = _make_pool()
        factory = _make_step_runner_factory()
        factory.return_value.run_step.side_effect = RuntimeError("sim crashed")
        server = IOSMCPServer(pool=pool, step_runner_factory=factory)
        with pytest.raises(Exception):
            server.run_test(scenario=_make_scenario())
        pool.release.assert_called_once()


@needs_server
class TestRunExploratory:
    """run_exploratory() delegates to ExploratoryAgent."""

    def test_run_exploratory_uses_exploratory_agent(self):
        pool = _make_pool()
        factory = _make_step_runner_factory()
        server = IOSMCPServer(pool=pool, step_runner_factory=factory)
        persona = {"name": "Tester", "role": "qa", "goals": ["find bugs"], "traits": ["thorough"]}
        result = server.run_exploratory(persona=persona, app_context="main screen")
        assert isinstance(result, dict)


@needs_server
class TestGetResults:
    """get_results() result store."""

    def test_get_results_returns_stored_results(self):
        pool = _make_pool()
        factory = _make_step_runner_factory()
        server = IOSMCPServer(pool=pool, step_runner_factory=factory)
        run = server.run_test(scenario=_make_scenario())
        run_id = run["run_id"]
        fetched = server.get_results(run_id)
        assert fetched is not None

    def test_get_results_raises_key_error_for_unknown_run_id(self):
        server = IOSMCPServer()
        with pytest.raises(KeyError):
            server.get_results("nonexistent-run-id-xyz")


@needs_server
class TestListDevicesAndScenarios:
    """list_devices() and list_scenarios() basic contracts."""

    def test_list_devices_calls_simctl_list(self):
        server = IOSMCPServer()
        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.stdout = _SIMCTL_DEVICES_OUTPUT
            mock_proc.returncode = 0
            mock_run.return_value = mock_proc
            devices = server.list_devices()
        mock_run.assert_called_once()
        args = mock_run.call_args
        # Extract the command from positional or keyword args.
        cmd = args[0][0] if args[0] else args.kwargs.get("args", [])
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        assert "simctl" in cmd_str, f"Expected 'simctl' in command, got: {cmd_str}"
        assert "devices" in cmd_str, f"Expected 'devices' in command, got: {cmd_str}"

    def test_list_scenarios_returns_list_of_dicts(self):
        server = IOSMCPServer()
        scenarios = server.list_scenarios()
        assert isinstance(scenarios, list)
        for s in scenarios:
            assert isinstance(s, dict)
