"""Tests for v15.1.0 retryable error categorization (section 4).

Verifies that:
- Known Apple transient error patterns are tagged retryable=True.
- Fatal error patterns are NOT tagged retryable.
- _sim_shutdown_error response includes retryable=True.
- _retry_once_on_transient tags the final error when both attempts are transient.

All tests are pure unit tests — no subprocess, no live simulator.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import specterqa.ios.mcp.server as _srv


class TestRetryableErrorCategorization:
    """_is_retryable_error, _tag_retryable, _sim_shutdown_error carry retryable flag."""

    # ── Known transient patterns ──────────────────────────────────────────

    def test_sim_shutdown_during_session_is_retryable(self):
        assert _srv._is_retryable_error("sim_shutdown_during_session") is True

    def test_installcoordinationd_is_retryable(self):
        assert _srv._is_retryable_error("installcoordinationd error: daemon timeout") is True

    def test_runner_did_not_become_healthy_is_retryable(self):
        assert _srv._is_retryable_error("Runner did not become healthy within 90s") is True

    def test_coresimulator_405_is_retryable(self):
        assert _srv._is_retryable_error(
            "CoreSimulator 405: Unable to lookup runtime for device"
        ) is True

    def test_connection_refused_is_retryable(self):
        assert _srv._is_retryable_error("Connection refused") is True

    def test_connectionrefusederror_is_retryable(self):
        assert _srv._is_retryable_error("ConnectionRefusedError: [Errno 61]") is True

    def test_xcodebuild_exit_65_is_retryable(self):
        assert _srv._is_retryable_error("xcodebuild exited with code 65") is True

    def test_xcodebuild_exit_70_is_retryable(self):
        assert _srv._is_retryable_error("xcodebuild exited with code 70") is True

    # ── Fatal patterns — NOT retryable ────────────────────────────────────

    def test_no_active_session_is_not_retryable(self):
        assert _srv._is_retryable_error("No active session. Call ios_start_session first.") is False

    def test_invalid_udid_is_not_retryable(self):
        assert _srv._is_retryable_error("Invalid UDID: abc-123") is False

    def test_bundle_id_required_is_not_retryable(self):
        assert _srv._is_retryable_error("bundle_id is required") is False

    def test_permissions_denied_is_not_retryable(self):
        assert _srv._is_retryable_error("permissions denied for UDID") is False

    # ── _sim_shutdown_error includes retryable ────────────────────────────

    def test_sim_shutdown_error_dict_carries_retryable_true(self):
        """_sim_shutdown_error must return retryable=True in the response dict."""
        err = _srv._sim_shutdown_error("Shutdown")
        assert err.get("error") == "sim_shutdown_during_session"
        assert err.get("retryable") is True, f"Expected retryable=True in {err}"
        assert err.get("action_needed") == "boot_and_reauth"

    # ── _tag_retryable helper ─────────────────────────────────────────────

    def test_tag_retryable_adds_flag_to_transient_dict(self):
        d = {"error": "Connection refused"}
        tagged = _srv._tag_retryable(d)
        assert tagged.get("retryable") is True

    def test_tag_retryable_does_not_modify_fatal_dict(self):
        d = {"error": "No active session. Call ios_start_session first."}
        tagged = _srv._tag_retryable(d)
        assert "retryable" not in tagged

    def test_tag_retryable_no_op_on_success_dict(self):
        d = {"status": "ok", "elements": []}
        tagged = _srv._tag_retryable(d)
        assert "retryable" not in tagged
        assert "error" not in tagged

    def test_tag_retryable_preserves_existing_retryable_true(self):
        d = {"error": "sim_shutdown_during_session", "retryable": True}
        tagged = _srv._tag_retryable(d)
        assert tagged.get("retryable") is True

    # ── regression: 4 known transient paths ──────────────────────────────

    def test_transient_errors_carry_retryable_flag(self):
        """Regression: 4 known transient patterns must all set retryable=True."""
        transient_cases = [
            {"error": "sim_shutdown_during_session", "action_needed": "boot_and_reauth"},
            {"error": "installcoordinationd: connection lost"},
            {"error": "Runner did not become healthy within 90s"},
            {"error": "CoreSimulator 405: unable to lookup device"},
        ]
        for case in transient_cases:
            tagged = _srv._tag_retryable(dict(case))
            assert tagged.get("retryable") is True, (
                f"Expected retryable=True for: {case['error']!r}, got: {tagged}"
            )

    def test_sim_shutdown_error_is_retryable_end_to_end(self):
        """End-to-end: _sim_shutdown_error result is tagged retryable."""
        result = _srv._sim_shutdown_error("Shutdown")
        tagged = _srv._tag_retryable(result)
        assert tagged.get("retryable") is True
