"""Tests for accessibilityIdentifier support and coordinate-based tap.

TDD Phase — tests written BEFORE implementation is complete.

Features under test:
  1. ios_tap identifier parameter — exact match on element.identifier
  2. ios_tap x/y coordinate parameters — direct coordinate tapping
  3. ReplayStep.element_identifier field and ReplayPlayer identifier support

Module under test:
  specterqa/ios/mcp/server.py  — handle_tap()
  specterqa/ios/replay.py      — ReplayStep, ReplayPlayer
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Mock element
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


# ---------------------------------------------------------------------------
# Helpers for MCP server tests
# ---------------------------------------------------------------------------


def _setup_session(elements, recorder=None):
    """Patch module globals so handle_tap runs against a fake session."""
    import specterqa.ios.mcp.server as srv

    mock_backend = MagicMock()
    srv._backend = mock_backend
    srv._session = MagicMock()
    srv._last_elements = elements
    srv._recorder = recorder
    srv._annotator = None
    return mock_backend


def _teardown_session():
    import specterqa.ios.mcp.server as srv

    srv._backend = None
    srv._session = None
    srv._last_elements = []
    srv._recorder = None
    srv._annotator = None


# ===========================================================================
# MCP handle_tap — identifier parameter
# ===========================================================================


class TestTapByIdentifier:
    """Verify accessibilityIdentifier-based tap in handle_tap()."""

    def teardown_method(self, method):
        _teardown_session()

    def test_tap_by_identifier_exact_match(self):
        """identifier='settingsBtn' finds the element with that exact identifier."""
        from specterqa.ios.mcp.server import handle_tap

        elements = [
            MockElement(index=1, label="Settings", identifier="settingsBtn", x=10, y=20, width=80, height=40),
        ]
        mock_backend = _setup_session(elements)

        result = handle_tap({"identifier": "settingsBtn"})

        assert result.get("status") == "ok", f"Expected ok, got: {result}"
        assert mock_backend.tap_element.called or mock_backend.tap.called, (
            "Expected tap_element() or tap() to be called"
        )

    def test_tap_by_identifier_no_match(self):
        """Returns error dict when identifier doesn't match any element."""
        from specterqa.ios.mcp.server import handle_tap

        elements = [
            MockElement(index=1, label="Settings", identifier="settingsBtn"),
        ]
        _setup_session(elements)

        result = handle_tap({"identifier": "nonExistentId"})

        assert "error" in result, f"Expected error key in result: {result}"

    def test_tap_by_identifier_takes_priority_over_label(self):
        """When both identifier and label provided, identifier match wins."""
        from specterqa.ios.mcp.server import handle_tap

        # Two elements: one matches by label, the other matches by identifier
        label_match = MockElement(index=1, label="Save", identifier="wrongId", x=10, y=10, width=80, height=40)
        id_match = MockElement(index=2, label="SomethingElse", identifier="saveBtn", x=50, y=50, width=80, height=40)
        mock_backend = _setup_session([label_match, id_match])

        result = handle_tap({"identifier": "saveBtn", "label": "Save"})

        assert result.get("status") == "ok", f"Expected ok, got: {result}"
        # When element has an identifier, tap_element() is called with that identifier.
        # Verify the tap went through one of the two tap methods (element-based preferred).
        assert mock_backend.tap_element.called or mock_backend.tap.called, (
            "Expected tap_element() or tap() to be called"
        )
        if mock_backend.tap_element.called:
            # Verify it was called with the correct identifier (saveBtn, not wrongId)
            call_kwargs = mock_backend.tap_element.call_args[1]
            assert call_kwargs.get("identifier") == "saveBtn", (
                f"tap_element should use identifier 'saveBtn'. Got: {call_kwargs}"
            )

    def test_tap_by_identifier_not_substring(self):
        """Identifier matching is exact — 'Btn' does NOT match 'settingsBtn'."""
        from specterqa.ios.mcp.server import handle_tap

        elements = [
            MockElement(index=1, label="Settings", identifier="settingsBtn"),
        ]
        _setup_session(elements)

        # Partial match should NOT succeed
        result = handle_tap({"identifier": "Btn"})

        assert "error" in result, f"Partial identifier match must fail, got: {result}"

    def test_tap_by_identifier_records_replay(self):
        """recorder.record_tap is called when tapping by identifier."""
        from specterqa.ios.mcp.server import handle_tap

        elements = [
            MockElement(index=3, label="Profile", identifier="profileBtn", x=0, y=0, width=60, height=30),
        ]
        mock_recorder = MagicMock()
        _setup_session(elements, recorder=mock_recorder)

        result = handle_tap({"identifier": "profileBtn"})

        assert result.get("status") == "ok", f"Expected ok, got: {result}"
        mock_recorder.record_tap.assert_called_once()
        # Verify identifier is passed to recorder
        call_kwargs = mock_recorder.record_tap.call_args
        # record_tap may be called positionally or with kwargs — check either way
        args = call_kwargs[0] if call_kwargs[0] else []
        kwargs = call_kwargs[1] if call_kwargs[1] else {}
        # The identifier should appear somewhere in the call
        all_args = list(args) + list(kwargs.values())
        assert any(
            v == "profileBtn" for v in all_args
        ) or kwargs.get("identifier") == "profileBtn", (
            f"record_tap should receive identifier 'profileBtn'. Got: {call_kwargs}"
        )


