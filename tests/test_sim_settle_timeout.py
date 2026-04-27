"""Tests for v15.1.0 sim_settle_timeout on ios_start_session (section 3).

Smart settle wait: sleeps only the remaining delta when the sim just booted.
No wait when the sim has been booted longer than settle_timeout seconds.

All tests use mocked subprocess / time — no live simulator required.
"""
from __future__ import annotations

import datetime
import json
from unittest.mock import MagicMock, patch

import pytest

import specterqa.ios.mcp.server as _srv


TEST_UDID = "SETTLE-TEST-UDID-0001"


def _make_simctl_response_with_boot_time(udid: str, state: str, boot_age_s: float) -> MagicMock:
    """Return a simctl response with lastBootedAt set boot_age_s seconds ago."""
    now = datetime.datetime.now(datetime.timezone.utc)
    boot_dt = now - datetime.timedelta(seconds=boot_age_s)
    boot_str = boot_dt.strftime("%Y-%m-%dT%H:%M:%S") + "+00:00"

    r = MagicMock()
    r.returncode = 0
    r.stdout = json.dumps({
        "devices": {
            "com.apple.CoreSimulator.SimRuntime.iOS-26-0": [
                {
                    "udid": udid,
                    "state": state,
                    "name": "iPhone 15",
                    "lastBootedAt": boot_str,
                }
            ]
        }
    })
    return r


class TestSimSettleTimeout:
    """_sim_settle_wait: wait only when sim just booted."""

    def test_sim_settle_skips_wait_if_sim_long_booted(self):
        """When sim has been booted >10s ago, no sleep should occur."""
        # Sim booted 30s ago — well past the 10s settle window
        with patch("specterqa.ios.mcp.server.subprocess.run", return_value=_make_simctl_response_with_boot_time(TEST_UDID, "Booted", boot_age_s=30.0)), \
             patch("specterqa.ios.mcp.server.time.sleep") as mock_sleep:
            waited = _srv._sim_settle_wait(TEST_UDID, settle_timeout_s=10.0)

        assert waited == 0.0, f"Expected 0 wait for long-booted sim, got {waited}"
        mock_sleep.assert_not_called()

    def test_sim_settle_waits_if_sim_just_booted(self):
        """When sim booted 3s ago with a 10s settle window, should wait ~7s."""
        # Sim booted 3s ago — 7s remaining in the 10s window
        with patch("specterqa.ios.mcp.server.subprocess.run", return_value=_make_simctl_response_with_boot_time(TEST_UDID, "Booted", boot_age_s=3.0)), \
             patch("specterqa.ios.mcp.server.time.sleep") as mock_sleep:
            waited = _srv._sim_settle_wait(TEST_UDID, settle_timeout_s=10.0)

        assert waited > 0, f"Expected wait > 0 for just-booted sim, got {waited}"
        # Should be approximately 7s (10 - 3)
        assert 5.0 <= waited <= 9.0, f"Expected ~7s wait, got {waited}"
        mock_sleep.assert_called_once()
        sleep_arg = mock_sleep.call_args[0][0]
        assert 5.0 <= sleep_arg <= 9.0, f"Expected sleep ~7s, got {sleep_arg}"

    def test_sim_settle_skips_when_timeout_is_zero(self):
        """When settle_timeout_s=0, no wait should occur."""
        with patch("specterqa.ios.mcp.server.subprocess.run") as mock_run, \
             patch("specterqa.ios.mcp.server.time.sleep") as mock_sleep:
            waited = _srv._sim_settle_wait(TEST_UDID, settle_timeout_s=0.0)

        assert waited == 0.0
        mock_sleep.assert_not_called()
        mock_run.assert_not_called()

    def test_sim_settle_handles_missing_boot_time(self):
        """If simctl doesn't report lastBootedAt, settle is skipped gracefully."""
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({
            "devices": {
                "com.apple.CoreSimulator.SimRuntime.iOS-26-0": [
                    {"udid": TEST_UDID, "state": "Booted", "name": "iPhone 15"}
                    # No lastBootedAt key
                ]
            }
        })
        with patch("specterqa.ios.mcp.server.subprocess.run", return_value=r), \
             patch("specterqa.ios.mcp.server.time.sleep") as mock_sleep:
            waited = _srv._sim_settle_wait(TEST_UDID, settle_timeout_s=10.0)

        assert waited == 0.0
        mock_sleep.assert_not_called()

    def test_sim_settle_handles_subprocess_failure(self):
        """If subprocess.run fails, settle is skipped gracefully."""
        r = MagicMock()
        r.returncode = 1
        r.stdout = ""
        with patch("specterqa.ios.mcp.server.subprocess.run", return_value=r), \
             patch("specterqa.ios.mcp.server.time.sleep") as mock_sleep:
            waited = _srv._sim_settle_wait(TEST_UDID, settle_timeout_s=10.0)

        assert waited == 0.0
        mock_sleep.assert_not_called()

    def test_sim_settle_waits_exact_delta_for_freshly_booted(self):
        """Sim booted 0.5s ago with 10s window → waits ~9.5s."""
        with patch("specterqa.ios.mcp.server.subprocess.run", return_value=_make_simctl_response_with_boot_time(TEST_UDID, "Booted", boot_age_s=0.5)), \
             patch("specterqa.ios.mcp.server.time.sleep") as mock_sleep:
            waited = _srv._sim_settle_wait(TEST_UDID, settle_timeout_s=10.0)

        assert waited > 8.0, f"Expected wait > 8s for brand-new sim, got {waited}"
        mock_sleep.assert_called_once()
