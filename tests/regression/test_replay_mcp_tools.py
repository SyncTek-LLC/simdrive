"""Regression tests for ios_list_replays, ios_replay, ios_validate_replay MCP handlers.

Tests exercise the handler functions directly (not via pytest-mcp or mock).
Per feedback_no_mock_tests_specterqa: no MagicMock/patch — real behavior.

Run:
    pytest tests/regression/test_replay_mcp_tools.py -v --tb=short
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# Fixture replay directory (checked into repo)
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "replays"
SMOKE_REPLAY = FIXTURES_DIR / "smoke_login.yaml"
INVALID_REPLAY = FIXTURES_DIR / "invalid_steps.yaml"


# ---------------------------------------------------------------------------
# Import handlers (not mcp.tool wrappers — the raw handle_* functions)
# ---------------------------------------------------------------------------

from specterqa.ios.mcp.server import (
    handle_list_replays,
    handle_replay,
    handle_validate_replay,
)


# ===========================================================================
# handle_list_replays
# ===========================================================================


class TestHandleListReplays:
    """handle_list_replays returns a structured list of replay files."""

    def test_returns_list_from_fixture_dir(self):
        result = handle_list_replays({"replay_dir": str(FIXTURES_DIR)})
        assert isinstance(result, list)
        assert len(result) >= 2  # smoke_login + invalid_steps at minimum

    def test_each_entry_has_required_keys(self):
        result = handle_list_replays({"replay_dir": str(FIXTURES_DIR)})
        for entry in result:
            assert "name" in entry, f"Missing 'name' in {entry}"
            assert "path" in entry, f"Missing 'path' in {entry}"
            assert "steps" in entry, f"Missing 'steps' in {entry}"
            assert "modified" in entry, f"Missing 'modified' in {entry}"

    def test_step_count_correct_for_smoke_replay(self):
        result = handle_list_replays({"replay_dir": str(FIXTURES_DIR)})
        smoke = next((r for r in result if r["name"] == "smoke_login"), None)
        assert smoke is not None, "smoke_login replay not found in list"
        assert smoke["steps"] == 3, f"Expected 3 steps, got {smoke['steps']}"

    def test_empty_list_for_nonexistent_dir(self):
        result = handle_list_replays({"replay_dir": "/nonexistent/path/xyz"})
        assert result == []

    def test_sorted_newest_first(self):
        """Entries should be sorted by mtime descending (newest first)."""
        result = handle_list_replays({"replay_dir": str(FIXTURES_DIR)})
        if len(result) >= 2:
            mtimes = [r["modified"] for r in result]
            assert mtimes == sorted(mtimes, reverse=True), "Expected newest-first ordering"

    def test_path_is_absolute(self):
        result = handle_list_replays({"replay_dir": str(FIXTURES_DIR)})
        for entry in result:
            p = Path(entry["path"])
            assert p.is_absolute(), f"Expected absolute path, got {entry['path']}"

    def test_default_dir_returns_list(self, tmp_path):
        """Default .specterqa/replays dir returns empty list if missing — doesn't crash."""
        # When called with non-existent default dir
        result = handle_list_replays({"replay_dir": str(tmp_path / ".specterqa" / "replays")})
        assert isinstance(result, list)


# ===========================================================================
# handle_validate_replay
# ===========================================================================


class TestHandleValidateReplay:
    """handle_validate_replay parses and validates without executing."""

    def test_valid_replay_returns_valid_true(self):
        result = handle_validate_replay({
            "name": str(SMOKE_REPLAY),
        })
        assert result["valid"] is True, f"Expected valid, got issues: {result.get('issues')}"
        assert result["step_count"] == 3
        assert result["name"] == "smoke_login"
        assert result["bundle_id"] == "com.example.testapp"
        assert result["issues"] == []

    def test_invalid_replay_reports_issues(self):
        result = handle_validate_replay({
            "name": str(INVALID_REPLAY),
        })
        assert isinstance(result["issues"], list)
        assert len(result["issues"]) > 0
        # Missing bundle_id is an issue
        any_bundle = any("bundle_id" in i.lower() for i in result["issues"])
        assert any_bundle, f"Expected bundle_id issue, got: {result['issues']}"

    def test_missing_name_returns_invalid(self):
        result = handle_validate_replay({})
        assert result["valid"] is False
        assert len(result["issues"]) > 0

    def test_nonexistent_replay_returns_invalid(self):
        result = handle_validate_replay({"name": "no_such_replay"})
        assert result["valid"] is False
        assert any("not found" in i.lower() for i in result["issues"])

    def test_lookup_by_name_in_dir(self):
        """Can find replay by name (without .yaml) in a directory."""
        result = handle_validate_replay({
            "name": "smoke_login",
            "replay_dir": str(FIXTURES_DIR),
        })
        assert result["valid"] is True
        assert result["name"] == "smoke_login"

    def test_lookup_by_name_with_extension(self):
        """Can find replay by name with .yaml extension."""
        result = handle_validate_replay({
            "name": "smoke_login.yaml",
            "replay_dir": str(FIXTURES_DIR),
        })
        assert result["valid"] is True

    def test_step_count_in_result(self):
        result = handle_validate_replay({"name": str(SMOKE_REPLAY)})
        assert isinstance(result["step_count"], int)
        assert result["step_count"] >= 0


# ===========================================================================
# handle_replay — no-session guard
# ===========================================================================


class TestHandleReplayNoSession:
    """handle_replay must fail gracefully when no session is active."""

    def test_requires_session(self):
        """Without an active session, replay returns an error dict (not an exception)."""
        # Ensure no session is active by checking the module global
        import specterqa.ios.mcp.server as srv
        original_backend = srv._backend
        try:
            srv._backend = None
            result = handle_replay({"name": "smoke_login"})
            assert "error" in result, f"Expected error, got: {result}"
            assert "ios_start_session" in result.get("error", "") or "ios_start_session" in result.get("hint", "")
        finally:
            srv._backend = original_backend

    def test_missing_name_returns_error(self):
        import specterqa.ios.mcp.server as srv
        original_backend = srv._backend
        try:
            srv._backend = None
            result = handle_replay({})
            assert "error" in result
        finally:
            srv._backend = original_backend

    def test_nonexistent_replay_returns_error(self):
        import specterqa.ios.mcp.server as srv
        original_backend = srv._backend
        try:
            srv._backend = None
            result = handle_replay({"name": "definitely_not_a_replay"})
            # No session → error about session, not about missing file
            assert "error" in result
        finally:
            srv._backend = original_backend
