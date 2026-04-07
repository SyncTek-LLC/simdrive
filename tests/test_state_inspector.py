"""Tests for M7: StateInspector — specterqa/ios/drivers/simulator/state.py

TDD Phase — INIT-2026-492.
These tests are written BEFORE the implementation exists and must be importable
even when the implementation module is absent.

Module under test (to be created by CodeAtlas):
  specterqa/ios/drivers/simulator/state.py  — StateInspector
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard — tests are importable even if impl is missing.
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.drivers.simulator.state import StateInspector  # type: ignore[import]

    _STATE_AVAILABLE = True
except ImportError:
    _STATE_AVAILABLE = False
    StateInspector = None  # type: ignore[assignment,misc]

needs_state = pytest.mark.skipif(
    not _STATE_AVAILABLE,
    reason="specterqa.ios.drivers.simulator.state not yet implemented",
)


# ---------------------------------------------------------------------------
# Mock builder helpers
# ---------------------------------------------------------------------------


def _make_subprocess_result(stdout: str = "", returncode: int = 0) -> MagicMock:
    """Build a mock subprocess.CompletedProcess result."""
    result = MagicMock()
    result.stdout = stdout
    result.returncode = returncode
    result.stderr = ""
    return result


# Fixture data — simctl outputs
CONTAINER_PATH_OUTPUT = (
    "/Users/runner/Library/Developer/CoreSimulator/Devices/ABC123/data/Containers/Data/Application/DEF456\n"
)

# plist-style output from `defaults read`
DEFAULTS_READ_OUTPUT = """\
{
    HasCompletedOnboarding = 1;
    LastLoggedInUser = "test@example.com";
    theme = "dark";
}
"""

# Realistic keychain output — implementation must always redact values
KEYCHAIN_OUTPUT = """\
keychain: "/Users/runner/Library/Keychains/login.keychain-db"
version: 512
class: "genp"
attributes:
    "acct"<blob>="com.example.testapp.auth_token"
    "svce"<blob>="com.example.testapp"
    "v_Data"<blob>=<some_auth_token_value>
keychain: "/Users/runner/Library/Keychains/login.keychain-db"
version: 512
class: "genp"
attributes:
    "acct"<blob>="com.example.testapp.refresh_token"
    "svce"<blob>="com.example.testapp"
    "v_Data"<blob>=<some_refresh_token_value>
"""

KEYCHAIN_OUTPUT_EMPTY = ""


# ===========================================================================
#  M7: StateInspector — 10 tests
# ===========================================================================


@needs_state
class TestGetContainerPath:
    """_get_container_path() — retrieve app's data container path via simctl."""

    def test_returns_container_path_from_simctl_output(self):
        """_get_container_path returns the stripped path string from simctl output."""
        inspector = StateInspector(device_id="booted", bundle_id="com.example.testapp")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_result(CONTAINER_PATH_OUTPUT)
            path = inspector._get_container_path()

        assert "/Containers/Data/Application/" in path
        assert path == path.strip()


@needs_state
class TestReadDefaults:
    """read_defaults() — read NSUserDefaults via simctl."""

    def test_parses_plist_style_defaults_output(self):
        """read_defaults parses the plist-style output from `defaults read` into a dict."""
        inspector = StateInspector(device_id="booted", bundle_id="com.example.testapp")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_result(DEFAULTS_READ_OUTPUT)
            defaults = inspector.read_defaults()

        assert isinstance(defaults, dict)
        assert "HasCompletedOnboarding" in defaults or len(defaults) > 0


@needs_state
class TestReadDefault:
    """read_default() — read a single key from NSUserDefaults."""

    def test_returns_single_value_for_key(self):
        """read_default returns the value string for a single key via defaults read."""
        inspector = StateInspector(device_id="booted", bundle_id="com.example.testapp")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_result("1\n")
            value = inspector.read_default("HasCompletedOnboarding")

        assert value is not None
        # The raw value "1" (or integer 1) for HasCompletedOnboarding
        assert str(value).strip() in ("1", "True", "true", 1) or value


@needs_state
class TestWriteDefault:
    """write_default() — write a value to NSUserDefaults via simctl."""

    def test_calls_simctl_with_correct_args(self):
        """write_default invokes subprocess with the correct simctl write command."""
        inspector = StateInspector(device_id="booted", bundle_id="com.example.testapp")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_result("")
            inspector.write_default("theme", "light")

        assert mock_run.called
        call_args = mock_run.call_args
        # The command must reference the key and value
        cmd_str = str(call_args)
        assert "theme" in cmd_str
        assert "light" in cmd_str


