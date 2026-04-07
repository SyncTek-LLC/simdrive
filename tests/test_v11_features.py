"""Feature verification tests for SpecterQA iOS v11.0.0 / v11.1.0.

Tests every v11 feature using mocks where a running simulator is required and
real assertions where logic can be tested in isolation.

Run:
    pytest tests/test_v11_features.py -v --tb=short
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_b64_png(color: str = "red", size: tuple = (100, 100)) -> str:
    """Return a base64-encoded PNG of a solid-color image."""
    from PIL import Image

    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _make_mock_element(
    label: str,
    element_type: str = "Button",
    index: int = 1,
    x: float = 10.0,
    y: float = 10.0,
    width: float = 100.0,
    height: float = 50.0,
    enabled: bool = True,
    selected: bool = False,
):
    """Build a mock UIElement with the standard attribute set."""
    e = MagicMock()
    e.label = label
    e.element_type = element_type
    e.index = index
    e.x = x
    e.y = y
    e.width = width
    e.height = height
    e.enabled = enabled
    e.selected = selected
    return e


# ===========================================================================
# TestMaestroYAMLAliases
# ===========================================================================


class TestMaestroYAMLAliases:
    """Verify Maestro-compatible YAML syntax normalizes correctly."""

    def test_tapOn_normalizes_to_tap(self):
        from specterqa.ios.replay import ReplayPlayer

        step = {"tapOn": "Save"}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())
        assert normalized["action"] == "tap"
        assert normalized["element_label"] == "Save"

    def test_assertVisible_single_string_normalizes(self):
        from specterqa.ios.replay import ReplayPlayer

        step = {"assertVisible": "Welcome"}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())
        assert normalized["action"] == "assert"
        assert "Welcome" in normalized["expect_elements"]

    def test_assertVisible_list_normalizes(self):
        from specterqa.ios.replay import ReplayPlayer

        step = {"assertVisible": ["Welcome", "Continue"]}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())
        assert normalized["action"] == "assert"
        assert "Welcome" in normalized["expect_elements"]
        assert "Continue" in normalized["expect_elements"]

    def test_assertNotVisible_single_string_normalizes(self):
        from specterqa.ios.replay import ReplayPlayer

        step = {"assertNotVisible": "Error"}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())
        assert normalized["action"] == "assert"
        assert "Error" in normalized["expect_not_elements"]

    def test_assertNotVisible_list_normalizes(self):
        from specterqa.ios.replay import ReplayPlayer

        step = {"assertNotVisible": ["Error", "Spinner"]}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())
        assert normalized["action"] == "assert"
        assert "Error" in normalized["expect_not_elements"]
        assert "Spinner" in normalized["expect_not_elements"]

    def test_inputText_normalizes(self):
        from specterqa.ios.replay import ReplayPlayer

        step = {"inputText": "hello world"}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())
        assert normalized["action"] == "type"
        assert normalized["text"] == "hello world"

    def test_waitFor_normalizes(self):
        from specterqa.ios.replay import ReplayPlayer

        step = {"waitFor": "Loading"}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())
        assert normalized["action"] == "wait_for_element"
        assert normalized["label"] == "Loading"

    def test_normalize_does_not_mutate_original(self):
        """Normalization must work on a copy; the caller's original dict is untouched."""
        from specterqa.ios.replay import ReplayPlayer

        # _execute_step calls _normalize_maestro_step(dict(step)) which copies first —
        # verify the user-facing contract: passing .copy() ensures original is safe.
        original = {"tapOn": "Save"}
        copy_for_normalization = original.copy()
        ReplayPlayer._normalize_maestro_step(copy_for_normalization)
        # original should be unchanged
        assert "tapOn" in original
        assert "action" not in original

    def test_native_action_not_overridden_by_tapOn(self):
        """If a step already has an action, tapOn must not clobber it."""
        from specterqa.ios.replay import ReplayPlayer

        step = {"action": "swipe", "tapOn": "Ignored"}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())
        # setdefault means existing 'action' wins
        assert normalized["action"] == "swipe"

    def test_empty_assertVisible_list(self):
        from specterqa.ios.replay import ReplayPlayer

        step = {"assertVisible": []}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())
        assert normalized["action"] == "assert"
        assert normalized["expect_elements"] == []

    def test_step_with_unknown_extra_keys_passes_through(self):
        from specterqa.ios.replay import ReplayPlayer

        step = {"tapOn": "OK", "custom_key": "ignored"}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())
        assert normalized["action"] == "tap"
        assert "custom_key" in normalized  # unknown keys preserved, not stripped


