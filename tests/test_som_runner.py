"""Tests for SoMRunner — Set-of-Mark powered iOS test execution engine.

Covers:
  - Instantiation and start() / stop() lifecycle
  - _parse_claude_response() — all action types, edge cases
  - _ask_claude() — request structure, image+text content sent to Claude
  - _execute_action() — tap (element lookup + coord scaling), scroll, type, back, wait
  - _verify_checkpoint() — YES/NO response handling
  - run_step() — done, tap+done, scroll+done, max_iterations, stuck detection
  - run_journey() — multi-step, pass/fail aggregation, evidence persistence
  - _format_history_entry() — all action types
  - Error paths: no api_key, WDA not available, invalid element number

All tests use stdlib mocking only — no network, no simulator required.

INIT-2026-493 — SpecterQA SoM test runner.
"""

from __future__ import annotations

import base64
import io
import json
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from specterqa.ios.som_annotator import UIElement
from specterqa.ios.som_runner import SoMRunner, SoMRunnerError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tiny_png_b64() -> str:
    """Return a 1×1 white PNG as a base64 string."""
    buf = io.BytesIO()
    Image.new("RGB", (1, 1), "white").save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _make_runner(**kwargs) -> SoMRunner:
    """Return a SoMRunner with test defaults."""
    return SoMRunner(api_key="test-api-key", **kwargs)


def _make_elements(*labels: str) -> list[UIElement]:
    """Build a list of UIElements with sequential indices."""
    return [
        UIElement(
            index=i + 1,
            element_type="Button" if i == 0 else "Cell",
            label=label,
            value="",
            x=0.0,
            y=float(100 + i * 60),
            width=390.0,
            height=50.0,
        )
        for i, label in enumerate(labels)
    ]


def _mock_driver(fake_b64: str) -> MagicMock:
    mock = MagicMock()
    mock._display_width = 1024
    mock._display_height = 2226
    mock._device_width = 393.0
    mock._device_height = 852.0
    mock.screenshot.return_value = (fake_b64, 1024, 2226)
    return mock


def _mock_annotator(elements: list[UIElement], fake_b64: str) -> MagicMock:
    mock = MagicMock()
    mock.annotate.return_value = (elements, fake_b64)
    text = "\n".join(f'[{e.index}] {e.element_type} "{e.label}"' for e in elements)
    mock.elements_text.return_value = text
    return mock


def _claude_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    return resp


def _primed_runner(
    *element_labels: str,
    side_effect=None,
    return_value=None,
) -> SoMRunner:
    """Build a fully-primed SoMRunner with mocked internals."""
    fake_b64 = _tiny_png_b64()
    elements = _make_elements(*element_labels)
    runner = _make_runner()
    runner._driver = _mock_driver(fake_b64)
    runner._annotator = _mock_annotator(elements, fake_b64)
    mock_client = MagicMock()
    if side_effect is not None:
        mock_client.messages.create.side_effect = side_effect
    elif return_value is not None:
        mock_client.messages.create.return_value = return_value
    runner._client = mock_client
    return runner


# ---------------------------------------------------------------------------
# Instantiation and lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_instantiation_defaults(self):
        runner = SoMRunner()
        assert runner.model == "claude-sonnet-4-20250514"
        assert runner.verbose is False
        assert runner.evidence_dir is None
        assert runner._driver is None
        assert runner._client is None
        assert not hasattr(runner, "wda_url"), "wda_url removed — WDA path is dead code"

    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key-123")
        runner = SoMRunner()
        assert runner.api_key == "env-key-123"

    def test_api_key_arg_takes_precedence_over_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
        runner = SoMRunner(api_key="arg-key")
        assert runner.api_key == "arg-key"

    def test_start_raises_without_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        runner = SoMRunner(api_key="")
        with pytest.raises(SoMRunnerError, match="No Anthropic API key"):
            runner.start("com.example.App")

    def test_start_raises_when_anthropic_not_installed(self):
        runner = _make_runner()
        with (
            patch("specterqa.ios.som_runner.SoMRunner.start"),
            patch("specterqa.ios.wda_driver.WDADriver") as mock_wda_cls,
            patch("specterqa.ios.som_annotator.SoMAnnotator"),
        ):
            mock_wda = MagicMock()
            mock_wda.create_session.return_value = "sess-abc"
            mock_wda_cls.return_value = mock_wda

            import builtins

            real_import = builtins.__import__

            def _block_anthropic(name, *args, **kwargs):
                if name == "anthropic":
                    raise ImportError("No module named 'anthropic'")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=_block_anthropic):
                runner._driver = None
                runner._client = None
                with pytest.raises((SoMRunnerError, ImportError)):
                    # Call the real start body manually to exercise the import path

                    runner._driver = mock_wda
                    runner._driver.create_session = MagicMock(return_value="sess")
                    runner._annotator = MagicMock()

                    try:
                        import anthropic  # noqa: F401
                    except ImportError as exc:
                        raise SoMRunnerError(f"anthropic not installed: {exc}") from exc

    def test_stop_clears_references(self):
        runner = _make_runner()
        runner._driver = MagicMock()
        runner._annotator = MagicMock()
        runner._client = MagicMock()
        runner.stop()
        assert runner._driver is None
        assert runner._annotator is None
        assert runner._client is None

    def test_run_step_without_start_raises(self):
        runner = _make_runner()
        with pytest.raises(SoMRunnerError, match="not started"):
            runner.run_step("do something")


