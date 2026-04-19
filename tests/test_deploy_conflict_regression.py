"""Regression test for v14.0.1: MCP pre-deploy vs session_manager stale-kill conflict.

Bug (v14.0.0):
    MCP handle_start_session deploys a RunnerProcess on :8222.
    session_manager._kill_stale_runners() then kills that process (treats it as stale).
    Session waits 60s for health and times out.

Fix (Option A):
    _kill_stale_runners skips any xcodebuild PID that is owned by a RunnerProcess
    registry entry — checked via RunnerProcess.owned_pids().
"""

import subprocess
import unittest
from unittest.mock import MagicMock, patch

from specterqa.ios.session_manager import TestSession


class TestDeployConflictRegression(unittest.TestCase):
    """_kill_stale_runners must not kill PIDs owned by RunnerProcess registry."""

    def test_stale_kill_skips_owned_pid(self) -> None:
        """Owned PIDs (in RunnerProcess registry) are NOT killed by _kill_stale_runners."""
        # Simulate: pgrep finds one xcodebuild process with PID 99999.
        owned_pid = 99999

        killed_pids: list[int] = []

        def fake_kill(pid: int, sig: int) -> None:
            killed_pids.append(pid)

        pgrep_result = MagicMock()
        pgrep_result.stdout = f"{owned_pid}\n"

        with (
            patch("specterqa.ios.session_manager.subprocess.run", return_value=pgrep_result),
            patch("specterqa.ios.session_manager.os.kill", side_effect=fake_kill),
            patch(
                "specterqa.ios.runner_process.RunnerProcess.owned_pids",
                return_value={owned_pid},
            ),
        ):
            TestSession._kill_stale_runners()

        self.assertNotIn(
            owned_pid,
            killed_pids,
            f"PID {owned_pid} was killed even though it is in RunnerProcess registry",
        )

    def test_stale_kill_kills_unowned_pid(self) -> None:
        """Truly orphaned PIDs (not in RunnerProcess registry) ARE killed."""
        orphan_pid = 88888

        killed_pids: list[int] = []

        def fake_kill(pid: int, sig: int) -> None:
            killed_pids.append(pid)

        pgrep_result = MagicMock()
        pgrep_result.stdout = f"{orphan_pid}\n"

        with (
            patch("specterqa.ios.session_manager.subprocess.run", return_value=pgrep_result),
            patch("specterqa.ios.session_manager.os.kill", side_effect=fake_kill),
            patch(
                "specterqa.ios.runner_process.RunnerProcess.owned_pids",
                return_value=set(),  # registry is empty — no owned pids
            ),
        ):
            TestSession._kill_stale_runners()

        self.assertIn(
            orphan_pid,
            killed_pids,
            f"PID {orphan_pid} should have been killed (not in registry)",
        )

    def test_owned_pids_returns_pids_from_registry(self) -> None:
        """RunnerProcess.owned_pids() reflects live Popen PIDs in the registry."""
        from specterqa.ios.runner_process import RunnerProcess, _registry, _registry_lock

        mock_proc = MagicMock()
        mock_proc.pid = 77777

        fake_instance = MagicMock()
        fake_instance._process = mock_proc

        key = ("FAKE-UDID", 8222)
        with _registry_lock:
            _registry[key] = fake_instance

        try:
            pids = RunnerProcess.owned_pids()
            self.assertIn(77777, pids)
        finally:
            with _registry_lock:
                _registry.pop(key, None)

    def test_owned_pids_empty_when_registry_empty(self) -> None:
        """RunnerProcess.owned_pids() returns empty set when registry has no entries."""
        from specterqa.ios.runner_process import RunnerProcess, _registry, _registry_lock

        with _registry_lock:
            snapshot = dict(_registry)
            _registry.clear()

        try:
            pids = RunnerProcess.owned_pids()
            self.assertEqual(pids, set())
        finally:
            with _registry_lock:
                _registry.update(snapshot)