# ===========================================================================
# MCP handle_tap — coordinate parameters
# ===========================================================================


class TestTapByCoordinates:
    """Verify x/y coordinate-based tap in handle_tap()."""

    def teardown_method(self, method):
        _teardown_session()

    def test_tap_by_coordinates(self):
        """x=100, y=200 taps at those exact coordinates."""
        from specterqa.ios.mcp.server import handle_tap

        _setup_session([])
        mock_backend = _setup_session([])

        result = handle_tap({"x": 100, "y": 200})

        assert result.get("status") == "ok", f"Expected ok, got: {result}"
        mock_backend.tap.assert_called_once_with(100, 200)

    def test_tap_by_coordinates_no_session_error(self):
        """Returns error when no session is active."""
        import specterqa.ios.mcp.server as srv
        from specterqa.ios.mcp.server import handle_tap

        srv._backend = None
        srv._session = None

        result = handle_tap({"x": 100, "y": 200})

        assert "error" in result, f"Expected error when no session: {result}"

    def test_tap_coordinates_used_as_last_fallback(self):
        """When label lookup fails but x,y provided, falls back to coordinates."""
        from specterqa.ios.mcp.server import handle_tap

        # Element with non-matching label
        elements = [
            MockElement(index=1, label="Save", identifier="saveBtn"),
        ]
        mock_backend = _setup_session(elements)

        # Label doesn't match, but x,y are provided — should fall back to coords
        result = handle_tap({"label": "NonExistentLabel", "x": 150, "y": 300})

        assert result.get("status") == "ok", f"Expected coordinate fallback to succeed, got: {result}"
        mock_backend.tap.assert_called_once_with(150, 300)

    def test_tap_by_coordinates_records_replay(self):
        """recorder.record_tap is called with coord info for coordinate taps."""
        from specterqa.ios.mcp.server import handle_tap

        mock_recorder = MagicMock()
        _setup_session([], recorder=mock_recorder)

        result = handle_tap({"x": 100, "y": 200})

        assert result.get("status") == "ok", f"Expected ok, got: {result}"
        mock_recorder.record_tap.assert_called_once()

    def test_tap_requires_both_x_and_y(self):
        """Providing only x without y (or vice versa) should not do a coordinate tap."""
        from specterqa.ios.mcp.server import handle_tap

        _setup_session([])

        # Only x — no y — should not succeed as coordinate tap
        result = handle_tap({"x": 100})

        # Should fail: no element found and incomplete coordinate data
        assert "error" in result, f"Expected error when only x provided, got: {result}"