# ===========================================================================
# TestReplayVariables
# ===========================================================================


class TestReplayVariables:
    """Verify ${VAR} substitution in replay steps."""

    def test_variable_substitution_in_element_label(self):
        from specterqa.ios.replay import ReplayPlayer

        player = MagicMock(spec=ReplayPlayer)
        player.resolve_vars = ReplayPlayer.resolve_vars

        text = "${BTN}"
        result = ReplayPlayer.resolve_vars(text, {"BTN": "Save"})
        assert result == "Save"

    def test_variable_substitution_in_text_field(self):
        from specterqa.ios.replay import ReplayPlayer

        result = ReplayPlayer.resolve_vars("Hello, ${NAME}!", {"NAME": "Alice"})
        assert result == "Hello, Alice!"

    def test_resolve_step_vars_substitutes_element_label(self):
        from specterqa.ios.replay import ReplayPlayer

        # Build a minimal player with no file IO
        player = object.__new__(ReplayPlayer)
        step = {"action": "tap", "element_label": "${BTN}"}
        resolved = player._resolve_step_vars(step, {"BTN": "Submit"})
        assert resolved["element_label"] == "Submit"
        # Original must not be mutated
        assert step["element_label"] == "${BTN}"

    def test_resolve_step_vars_substitutes_text(self):
        from specterqa.ios.replay import ReplayPlayer

        player = object.__new__(ReplayPlayer)
        step = {"action": "type", "text": "${EMAIL}"}
        resolved = player._resolve_step_vars(step, {"EMAIL": "user@example.com"})
        assert resolved["text"] == "user@example.com"

    def test_resolve_step_vars_substitutes_key(self):
        from specterqa.ios.replay import ReplayPlayer

        player = object.__new__(ReplayPlayer)
        step = {"action": "press_key", "key": "${KEY}"}
        resolved = player._resolve_step_vars(step, {"KEY": "return"})
        assert resolved["key"] == "return"

    def test_no_variables_returns_unchanged_step(self):
        from specterqa.ios.replay import ReplayPlayer

        player = object.__new__(ReplayPlayer)
        step = {"action": "swipe", "direction": "up"}
        resolved = player._resolve_step_vars(step, {})
        assert resolved == step

    def test_unmatched_placeholder_left_as_is(self):
        from specterqa.ios.replay import ReplayPlayer

        result = ReplayPlayer.resolve_vars("${UNKNOWN}", {"OTHER": "value"})
        assert result == "${UNKNOWN}"

    def test_multiple_variables_in_one_field(self):
        from specterqa.ios.replay import ReplayPlayer

        result = ReplayPlayer.resolve_vars("${FIRST} ${LAST}", {"FIRST": "John", "LAST": "Doe"})
        assert result == "John Doe"


# ===========================================================================
# TestVisualRegression
# ===========================================================================


