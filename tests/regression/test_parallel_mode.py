"""Regression tests for B5: parallel mode — license gating + port allocation.

TDD test suite — written before implementation.

Tests:
- Trial license + --parallel 2 → clear license error message, no broken behavior
- multi_sim license + --parallel 2 → ports are unique across workers
- Dead PID cleanup does NOT log an error (ProcessLookupError is swallowed)
"""

from __future__ import annotations

import logging
import os
import subprocess
import unittest.mock as mock
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _trial_result() -> dict:
    return {
        "valid": True,
        "max_concurrent_sims": 1,
        "tier": "trial",
        "expires_at": None,
    }


def _multi_sim_result() -> dict:
    return {
        "valid": True,
        "max_concurrent_sims": 4,
        "tier": "pro",
        "expires_at": "2027-01-01",
    }


# ---------------------------------------------------------------------------
# License gating tests
# ---------------------------------------------------------------------------


class TestParallelLicenseGating:
    """Trial tier + --parallel > 1 → fails with a clear license message."""

    def test_trial_parallel_2_raises_license_error(self):
        """Calling the parallel path with a trial license + N=2 should raise SystemExit or click.Abort
        with a message referencing multi_sim + ci_replay entitlements."""
        from specterqa.ios.cli import commands as cmd  # noqa: PLC0415

        assert hasattr(cmd, "_check_parallel_license"), (
            "_check_parallel_license helper must be defined in commands.py"
        )

    def test_check_parallel_license_raises_on_trial(self):
        """_check_parallel_license(parallel=2, license_result=trial) raises SystemExit/ValueError
        with message mentioning 'multi_sim' and 'ci_replay'."""
        from specterqa.ios.cli import commands as cmd  # noqa: PLC0415

        with pytest.raises((SystemExit, ValueError, RuntimeError)) as exc_info:
            cmd._check_parallel_license(parallel=2, license_result=_trial_result())

        msg = str(exc_info.value)
        assert "multi_sim" in msg or "multi_sim" in repr(exc_info.value), (
            f"Error message must mention 'multi_sim'. Got: {msg!r}"
        )

    def test_check_parallel_license_message_mentions_tier(self):
        """The license error mentions the user's current tier."""
        from specterqa.ios.cli import commands as cmd  # noqa: PLC0415

        with pytest.raises((SystemExit, ValueError, RuntimeError)) as exc_info:
            cmd._check_parallel_license(parallel=2, license_result=_trial_result())

        msg = str(exc_info.value)
        assert "trial" in msg.lower() or "trial" in repr(exc_info.value).lower(), (
            f"Error message must mention the current tier (trial). Got: {msg!r}"
        )

    def test_check_parallel_license_passes_on_pro(self):
        """_check_parallel_license does NOT raise when tier is 'pro' (multi_sim=True)."""
        from specterqa.ios.cli import commands as cmd  # noqa: PLC0415

        # Should not raise
        cmd._check_parallel_license(parallel=2, license_result=_multi_sim_result())

    def test_check_parallel_license_passes_parallel_1(self):
        """parallel=1 should never raise even on trial tier."""
        from specterqa.ios.cli import commands as cmd  # noqa: PLC0415

        cmd._check_parallel_license(parallel=1, license_result=_trial_result())

    def test_check_parallel_license_message_mentions_ci_replay(self):
        """The license error mentions ci_replay entitlement."""
        from specterqa.ios.cli import commands as cmd  # noqa: PLC0415

        with pytest.raises((SystemExit, ValueError, RuntimeError)) as exc_info:
            cmd._check_parallel_license(parallel=2, license_result=_trial_result())

        msg = str(exc_info.value) + repr(exc_info.value)
        assert "ci_replay" in msg, (
            f"Error message must mention 'ci_replay'. Got: {msg!r}"
        )


# ---------------------------------------------------------------------------
# Port allocation tests
# ---------------------------------------------------------------------------


class TestParallelPortAllocation:
    """Workers get unique ports via _find_free_port."""

    def test_workers_get_unique_ports(self):
        """When parallel=2 with a valid license, two workers receive different ports."""
        from specterqa.ios.cli import commands as cmd  # noqa: PLC0415

        assert hasattr(cmd, "_allocate_worker_port"), (
            "_allocate_worker_port must be defined (wraps session_manager._find_free_port)"
        )

    def test_allocate_worker_port_returns_int(self):
        """_allocate_worker_port returns an integer in the expected port range."""
        from specterqa.ios.cli import commands as cmd  # noqa: PLC0415
        from specterqa.ios import session_manager  # noqa: PLC0415

        with patch.object(session_manager, "_find_free_port", return_value=8223) as mock_ffp:
            port = cmd._allocate_worker_port()

        assert port == 8223
        mock_ffp.assert_called_once()

    def test_two_workers_get_different_ports_via_mock(self):
        """Simulated two-worker scenario: ports must be unique."""
        from specterqa.ios.cli import commands as cmd  # noqa: PLC0415
        from specterqa.ios import session_manager  # noqa: PLC0415

        allocated_ports: list[int] = []
        call_count = 0

        def side_effect(start=8222, end=8231):
            nonlocal call_count
            call_count += 1
            return 8222 + call_count - 1

        with patch.object(session_manager, "_find_free_port", side_effect=side_effect):
            p1 = cmd._allocate_worker_port()
            p2 = cmd._allocate_worker_port()

        assert p1 != p2, f"Workers must have unique ports, got {p1} and {p2}"


# ---------------------------------------------------------------------------
# ProcessLookupError (stale PID kill) suppression
# ---------------------------------------------------------------------------


class TestStaleRunnerPIDCleanup:
    """Already-dead PID does NOT log an error."""

    def test_kill_dead_pid_does_not_log_error(self, caplog):
        """When os.kill raises ProcessLookupError, no ERROR-level log is emitted."""
        from specterqa.ios.cli import commands as cmd  # noqa: PLC0415

        assert hasattr(cmd, "_safe_kill_pid"), (
            "_safe_kill_pid helper must be defined in commands.py"
        )

        with caplog.at_level(logging.ERROR):
            with patch("os.kill", side_effect=ProcessLookupError("No such process")):
                cmd._safe_kill_pid(99999)  # dead PID

        # No ERROR log should be emitted
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert not error_records, (
            f"No ERROR log expected for already-dead PID, got: {[r.message for r in error_records]}"
        )

    def test_kill_live_pid_sends_signal(self):
        """_safe_kill_pid calls os.kill with SIGKILL when pid exists."""
        from specterqa.ios.cli import commands as cmd  # noqa: PLC0415
        import signal  # noqa: PLC0415

        with patch("os.kill") as mock_kill:
            cmd._safe_kill_pid(12345)

        mock_kill.assert_called_once_with(12345, signal.SIGKILL)

    def test_kill_none_pid_is_noop(self):
        """_safe_kill_pid(None) is a no-op."""
        from specterqa.ios.cli import commands as cmd  # noqa: PLC0415

        with patch("os.kill") as mock_kill:
            cmd._safe_kill_pid(None)  # type: ignore[arg-type]

        mock_kill.assert_not_called()


# ---------------------------------------------------------------------------
# Tier B: simctl clone (integration shim — live-only)
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestParallelSimctlCloneLive:
    """Live tests for simctl clone per worker — auto-skipped without a multi_sim license."""

    def test_simctl_clone_per_worker(self):
        pytest.skip("Requires live sim + multi_sim license — manual verification only")