@needs_state
class TestKeychainItems:
    """keychain_items() — list keychain entries with all values REDACTED."""

    def test_returns_list_with_redacted_values(self):
        """keychain_items returns a list of dicts; all 'value' fields are '[REDACTED]'."""
        inspector = StateInspector(device_id="booted", bundle_id="com.example.testapp")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_result(KEYCHAIN_OUTPUT)
            items = inspector.keychain_items()

        assert isinstance(items, list)
        for item in items:
            assert isinstance(item, dict)
            # Every value/data field must be redacted — actual token values must not appear
            item_str = str(item)
            assert "some_auth_token_value" not in item_str
            assert "some_refresh_token_value" not in item_str
            # Either a 'value' key is present with [REDACTED], or the item carries
            # a 'redacted' flag — the exact schema is left to CodeAtlas but values
            # must never be exposed.
            if "value" in item:
                assert item["value"] == "[REDACTED]"


@needs_state
class TestHasAuthToken:
    """has_auth_token() — detect presence of auth-related keychain items."""

    def test_returns_true_when_auth_token_exists(self):
        """has_auth_token returns True when keychain contains an auth_token entry."""
        inspector = StateInspector(device_id="booted", bundle_id="com.example.testapp")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_result(KEYCHAIN_OUTPUT)
            result = inspector.has_auth_token()

        assert result is True

    def test_returns_false_when_no_auth_items(self):
        """has_auth_token returns False when keychain has no auth-related entries."""
        inspector = StateInspector(device_id="booted", bundle_id="com.example.testapp")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_result(KEYCHAIN_OUTPUT_EMPTY)
            result = inspector.has_auth_token()

        assert result is False


@needs_state
class TestListDocuments:
    """list_documents() — list files in the app's Documents directory."""

    def test_returns_file_list(self, tmp_path: Path):
        """list_documents returns a list of filename strings from the Documents dir."""
        inspector = StateInspector(device_id="booted", bundle_id="com.example.testapp")

        # Simulate the container directory structure
        container = tmp_path / "AppContainer"
        documents = container / "Documents"
        documents.mkdir(parents=True)
        (documents / "cache.db").touch()
        (documents / "user_prefs.plist").touch()

        with patch.object(inspector, "_get_container_path", return_value=str(container)):
            file_list = inspector.list_documents()

        assert isinstance(file_list, list)
        assert len(file_list) == 2
        assert any("cache.db" in f for f in file_list)
        assert any("user_prefs.plist" in f for f in file_list)


@needs_state
class TestFileExists:
    """file_exists() — check if a relative path exists in the app container."""

    def test_returns_true_for_existing_file(self, tmp_path: Path):
        """file_exists returns True when the file exists in the container."""
        inspector = StateInspector(device_id="booted", bundle_id="com.example.testapp")

        container = tmp_path / "AppContainer"
        documents = container / "Documents"
        documents.mkdir(parents=True)
        (documents / "session.json").touch()

        with patch.object(inspector, "_get_container_path", return_value=str(container)):
            assert inspector.file_exists("Documents/session.json") is True

    def test_returns_false_for_missing_file(self, tmp_path: Path):
        """file_exists returns False when the file does not exist in the container."""
        inspector = StateInspector(device_id="booted", bundle_id="com.example.testapp")

        container = tmp_path / "AppContainer"
        container.mkdir(parents=True)

        with patch.object(inspector, "_get_container_path", return_value=str(container)):
            assert inspector.file_exists("Documents/nonexistent.json") is False


@needs_state
class TestSnapshot:
    """snapshot() — aggregate all state into a single dict."""

    def test_snapshot_aggregates_all_state_into_dict(self, tmp_path: Path):
        """snapshot() returns a dict with user_defaults, has_auth_token, document_count, container_path."""
        inspector = StateInspector(device_id="booted", bundle_id="com.example.testapp")

        container = tmp_path / "AppContainer"
        documents = container / "Documents"
        documents.mkdir(parents=True)
        (documents / "file1.txt").touch()
        (documents / "file2.txt").touch()

        with (
            patch.object(inspector, "_get_container_path", return_value=str(container)),
            patch.object(inspector, "read_defaults", return_value={"theme": "dark"}),
            patch.object(inspector, "has_auth_token", return_value=True),
            patch.object(inspector, "list_documents", return_value=["file1.txt", "file2.txt"]),
        ):
            snap = inspector.snapshot()

        assert isinstance(snap, dict)
        assert "user_defaults" in snap
        assert "has_auth_token" in snap
        assert "document_count" in snap
        assert "container_path" in snap
        assert snap["has_auth_token"] is True
        assert snap["document_count"] == 2
        assert snap["user_defaults"] == {"theme": "dark"}