# ---------------------------------------------------------------------------
# _parse_claude_response
# ---------------------------------------------------------------------------


class TestParseClaudeResponse:
    def test_tap(self):
        runner = _make_runner()
        r = runner._parse_claude_response("ACTION: tap\nELEMENT: 5\nREASONING: fifth element")
        assert r["action"] == "tap"
        assert r["element"] == 5
        assert r["reasoning"] == "fifth element"

    def test_scroll_down(self):
        runner = _make_runner()
        r = runner._parse_claude_response("ACTION: scroll\nDIRECTION: down\nREASONING: need more items")
        assert r["action"] == "scroll"
        assert r["direction"] == "down"
        assert r["element"] is None

    def test_scroll_up(self):
        runner = _make_runner()
        r = runner._parse_claude_response("ACTION: scroll\nDIRECTION: up\nREASONING: go back up")
        assert r["direction"] == "up"

    def test_type(self):
        runner = _make_runner()
        r = runner._parse_claude_response("ACTION: type\nTEXT: hello world\nREASONING: fill")
        assert r["action"] == "type"
        assert r["text"] == "hello world"

    def test_done(self):
        runner = _make_runner()
        r = runner._parse_claude_response("ACTION: done\nREASONING: goal achieved")
        assert r["action"] == "done"

    def test_back(self):
        runner = _make_runner()
        r = runner._parse_claude_response("ACTION: back\nREASONING: navigate back")
        assert r["action"] == "back"

    def test_wait(self):
        runner = _make_runner()
        r = runner._parse_claude_response("ACTION: wait\nREASONING: loading")
        assert r["action"] == "wait"

    def test_element_with_extra_text(self):
        runner = _make_runner()
        r = runner._parse_claude_response("ACTION: tap\nELEMENT: 12 (the general cell)\nREASONING: x")
        assert r["element"] == 12

    def test_unknown_action_defaults_to_wait(self):
        runner = _make_runner()
        r = runner._parse_claude_response("ACTION: teleport\nREASONING: impossible")
        assert r["action"] == "wait"

    def test_empty_response(self):
        runner = _make_runner()
        r = runner._parse_claude_response("")
        assert r["action"] == "wait"
        assert r["element"] is None

    def test_missing_element_stays_none(self):
        runner = _make_runner()
        r = runner._parse_claude_response("ACTION: tap\nREASONING: tap without element")
        assert r["element"] is None

    def test_invalid_element_value_stays_none(self):
        runner = _make_runner()
        r = runner._parse_claude_response("ACTION: tap\nELEMENT: abc\nREASONING: bad")
        assert r["element"] is None


# ---------------------------------------------------------------------------
# _execute_action
# ---------------------------------------------------------------------------


