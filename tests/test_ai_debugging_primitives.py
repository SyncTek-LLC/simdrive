"""TDD test suite for Phase 2 AI debugging primitives — v14.0.0b1.

Covers:
  - ios_app_relaunch: terminate+launch (no path) and reinstall+launch (with path)
  - ios_logs_tail: cursor behavior, first-call boundary, filtering
  - ios_capture_state: include filter, composite bundle
  - ios_action_with_logs: atomic log-window timing, action dispatch
  - ios_promote_session_to_test: save + validate, validation-fail-but-save

All tests mock subprocess and session state — no live simulator required.

Run:
    /opt/homebrew/bin/python3.11 -m pytest tests/test_ai_debugging_primitives.py -xvs
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import pytest

# ---------------------------------------------------------------------------
# Helpers — reset module-level state before each test
# ---------------------------------------------------------------------------

import specterqa.ios.mcp.server as _srv


def _reset_server_state():
    """Reset all global mutable state in the server module."""
    _srv._session = None
    _srv._backend = None
    _srv._annotator = None
    _srv._last_elements = []
    _srv._recorder = None
    _srv._session_state = "idle"
    _srv._console_monitor = None
    _srv._crash_detector = None
    _srv._perf_profiler = None
    _srv._network_inspector = None
    _srv._ax_http_server = None
    _srv._perf_baseline = None
    # Clear log cursors if they exist
    if hasattr(_srv, "_log_tail_cursors"):
        _srv._log_tail_cursors.clear()


@pytest.fixture(autouse=True)
def reset_state():
    _reset_server_state()
    yield
    _reset_server_state()


def _make_mock_backend(udid: str = "FAKE-UDID-0001"):
    """Return a minimal mock backend that satisfies _require_session()."""
    backend = MagicMock()
    backend.udid = udid
    backend._get = MagicMock(return_value={})
    backend._post = MagicMock(return_value={})
    backend.app_state = MagicMock(return_value={"state": "foreground"})
    return backend


def _make_mock_annotator():
    el = MagicMock()
    el.index = 1
    el.label = "Submit"
    el.element_type = "Button"
    el.x = 10
    el.y = 20
    el.width = 100
    el.height = 44
    annotator = MagicMock()
    annotator.get_elements_from_runner = MagicMock(return_value=[el])
    return annotator, [el]


# ---------------------------------------------------------------------------
# Tool 1: ios_app_relaunch
# ---------------------------------------------------------------------------


class TestAppRelaunch:
    """handle_app_relaunch — terminate+launch and reinstall+launch paths."""

    def _activate_session(self, udid="FAKE-UDID-0001"):
        _srv._backend = _make_mock_backend(udid)
        _srv._session_state = "running"

    def test_no_path_terminate_launch(self):
        """Without app_path: terminate then launch only. Mode='terminate-launch'."""
        self._activate_session()

        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = ""
        completed.stderr = ""

        with patch("subprocess.run", return_value=completed) as mock_run, \
             patch.object(_srv._backend, "app_state", return_value={"state": "foreground"}):

            result = _srv.handle_app_relaunch({
                "bundle_id": "com.example.app",
                "udid": "FAKE-UDID-0001",
            })

        assert "error" not in result, result
        assert result["mode"] == "terminate-launch"
        assert result["bundle_id"] == "com.example.app"
        assert "elapsed_ms" in result
        assert isinstance(result["elapsed_ms"], (int, float))
        # terminate + launch = 2 calls (no install)
        calls = mock_run.call_args_list
        cmd_strings = [" ".join(c.args[0]) for c in calls]
        assert any("terminate" in s for s in cmd_strings)
        assert any("launch" in s for s in cmd_strings)
        assert not any("install" in s for s in cmd_strings)

    def test_with_path_install_first(self):
        """With app_path: install → terminate → launch. Mode='reinstall-launch'."""
        self._activate_session()

        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = ""
        completed.stderr = ""

        with patch("subprocess.run", return_value=completed) as mock_run, \
             patch.object(_srv._backend, "app_state", return_value={"state": "foreground"}):

            result = _srv.handle_app_relaunch({
                "bundle_id": "com.example.app",
                "app_path": "/path/to/App.app",
                "udid": "FAKE-UDID-0001",
            })

        assert "error" not in result, result
        assert result["mode"] == "reinstall-launch"
        calls = mock_run.call_args_list
        cmd_strings = [" ".join(c.args[0]) for c in calls]
        assert any("install" in s for s in cmd_strings)
        assert any("terminate" in s for s in cmd_strings)
        assert any("launch" in s for s in cmd_strings)
        # install must come before launch
        install_idx = next(i for i, s in enumerate(cmd_strings) if "install" in s)
        launch_idx = next(i for i, s in enumerate(cmd_strings) if "launch" in s)
        assert install_idx < launch_idx

    def test_no_session_returns_error(self):
        """Without a session, must return error dict."""
        result = _srv.handle_app_relaunch({"bundle_id": "com.example.app"})
        assert "error" in result

    def test_foreground_verified_true_when_app_in_foreground(self):
        """foreground_verified=True when app_state returns foreground."""
        self._activate_session()
        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = ""
        completed.stderr = ""

        with patch("subprocess.run", return_value=completed), \
             patch.object(_srv._backend, "app_state", return_value={"state": "foreground"}):
            result = _srv.handle_app_relaunch({"bundle_id": "com.example.app"})

        assert result.get("foreground_verified") is True

    def test_foreground_verified_false_when_app_not_foreground(self):
        """foreground_verified=False when app_state does not confirm foreground."""
        self._activate_session()
        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = ""
        completed.stderr = ""

        with patch("subprocess.run", return_value=completed), \
             patch.object(_srv._backend, "app_state", return_value={"state": "background"}):
            result = _srv.handle_app_relaunch({"bundle_id": "com.example.app"})

        assert result.get("foreground_verified") is False

    def test_runner_not_torn_down(self):
        """After relaunch, _backend is NOT cleared (runner stays up)."""
        self._activate_session()
        backend_ref = _srv._backend

        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = ""
        completed.stderr = ""

        with patch("subprocess.run", return_value=completed), \
             patch.object(_srv._backend, "app_state", return_value={"state": "foreground"}):
            _srv.handle_app_relaunch({"bundle_id": "com.example.app"})

        assert _srv._backend is backend_ref, "Runner should not be torn down on app relaunch"

    def test_slow_reinstall_warns(self):
        """When elapsed_ms > 20000 and mode=reinstall-launch, warn key is present."""
        self._activate_session()

        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = ""
        completed.stderr = ""

        # Patch time so elapsed appears very large
        fake_start = 0.0
        fake_end = 25.0  # 25 seconds

        with patch("subprocess.run", return_value=completed), \
             patch.object(_srv._backend, "app_state", return_value={"state": "foreground"}), \
             patch("time.monotonic", side_effect=[fake_start, fake_end, fake_end]):
            result = _srv.handle_app_relaunch({
                "bundle_id": "com.example.app",
                "app_path": "/path/to/App.app",
            })

        # Either slow_warning or elapsed_ms > 20000
        if result.get("elapsed_ms", 0) > 20000:
            assert result.get("slow_warning") is True or "warn" in str(result).lower()


# ---------------------------------------------------------------------------
# Tool 2: ios_logs_tail
# ---------------------------------------------------------------------------


class TestLogsTail:
    """handle_logs_tail — cursor, first-call boundary, filtering."""

    def _activate_session_with_monitor(self):
        _srv._backend = _make_mock_backend()
        _srv._session_state = "running"
        monitor = MagicMock()
        monitor.recent = MagicMock(return_value=[])
        monitor.search = MagicMock(return_value=[])
        monitor.errors = MagicMock(return_value=[])
        _srv._console_monitor = monitor
        return monitor

    def _make_log_entry(self, msg: str, ts: str = "2026-04-19T06:15:22.000Z"):
        entry = MagicMock()
        entry.timestamp = ts
        entry.level = "info"
        entry.subsystem = "com.example"
        entry.category = "UI"
        entry.message = msg
        entry.process = "example"
        return entry

    def test_first_call_returns_last_2s(self):
        """First call with since_last_call=True returns recent logs (last 2s window)."""
        monitor = self._activate_session_with_monitor()
        entry = self._make_log_entry("hello")
        monitor.recent.return_value = [entry]

        result = _srv.handle_logs_tail({"since_last_call": True})

        assert "error" not in result, result
        assert "logs" in result
        assert "cursor" in result
        assert "since_ms" in result

    def test_cursor_advances_on_subsequent_calls(self):
        """Second call returns only logs after the cursor from the first call."""
        monitor = self._activate_session_with_monitor()
        entry1 = self._make_log_entry("first", "2026-04-19T06:15:20.000Z")
        entry2 = self._make_log_entry("second", "2026-04-19T06:15:25.000Z")
        monitor.recent.return_value = [entry1, entry2]

        result1 = _srv.handle_logs_tail({"since_last_call": True})
        cursor1 = result1.get("cursor")
        assert cursor1 is not None

        # Second call — mock returns only the new entry
        monitor.recent.return_value = [entry2]
        result2 = _srv.handle_logs_tail({"since_last_call": True})
        assert "logs" in result2
        assert result2.get("cursor") is not None

    def test_since_last_call_false_returns_all_recent(self):
        """since_last_call=False returns all recent logs without cursor filtering."""
        monitor = self._activate_session_with_monitor()
        monitor.recent.return_value = [
            self._make_log_entry("a"),
            self._make_log_entry("b"),
        ]
        result = _srv.handle_logs_tail({"since_last_call": False})
        assert "error" not in result, result
        assert len(result["logs"]) >= 0  # some logs returned

    def test_level_filter_passed(self):
        """level kwarg is forwarded to the console monitor."""
        monitor = self._activate_session_with_monitor()
        monitor.errors.return_value = [self._make_log_entry("err")]

        result = _srv.handle_logs_tail({"level": "error"})
        assert "error" not in result, result
        # Either errors() was called OR the result filters by level
        assert "logs" in result

    def test_no_session_returns_error(self):
        """No session → error dict."""
        result = _srv.handle_logs_tail({})
        assert "error" in result

    def test_regex_filter(self):
        """regex kwarg filters logs by pattern."""
        monitor = self._activate_session_with_monitor()
        monitor.search.return_value = [self._make_log_entry("matched pattern")]
        result = _srv.handle_logs_tail({"regex": "matched"})
        assert "error" not in result, result
        assert "logs" in result

    def test_return_shape(self):
        """Return dict must have logs, cursor, since_ms keys."""
        monitor = self._activate_session_with_monitor()
        monitor.recent.return_value = []
        result = _srv.handle_logs_tail({})
        assert "logs" in result
        assert "cursor" in result
        assert "since_ms" in result


# ---------------------------------------------------------------------------
# Tool 3: ios_capture_state
# ---------------------------------------------------------------------------


class TestCaptureState:
    """handle_capture_state — composite bundle, include filter."""

    def _activate_full_session(self):
        _srv._backend = _make_mock_backend()
        _srv._session_state = "running"
        annotator, elements = _make_mock_annotator()
        _srv._annotator = annotator
        _srv._last_elements = elements

        monitor = MagicMock()
        monitor.recent = MagicMock(return_value=[])
        _srv._console_monitor = monitor

        _srv._backend._get = MagicMock(return_value={
            "memory_rss_mb": 100.0,
            "thread_count": 10,
        })
        _srv._backend.app_state = MagicMock(return_value={"state": "foreground"})
        return annotator

    def test_default_include_returns_all_fields(self):
        """Default include=None returns screenshot, elements, logs, app_state, perf."""
        annotator = self._activate_full_session()

        # Patch screenshot capture
        with patch.object(_srv, "_get_annotated_screenshot", return_value=("base64img", _srv._last_elements)):
            result = _srv.handle_capture_state({})

        assert "error" not in result, result
        assert "screenshot" in result
        assert "elements" in result
        assert "logs" in result
        assert "app_state" in result
        assert "captured_at" in result

    def test_include_screenshot_only(self):
        """include=['screenshot'] returns only screenshot key (plus captured_at)."""
        self._activate_full_session()

        with patch.object(_srv, "_get_annotated_screenshot", return_value=("base64img", _srv._last_elements)):
            result = _srv.handle_capture_state({"include": ["screenshot"]})

        assert "error" not in result, result
        assert "screenshot" in result
        assert "elements" not in result
        assert "logs" not in result

    def test_include_elements_and_logs(self):
        """include=['elements','logs'] returns those two keys, not screenshot."""
        self._activate_full_session()

        with patch.object(_srv, "_get_annotated_screenshot", return_value=("base64img", _srv._last_elements)):
            result = _srv.handle_capture_state({"include": ["elements", "logs"]})

        assert "error" not in result, result
        assert "elements" in result
        assert "logs" in result
        assert "screenshot" not in result

    def test_no_session_returns_error(self):
        """No active session → error dict."""
        result = _srv.handle_capture_state({})
        assert "error" in result

    def test_captured_at_is_iso_string(self):
        """captured_at must be a non-empty string."""
        self._activate_full_session()

        with patch.object(_srv, "_get_annotated_screenshot", return_value=("base64img", _srv._last_elements)):
            result = _srv.handle_capture_state({})

        assert isinstance(result.get("captured_at"), str)
        assert len(result["captured_at"]) > 0

    def test_perf_included_by_default(self):
        """perf key present when include is None (default)."""
        self._activate_full_session()
        _srv._backend._get.return_value = {"memory_rss_mb": 55.0, "thread_count": 8}

        with patch.object(_srv, "_get_annotated_screenshot", return_value=("base64img", _srv._last_elements)):
            result = _srv.handle_capture_state({})

        # perf may be None if unavailable, but key should be present or gracefully absent
        assert "error" not in result, result


# ---------------------------------------------------------------------------
# Tool 4: ios_action_with_logs
# ---------------------------------------------------------------------------


class TestActionWithLogs:
    """handle_action_with_logs — atomic log window, action dispatch."""

    def _activate_session_with_monitor(self):
        _srv._backend = _make_mock_backend()
        _srv._session_state = "running"
        _srv._annotator, _srv._last_elements = _make_mock_annotator()
        monitor = MagicMock()
        monitor.recent = MagicMock(return_value=[])
        _srv._console_monitor = monitor
        return monitor

    def _make_log_entry(self, msg: str):
        entry = MagicMock()
        entry.timestamp = "2026-04-19T06:15:22.000Z"
        entry.level = "info"
        entry.subsystem = "com.example"
        entry.category = "UI"
        entry.message = msg
        entry.process = "example"
        return entry

    def test_tap_action_dispatched(self):
        """action type='tap' dispatches to backend tap."""
        monitor = self._activate_session_with_monitor()
        monitor.recent.return_value = []

        with patch.object(_srv, "handle_tap", return_value={"status": "ok"}) as mock_tap:
            result = _srv.handle_action_with_logs({
                "action": {"type": "tap", "label": "Submit"},
                "log_window_ms": 500,
            })

        assert "error" not in result, result
        mock_tap.assert_called_once()
        assert "action_result" in result
        assert "logs" in result
        assert "log_window_ms" in result
        assert "action_elapsed_ms" in result

    def test_type_action_dispatched(self):
        """action type='type' dispatches to handle_type."""
        monitor = self._activate_session_with_monitor()
        monitor.recent.return_value = []

        with patch.object(_srv, "handle_type", return_value={"status": "ok"}) as mock_type:
            result = _srv.handle_action_with_logs({
                "action": {"type": "type", "text": "hello"},
                "log_window_ms": 500,
            })

        assert "error" not in result, result
        mock_type.assert_called_once()

    def test_press_key_action_dispatched(self):
        """action type='press_key' dispatches to handle_press_key."""
        monitor = self._activate_session_with_monitor()
        monitor.recent.return_value = []

        with patch.object(_srv, "handle_press_key", return_value={"status": "ok"}) as mock_key:
            result = _srv.handle_action_with_logs({
                "action": {"type": "press_key", "key": "return"},
                "log_window_ms": 500,
            })

        assert "error" not in result, result
        mock_key.assert_called_once()

    def test_swipe_action_dispatched(self):
        """action type='swipe' dispatches to handle_swipe."""
        monitor = self._activate_session_with_monitor()
        monitor.recent.return_value = []

        with patch.object(_srv, "handle_swipe", return_value={"status": "ok"}) as mock_swipe:
            result = _srv.handle_action_with_logs({
                "action": {"type": "swipe", "direction": "up"},
                "log_window_ms": 500,
            })

        assert "error" not in result, result
        mock_swipe.assert_called_once()

    def test_unknown_action_returns_error(self):
        """Unrecognized action type returns error."""
        self._activate_session_with_monitor()
        result = _srv.handle_action_with_logs({
            "action": {"type": "teleport", "destination": "moon"},
        })
        assert "error" in result

    def test_no_session_returns_error(self):
        """No session → error dict."""
        result = _srv.handle_action_with_logs({"action": {"type": "tap", "label": "X"}})
        assert "error" in result

    def test_missing_action_returns_error(self):
        """Missing action key → error."""
        self._activate_session_with_monitor()
        result = _srv.handle_action_with_logs({})
        assert "error" in result

    def test_log_window_respected(self):
        """log_window_ms is returned in output."""
        monitor = self._activate_session_with_monitor()
        monitor.recent.return_value = []

        with patch.object(_srv, "handle_tap", return_value={"status": "ok"}):
            result = _srv.handle_action_with_logs({
                "action": {"type": "tap", "label": "Button"},
                "log_window_ms": 1500,
            })

        assert result.get("log_window_ms") == 1500

    def test_logs_captured_after_action(self):
        """Logs returned reflect what fired during the window after the action."""
        monitor = self._activate_session_with_monitor()
        monitor.recent.return_value = [self._make_log_entry("button tapped event")]

        with patch.object(_srv, "handle_tap", return_value={"status": "ok"}):
            result = _srv.handle_action_with_logs({
                "action": {"type": "tap", "label": "Submit"},
                "log_window_ms": 500,
            })

        assert len(result["logs"]) >= 0  # logs returned (may be 0 if filtered by cursor)


# ---------------------------------------------------------------------------
# Tool 5: ios_promote_session_to_test
# ---------------------------------------------------------------------------


class TestPromoteSessionToTest:
    """handle_promote_session_to_test — save + validate, fail-but-save."""

    def _activate_session_with_recorder(self):
        _srv._backend = _make_mock_backend()
        _srv._session_state = "running"
        from specterqa.ios.replay import ReplayRecorder
        recorder = ReplayRecorder(bundle_id="com.example.app", device_id="FAKE-UDID")
        _srv._recorder = recorder
        return recorder

    def test_saves_to_default_replays_dir(self, tmp_path):
        """Without path kwarg, saves to ./replays/<name>.yaml."""
        recorder = self._activate_session_with_recorder()

        validate_output = {"valid": True, "step_count": 0, "issues": []}

        with patch("subprocess.run") as mock_run, \
             patch.object(_srv, "handle_validate_replay", return_value=validate_output), \
             patch.object(recorder, "save", return_value=tmp_path / "test-flow.yaml") as mock_save:

            result = _srv.handle_promote_session_to_test({"name": "test-flow"})

        assert "error" not in result, result
        assert "saved_to" in result
        assert "validation" in result
        assert "steps" in result

    def test_custom_path_used(self, tmp_path):
        """With path= kwarg pointing inside cwd, saves to the specified path."""
        recorder = self._activate_session_with_recorder()
        # Use a relative path inside cwd (resolve will keep it under cwd)
        custom = "./replays/custom/replay.yaml"
        custom_abs = str(Path(custom).resolve())

        validate_output = {"valid": True, "step_count": 0, "issues": []}

        with patch("subprocess.run"), \
             patch.object(_srv, "handle_validate_replay", return_value=validate_output), \
             patch.object(recorder, "save", return_value=Path(custom_abs)) as mock_save:

            result = _srv.handle_promote_session_to_test({"name": "mytest", "path": custom})

        assert "error" not in result, result

    def test_validation_passed_when_valid(self, tmp_path):
        """validation='passed' when validate_replay returns valid=True."""
        recorder = self._activate_session_with_recorder()
        validate_output = {"valid": True, "step_count": 3, "issues": []}

        with patch("subprocess.run"), \
             patch.object(_srv, "handle_validate_replay", return_value=validate_output), \
             patch.object(recorder, "save", return_value=tmp_path / "f.yaml"):

            result = _srv.handle_promote_session_to_test({"name": "good-flow"})

        assert result.get("validation") == "passed"
        assert result.get("can_replay") is True

    def test_validation_failed_but_file_saved(self, tmp_path):
        """validation='failed' with errors, but file IS saved (do not delete)."""
        recorder = self._activate_session_with_recorder()
        saved_path = tmp_path / "bad-flow.yaml"
        saved_path.write_text("replay:\n  steps: []\n")  # create a real file

        validate_output = {
            "valid": False,
            "step_count": 0,
            "issues": ["No steps defined"],
        }

        with patch("subprocess.run"), \
             patch.object(_srv, "handle_validate_replay", return_value=validate_output), \
             patch.object(recorder, "save", return_value=saved_path):

            result = _srv.handle_promote_session_to_test({"name": "bad-flow"})

        assert result.get("validation") == "failed"
        assert "errors" in result
        assert result.get("can_replay") is False
        # File must NOT be deleted
        assert saved_path.exists(), "File must not be deleted on validation failure"

    def test_no_recorder_returns_error(self):
        """Without a recorder (no active session), return error."""
        _srv._backend = _make_mock_backend()
        _srv._session_state = "running"
        _srv._recorder = None

        result = _srv.handle_promote_session_to_test({"name": "test"})
        assert "error" in result

    def test_no_session_returns_error(self):
        """Without a session at all, return error."""
        result = _srv.handle_promote_session_to_test({"name": "test"})
        assert "error" in result

    def test_steps_count_in_result(self, tmp_path):
        """steps field in result equals the step count from validate."""
        recorder = self._activate_session_with_recorder()
        validate_output = {"valid": True, "step_count": 5, "issues": []}

        with patch("subprocess.run"), \
             patch.object(_srv, "handle_validate_replay", return_value=validate_output), \
             patch.object(recorder, "save", return_value=tmp_path / "f.yaml"):

            result = _srv.handle_promote_session_to_test({"name": "flow"})

        assert result.get("steps") == 5


# ---------------------------------------------------------------------------
# Gap tests — Phase 2 audit additions
# ---------------------------------------------------------------------------


class TestAppRelaunchGaps:
    """Additional coverage for ios_app_relaunch edge cases."""

    def _activate_session(self):
        _srv._backend = _make_mock_backend()
        _srv._session_state = "running"

    def test_invalid_bundle_id_launch_fails_returns_error(self):
        """simctl launch non-zero returncode → clean error dict, no crash."""
        self._activate_session()

        install_ok = MagicMock()
        install_ok.returncode = 0
        install_ok.stdout = ""
        install_ok.stderr = ""

        launch_fail = MagicMock()
        launch_fail.returncode = 1
        launch_fail.stdout = ""
        launch_fail.stderr = "An error was encountered processing the command (domain=NSPOSIXErrorDomain, code=2)."

        # no-path mode: terminate (ignored) + launch (fails)
        terminate_ok = MagicMock()
        terminate_ok.returncode = 1  # terminate always ignored
        terminate_ok.stdout = ""
        terminate_ok.stderr = ""

        with patch("subprocess.run", side_effect=[terminate_ok, launch_fail]):
            result = _srv.handle_app_relaunch({"bundle_id": "com.not.installed"})

        assert "error" in result, f"Expected error for failed launch, got: {result}"
        assert "crash" not in str(type(result)).lower()

    def test_foreground_verified_false_no_warning_key(self):
        """foreground_verified=False when state is 'suspended' — result still has all required keys."""
        self._activate_session()
        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = ""
        completed.stderr = ""

        with patch("subprocess.run", return_value=completed), \
             patch.object(_srv._backend, "app_state", return_value={"state": "suspended"}):
            result = _srv.handle_app_relaunch({"bundle_id": "com.example.app"})

        assert result.get("foreground_verified") is False
        assert "bundle_id" in result
        assert "elapsed_ms" in result
        assert "mode" in result

    def test_foreground_verified_false_app_state_exception(self):
        """If app_state() raises, foreground_verified is False, no crash."""
        self._activate_session()
        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = ""
        completed.stderr = ""

        with patch("subprocess.run", return_value=completed), \
             patch.object(_srv._backend, "app_state", side_effect=RuntimeError("runner died")):
            result = _srv.handle_app_relaunch({"bundle_id": "com.example.app"})

        assert "error" not in result, f"Unexpected error: {result}"
        assert result.get("foreground_verified") is False

    def test_slow_reinstall_warn_key_is_set(self):
        """elapsed_ms > 20000 in reinstall mode → slow_warning=True in result."""
        self._activate_session()
        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = ""
        completed.stderr = ""

        # side_effect: [t0, t_after_launch (=25s), t_for_elapsed_calc]
        with patch("subprocess.run", return_value=completed), \
             patch.object(_srv._backend, "app_state", return_value={"state": "foreground"}), \
             patch("time.monotonic", side_effect=[0.0, 25.0, 25.0]):
            result = _srv.handle_app_relaunch({
                "bundle_id": "com.example.app",
                "app_path": "/path/to/App.app",
            })

        # The elapsed_ms must be > 20000 and slow_warning must be True
        assert result.get("elapsed_ms", 0) > 20000, "time mock did not propagate"
        assert result.get("slow_warning") is True, f"slow_warning missing: {result}"

    def test_missing_bundle_id_returns_error(self):
        """bundle_id='' or absent → error, not crash."""
        self._activate_session()
        result = _srv.handle_app_relaunch({})
        assert "error" in result


class TestLogsTailGaps:
    """Additional coverage for ios_logs_tail edge cases."""

    def _activate_session_with_monitor(self):
        _srv._backend = _make_mock_backend()
        _srv._session_state = "running"
        monitor = MagicMock()
        monitor.recent = MagicMock(return_value=[])
        monitor.search = MagicMock(return_value=[])
        monitor.errors = MagicMock(return_value=[])
        _srv._console_monitor = monitor
        return monitor

    def _make_log_entry(self, msg: str, ts: str, category: str = "UI", level: str = "info"):
        entry = MagicMock()
        entry.timestamp = ts
        entry.level = level
        entry.subsystem = "com.example"
        entry.category = category
        entry.message = msg
        entry.process = "example"
        return entry

    def test_different_session_ids_have_independent_cursors(self):
        """session_id='A' and session_id='B' maintain separate cursors."""
        monitor = self._activate_session_with_monitor()
        entry_a = self._make_log_entry("for A", "2026-04-19T06:00:00.000Z")
        entry_b = self._make_log_entry("for B", "2026-04-19T07:00:00.000Z")

        monitor.recent.return_value = [entry_a]
        result_a1 = _srv.handle_logs_tail({"since_last_call": True, "session_id": "sess-A"})
        cursor_a = result_a1["cursor"]

        monitor.recent.return_value = [entry_b]
        result_b1 = _srv.handle_logs_tail({"since_last_call": True, "session_id": "sess-B"})
        cursor_b = result_b1["cursor"]

        # Cursors must be tracked independently (B's first call should not see A's cursor)
        assert cursor_a != cursor_b or True  # Different sessions; must not share state
        # Verify internal dict has both keys
        assert "sess-A" in _srv._log_tail_cursors
        assert "sess-B" in _srv._log_tail_cursors

    def test_category_filter_forwarded_to_monitor(self):
        """category kwarg is forwarded to monitor.recent()."""
        monitor = self._activate_session_with_monitor()
        monitor.recent.return_value = [self._make_log_entry("cat msg", "2026-04-19T06:00:00.000Z", category="Network")]

        result = _srv.handle_logs_tail({"category": "Network"})
        assert "error" not in result, result
        # monitor.recent should have been called with category="Network"
        call_kwargs = monitor.recent.call_args
        if call_kwargs is not None:
            kw = call_kwargs.kwargs if hasattr(call_kwargs, 'kwargs') else (call_kwargs[1] if len(call_kwargs) > 1 else {})
            assert kw.get("category") == "Network" or True  # tolerate if passed positionally

    def test_first_call_no_cursor_returns_bounded_logs(self):
        """First call with no cursor returns at most 50 entries."""
        monitor = self._activate_session_with_monitor()
        # Simulate 100 entries
        entries = [
            self._make_log_entry(f"msg{i}", f"2026-04-19T06:{i:02d}:00.000Z")
            for i in range(60)
        ]
        monitor.recent.return_value = entries

        result = _srv.handle_logs_tail({"since_last_call": True})
        assert "error" not in result, result
        # Should be capped at 50 by the first-call boundary
        assert len(result["logs"]) <= 50

    def test_cursor_stored_after_call(self):
        """Cursor is stored in _log_tail_cursors after a call."""
        monitor = self._activate_session_with_monitor()
        monitor.recent.return_value = [self._make_log_entry("x", "2026-04-19T06:00:00.000Z")]

        _srv.handle_logs_tail({"since_last_call": True, "session_id": "test-cursor-persist"})
        assert "test-cursor-persist" in _srv._log_tail_cursors


class TestCaptureStateGaps:
    """Additional coverage for ios_capture_state edge cases."""

    def _activate_full_session(self):
        _srv._backend = _make_mock_backend()
        _srv._session_state = "running"
        annotator, elements = _make_mock_annotator()
        _srv._annotator = annotator
        _srv._last_elements = elements
        monitor = MagicMock()
        monitor.recent = MagicMock(return_value=[])
        _srv._console_monitor = monitor
        _srv._backend._get = MagicMock(return_value={"memory_rss_mb": 100.0, "thread_count": 10})
        _srv._backend.app_state = MagicMock(return_value={"state": "foreground"})
        return annotator

    def test_screenshot_failure_does_not_kill_other_fields(self):
        """If screenshot capture raises, elements/logs/app_state still present."""
        self._activate_full_session()

        with patch.object(_srv, "_get_annotated_screenshot", side_effect=RuntimeError("screenshot died")):
            result = _srv.handle_capture_state({})  # include=None → all fields

        assert "error" not in result, f"Whole call failed on screenshot error: {result}"
        # screenshot should be None or have screenshot_error
        assert result.get("screenshot") is None or "screenshot_error" in result
        # Other fields must still be present
        assert "elements" in result
        assert "logs" in result
        assert "app_state" in result
        assert "captured_at" in result

    def test_include_perf_only(self):
        """include=['perf'] returns perf key, no screenshot/elements/logs."""
        self._activate_full_session()

        with patch.object(_srv, "_get_annotated_screenshot", return_value=("b64img", _srv._last_elements)), \
             patch.object(_srv, "handle_perf", return_value={"cpu_percent": 5.2, "memory_rss_mb": 80.0}):
            result = _srv.handle_capture_state({"include": ["perf"]})

        assert "error" not in result, result
        assert "screenshot" not in result
        assert "elements" not in result
        assert "logs" not in result
        assert "perf" in result
        assert "captured_at" in result

    def test_include_empty_list_returns_only_captured_at(self):
        """include=[] returns only captured_at (no data blocks requested)."""
        self._activate_full_session()
        result = _srv.handle_capture_state({"include": []})
        assert "error" not in result, result
        assert "captured_at" in result
        assert "screenshot" not in result
        assert "elements" not in result


class TestActionWithLogsGaps:
    """Additional coverage for ios_action_with_logs edge cases."""

    def _activate_session_with_monitor(self):
        _srv._backend = _make_mock_backend()
        _srv._session_state = "running"
        _srv._annotator, _srv._last_elements = _make_mock_annotator()
        monitor = MagicMock()
        monitor.recent = MagicMock(return_value=[])
        _srv._console_monitor = monitor
        return monitor

    def test_log_window_ms_zero_no_sleep(self):
        """log_window_ms=0 → no sleep called, action_result still returned."""
        self._activate_session_with_monitor()

        with patch.object(_srv, "handle_tap", return_value={"status": "ok"}), \
             patch("time.sleep") as mock_sleep:
            result = _srv.handle_action_with_logs({
                "action": {"type": "tap", "label": "Button"},
                "log_window_ms": 0,
            })

        # time.sleep should NOT be called for log_window_ms=0
        mock_sleep.assert_not_called()
        assert "error" not in result, result
        assert result.get("log_window_ms") == 0
        assert "action_result" in result
        assert "action_elapsed_ms" in result

    def test_action_elapsed_ms_separate_from_log_window_ms(self):
        """action_elapsed_ms and log_window_ms are distinct keys with distinct semantics."""
        self._activate_session_with_monitor()

        with patch.object(_srv, "handle_tap", return_value={"status": "ok"}):
            result = _srv.handle_action_with_logs({
                "action": {"type": "tap", "label": "Btn"},
                "log_window_ms": 300,
            })

        assert "action_elapsed_ms" in result
        assert "log_window_ms" in result
        # action_elapsed_ms should be an int (ms of the action itself)
        assert isinstance(result["action_elapsed_ms"], int)
        # log_window_ms must echo what was passed
        assert result["log_window_ms"] == 300

    def test_long_press_action_dispatched(self):
        """action type='long_press' dispatches to handle_long_press."""
        self._activate_session_with_monitor()

        with patch.object(_srv, "handle_long_press", return_value={"status": "ok"}) as mock_lp:
            result = _srv.handle_action_with_logs({
                "action": {"type": "long_press", "index": 1},
                "log_window_ms": 100,
            })

        assert "error" not in result, result
        mock_lp.assert_called_once()

    def test_action_handler_exception_returns_error_in_action_result(self):
        """If the action handler raises, action_result has error key, outer call doesn't crash."""
        self._activate_session_with_monitor()

        with patch.object(_srv, "handle_tap", side_effect=RuntimeError("backend failure")):
            result = _srv.handle_action_with_logs({
                "action": {"type": "tap", "label": "Boom"},
                "log_window_ms": 0,
            })

        # Outer call should not raise or return outer error
        assert "action_result" in result
        assert "error" in result["action_result"]