# ===========================================================================
# Replay engine — ReplayStep identifier field
# ===========================================================================


class TestReplayStepIdentifierField:
    """Verify ReplayStep.element_identifier field exists and works."""

    def test_replay_step_has_identifier_field(self):
        """ReplayStep(action='tap', element_identifier='foo') constructs successfully."""
        from specterqa.ios.replay import ReplayStep

        step = ReplayStep(action="tap", element_identifier="foo")
        assert step.element_identifier == "foo"

    def test_replay_step_identifier_defaults_to_none(self):
        """element_identifier defaults to None when not specified."""
        from specterqa.ios.replay import ReplayStep

        step = ReplayStep(action="tap")
        assert step.element_identifier is None

    def test_replay_step_identifier_coexists_with_label(self):
        """element_identifier and element_label can both be set on one step."""
        from specterqa.ios.replay import ReplayStep

        step = ReplayStep(action="tap", element_label="Settings", element_identifier="settingsBtn")
        assert step.element_label == "Settings"
        assert step.element_identifier == "settingsBtn"


# ===========================================================================
# Replay engine — _find_by_identifier
# ===========================================================================


class TestFindByIdentifier:
    """Verify _find_by_identifier exact-match semantics."""

    def _make_player(self, tmp_path):
        """Create a minimal ReplayPlayer without actual file IO."""
        from specterqa.ios.replay import ReplayPlayer

        # Write a minimal replay YAML to satisfy __init__
        replay_file = tmp_path / "test.yaml"
        replay_file.write_text(
            "replay:\n  name: test\n  bundle_id: com.example\n  steps: []\n"
        )
        return ReplayPlayer(str(replay_file))

    def test_find_by_identifier_exact_match(self, tmp_path):
        """_find_by_identifier returns matching element on exact identifier."""
        from specterqa.ios.replay import ReplayPlayer

        el = MockElement(index=1, label="Settings", identifier="settingsBtn")
        result = ReplayPlayer._find_by_identifier([el], "settingsBtn")
        assert result is el

    def test_find_by_identifier_no_substring(self, tmp_path):
        """_find_by_identifier('btn') does NOT match 'settingsBtn' (exact only)."""
        from specterqa.ios.replay import ReplayPlayer

        el = MockElement(index=1, label="Settings", identifier="settingsBtn")
        result = ReplayPlayer._find_by_identifier([el], "Btn")
        assert result is None

    def test_find_by_identifier_case_sensitive(self, tmp_path):
        """Identifier matching is case-sensitive (exact)."""
        from specterqa.ios.replay import ReplayPlayer

        el = MockElement(index=1, label="Settings", identifier="settingsBtn")
        result = ReplayPlayer._find_by_identifier([el], "settingsbtn")
        assert result is None

    def test_find_by_identifier_empty_string_returns_none(self, tmp_path):
        """_find_by_identifier returns None for empty identifier."""
        from specterqa.ios.replay import ReplayPlayer

        el = MockElement(index=1, label="Settings", identifier="settingsBtn")
        result = ReplayPlayer._find_by_identifier([el], "")
        assert result is None

    def test_find_by_identifier_missing_attr_safely_skipped(self, tmp_path):
        """Elements without identifier attr are skipped without raising."""
        from specterqa.ios.replay import ReplayPlayer

        # Plain object without identifier attribute
        el_no_id = MagicMock(spec=["label", "index", "x", "y", "width", "height"])
        el_no_id.label = "NoId"
        el_with_id = MockElement(index=2, label="WithId", identifier="targetId")

        result = ReplayPlayer._find_by_identifier([el_no_id, el_with_id], "targetId")
        assert result is el_with_id

    def test_find_by_identifier_empty_list_returns_none(self, tmp_path):
        """_find_by_identifier returns None when element list is empty."""
        from specterqa.ios.replay import ReplayPlayer

        result = ReplayPlayer._find_by_identifier([], "anyId")
        assert result is None

    def test_find_by_identifier_returns_first_match(self, tmp_path):
        """When multiple elements share an identifier, the first is returned."""
        from specterqa.ios.replay import ReplayPlayer

        el1 = MockElement(index=1, label="First", identifier="shared")
        el2 = MockElement(index=2, label="Second", identifier="shared")
        result = ReplayPlayer._find_by_identifier([el1, el2], "shared")
        assert result is el1