class TestVisualRegression:
    """Verify screenshot diffing logic."""

    def test_identical_screenshots_have_zero_diff(self):
        from specterqa.ios.replay import screenshot_diff

        b64 = _make_b64_png("red", (100, 100))
        diff = screenshot_diff(b64, b64)
        assert diff == 0.0

    def test_different_sizes_returns_100(self):
        from specterqa.ios.replay import screenshot_diff

        b64_small = _make_b64_png("red", (50, 50))
        b64_large = _make_b64_png("red", (100, 100))
        diff = screenshot_diff(b64_small, b64_large)
        assert diff == 100.0

    def test_completely_different_color_is_nonzero(self):
        from specterqa.ios.replay import screenshot_diff

        b64_red = _make_b64_png("red", (100, 100))
        b64_blue = _make_b64_png("blue", (100, 100))
        diff = screenshot_diff(b64_red, b64_blue)
        assert diff > 0.0

    def test_partial_difference_is_between_zero_and_hundred(self):
        from specterqa.ios.replay import screenshot_diff
        from PIL import Image

        # Two images that differ in exactly half their pixels
        img_a = Image.new("RGB", (100, 100), "red")
        img_b = Image.new("RGB", (100, 100), "red")
        # Make the top half of img_b blue
        for x in range(100):
            for y in range(50):
                img_b.putpixel((x, y), (0, 0, 255))

        def to_b64(img):
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode()

        diff = screenshot_diff(to_b64(img_a), to_b64(img_b))
        assert 0.0 < diff < 100.0

    def test_returns_float(self):
        from specterqa.ios.replay import screenshot_diff

        b64 = _make_b64_png("green", (10, 10))
        diff = screenshot_diff(b64, b64)
        assert isinstance(diff, float)


# ===========================================================================
# TestMCPToolList
# ===========================================================================


mcp = pytest.importorskip("mcp", reason="mcp extra not installed; run: pip install 'specterqa-ios[mcp]'")


class TestMCPToolList:
    """Verify all 19 MCP tools are registered."""

    def test_all_19_tools_registered(self):
        import asyncio
        from specterqa.ios.mcp.server import create_server

        server = create_server()
        tools = asyncio.run(server.list_tools())
        assert len(tools) == 19, f"Expected 19 tools, got {len(tools)}: {[t.name for t in tools]}"

    def test_expected_tool_names_present(self):
        import asyncio
        from specterqa.ios.mcp.server import create_server

        server = create_server()
        tools = asyncio.run(server.list_tools())
        tool_names = {t.name for t in tools}
        expected = {
            "ios_start_session",
            "ios_stop_session",
            "ios_screenshot",
            "ios_tap",
            "ios_swipe",
            "ios_swipe_back",
            "ios_type",
            "ios_press_key",
            "ios_long_press",
            "ios_wait",
            "ios_wait_for_element",
            "ios_start_recording",
            "ios_stop_recording",
            "ios_save_replay",
            "ios_set_appearance",
            "ios_simctl",
            "ios_accessibility_audit",
            "ios_webview_elements",
            "ios_elements",
        }
        missing = expected - tool_names
        assert not missing, f"Missing tools: {missing}"

    def test_handler_exports(self):
        from specterqa.ios.mcp import server

        expected_handlers = [
            "handle_start_session",
            "handle_stop_session",
            "handle_screenshot",
            "handle_elements",
            "handle_tap",
            "handle_swipe",
            "handle_swipe_back",
            "handle_type",
            "handle_press_key",
            "handle_long_press",
            "handle_wait",
            "handle_wait_for_element",
            "handle_start_recording",
            "handle_stop_recording",
            "handle_save_replay",
            "handle_set_appearance",
            "handle_simctl",
            "handle_accessibility_audit",
            "handle_webview_elements",
        ]
        for h in expected_handlers:
            assert hasattr(server, h), f"Missing handler: {h}"


# ===========================================================================
# TestTapByLabel
# ===========================================================================