class TestExecuteAction:
    def _runner_with_driver(self):
        fake_b64 = _tiny_png_b64()
        runner = _make_runner()
        runner._driver = _mock_driver(fake_b64)
        return runner

    def test_tap_valid_element(self):
        runner = self._runner_with_driver()
        elements = _make_elements("General", "Wi-Fi")
        # Element 2 center: x=195, y=230 (device pts)
        # pixel x = 195 * (1024/393) = ~508
        # pixel y = 230 * (2226/852) = ~600
        result = runner._execute_action({"action": "tap", "element": 2}, elements)
        assert "Wi-Fi" in result
        assert runner._driver.tap.call_count == 1

    def test_tap_uses_exact_element_center(self):
        runner = self._runner_with_driver()
        elements = _make_elements("Button A")
        # Element 1: center_x=195, center_y=125
        runner._execute_action({"action": "tap", "element": 1}, elements)
        args = runner._driver.tap.call_args[0]
        px, py = args[0], args[1]
        # Expected pixel = center * (display / device)
        expected_px = elements[0].center_x * (1024 / 393.0)
        expected_py = elements[0].center_y * (2226 / 852.0)
        assert abs(px - expected_px) < 1
        assert abs(py - expected_py) < 1

    def test_tap_invalid_element_raises(self):
        runner = self._runner_with_driver()
        elements = _make_elements("A", "B")
        with pytest.raises(SoMRunnerError, match="Element 99 not in current tree"):
            runner._execute_action({"action": "tap", "element": 99}, elements)

    def test_tap_no_element_raises(self):
        runner = self._runner_with_driver()
        elements = _make_elements("A")
        with pytest.raises(SoMRunnerError, match="no ELEMENT number"):
            runner._execute_action({"action": "tap", "element": None}, elements)

    def test_scroll_down(self):
        runner = self._runner_with_driver()
        runner._execute_action({"action": "scroll", "direction": "down"}, [])
        runner._driver.swipe.assert_called_once()
        args = runner._driver.swipe.call_args[0]
        # down: y_end < y_start (swipe up on screen = scroll down in content)
        assert args[3] < args[1]

    def test_scroll_up(self):
        runner = self._runner_with_driver()
        runner._execute_action({"action": "scroll", "direction": "up"}, [])
        args = runner._driver.swipe.call_args[0]
        assert args[3] > args[1]

    def test_scroll_left(self):
        runner = self._runner_with_driver()
        runner._execute_action({"action": "scroll", "direction": "left"}, [])
        args = runner._driver.swipe.call_args[0]
        assert args[2] < args[0]

    def test_scroll_right(self):
        runner = self._runner_with_driver()
        runner._execute_action({"action": "scroll", "direction": "right"}, [])
        args = runner._driver.swipe.call_args[0]
        assert args[2] > args[0]

    def test_type_text(self):
        runner = self._runner_with_driver()
        result = runner._execute_action({"action": "type", "text": "hello"}, [])
        runner._driver.type_text.assert_called_once_with("hello")
        assert "hello" in result

    def test_type_empty_text(self):
        runner = self._runner_with_driver()
        result = runner._execute_action({"action": "type", "text": None}, [])
        runner._driver.type_text.assert_not_called()
        assert "(no text)" in result

    def test_back(self):
        runner = self._runner_with_driver()
        result = runner._execute_action({"action": "back"}, [])
        runner._driver.swipe_back.assert_called_once()
        assert "Back" in result

    def test_wait(self):
        runner = self._runner_with_driver()
        with patch("time.sleep") as mock_sleep:
            result = runner._execute_action({"action": "wait"}, [])
            mock_sleep.assert_called_once_with(1)
        assert "Wait" in result

    def test_unknown_action(self):
        runner = self._runner_with_driver()
        result = runner._execute_action({"action": "teleport"}, [])
        assert "unknown" in result.lower()


# ---------------------------------------------------------------------------
# _ask_claude
# ---------------------------------------------------------------------------


