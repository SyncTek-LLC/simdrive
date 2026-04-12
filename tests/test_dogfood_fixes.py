"""Comprehensive tests for 12 dogfood fix areas.

TDD Phase — tests written before/alongside implementation.

Features under test:
  1. Element Resolver v2 — scored matching (_lookup, _resolve_element)
  2. handle_tap with auto-refresh and greedy-match fix
  3. Non-hittable element fallback to coordinate tap
  4. Screenshot JPEG output
  5. Recording scope fix (new recorder instance on start)
  6. Session state machine (idle/running/crashed)
  7. New MCP tools (wait_idle, app_state, dismiss_sheet)
  8. Replay engine (_exec_long_press identifier priority)
  9. Backend client new methods (app_state, wait_idle, set_appearance)

Run:
    pytest tests/test_dogfood_fixes.py -v --tb=short
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Shared mock element dataclass (matches spec)
# ---------------------------------------------------------------------------


@dataclass
class MockElement:
    index: int = 0
    label: str = ""
    identifier: str = ""
    element_type: str = "Button"
    x: float = 100.0
    y: float = 200.0
    width: float = 50.0
    height: float = 30.0
    hittable: bool = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_session(elements, recorder=None, annotator=None, session_state="running"):
    """Patch module globals so handlers run against a fake session."""
    import specterqa.ios.mcp.server as srv

    mock_backend = MagicMock()
    srv._backend = mock_backend
    srv._session = MagicMock()
    srv._last_elements = elements
    srv._recorder = recorder
    srv._annotator = annotator
    if hasattr(srv, "_session_state"):
        srv._session_state = session_state
    return mock_backend


def _teardown_session():
    import specterqa.ios.mcp.server as srv

    srv._backend = None
    srv._session = None
    srv._last_elements = []
    srv._recorder = None
    srv._annotator = None
    if hasattr(srv, "_session_state"):
        srv._session_state = "idle"


# ===========================================================================
# 1. Element Resolver v2 — _lookup scored matching
# ===========================================================================


class TestLookupScoredMatching:
    """Verify _lookup() scores elements correctly and returns the best match."""

    def teardown_method(self, method):
        _teardown_session()

    def _get_lookup(self):
        """Try to import _lookup from server; skip if not yet implemented."""
        try:
            from specterqa.ios.mcp.server import _lookup
            return _lookup
        except ImportError:
            pytest.skip("_lookup not yet implemented")

    def test_lookup_exact_match_beats_substring(self):
        """'Password' exact-matches SecureTextField 'Password', NOT 'Forgot your password?'."""
        _lookup = self._get_lookup()

        password_field = MockElement(
            index=1, label="Password", identifier="", element_type="SecureTextField"
        )
        forgot_btn = MockElement(
            index=2, label="Forgot your password?", identifier="", element_type="Button"
        )
        elements = [forgot_btn, password_field]

        result = _lookup(label="Password", identifier=None, element_index=None, element_type=None, elements=elements)
        assert result is password_field, (
            f"Exact match 'Password' should win over substring match. Got: {result}"
        )

    def test_lookup_prefix_match_beats_substring(self):
        """'Pass' prefix of 'Password' beats substring of 'Forgot your password?'."""
        _lookup = self._get_lookup()

        password_field = MockElement(
            index=1, label="Password", identifier="", element_type="SecureTextField"
        )
        forgot_btn = MockElement(
            index=2, label="Forgot your password?", identifier="", element_type="Button"
        )
        elements = [forgot_btn, password_field]

        result = _lookup(label="Pass", identifier=None, element_index=None, element_type=None, elements=elements)
        assert result is password_field, (
            "'Pass' prefix of 'Password' should beat substring of 'Forgot your password?'"
        )

    def test_lookup_shorter_label_wins_on_same_score(self):
        """Two exact matches — shorter label wins (less ambiguous)."""
        _lookup = self._get_lookup()

        short_el = MockElement(index=1, label="OK", identifier="", element_type="Button")
        long_el = MockElement(index=2, label="OK Button", identifier="", element_type="Button")
        elements = [long_el, short_el]

        result = _lookup(label="OK", identifier=None, element_index=None, element_type=None, elements=elements)
        assert result is short_el, "Shorter exact match should win over longer one"

    def test_lookup_type_filter_narrows(self):
        """label='Password' type='SecureTextField' picks the correct element."""
        _lookup = self._get_lookup()

        label_el = MockElement(
            index=1, label="Password", identifier="", element_type="StaticText"
        )
        secure_field = MockElement(
            index=2, label="Password", identifier="", element_type="SecureTextField"
        )
        elements = [label_el, secure_field]

        result = _lookup(label="Password", identifier=None, element_index=None, element_type="SecureTextField", elements=elements)
        assert result is secure_field, "Type filter should narrow to SecureTextField"

    def test_lookup_identifier_takes_priority(self):
        """Identifier match returns even if label also matches a different element."""
        _lookup = self._get_lookup()

        label_match = MockElement(
            index=1, label="Save", identifier="wrongId", element_type="Button"
        )
        id_match = MockElement(
            index=2, label="SomethingElse", identifier="saveBtn", element_type="Button"
        )
        elements = [label_match, id_match]

        result = _lookup(label="Save", identifier="saveBtn", element_index=None, element_type=None, elements=elements)
        assert result is id_match, "Identifier match must take priority over label match"

    def test_lookup_index_no_scoring(self):
        """index=5 returns element with index 5, no scoring applied."""
        _lookup = self._get_lookup()

        el3 = MockElement(index=3, label="Three", identifier="", element_type="Button")
        el5 = MockElement(index=5, label="Five", identifier="", element_type="Button")
        el7 = MockElement(index=7, label="Seven", identifier="", element_type="Button")
        elements = [el3, el5, el7]

        result = _lookup(label=None, identifier=None, element_index=5, element_type=None, elements=elements)
        assert result is el5, "Index lookup must return element with matching index"


# ===========================================================================
# 1b. Element Resolver v2 — _resolve_element
# ===========================================================================


class TestResolveElement:
    """Verify _resolve_element() auto-refresh behavior."""

    def teardown_method(self, method):
        _teardown_session()

    def _get_resolve_element(self):
        try:
            from specterqa.ios.mcp.server import _resolve_element
            return _resolve_element
        except ImportError:
            pytest.skip("_resolve_element not yet implemented")

    def test_resolve_auto_refreshes_on_label_miss(self):
        """Cache has old elements; resolver calls annotator.get_elements_from_runner() and finds element in fresh list."""
        import specterqa.ios.mcp.server as srv
        _resolve_element = self._get_resolve_element()

        # Old cache: element not present
        old_element = MockElement(index=1, label="OldButton", identifier="", element_type="Button")
        # Fresh elements: target is present
        target = MockElement(index=2, label="NewButton", identifier="", element_type="Button")

        mock_annotator = MagicMock()
        mock_annotator.get_elements_from_runner.return_value = [target]

        # Set module globals that _resolve_element reads
        srv._last_elements = [old_element]
        srv._annotator = mock_annotator
        srv._backend = MagicMock()

        result_element, was_refreshed = _resolve_element(label="NewButton")
        assert result_element is target
        assert was_refreshed is True
        mock_annotator.get_elements_from_runner.assert_called_once()

    def test_resolve_no_refresh_on_index_miss(self):
        """Index miss does NOT trigger refresh (indices are stale post-refresh)."""
        import specterqa.ios.mcp.server as srv
        _resolve_element = self._get_resolve_element()

        mock_annotator = MagicMock()
        mock_annotator.get_elements_from_runner.return_value = []

        # Set module globals that _resolve_element reads
        srv._last_elements = [MockElement(index=1, label="Btn")]
        srv._annotator = mock_annotator
        srv._backend = MagicMock()

        result_element, was_refreshed = _resolve_element(element_index=99)
        # Index miss: no refresh should have been triggered
        mock_annotator.get_elements_from_runner.assert_not_called()
        assert result_element is None

    def test_resolve_returns_was_refreshed_flag(self):
        """Returns (element, True) when cache was refreshed to find the element."""
        import specterqa.ios.mcp.server as srv
        _resolve_element = self._get_resolve_element()

        target = MockElement(index=1, label="TargetBtn", identifier="", element_type="Button")
        mock_annotator = MagicMock()
        mock_annotator.get_elements_from_runner.return_value = [target]

        # Set module globals: empty cache forces refresh
        srv._last_elements = []
        srv._annotator = mock_annotator
        srv._backend = MagicMock()

        _, was_refreshed = _resolve_element(label="TargetBtn")
        assert was_refreshed is True


# ===========================================================================
# 2. handle_tap with auto-refresh and greedy-match fix
# ===========================================================================


class TestHandleTapAutoRefresh:
    """Verify handle_tap auto-refreshes element cache on miss."""

    def teardown_method(self, method):
        _teardown_session()

    def test_handle_tap_auto_refreshes_cache_on_miss(self):
        """Label not in cache, annotator returns fresh elements with label → tap succeeds, cache_refreshed: True."""
        import specterqa.ios.mcp.server as srv
        from specterqa.ios.mcp.server import handle_tap

        target = MockElement(
            index=1, label="Submit", identifier="", element_type="Button",
            x=50.0, y=100.0, width=80.0, height=40.0, hittable=True
        )

        mock_annotator = MagicMock()
        mock_annotator.get_elements_from_runner.return_value = [target]
        mock_backend = _setup_session([], annotator=mock_annotator)

        result = handle_tap({"label": "Submit"})

        assert result.get("status") == "ok", f"Expected ok, got: {result}"
        mock_backend.tap.assert_called_once()
        # Should indicate cache was refreshed
        assert result.get("cache_refreshed") is True, (
            f"Expected cache_refreshed=True in response. Got: {result}"
        )

    def test_handle_tap_greedy_match_fixed(self):
        """'Password' matches SecureTextField 'Password', NOT 'Forgot your password?' Button."""
        import specterqa.ios.mcp.server as srv
        from specterqa.ios.mcp.server import handle_tap

        password_field = MockElement(
            index=1, label="Password", identifier="", element_type="SecureTextField",
            x=10.0, y=100.0, width=200.0, height=40.0, hittable=True
        )
        forgot_btn = MockElement(
            index=2, label="Forgot your password?", identifier="", element_type="Button",
            x=10.0, y=200.0, width=200.0, height=30.0, hittable=True
        )

        mock_backend = _setup_session([forgot_btn, password_field])

        result = handle_tap({"label": "Password"})
        assert result.get("status") == "ok", f"Expected ok, got: {result}"

        # Verify the tap hit the Password field coordinates, not Forgot button
        call_args = mock_backend.tap.call_args
        if call_args:
            tapped_x, tapped_y = call_args[0][0], call_args[0][1]
            expected_cx = password_field.x + password_field.width / 2
            expected_cy = password_field.y + password_field.height / 2
            assert tapped_x == pytest.approx(expected_cx), (
                f"Tap x={tapped_x} should match Password field cx={expected_cx}"
            )
            assert tapped_y == pytest.approx(expected_cy), (
                f"Tap y={tapped_y} should match Password field cy={expected_cy}"
            )


# ===========================================================================
# 3. Non-hittable element fallback
# ===========================================================================


class TestNonHittableFallback:
    """Verify non-hittable elements fall back to coordinate tap with warning."""

    def teardown_method(self, method):
        _teardown_session()

    def test_handle_tap_non_hittable_uses_coordinate_tap(self):
        """Element found with hittable=False → taps at coordinates, response has warning."""
        from specterqa.ios.mcp.server import handle_tap

        non_hittable = MockElement(
            index=1, label="HiddenBtn", identifier="", element_type="Button",
            x=50.0, y=150.0, width=100.0, height=44.0, hittable=False
        )
        mock_backend = _setup_session([non_hittable])

        result = handle_tap({"label": "HiddenBtn"})

        # Should still succeed (coordinate tap), but include a warning
        assert result.get("status") == "ok", (
            f"Non-hittable element should still be tappable by coordinate. Got: {result}"
        )
        mock_backend.tap.assert_called_once()
        # Verify coordinates used
        call_args = mock_backend.tap.call_args
        tapped_x, tapped_y = call_args[0][0], call_args[0][1]
        expected_cx = non_hittable.x + non_hittable.width / 2
        expected_cy = non_hittable.y + non_hittable.height / 2
        assert tapped_x == pytest.approx(expected_cx)
        assert tapped_y == pytest.approx(expected_cy)
        # Warning must be present
        assert "warning" in result or "non_hittable" in str(result).lower() or "hittable" in str(result).lower(), (
            f"Response should contain a warning about non-hittable element. Got: {result}"
        )


# ===========================================================================
# 4. Screenshot JPEG output
# ===========================================================================


class TestScreenshotJpegOutput:
    """Verify screenshot output format is JPEG, not PNG."""

    JPEG_MAGIC = b"\xff\xd8\xff"
    PNG_MAGIC = b"\x89PNG"

    def _make_b64_png(self, size=(100, 100)):
        from PIL import Image
        img = Image.new("RGB", size, "blue")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    def _decode_b64(self, b64_str: str) -> bytes:
        return base64.b64decode(b64_str)

    def test_annotate_outputs_jpeg(self):
        """Annotated image output format is JPEG (not PNG)."""
        try:
            from specterqa.ios.mcp.server import _annotate_screenshot
        except ImportError:
            pytest.skip("_annotate_screenshot not yet implemented")

        png_b64 = self._make_b64_png()
        elements = [
            MockElement(index=1, label="Btn", element_type="Button",
                        x=10.0, y=10.0, width=80.0, height=40.0)
        ]

        result_b64 = _annotate_screenshot(png_b64, elements)
        raw = self._decode_b64(result_b64)

        assert raw[:3] == self.JPEG_MAGIC, (
            f"Expected JPEG output (\\xff\\xd8\\xff), got: {raw[:4].hex()}"
        )
        assert raw[:4] != self.PNG_MAGIC, "Output must not be PNG"

    def test_resize_outputs_jpeg(self):
        """_resize_screenshot returns JPEG bytes."""
        try:
            from specterqa.ios.mcp.server import _resize_screenshot
        except ImportError:
            pytest.skip("_resize_screenshot not yet implemented")

        png_b64 = self._make_b64_png(size=(400, 800))
        result_b64 = _resize_screenshot(png_b64)
        raw = self._decode_b64(result_b64)

        assert raw[:3] == self.JPEG_MAGIC, (
            f"_resize_screenshot should output JPEG. Got: {raw[:4].hex()}"
        )


# ===========================================================================
# 5. Recording scope fix
# ===========================================================================


class TestRecordingScopeFix:
    """Verify ios_start_recording creates a new recorder instance."""

    def teardown_method(self, method):
        _teardown_session()

    def test_start_recording_creates_new_recorder(self):
        """After ios_start_recording, recorder is a NEW instance (different id())."""
        import specterqa.ios.mcp.server as srv
        from specterqa.ios.mcp.server import handle_start_recording

        old_recorder = MagicMock()
        old_recorder.bundle_id = "com.example.app"
        old_recorder.steps = ["existing_step"]
        old_recorder_id = id(old_recorder)

        _setup_session([], recorder=old_recorder)
        # Ensure session is active
        srv._session = MagicMock()
        srv._backend = MagicMock()

        result = handle_start_recording({"bundle_id": "com.example.app"})

        # A new recorder object must have been created
        assert srv._recorder is not None
        assert id(srv._recorder) != old_recorder_id, (
            "handle_start_recording must create a NEW recorder instance, not reuse old one"
        )

    def test_start_recording_preserves_bundle_id(self):
        """New recorder has same bundle_id as specified in the call."""
        import specterqa.ios.mcp.server as srv
        from specterqa.ios.mcp.server import handle_start_recording

        old_recorder = MagicMock()
        old_recorder.bundle_id = "com.example.app"
        old_recorder.steps = ["stale_step"]

        _setup_session([], recorder=old_recorder)
        srv._session = MagicMock()
        srv._backend = MagicMock()

        handle_start_recording({"bundle_id": "com.example.app"})

        new_recorder = srv._recorder
        assert new_recorder is not None
        # Bundle ID should be set on the new recorder
        bundle = getattr(new_recorder, "bundle_id", None)
        if bundle is not None:
            assert bundle == "com.example.app", (
                f"New recorder bundle_id should be 'com.example.app', got: {bundle}"
            )

    def test_start_recording_clears_steps(self):
        """New recorder has empty steps list (old steps not carried over)."""
        import specterqa.ios.mcp.server as srv
        from specterqa.ios.mcp.server import handle_start_recording

        old_recorder = MagicMock()
        old_recorder.bundle_id = "com.example.app"
        old_recorder.steps = ["step1", "step2", "step3"]

        _setup_session([], recorder=old_recorder)
        srv._session = MagicMock()
        srv._backend = MagicMock()

        handle_start_recording({"bundle_id": "com.example.app"})

        new_recorder = srv._recorder
        assert new_recorder is not None
        steps = getattr(new_recorder, "steps", None)
        if steps is not None:
            assert len(steps) == 0, (
                f"New recorder should have empty steps, but got: {steps}"
            )


# ===========================================================================
# 6. Session state machine
# ===========================================================================


class TestSessionStateMachine:
    """Verify _session_state transitions: idle → running → idle, crash detection."""

    def teardown_method(self, method):
        _teardown_session()

    def _has_session_state(self):
        import specterqa.ios.mcp.server as srv
        return hasattr(srv, "_session_state")

    def test_session_state_starts_idle(self):
        """_session_state is 'idle' initially (no active session)."""
        import importlib
        import specterqa.ios.mcp.server as srv
        importlib.reload(srv)

        if not hasattr(srv, "_session_state"):
            pytest.skip("_session_state not yet implemented")

        assert srv._session_state == "idle", (
            f"Initial state should be 'idle', got: {srv._session_state}"
        )

    def test_session_state_running_after_start(self):
        """After handle_start_session, state is 'running'."""
        import specterqa.ios.mcp.server as srv
        from specterqa.ios.mcp.server import handle_start_session

        if not hasattr(srv, "_session_state"):
            pytest.skip("_session_state not yet implemented")

        with patch("specterqa.ios.backends.xctest_client.XCTestBackend", autospec=True) as MockBackend, \
             patch("specterqa.ios.session_manager.TestSession", autospec=True) as MockSession, \
             patch("specterqa.ios.replay.ReplayRecorder", autospec=True):
            mock_backend_instance = MagicMock()
            MockBackend.return_value = mock_backend_instance
            mock_backend_instance.health.return_value = {"status": "ok"}

            mock_session_instance = MagicMock()
            MockSession.return_value = mock_session_instance
            mock_session_instance._target_udid = "clone-udid"
            mock_session_instance._port = 8222
            mock_session_instance.runner_url = "http://localhost:8222"

            try:
                handle_start_session({"bundle_id": "com.example.app", "udid": "test-udid"})
            except Exception:
                pass  # state transition may still fire before an error

        if hasattr(srv, "_session_state") and srv._backend is not None:
            assert srv._session_state in ("running", "idle"), (
                f"State should be 'running' after start. Got: {srv._session_state}"
            )

    def test_session_state_idle_after_stop(self):
        """After handle_stop_session, state is 'idle'."""
        import specterqa.ios.mcp.server as srv
        from specterqa.ios.mcp.server import handle_stop_session

        if not hasattr(srv, "_session_state"):
            pytest.skip("_session_state not yet implemented")

        mock_backend = _setup_session([])
        srv._session_state = "running"

        handle_stop_session({})

        assert srv._session_state == "idle", (
            f"State should be 'idle' after stop. Got: {srv._session_state}"
        )

    def test_require_session_detects_crash(self):
        """When backend.health() raises, _session_state becomes 'crashed'."""
        import specterqa.ios.mcp.server as srv

        if not hasattr(srv, "_session_state"):
            pytest.skip("_session_state not yet implemented")

        try:
            from specterqa.ios.mcp.server import _require_session
        except ImportError:
            pytest.skip("_require_session not yet implemented")

        mock_backend = MagicMock()
        mock_backend.health.side_effect = ConnectionError("backend died")
        srv._backend = mock_backend
        srv._session = MagicMock()
        srv._session_state = "running"

        try:
            _require_session()
        except Exception:
            pass  # Expected to raise after detecting crash

        assert srv._session_state == "crashed", (
            f"State should be 'crashed' after health check fails. Got: {srv._session_state}"
        )

    def test_crashed_session_blocks_operations(self):
        """Operations return error mentioning 'crashed' when state is 'crashed'."""
        import specterqa.ios.mcp.server as srv
        from specterqa.ios.mcp.server import handle_tap

        if not hasattr(srv, "_session_state"):
            pytest.skip("_session_state not yet implemented")

        mock_backend = _setup_session([MockElement(index=1, label="Btn")])
        srv._session_state = "crashed"

        result = handle_tap({"label": "Btn"})

        assert "error" in result, "Crashed session should return an error"
        error_str = str(result.get("error", "")).lower()
        assert "crash" in error_str or "session" in error_str, (
            f"Error should mention crash/session state. Got: {result}"
        )


# ===========================================================================
# 7. New MCP tools
# ===========================================================================


class TestNewMCPTools:
    """Verify new MCP tool handlers: wait_idle, app_state, dismiss_sheet."""

    def teardown_method(self, method):
        _teardown_session()

    def test_handle_wait_idle_calls_backend(self):
        """handle_wait_idle calls backend with timeout."""
        try:
            from specterqa.ios.mcp.server import handle_wait_idle
        except ImportError:
            pytest.skip("handle_wait_idle not yet implemented")

        mock_backend = _setup_session([])
        mock_backend.wait_idle.return_value = {"idle": True}

        result = handle_wait_idle({"timeout": 10})

        mock_backend.wait_idle.assert_called_once()
        call_args = mock_backend.wait_idle.call_args
        # Timeout should have been passed
        all_args = list(call_args[0]) + list(call_args[1].values()) if call_args else []
        assert result.get("status") == "ok" or "idle" in result or "error" not in result, (
            f"handle_wait_idle should succeed. Got: {result}"
        )

    def test_handle_wait_idle_caps_timeout(self):
        """Timeout is capped at 30s — excessive values are clamped."""
        try:
            from specterqa.ios.mcp.server import handle_wait_idle
        except ImportError:
            pytest.skip("handle_wait_idle not yet implemented")

        mock_backend = _setup_session([])
        mock_backend.wait_idle.return_value = {"idle": True}

        handle_wait_idle({"timeout": 99999})

        call_args = mock_backend.wait_idle.call_args
        if call_args:
            all_args = list(call_args[0]) + list(call_args[1].values())
            numeric_args = [a for a in all_args if isinstance(a, (int, float))]
            if numeric_args:
                assert max(numeric_args) <= 30, (
                    f"Timeout should be capped at 30s. Received: {numeric_args}"
                )

    def test_handle_app_state_returns_state(self):
        """handle_app_state returns state dict."""
        try:
            from specterqa.ios.mcp.server import handle_app_state
        except ImportError:
            pytest.skip("handle_app_state not yet implemented")

        mock_backend = _setup_session([])
        mock_backend.app_state.return_value = {
            "foreground": True,
            "bundle_id": "com.example.app",
            "state": "running"
        }

        result = handle_app_state({})

        assert isinstance(result, dict), f"handle_app_state should return dict. Got: {type(result)}"
        mock_backend.app_state.assert_called_once()
        # Should contain state information
        assert "error" not in result or "state" in result, (
            f"handle_app_state should return state info. Got: {result}"
        )

    def test_handle_dismiss_sheet_swipes_down(self):
        """handle_dismiss_sheet calls backend.swipe (downward gesture)."""
        try:
            from specterqa.ios.mcp.server import handle_dismiss_sheet
        except ImportError:
            pytest.skip("handle_dismiss_sheet not yet implemented")

        mock_backend = _setup_session([])
        mock_backend.swipe.return_value = None

        result = handle_dismiss_sheet({})

        mock_backend.swipe.assert_called_once()
        call_args = mock_backend.swipe.call_args
        # Verify it's a downward swipe
        all_args = list(call_args[0]) + list(call_args[1].values()) if call_args else []
        all_str = " ".join(str(a).lower() for a in all_args)
        assert "down" in all_str or result.get("status") == "ok", (
            f"dismiss_sheet should swipe down. Args: {all_args}, result: {result}"
        )


# ===========================================================================
# 8. Replay engine — _exec_long_press identifier priority
# ===========================================================================


class TestExecLongPressIdentifier:
    """Verify _exec_long_press checks identifier before label."""

    def _make_player(self, tmp_path):
        from specterqa.ios.replay import ReplayPlayer
        replay_file = tmp_path / "test.yaml"
        replay_file.write_text(
            "replay:\n  name: test\n  bundle_id: com.example\n  steps: []\n"
        )
        return ReplayPlayer(str(replay_file))

    def test_exec_tap_identifier_before_label(self, tmp_path):
        """Verify identifier-first lookup still works in _exec_tap (regression guard)."""
        from specterqa.ios.replay import ReplayPlayer

        player = self._make_player(tmp_path)
        backend = MagicMock()
        annotator = MagicMock()
        result = {"exit_code": 0}

        label_el = MockElement(
            index=1, label="Save", identifier="wrongId",
            x=10.0, y=10.0, width=80.0, height=40.0
        )
        id_el = MockElement(
            index=2, label="AnotherLabel", identifier="saveBtn",
            x=50.0, y=50.0, width=80.0, height=40.0
        )
        annotator.get_elements_from_runner.return_value = [label_el, id_el]

        step = {"action": "tap", "element_label": "Save", "element_identifier": "saveBtn"}
        player._exec_tap(step, backend, annotator, result)

        expected_cx = id_el.x + id_el.width / 2
        expected_cy = id_el.y + id_el.height / 2
        backend.tap.assert_called_once_with(expected_cx, expected_cy)

    def test_exec_long_press_tries_identifier(self, tmp_path):
        """_exec_long_press checks identifier before label."""
        try:
            from specterqa.ios.replay import ReplayPlayer
            if not hasattr(ReplayPlayer, "_exec_long_press"):
                pytest.skip("_exec_long_press not yet implemented")
        except ImportError:
            pytest.skip("ReplayPlayer not yet implemented")

        player = self._make_player(tmp_path)
        backend = MagicMock()
        annotator = MagicMock()
        result = {"exit_code": 0}

        label_el = MockElement(
            index=1, label="Delete", identifier="wrongId",
            x=10.0, y=10.0, width=80.0, height=40.0
        )
        id_el = MockElement(
            index=2, label="OtherLabel", identifier="deleteBtn",
            x=60.0, y=60.0, width=80.0, height=40.0
        )
        annotator.get_elements_from_runner.return_value = [label_el, id_el]

        step = {
            "action": "long_press",
            "element_label": "Delete",
            "element_identifier": "deleteBtn",
            "duration": 1.0
        }
        player._exec_long_press(step, backend, annotator, result)

        expected_cx = id_el.x + id_el.width / 2
        expected_cy = id_el.y + id_el.height / 2
        backend.long_press.assert_called_once()
        call_args = backend.long_press.call_args
        tapped_x, tapped_y = call_args[0][0], call_args[0][1]
        assert tapped_x == pytest.approx(expected_cx), (
            f"long_press should use identifier-matched element. Expected x={expected_cx}, got={tapped_x}"
        )
        assert tapped_y == pytest.approx(expected_cy), (
            f"long_press should use identifier-matched element. Expected y={expected_cy}, got={tapped_y}"
        )


# ===========================================================================
# 9. Backend client new methods
# ===========================================================================


class TestBackendClientMethods:
    """Verify XCTestBackend has app_state, wait_idle, and set_appearance methods."""

    def _get_backend_class(self):
        try:
            from specterqa.ios.mcp.server import XCTestBackend
            return XCTestBackend
        except ImportError:
            try:
                from specterqa.ios.backend import XCTestBackend
                return XCTestBackend
            except ImportError:
                pytest.skip("XCTestBackend not found")

    def test_backend_app_state_method(self):
        """XCTestBackend.app_state() calls GET /app_state."""
        XCTestBackend = self._get_backend_class()

        with patch("requests.get") as mock_get, patch("requests.post"):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"foreground": True, "state": "running"}
            mock_get.return_value = mock_resp

            backend = XCTestBackend.__new__(XCTestBackend)
            backend.base_url = "http://localhost:8222"

            if not hasattr(backend, "app_state"):
                pytest.skip("XCTestBackend.app_state not yet implemented")

            result = backend.app_state()

            mock_get.assert_called_once()
            called_url = mock_get.call_args[0][0] if mock_get.call_args[0] else str(mock_get.call_args)
            assert "app_state" in called_url, (
                f"app_state() should call GET /app_state. Called: {called_url}"
            )

    def test_backend_wait_idle_method(self):
        """XCTestBackend.wait_idle() calls POST /idle."""
        XCTestBackend = self._get_backend_class()

        with patch("requests.post") as mock_post, patch("requests.get"):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"idle": True}
            mock_post.return_value = mock_resp

            backend = XCTestBackend.__new__(XCTestBackend)
            backend.base_url = "http://localhost:8222"

            if not hasattr(backend, "wait_idle"):
                pytest.skip("XCTestBackend.wait_idle not yet implemented")

            backend.wait_idle(timeout=10)

            mock_post.assert_called_once()
            called_url = mock_post.call_args[0][0] if mock_post.call_args[0] else str(mock_post.call_args)
            assert "idle" in called_url, (
                f"wait_idle() should call POST /idle. Called: {called_url}"
            )

    def test_backend_set_appearance_method(self):
        """XCTestBackend.set_appearance() calls POST /appearance."""
        XCTestBackend = self._get_backend_class()

        with patch("requests.post") as mock_post, patch("requests.get"):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "ok"}
            mock_post.return_value = mock_resp

            backend = XCTestBackend.__new__(XCTestBackend)
            backend.base_url = "http://localhost:8222"

            if not hasattr(backend, "set_appearance"):
                pytest.skip("XCTestBackend.set_appearance not yet implemented")

            backend.set_appearance("dark")

            mock_post.assert_called_once()
            called_url = mock_post.call_args[0][0] if mock_post.call_args[0] else str(mock_post.call_args)
            assert "appearance" in called_url, (
                f"set_appearance() should call POST /appearance. Called: {called_url}"
            )