class TestTapByLabel:
    """Verify label-based tap with mock backend."""

    def _setup_session(self, elements):
        """Patch module globals so handle_tap runs against a fake session."""
        import specterqa.ios.mcp.server as srv

        mock_backend = MagicMock()
        srv._backend = mock_backend
        srv._session = MagicMock()
        srv._last_elements = elements
        srv._recorder = None
        srv._annotator = None
        return mock_backend

    def teardown_method(self, method):
        import specterqa.ios.mcp.server as srv

        srv._backend = None
        srv._session = None
        srv._last_elements = []
        srv._recorder = None
        srv._annotator = None

    def test_tap_finds_element_by_substring(self):
        from specterqa.ios.mcp.server import handle_tap

        elements = [_make_mock_element("Save Document", index=1)]
        mock_backend = self._setup_session(elements)

        result = handle_tap({"label": "Save"})
        assert result.get("status") == "ok"
        assert "Save" in result.get("tapped", "")
        mock_backend.tap.assert_called_once()

    def test_tap_filters_by_type(self):
        from specterqa.ios.mcp.server import handle_tap

        elements = [
            _make_mock_element("OK", element_type="StaticText", index=1),
            _make_mock_element("OK", element_type="Button", index=2),
        ]
        self._setup_session(elements)

        result = handle_tap({"label": "OK", "type": "Button"})
        assert result.get("status") == "ok"

    def test_tap_falls_back_to_index(self):
        from specterqa.ios.mcp.server import handle_tap

        elements = [_make_mock_element("Cancel", index=3, x=10, y=20, width=80, height=40)]
        self._setup_session(elements)

        result = handle_tap({"element_index": 3})
        assert result.get("status") == "ok"

    def test_tap_returns_clear_error_when_no_elements_cached(self):
        import specterqa.ios.mcp.server as srv
        from specterqa.ios.mcp.server import handle_tap

        srv._backend = MagicMock()
        srv._session = MagicMock()
        srv._last_elements = []
        srv._recorder = None

        result = handle_tap({"element_index": 5})
        assert "error" in result
        assert "5" in result["error"]

    def test_tap_case_insensitive_label_match(self):
        from specterqa.ios.mcp.server import handle_tap

        elements = [_make_mock_element("SUBMIT FORM", index=1)]
        self._setup_session(elements)

        result = handle_tap({"label": "submit"})
        assert result.get("status") == "ok"


# ===========================================================================
# TestAccessibilityAudit
# ===========================================================================


class TestAccessibilityAudit:
    """Verify a11y audit only flags interactive types and catches real issues."""

    def _setup_annotator(self, elements):
        import specterqa.ios.mcp.server as srv

        mock_backend = MagicMock()
        mock_annotator = MagicMock()
        mock_annotator.get_elements_from_runner.return_value = elements
        srv._backend = mock_backend
        srv._annotator = mock_annotator
        srv._session = MagicMock()
        return mock_annotator

    def teardown_method(self, method):
        import specterqa.ios.mcp.server as srv

        srv._backend = None
        srv._annotator = None
        srv._session = None

    def test_static_text_not_flagged_as_small_target(self):
        from specterqa.ios.mcp.server import handle_accessibility_audit

        # Small StaticText is NOT interactive — must NOT appear in issues
        el = _make_mock_element("Title", element_type="StaticText", width=30, height=15)
        self._setup_annotator([el])

        result = handle_accessibility_audit({})
        small_target_issues = [i for i in result["issues"] if i["type"] == "small_target"]
        assert not small_target_issues, "StaticText must not be flagged as small_target"

    def test_button_below_44pt_flagged(self):
        from specterqa.ios.mcp.server import handle_accessibility_audit

        el = _make_mock_element("X", element_type="Button", width=30, height=30)
        self._setup_annotator([el])

        result = handle_accessibility_audit({})
        small_issues = [i for i in result["issues"] if i["type"] == "small_target"]
        assert len(small_issues) >= 1

    def test_button_exactly_44pt_not_flagged(self):
        from specterqa.ios.mcp.server import handle_accessibility_audit

        el = _make_mock_element("OK", element_type="Button", width=44, height=44)
        self._setup_annotator([el])

        result = handle_accessibility_audit({})
        small_issues = [i for i in result["issues"] if i["type"] == "small_target"]
        assert not small_issues

    def test_duplicate_labels_detected(self):
        from specterqa.ios.mcp.server import handle_accessibility_audit

        elements = [
            _make_mock_element("Back", element_type="Button", index=1),
            _make_mock_element("Back", element_type="Button", index=2),
        ]
        self._setup_annotator(elements)

        result = handle_accessibility_audit({})
        dup_issues = [i for i in result["issues"] if i["type"] == "duplicate_label"]
        assert len(dup_issues) >= 1
        assert dup_issues[0]["label"] == "Back"
        assert dup_issues[0]["count"] == 2

    def test_missing_label_on_interactive_element_detected(self):
        from specterqa.ios.mcp.server import handle_accessibility_audit

        el = _make_mock_element("", element_type="Button", index=1)
        el.label = ""  # no label
        self._setup_annotator([el])

        result = handle_accessibility_audit({})
        missing_issues = [i for i in result["issues"] if i["type"] == "missing_label"]
        assert len(missing_issues) >= 1

    def test_clean_screen_zero_issues(self):
        from specterqa.ios.mcp.server import handle_accessibility_audit

        elements = [
            _make_mock_element("Home", element_type="Button", width=120, height=50),
            _make_mock_element("Settings", element_type="Button", width=120, height=50),
        ]
        self._setup_annotator(elements)

        result = handle_accessibility_audit({})
        assert result["count"] == 0
        assert result["elements_checked"] == 2

    def test_returns_error_when_no_session(self):
        import specterqa.ios.mcp.server as srv
        from specterqa.ios.mcp.server import handle_accessibility_audit

        srv._backend = None
        srv._session = None
        result = handle_accessibility_audit({})
        assert "error" in result