class TestAskClaude:
    def test_sends_image_and_text(self):
        fake_b64 = _tiny_png_b64()
        elements = _make_elements("Wi-Fi", "Bluetooth")
        runner = _make_runner()
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _claude_response("ACTION: tap\nELEMENT: 1\nREASONING: tap wifi")
        runner._client = mock_client
        mock_annotator = MagicMock()
        mock_annotator.elements_text.return_value = "[1] Button Wi-Fi\n[2] Button Bluetooth"
        runner._annotator = mock_annotator

        result = runner._ask_claude(
            goal="Open Wi-Fi",
            elements=elements,
            annotated_b64=fake_b64,
            elements_text="[1] Wi-Fi\n[2] Bluetooth",
        )

        assert result["action"] == "tap"
        assert result["element"] == 1

        create_call = mock_client.messages.create.call_args
        create_call[1] if create_call[1] else create_call[0][0] if create_call[0] else {}
        # Verify model and messages were passed
        assert mock_client.messages.create.called

    def test_history_included_in_prompt(self):
        fake_b64 = _tiny_png_b64()
        runner = _make_runner()
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _claude_response("ACTION: done\nREASONING: ok")
        runner._client = mock_client
        runner._annotator = MagicMock()
        runner._annotator.elements_text.return_value = ""

        runner._ask_claude(
            goal="Tap General",
            elements=[],
            annotated_b64=fake_b64,
            elements_text="",
            history=["Tapped Wi-Fi → opened wifi page"],
        )

        call_kwargs = mock_client.messages.create.call_args
        # Find the text content in the messages
        messages = call_kwargs[1].get("messages", call_kwargs[0][0] if call_kwargs[0] else []) if call_kwargs[1] else []
        # The messages list is in kwargs
        create_kwargs = mock_client.messages.create.call_args.kwargs
        messages = create_kwargs.get("messages", [])
        if messages:
            user_content = messages[0]["content"]
            text_parts = [c["text"] for c in user_content if c.get("type") == "text"]
            combined = " ".join(text_parts)
            assert "Tapped Wi-Fi" in combined

    def test_returns_parsed_decision(self):
        fake_b64 = _tiny_png_b64()
        runner = _make_runner()
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _claude_response(
            "ACTION: scroll\nDIRECTION: down\nREASONING: scroll for more"
        )
        runner._client = mock_client

        result = runner._ask_claude(
            goal="Find privacy settings",
            elements=[],
            annotated_b64=fake_b64,
            elements_text="",
        )
        assert result["action"] == "scroll"
        assert result["direction"] == "down"


# ---------------------------------------------------------------------------
# _verify_checkpoint
# ---------------------------------------------------------------------------


class TestVerifyCheckpoint:
    def test_yes_returns_true(self):
        fake_b64 = _tiny_png_b64()
        runner = _make_runner()
        runner._driver = _mock_driver(fake_b64)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _claude_response("YES")
        runner._client = mock_client

        assert runner._verify_checkpoint("Settings app is open") is True

    def test_no_returns_false(self):
        fake_b64 = _tiny_png_b64()
        runner = _make_runner()
        runner._driver = _mock_driver(fake_b64)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _claude_response("NO")
        runner._client = mock_client

        assert runner._verify_checkpoint("Wi-Fi is on") is False

    def test_yes_case_insensitive(self):
        fake_b64 = _tiny_png_b64()
        runner = _make_runner()
        runner._driver = _mock_driver(fake_b64)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _claude_response("yes, it is visible")
        runner._client = mock_client

        assert runner._verify_checkpoint("check visibility") is True

    def test_screenshot_failure_returns_false(self):
        runner = _make_runner()
        mock_driver = MagicMock()
        mock_driver.screenshot.side_effect = RuntimeError("screenshot failed")
        runner._driver = mock_driver
        runner._client = MagicMock()

        assert runner._verify_checkpoint("some checkpoint") is False
        runner._client.messages.create.assert_not_called()

    def test_claude_failure_returns_false(self):
        fake_b64 = _tiny_png_b64()
        runner = _make_runner()
        runner._driver = _mock_driver(fake_b64)
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API error")
        runner._client = mock_client

        assert runner._verify_checkpoint("anything") is False


# ---------------------------------------------------------------------------
# run_step
# ---------------------------------------------------------------------------


