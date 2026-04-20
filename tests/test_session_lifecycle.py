"""Regression tests for F3: ios_stop_session must NOT shut down the simulator.

The sim should remain Booted after stop_session — runner teardown stops
xcodebuild + releases port only, leaving the sim in its pre-session state.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch, call

import pytest

from specterqa.ios.session_manager import TestSession


class TestStopSessionDoesNotShutdownSim:
    """_teardown() in direct mode must NOT call simctl shutdown on the source sim."""

    def _make_session(self, udid: str = "FAKE-DIRECT-UDID-001") -> TestSession:
        """Build a minimal direct-mode TestSession (no clone)."""
        session = TestSession.__new__(TestSession)
        session.source_udid = udid
        session._target_udid = udid
        session._clone_udid = None       # direct mode: no clone
        session._clone_name = None
        session.device_type = "simulator"
        session.bundle_id = "com.example.app"
        session.app_path = None
        session._port = 8222
        session._runner = None
        session._runner_process = None
        session._iproxy_process = None
        return session

    def test_stop_session_does_not_call_simctl_shutdown(self):
        """simctl shutdown must NOT be called during direct-mode teardown (F3 regression)."""
        session = self._make_session()

        shutdown_calls = []

        def fake_simctl(cmd, *args, check=True, **kwargs):
            r = MagicMock()
            r.returncode = 0
            r.stdout = ""
            r.stderr = ""
            # Track any shutdown calls
            if cmd == "shutdown":
                shutdown_calls.append(("shutdown", args))
            return r

        with patch("specterqa.ios.session_manager._simctl", side_effect=fake_simctl), \
             patch("time.sleep"):
            session._teardown()

        assert not shutdown_calls, (
            f"simctl shutdown was called during direct-mode stop_session: {shutdown_calls}. "
            "F3 regression — stop_session must NOT shut down the simulator."
        )

    def test_stop_session_sends_boot_to_keep_sim_alive(self):
        """After direct-mode teardown, simctl boot is called to keep the sim Booted."""
        session = self._make_session()

        boot_calls = []

        def fake_simctl(cmd, *args, check=True, **kwargs):
            r = MagicMock()
            r.returncode = 0
            r.stdout = ""
            r.stderr = ""
            if cmd == "boot":
                boot_calls.append(("boot", args))
            return r

        with patch("specterqa.ios.session_manager._simctl", side_effect=fake_simctl), \
             patch("time.sleep"):
            session._teardown()

        # boot should be called to keep sim alive after xcodebuild teardown
        assert any(c[0] == "boot" for c in boot_calls), (
            "simctl boot was NOT called after direct-mode teardown — "
            "sim may end up Shutdown after stop_session."
        )

    def test_clone_mode_shutdown_is_still_called(self):
        """Clone mode teardown must still shutdown + delete the clone."""
        session = self._make_session()
        session._clone_udid = "FAKE-CLONE-UDID-999"
        session._clone_name = "specterqa-test-abcd1234"

        clone_shutdown_calls = []
        clone_delete_calls = []

        def fake_simctl(cmd, *args, check=True, **kwargs):
            r = MagicMock()
            r.returncode = 0
            r.stdout = ""
            r.stderr = ""
            if cmd == "shutdown" and "FAKE-CLONE-UDID-999" in args:
                clone_shutdown_calls.append(args)
            if cmd == "delete" and "FAKE-CLONE-UDID-999" in args:
                clone_delete_calls.append(args)
            return r

        with patch("specterqa.ios.session_manager._simctl", side_effect=fake_simctl), \
             patch("time.sleep"):
            session._teardown()

        assert clone_shutdown_calls, "Clone should be shutdown during teardown"
        assert clone_delete_calls, "Clone should be deleted during teardown"