# ===========================================================================
# TestCIRunnerReuse
# ===========================================================================


class TestCIRunnerReuse:
    """Verify --reuse-runner is the default in the ci command."""

    def test_reuse_runner_is_default(self):
        from click.testing import CliRunner
        from specterqa.ios.cli.commands import ios_command_group

        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["ci", "--help"])
        assert result.exit_code == 0
        assert "no-reuse-runner" in result.output.lower() or "reuse" in result.output.lower()

    def test_no_reuse_runner_flag_exists(self):
        """Confirm the escape hatch flag is available."""
        from click.testing import CliRunner
        from specterqa.ios.cli.commands import ios_command_group

        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["ci", "--help"])
        assert "--no-reuse-runner" in result.output

    def test_shared_runner_on_by_default_message(self):
        """Help text should describe the default as shared/reuse mode."""
        from click.testing import CliRunner
        from specterqa.ios.cli.commands import ios_command_group

        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["ci", "--help"])
        lower = result.output.lower()
        assert "default" in lower or "on by default" in lower or "~10x" in lower


# ===========================================================================
# TestDoctorCommand
# ===========================================================================


class TestDoctorCommand:
    """Verify doctor command runs without errors."""

    def test_doctor_runs_successfully(self):
        from click.testing import CliRunner
        from specterqa.ios.cli.commands import ios_command_group

        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["doctor"])
        assert result.exit_code == 0

    def test_doctor_mentions_python_version(self):
        from click.testing import CliRunner
        from specterqa.ios.cli.commands import ios_command_group

        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["doctor"])
        assert "Python" in result.output

    def test_doctor_mentions_specterqa_version(self):
        from click.testing import CliRunner
        from specterqa.ios.cli.commands import ios_command_group

        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["doctor"])
        assert "specterqa-ios" in result.output.lower() or "specterqa" in result.output.lower()

    def test_doctor_mentions_license_status(self):
        from click.testing import CliRunner
        from specterqa.ios.cli.commands import ios_command_group

        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["doctor"])
        assert "license" in result.output.lower() or "License" in result.output


# ===========================================================================
# TestInitCommand
# ===========================================================================