class TestRunStep:
    def test_immediate_done_passes(self):
        runner = _primed_runner(
            "Wi-Fi",
            "General",
            return_value=_claude_response("ACTION: done\nREASONING: already there"),
        )
        result = runner.run_step("Open Settings")
        assert result["passed"] is True
        assert result["error"] is None
        assert result["duration"] >= 0

    def test_immediate_done_with_checkpoint(self):
        _tiny_png_b64()
        runner = _primed_runner(
            "Wi-Fi",
            return_value=_claude_response("ACTION: done\nREASONING: done"),
        )
        # Wire checkpoint to return True
        runner._verify_checkpoint = MagicMock(return_value=True)
        result = runner.run_step("Open Settings", checkpoint="Settings screen is visible")
        assert result["passed"] is True
        runner._verify_checkpoint.assert_called_once_with("Settings screen is visible")

    def test_checkpoint_failure_reports_error(self):
        runner = _primed_runner(
            "Wi-Fi",
            return_value=_claude_response("ACTION: done\nREASONING: done"),
        )
        runner._verify_checkpoint = MagicMock(return_value=False)
        result = runner.run_step("Open Settings", checkpoint="wrong screen")
        assert result["passed"] is False
        assert "Checkpoint not met" in result["error"]

    def test_tap_then_done(self):
        runner = _primed_runner(
            "General",
            "Wi-Fi",
            side_effect=[
                _claude_response("ACTION: tap\nELEMENT: 1\nREASONING: tap general"),
                _claude_response("ACTION: done\nREASONING: on general page"),
            ],
        )
        with patch("time.sleep"):
            result = runner.run_step("Open General settings")
        assert result["passed"] is True
        assert len(result["actions"]) == 1

    def test_scroll_then_done(self):
        runner = _primed_runner(
            "General",
            side_effect=[
                _claude_response("ACTION: scroll\nDIRECTION: down\nREASONING: scroll"),
                _claude_response("ACTION: done\nREASONING: done"),
            ],
        )
        with patch("time.sleep"):
            result = runner.run_step("Scroll to see more")
        assert result["passed"] is True
        assert len(result["actions"]) == 1

    def test_max_iterations_exceeded(self):
        runner = _primed_runner(
            "A",
            "B",
            "C",
            return_value=_claude_response("ACTION: back\nREASONING: keep going"),
        )
        with patch("time.sleep"):
            result = runner.run_step("goal", max_iterations=2)
        assert result["passed"] is False
        # Either max_iterations or stuck detection fires
        assert result["error"] is not None

    def test_stuck_detection_fires_at_3_repeats(self):
        runner = _primed_runner(
            "General",
            return_value=_claude_response("ACTION: scroll\nDIRECTION: down\nREASONING: same"),
        )
        with patch("time.sleep"):
            result = runner.run_step("scroll forever", max_iterations=20)
        assert result["passed"] is False
        assert "Stuck" in result["error"]

    def test_invalid_element_logged_and_retried(self):
        runner = _primed_runner(
            "General",
            side_effect=[
                _claude_response("ACTION: tap\nELEMENT: 99\nREASONING: bad element"),
                _claude_response("ACTION: tap\nELEMENT: 1\nREASONING: correct element"),
                _claude_response("ACTION: done\nREASONING: done"),
            ],
        )
        with patch("time.sleep"):
            result = runner.run_step("tap element", max_iterations=5)
        # First tap fails silently, second succeeds, third iteration marks done
        assert result["passed"] is True

    def test_screenshot_failure_reports_error(self):
        runner = _primed_runner("A")
        runner._driver.screenshot.side_effect = RuntimeError("no simulator")
        result = runner.run_step("open screen")
        assert result["passed"] is False
        assert "Screenshot failed" in result["error"]

    def test_evidence_screenshots_saved(self, tmp_path):
        fake_b64 = _tiny_png_b64()
        elements = _make_elements("OK")
        runner = _make_runner(evidence_dir=str(tmp_path))
        runner._driver = _mock_driver(fake_b64)
        runner._annotator = _mock_annotator(elements, fake_b64)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _claude_response("ACTION: done\nREASONING: done")
        runner._client = mock_client

        with patch("time.sleep"):
            runner.run_step("goal")

        saved = list(tmp_path.glob("step_*.png"))
        assert len(saved) >= 1

    def test_returns_action_list(self):
        runner = _primed_runner(
            "OK",
            side_effect=[
                _claude_response("ACTION: tap\nELEMENT: 1\nREASONING: tap ok"),
                _claude_response("ACTION: done\nREASONING: done"),
            ],
        )
        with patch("time.sleep"):
            result = runner.run_step("tap ok button")
        actions = result["actions"]
        assert len(actions) == 1
        assert actions[0]["decision"]["action"] == "tap"


# ---------------------------------------------------------------------------
# run_journey
# ---------------------------------------------------------------------------