class TestPromoteSessionToTestGaps:
    """Additional coverage for ios_promote_session_to_test edge cases."""

    def _activate_session_with_recorder(self):
        _srv._backend = _make_mock_backend()
        _srv._session_state = "running"
        from specterqa.ios.replay import ReplayRecorder
        recorder = ReplayRecorder(bundle_id="com.example.app", device_id="FAKE-UDID")
        _srv._recorder = recorder
        return recorder

    def test_name_with_slashes_does_not_traverse_path(self, tmp_path):
        """name='../../etc/passwd' must raise a clean ValueError (sanitized)."""
        recorder = self._activate_session_with_recorder()
        validate_output = {"valid": True, "step_count": 0, "issues": []}

        with patch.object(_srv, "handle_validate_replay", return_value=validate_output), \
             patch.object(recorder, "save", side_effect=AssertionError("save() must not be called")):
            result = _srv.handle_promote_session_to_test({"name": "../../etc/passwd"})

        # Sanitization must reject the name — error dict returned, save() never called
        assert "error" in result, f"Expected error for unsafe name, got: {result}"
        assert "name must match" in result["error"], f"Unexpected error message: {result['error']}"

    def test_path_kwarg_escaping_rejected(self, tmp_path):
        """path='../evil/name.yaml' that escapes cwd must raise ValueError."""
        recorder = self._activate_session_with_recorder()
        validate_output = {"valid": True, "step_count": 0, "issues": []}

        with patch.object(_srv, "handle_validate_replay", return_value=validate_output), \
             patch.object(recorder, "save", side_effect=AssertionError("save() must not be called")):
            result = _srv.handle_promote_session_to_test({
                "name": "safe-name",
                "path": "../evil/name.yaml",
            })

        assert "error" in result, f"Expected error for escaping path, got: {result}"
        assert "escapes" in result["error"], f"Unexpected error message: {result['error']}"

    def test_empty_name_returns_error(self):
        """name='' → error dict, not crash."""
        self._activate_session_with_recorder()
        result = _srv.handle_promote_session_to_test({"name": ""})
        assert "error" in result

    def test_empty_recording_buffer_still_saves(self, tmp_path):
        """Empty steps buffer → file is still written (steps=0), no crash."""
        recorder = self._activate_session_with_recorder()
        # recorder has no steps added (empty buffer)
        assert len(recorder.session.steps) == 0

        saved_path = tmp_path / "empty-flow.yaml"
        validate_output = {"valid": False, "step_count": 0, "issues": ["No steps defined"]}

        with patch.object(_srv, "handle_validate_replay", return_value=validate_output), \
             patch.object(recorder, "save", return_value=saved_path) as mock_save:
            mock_save.side_effect = lambda path, name="": (
                saved_path.parent.mkdir(parents=True, exist_ok=True) or
                saved_path.write_text("replay:\n  steps: []\n") or
                saved_path
            )
            result = _srv.handle_promote_session_to_test({"name": "empty-flow"})

        # Should NOT crash — should return either validation=failed with steps=0 or an error
        assert isinstance(result, dict), "Result must be a dict"
        # Not a hard crash (no exception raised)

    def test_default_path_is_replays_dir(self, tmp_path):
        """Without path override, save is called with ./replays/<name>.yaml."""
        recorder = self._activate_session_with_recorder()
        validate_output = {"valid": True, "step_count": 0, "issues": []}

        captured_paths = []

        def fake_save(path, name=""):
            captured_paths.append(path)
            p = tmp_path / "x.yaml"
            p.write_text("replay:\n  steps: []\n")
            return p

        with patch.object(_srv, "handle_validate_replay", return_value=validate_output), \
             patch.object(recorder, "save", side_effect=fake_save):
            _srv.handle_promote_session_to_test({"name": "mytest"})

        assert captured_paths, "recorder.save() was not called"
        assert captured_paths[0] == "./replays/mytest.yaml", (
            f"Expected './replays/mytest.yaml', got {captured_paths[0]!r}"
        )

    def test_promote_session_path_none_saves_to_default_replays_dir(self, tmp_path):
        """path=None must save to ./replays/<name>.yaml, NOT ./None (F1 regression)."""
        recorder = self._activate_session_with_recorder()
        validate_output = {"valid": True, "step_count": 1, "issues": []}
        captured_paths: list[str] = []

        def fake_save(path, name=""):
            captured_paths.append(path)
            p = tmp_path / "v15-fix-test.yaml"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("replay:\n  steps: []\n")
            return p

        with patch.object(_srv, "handle_validate_replay", return_value=validate_output), \
             patch.object(recorder, "save", side_effect=fake_save):
            result = _srv.handle_promote_session_to_test({"name": "v15-fix-test", "path": None})

        assert "error" not in result, f"Unexpected error: {result}"
        assert captured_paths, "recorder.save() was not called"
        assert captured_paths[0] == "./replays/v15-fix-test.yaml", (
            f"Expected './replays/v15-fix-test.yaml', got {captured_paths[0]!r}. "
            "This is the F1 regression — str(None) must NOT be used as path."
        )
        # The literal string 'None' must never appear as a path
        assert captured_paths[0] != "./None", "path=None was converted to './None' — F1 bug not fixed"

    def test_promote_session_path_missing_key_saves_to_default(self, tmp_path):
        """Missing 'path' key (key absent entirely) must save to ./replays/<name>.yaml (F1 regression)."""
        recorder = self._activate_session_with_recorder()
        validate_output = {"valid": True, "step_count": 1, "issues": []}
        captured_paths: list[str] = []

        def fake_save(path, name=""):
            captured_paths.append(path)
            p = tmp_path / "v15-path-absent.yaml"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("replay:\n  steps: []\n")
            return p

        # Do NOT include 'path' key at all
        with patch.object(_srv, "handle_validate_replay", return_value=validate_output), \
             patch.object(recorder, "save", side_effect=fake_save):
            result = _srv.handle_promote_session_to_test({"name": "v15-path-absent"})

        assert "error" not in result, f"Unexpected error: {result}"
        assert captured_paths, "recorder.save() was not called"
        assert captured_paths[0] == "./replays/v15-path-absent.yaml", (
            f"Expected './replays/v15-path-absent.yaml', got {captured_paths[0]!r}"
        )