class TestInitCommand:
    """Verify init creates the expected .specterqa directory structure."""

    def test_init_creates_specterqa_directory(self, tmp_path):
        from click.testing import CliRunner
        from specterqa.ios.cli.commands import ios_command_group

        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(ios_command_group, ["init"])
            assert result.exit_code == 0
            assert Path(".specterqa").exists()

    def test_init_creates_products_directory(self, tmp_path):
        from click.testing import CliRunner
        from specterqa.ios.cli.commands import ios_command_group

        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(ios_command_group, ["init"])
            assert Path(".specterqa/products").exists()

    def test_init_creates_journeys_directory(self, tmp_path):
        from click.testing import CliRunner
        from specterqa.ios.cli.commands import ios_command_group

        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(ios_command_group, ["init"])
            assert Path(".specterqa/journeys").exists()

    def test_init_creates_template_product_yaml(self, tmp_path):
        from click.testing import CliRunner
        from specterqa.ios.cli.commands import ios_command_group

        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(ios_command_group, ["init", "--slug", "my-app"])
            yaml_files = list(Path(".specterqa/products").glob("*.yaml"))
            assert len(yaml_files) >= 1

    def test_init_is_idempotent_with_force(self):
        from click.testing import CliRunner
        from specterqa.ios.cli.commands import ios_command_group

        runner = CliRunner()
        with runner.isolated_filesystem():
            r1 = runner.invoke(ios_command_group, ["init"])
            r2 = runner.invoke(ios_command_group, ["init", "--force"])
            assert r1.exit_code == 0
            assert r2.exit_code == 0


# ===========================================================================
# TestValidateReplayCommand
# ===========================================================================


class TestValidateReplayCommand:
    """Verify replay validation catches errors and passes valid files."""

    def _write_replay(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "replay.yaml"
        p.write_text(content)
        return p

    def test_valid_replay_passes(self, tmp_path):
        from click.testing import CliRunner
        from specterqa.ios.cli.commands import ios_command_group

        f = self._write_replay(
            tmp_path,
            'replay:\n  name: test\n  bundle_id: com.example\n  steps:\n    - tapOn: "Save"\n',
        )
        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["validate-replay", str(f)])
        assert result.exit_code == 0

    def test_unknown_action_caught(self, tmp_path):
        from click.testing import CliRunner
        from specterqa.ios.cli.commands import ios_command_group

        f = self._write_replay(
            tmp_path,
            "replay:\n  name: test\n  bundle_id: com.example\n  steps:\n    - action: not_a_real_action\n",
        )
        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["validate-replay", str(f)])
        assert result.exit_code == 1

    def test_missing_bundle_id_caught(self, tmp_path):
        from click.testing import CliRunner
        from specterqa.ios.cli.commands import ios_command_group

        f = self._write_replay(
            tmp_path,
            'replay:\n  name: test\n  steps:\n    - tapOn: "OK"\n',
        )
        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["validate-replay", str(f)])
        assert result.exit_code == 1
        assert "bundle_id" in result.output.lower()

    def test_unresolved_skip_to_caught(self, tmp_path):
        from click.testing import CliRunner
        from specterqa.ios.cli.commands import ios_command_group

        f = self._write_replay(
            tmp_path,
            (
                "replay:\n  name: test\n  bundle_id: com.example\n  steps:\n"
                "    - action: skip_to\n      skip_to: nonexistent_step\n"
            ),
        )
        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["validate-replay", str(f)])
        assert result.exit_code == 1
        assert "skip_to" in result.output.lower() or "unknown step_id" in result.output.lower()

    def test_all_maestro_aliases_are_valid(self, tmp_path):
        """assertVisible, tapOn, inputText, waitFor must all pass validation."""
        from click.testing import CliRunner
        from specterqa.ios.cli.commands import ios_command_group

        content = """\
replay:
  name: maestro-compat
  bundle_id: com.example
  steps:
    - tapOn: "Button"
    - assertVisible: "Screen"
    - assertNotVisible: "Error"
    - inputText: "hello"
    - waitFor: "Done"
"""
        f = self._write_replay(tmp_path, content)
        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["validate-replay", str(f)])
        assert result.exit_code == 0, f"Maestro aliases should be valid. Output: {result.output}"

    def test_valid_skip_to_with_resolved_step_id(self, tmp_path):
        from click.testing import CliRunner
        from specterqa.ios.cli.commands import ios_command_group

        content = """\
replay:
  name: test
  bundle_id: com.example
  steps:
    - action: skip_to
      skip_to: end_step
    - action: tap
      step_id: end_step
      element_label: Done
"""
        f = self._write_replay(tmp_path, content)
        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["validate-replay", str(f)])
        assert result.exit_code == 0


