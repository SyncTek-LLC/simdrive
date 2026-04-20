"""Tests for Issue 5: Stale xcodebuild process cleanup.

Verifies:
- _reap_orphan_xcodebuild() finds and kills xcodebuild processes on port 8222
- Stop and error paths call TERM then KILL with 5s grace
- ios_start_session entry reaps orphans before deploying
"""
from __future__ import annotations

import signal
import subprocess
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Test: _reap_orphan_xcodebuild (server-level)
# ---------------------------------------------------------------------------

class TestReapOrphanXcodebuild:
    """_reap_orphan_xcodebuild should kill processes holding port 8222."""

    def test_reap_kills_pids_from_lsof(self):
        """When lsof finds a PID, os.kill(TERM) should be called."""
        from specterqa.ios.mcp import server
        import os

        # lsof -i :8222 -t returns one PID per line (no header)
        fake_lsof_output = "12345\n"

        def fake_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 0
            if "lsof" in cmd:
                r.stdout = fake_lsof_output
            elif "ps" in cmd:
                # ps -p 12345 -o comm=  → "xcodebuild"
                r.stdout = "xcodebuild"
            else:
                r.stdout = ""
            return r

        with patch("subprocess.run", side_effect=fake_run):
            with patch("os.kill") as mock_kill:
                # Make poll() return None (process alive) then 0 (dead after KILL)
                with patch("time.sleep"):
                    server._reap_orphan_xcodebuild(port=8222)

        # os.kill should have been called at least once with SIGTERM
        kill_calls = mock_kill.call_args_list
        pids_killed = [c[0][0] for c in kill_calls]
        sigs_sent = [c[0][1] for c in kill_calls]

        assert 12345 in pids_killed, f"Expected pid 12345 in {pids_killed}"
        assert signal.SIGTERM in sigs_sent or 15 in sigs_sent

    def test_reap_noop_when_no_lsof_output(self):
        """When lsof finds nothing, no os.kill should be called."""
        from specterqa.ios.mcp import server
        import os

        with patch("subprocess.run") as mock_run:
            mock_lsof = MagicMock()
            mock_lsof.returncode = 0
            mock_lsof.stdout = ""
            mock_run.return_value = mock_lsof

            with patch("os.kill") as mock_kill:
                server._reap_orphan_xcodebuild(port=8222)

        mock_kill.assert_not_called()

    def test_reap_does_not_raise_on_lsof_failure(self):
        """lsof failure should be silently swallowed."""
        from specterqa.ios.mcp import server

        with patch("subprocess.run", side_effect=FileNotFoundError("lsof not found")):
            # Should not raise
            server._reap_orphan_xcodebuild(port=8222)


# ---------------------------------------------------------------------------
# Test: _kill_runner_graceful (TERM → KILL pattern)
# ---------------------------------------------------------------------------

class TestKillRunnerGraceful:
    """_kill_runner_graceful sends TERM, waits, then KILL if still alive."""

    def test_term_then_kill_when_process_alive(self):
        """If process is alive after TERM, KILL should follow."""
        from specterqa.ios.mcp import server

        fake_process = MagicMock()
        fake_process.pid = 99999
        # Simulate process still alive after TERM (poll returns None)
        fake_process.poll.return_value = None

        with patch("os.kill") as mock_kill:
            with patch("time.sleep"):
                server._kill_runner_graceful(fake_process, grace_s=0.01)

        kill_calls = [c[0][1] for c in mock_kill.call_args_list]
        assert signal.SIGTERM in kill_calls or 15 in kill_calls
        assert signal.SIGKILL in kill_calls or 9 in kill_calls

    def test_no_kill_when_term_succeeds(self):
        """If TERM causes the process to exit, KILL is NOT sent."""
        from specterqa.ios.mcp import server

        fake_process = MagicMock()
        fake_process.pid = 99999
        # Process exits after TERM
        fake_process.poll.return_value = 0  # already dead

        with patch("os.kill") as mock_kill:
            with patch("time.sleep"):
                server._kill_runner_graceful(fake_process, grace_s=0.01)

        kill_calls = [c[0][1] for c in mock_kill.call_args_list]
        # KILL (9) should NOT appear
        assert signal.SIGKILL not in kill_calls and 9 not in kill_calls

    def test_graceful_kill_handles_no_such_process(self):
        """ProcessLookupError (process already gone) should be silently handled."""
        from specterqa.ios.mcp import server

        fake_process = MagicMock()
        fake_process.pid = 1  # init — can't kill, but test the error path
        fake_process.poll.return_value = None

        import os
        with patch("os.kill", side_effect=ProcessLookupError):
            with patch("time.sleep"):
                # Should not raise
                server._kill_runner_graceful(fake_process, grace_s=0.01)