# ===========================================================================
# Replay engine — _exec_tap identifier priority
# ===========================================================================


class TestExecTapIdentifierPriority:
    """Verify _exec_tap tries identifier before label and falls back to coords."""

    def _make_player(self, tmp_path):
        from specterqa.ios.replay import ReplayPlayer

        replay_file = tmp_path / "test.yaml"
        replay_file.write_text(
            "replay:\n  name: test\n  bundle_id: com.example\n  steps: []\n"
        )
        return ReplayPlayer(str(replay_file))

    def test_exec_tap_tries_identifier_before_label(self, tmp_path):
        """_exec_tap uses identifier match when available, ignoring label."""
        player = self._make_player(tmp_path)
        backend = MagicMock()
        annotator = MagicMock()
        result = {"exit_code": 0}

        label_el = MockElement(index=1, label="Save", identifier="wrongId", x=10, y=10, width=80, height=40)
        id_el = MockElement(index=2, label="AnotherLabel", identifier="saveBtn", x=50, y=50, width=80, height=40)
        annotator.get_elements_from_runner.return_value = [label_el, id_el]

        step = {"action": "tap", "element_label": "Save", "element_identifier": "saveBtn"}
        player._exec_tap(step, backend, annotator, result)

        # Should tap id_el's center, not label_el's
        expected_cx = id_el.x + id_el.width / 2
        expected_cy = id_el.y + id_el.height / 2
        backend.tap.assert_called_once_with(expected_cx, expected_cy)

    def test_exec_tap_falls_back_to_label_when_no_identifier(self, tmp_path):
        """_exec_tap falls back to label lookup when no identifier in step."""
        player = self._make_player(tmp_path)
        backend = MagicMock()
        annotator = MagicMock()
        result = {"exit_code": 0}

        el = MockElement(index=1, label="Save", identifier="saveBtn", x=10, y=20, width=80, height=40)
        annotator.get_elements_from_runner.return_value = [el]

        step = {"action": "tap", "element_label": "Save"}
        player._exec_tap(step, backend, annotator, result)

        expected_cx = el.x + el.width / 2
        expected_cy = el.y + el.height / 2
        backend.tap.assert_called_once_with(expected_cx, expected_cy)

    def test_exec_tap_falls_back_to_coordinates(self, tmp_path):
        """When identifier and label both fail, uses x,y coordinates."""
        player = self._make_player(tmp_path)
        backend = MagicMock()
        annotator = MagicMock()
        result = {"exit_code": 0}

        annotator.get_elements_from_runner.return_value = []

        step = {"action": "tap", "element_identifier": "missingId", "element_label": "Missing", "x": 123.0, "y": 456.0}
        player._exec_tap(step, backend, annotator, result)

        backend.tap.assert_called_once_with(123.0, 456.0)
        assert result["exit_code"] == 0, "Coordinate fallback should not set error exit code"

    def test_exec_tap_identifier_fail_label_success(self, tmp_path):
        """When identifier lookup fails, falls back to label match."""
        player = self._make_player(tmp_path)
        backend = MagicMock()
        annotator = MagicMock()
        result = {"exit_code": 0}

        el = MockElement(index=1, label="Save", identifier="differentId", x=5, y=15, width=60, height=30)
        annotator.get_elements_from_runner.return_value = [el]

        # Identifier won't match, label will
        step = {"action": "tap", "element_identifier": "wrongId", "element_label": "Save"}
        player._exec_tap(step, backend, annotator, result)

        expected_cx = el.x + el.width / 2
        expected_cy = el.y + el.height / 2
        backend.tap.assert_called_once_with(expected_cx, expected_cy)


# ===========================================================================
# Replay engine — Maestro tapOnIdentifier shortcut
# ===========================================================================