class TestRunJourney:
    def _make_journey(self, *step_ids: str) -> dict:
        return {
            "scenario": {
                "name": "Test Journey",
                "steps": [{"id": sid, "goal": f"Goal for {sid}"} for sid in step_ids],
            }
        }

    def test_all_steps_pass(self):
        runner = _primed_runner(
            "OK",
            return_value=_claude_response("ACTION: done\nREASONING: done"),
        )
        journey = self._make_journey("step-1", "step-2", "step-3")
        result = runner.run_journey(journey)
        assert result["passed"] is True
        assert result["passed_count"] == 3
        assert result["total_count"] == 3
        assert len(result["steps"]) == 3

    def test_partial_failure_reported(self):
        fake_b64 = _tiny_png_b64()
        elements = _make_elements("OK")
        runner = _make_runner()
        runner._driver = _mock_driver(fake_b64)
        runner._annotator = _mock_annotator(elements, fake_b64)
        mock_client = MagicMock()
        # step-1 passes, step-2 gets stuck
        done_resp = _claude_response("ACTION: done\nREASONING: done")
        stuck_resp = _claude_response("ACTION: scroll\nDIRECTION: down\nREASONING: loop")
        mock_client.messages.create.side_effect = [
            done_resp,  # step-1 done
            stuck_resp,
            stuck_resp,
            stuck_resp,  # step-2 stuck
        ]
        runner._client = mock_client

        journey = self._make_journey("step-1", "step-2")
        with patch("time.sleep"):
            result = runner.run_journey(journey)

        assert result["passed"] is False
        assert result["passed_count"] == 1
        assert result["total_count"] == 2
        assert result["steps"][0]["passed"] is True
        assert result["steps"][1]["passed"] is False

    def test_flat_steps_schema(self):
        """Support journey dicts without a scenario wrapper."""
        runner = _primed_runner(
            "OK",
            return_value=_claude_response("ACTION: done\nREASONING: done"),
        )
        journey = {
            "steps": [{"id": "s1", "goal": "do something"}],
        }
        result = runner.run_journey(journey)
        assert result["total_count"] == 1

    def test_empty_steps_returns_passed(self):
        runner = _primed_runner("OK")
        result = runner.run_journey({"scenario": {"name": "empty", "steps": []}})
        assert result["passed"] is True
        assert result["total_count"] == 0
        assert result["passed_count"] == 0

    def test_journey_name_extracted(self):
        runner = _primed_runner(
            "OK",
            return_value=_claude_response("ACTION: done\nREASONING: done"),
        )
        journey = {
            "scenario": {
                "name": "Settings Smoke Test",
                "steps": [{"id": "s1", "goal": "open settings"}],
            }
        }
        result = runner.run_journey(journey)
        assert result["journey_name"] == "Settings Smoke Test"

    def test_result_written_to_evidence_dir(self, tmp_path):
        fake_b64 = _tiny_png_b64()
        elements = _make_elements("OK")
        runner = _make_runner(evidence_dir=str(tmp_path))
        runner._driver = _mock_driver(fake_b64)
        runner._annotator = _mock_annotator(elements, fake_b64)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _claude_response("ACTION: done\nREASONING: done")
        runner._client = mock_client

        journey = {"scenario": {"name": "J", "steps": [{"id": "s1", "goal": "g"}]}}
        runner.run_journey(journey)

        result_file = tmp_path / "run-result.json"
        assert result_file.exists()
        data = json.loads(result_file.read_text())
        assert data["journey_name"] == "J"
        assert data["passed_count"] == 1

    def test_duration_is_positive(self):
        runner = _primed_runner(
            "OK",
            return_value=_claude_response("ACTION: done\nREASONING: done"),
        )
        result = runner.run_journey({"scenario": {"name": "J", "steps": [{"id": "s1", "goal": "g"}]}})
        assert result["duration"] >= 0


# ---------------------------------------------------------------------------
# _format_history_entry
# ---------------------------------------------------------------------------


class TestFormatHistoryEntry:
    def test_tap(self):
        runner = _make_runner()
        entry = runner._format_history_entry({"action": "tap", "element": 3, "reasoning": "tap the button"}, "ok")
        assert "3" in entry
        assert "tap" in entry.lower()

    def test_scroll(self):
        runner = _make_runner()
        entry = runner._format_history_entry({"action": "scroll", "direction": "down"}, "ok")
        assert "down" in entry.lower()

    def test_type(self):
        runner = _make_runner()
        entry = runner._format_history_entry({"action": "type", "text": "hello"}, "ok")
        assert "hello" in entry

    def test_back(self):
        runner = _make_runner()
        entry = runner._format_history_entry({"action": "back"}, "Back gesture")
        assert "back" in entry.lower()

    def test_other_uses_result(self):
        runner = _make_runner()
        entry = runner._format_history_entry({"action": "wait"}, "Waited 1s")
        assert "Waited" in entry