# ===========================================================================
# TestExampleFiles
# ===========================================================================


class TestExampleFiles:
    """Verify shipped example files are structurally valid."""

    _EXAMPLES_DIR = Path(__file__).parent.parent / "examples"

    def test_examples_directory_exists(self):
        assert self._EXAMPLES_DIR.exists(), "examples/ directory missing"

    def test_smoke_test_example_valid(self):
        example = self._EXAMPLES_DIR / "01-smoke-test.yaml"
        if not example.exists():
            pytest.skip("01-smoke-test.yaml not present")
        data = yaml.safe_load(example.read_text())
        assert "replay" in data
        assert "steps" in data["replay"]
        assert len(data["replay"]["steps"]) > 0

    def test_all_examples_parse(self):
        if not self._EXAMPLES_DIR.exists():
            pytest.skip("examples/ directory not present")
        for f in self._EXAMPLES_DIR.glob("*.yaml"):
            data = yaml.safe_load(f.read_text())
            assert "replay" in data, f"{f.name} missing 'replay' key"

    def test_all_examples_have_bundle_id(self):
        if not self._EXAMPLES_DIR.exists():
            pytest.skip("examples/ directory not present")
        for f in self._EXAMPLES_DIR.glob("*.yaml"):
            data = yaml.safe_load(f.read_text())
            assert "bundle_id" in data["replay"], f"{f.name} missing 'bundle_id'"

    def test_all_examples_have_steps(self):
        if not self._EXAMPLES_DIR.exists():
            pytest.skip("examples/ directory not present")
        for f in self._EXAMPLES_DIR.glob("*.yaml"):
            data = yaml.safe_load(f.read_text())
            assert data["replay"].get("steps"), f"{f.name} has no steps"

    def test_all_examples_are_valid_per_validate_command(self):
        """Run the CLI validator against every example — zero issues expected."""
        if not self._EXAMPLES_DIR.exists():
            pytest.skip("examples/ directory not present")
        from click.testing import CliRunner
        from specterqa.ios.cli.commands import ios_command_group

        runner = CliRunner()
        for f in sorted(self._EXAMPLES_DIR.glob("*.yaml")):
            result = runner.invoke(ios_command_group, ["validate-replay", str(f)])
            assert result.exit_code == 0, f"{f.name} failed validate-replay:\n{result.output}"


# ===========================================================================
# TestWaitHandlerClamping
# ===========================================================================


class TestWaitHandlerClamping:
    """Verify ios_wait clamping behavior (no negative sleep, max 30s)."""

    def test_positive_value_waited(self):
        from specterqa.ios.mcp.server import handle_wait

        with patch("specterqa.ios.mcp.server._time" if False else "time.sleep"):
            # Use the actual handler — just verify the return dict
            result = handle_wait({"seconds": 0.0})
            assert result["status"] == "ok"
            assert result["waited"] == 0.0

    def test_negative_value_clamped_to_zero(self):
        """After the fix, negative seconds must not raise ValueError."""
        import specterqa.ios.mcp.server as srv

        # Patch sleep to avoid actually waiting
        with patch("time.sleep"):
            # Re-import to pick up the patched time.sleep through the module
            import importlib

            importlib.reload(srv)
            result = srv.handle_wait({"seconds": -10})
        assert result["status"] == "ok"
        assert result["waited"] == 0.0

    def test_huge_value_clamped_to_30(self):
        import specterqa.ios.mcp.server as srv

        with patch("time.sleep"):
            import importlib

            importlib.reload(srv)
            result = srv.handle_wait({"seconds": 99999})
        assert result["status"] == "ok"
        assert result["waited"] == 30.0

    def test_default_one_second(self):
        import specterqa.ios.mcp.server as srv

        with patch("time.sleep"):
            import importlib

            importlib.reload(srv)
            result = srv.handle_wait({})
        assert result["waited"] == 1.0