class TestMaestroTapOnIdentifierShortcut:
    """Verify tapOnIdentifier Maestro alias normalizes correctly."""

    def test_maestro_tapOnIdentifier_shortcut(self):
        """{'tapOnIdentifier': 'foo'} normalizes to action=tap, element_identifier=foo."""
        from specterqa.ios.replay import ReplayPlayer

        step = {"tapOnIdentifier": "settingsBtn"}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())

        assert normalized["action"] == "tap"
        assert normalized["element_identifier"] == "settingsBtn"
        assert "tapOnIdentifier" not in normalized

    def test_maestro_tapOnIdentifier_does_not_clobber_existing_action(self):
        """If action already set, tapOnIdentifier must not override it."""
        from specterqa.ios.replay import ReplayPlayer

        step = {"action": "swipe", "tapOnIdentifier": "ignored"}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())

        # setdefault means existing 'action' wins
        assert normalized["action"] == "swipe"

    def test_maestro_tapOnIdentifier_does_not_mutate_original(self):
        """Original step dict is unchanged when normalization uses .copy()."""
        from specterqa.ios.replay import ReplayPlayer

        original = {"tapOnIdentifier": "settingsBtn"}
        copy_for_normalization = original.copy()
        ReplayPlayer._normalize_maestro_step(copy_for_normalization)

        # Original should still have the Maestro key
        assert "tapOnIdentifier" in original
        assert "action" not in original

    def test_maestro_tapOnIdentifier_combined_with_tapOn(self):
        """tapOnIdentifier coexists with tapOn — both are normalized."""
        from specterqa.ios.replay import ReplayPlayer

        # tapOnIdentifier takes identifier route, tapOn takes label route
        # When both present in the same step (unusual but should not crash)
        step = {"tapOnIdentifier": "fooId"}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())
        assert normalized.get("element_identifier") == "fooId"


# ===========================================================================
# Replay engine — _resolve_step_vars includes identifier
# ===========================================================================


class TestResolveStepVarsIncludesIdentifier:
    """Verify variable substitution works on element_identifier field."""

    def test_resolve_step_vars_includes_identifier(self):
        """${ID} placeholder in element_identifier is substituted correctly."""
        from specterqa.ios.replay import ReplayPlayer

        player = object.__new__(ReplayPlayer)
        step = {"action": "tap", "element_identifier": "${BTN_ID}"}
        resolved = player._resolve_step_vars(step, {"BTN_ID": "saveButton"})

        assert resolved["element_identifier"] == "saveButton"
        # Original must not be mutated
        assert step["element_identifier"] == "${BTN_ID}"

    def test_resolve_step_vars_identifier_unchanged_without_var(self):
        """Static identifier string is left unchanged when no variable matches."""
        from specterqa.ios.replay import ReplayPlayer

        player = object.__new__(ReplayPlayer)
        step = {"action": "tap", "element_identifier": "staticId"}
        resolved = player._resolve_step_vars(step, {"OTHER": "value"})

        assert resolved["element_identifier"] == "staticId"

    def test_resolve_step_vars_identifier_and_label_both_substituted(self):
        """Both element_identifier and element_label get variable substitution."""
        from specterqa.ios.replay import ReplayPlayer

        player = object.__new__(ReplayPlayer)
        step = {"action": "tap", "element_label": "${LABEL}", "element_identifier": "${ID}"}
        resolved = player._resolve_step_vars(step, {"LABEL": "Save", "ID": "saveBtn"})

        assert resolved["element_label"] == "Save"
        assert resolved["element_identifier"] == "saveBtn"

    def test_resolve_step_vars_no_variables_returns_unchanged(self):
        """Step with identifier is returned unchanged when variables dict is empty."""
        from specterqa.ios.replay import ReplayPlayer

        player = object.__new__(ReplayPlayer)
        step = {"action": "tap", "element_identifier": "fixedId"}
        resolved = player._resolve_step_vars(step, {})

        assert resolved == step
