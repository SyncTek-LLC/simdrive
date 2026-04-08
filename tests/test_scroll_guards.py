"""Tests for scroll-stuck prevention guards (INIT-2026-507).

Guards implemented in this staging branch
------------------------------------------
Guard 1 — Pre-scroll visibility check
    ``SoMRunner._is_element_visible(target_description: str, element_tree_xml: str) -> bool``
    Returns True when the target label (substring, case-insensitive) is found
    with non-zero width/height inside the XML element tree.  Skip the scroll
    when True — the element is already on screen.

Guard 2 — Scroll state-change detection
    ``SoMAnnotator._screen_changed(before_tree: str, after_tree: str) -> bool``
    Returns False when >80% of (label, y_bucket) pairs overlap between the two
    trees, meaning the screen is effectively unchanged after the scroll.
    Returns True (changed) in all other cases, including parse failures.
    Empty trees are treated as "changed" (safe default).

Guard 3 — Max consecutive scroll cap
    ``MAX_CONSECUTIVE_SCROLLS = 5``  (module-level constant in som_runner)
    ``SoMRunner`` increments ``_scroll_count`` for each scroll action and resets
    it to 0 on any non-scroll action.  When the cap is reached the loop breaks
    with error "Max consecutive scrolls (5) reached".

All tests use stdlib mocking only — no network, no simulator required.
"""

from __future__ import annotations

import base64
import io
import logging
import textwrap
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from specterqa.ios.som_annotator import SoMAnnotator, UIElement
from specterqa.ios.som_runner import MAX_CONSECUTIVE_SCROLLS, SoMRunner, SoMRunnerError


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _tiny_png_b64(color: str = "white") -> str:
    """Return a 1×1 PNG of *color* as a base64 string."""
    buf = io.BytesIO()
    Image.new("RGB", (1, 1), color).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _make_runner(**kwargs) -> SoMRunner:
    """Return a SoMRunner pre-configured for unit tests (no real API key)."""
    return SoMRunner(api_key="test-key", **kwargs)


def _wire_runner(runner: SoMRunner) -> tuple[MagicMock, MagicMock]:
    """Attach mock driver + annotator to *runner*; return (driver, annotator)."""
    driver = MagicMock()
    driver._display_width = 390
    driver._display_height = 844
    driver._device_width = 390
    driver._device_height = 844
    driver.screenshot.return_value = (_tiny_png_b64(), 390, 844)

    annotator = MagicMock(spec=SoMAnnotator)
    annotator.annotate.return_value = ([], _tiny_png_b64())
    annotator.elements_text.return_value = "(no elements detected)"

    runner._driver = driver
    runner._annotator = annotator
    return driver, annotator


def _make_elements(*labels: str) -> list[UIElement]:
    """Build UIElement objects from a sequence of label strings."""
    return [
        UIElement(
            index=i + 1,
            element_type="Cell",
            label=label,
            value="",
            x=0.0,
            y=float(i * 44),
            width=390.0,
            height=44.0,
        )
        for i, label in enumerate(labels)
    ]


def _xml_with_elements(*labels: str) -> str:
    """Minimal WDA-style XML containing one Cell per label."""
    cells = "\n".join(
        f'  <XCUIElementTypeCell label="{label}" name="{label}" '
        f'enabled="true" visible="true" '
        f'x="0" y="{i * 44}" width="390" height="44" />'
        for i, label in enumerate(labels)
    )
    return f"<AppElement>\n{cells}\n</AppElement>"


def _xml_empty() -> str:
    return "<AppElement></AppElement>"


def _xml_with_zero_size(label: str) -> str:
    """XML with a matching label but zero width/height — should be invisible."""
    return (
        f"<AppElement>"
        f'<XCUIElementTypeCell label="{label}" name="{label}" '
        f'enabled="true" visible="true" x="0" y="0" width="0" height="0" />'
        f"</AppElement>"
    )


def _claude_resp(text: str) -> MagicMock:
    m = MagicMock()
    m.content = [MagicMock(text=text)]
    return m


# ---------------------------------------------------------------------------
# Guard 1: Pre-scroll visibility check
# ---------------------------------------------------------------------------


class TestPreScrollVisibilityCheck:
    """Unit tests for SoMRunner._is_element_visible.

    Contract:
    - Returns True when an XML node whose label CONTAINS the target string
      (case-insensitive substring) is found AND has width > 0 AND height > 0.
    - Returns False otherwise: absent label, zero-size element, empty XML,
      invalid XML, or empty target string.
    """

    def test_returns_true_when_element_present_with_size(self):
        """Label found with non-zero dimensions → True."""
        runner = _make_runner()
        xml = _xml_with_elements("Wi-Fi", "Bluetooth", "General", "Settings")
        assert runner._is_element_visible("Settings", xml) is True

    def test_returns_false_when_element_absent(self):
        """Label not in the XML tree → False."""
        runner = _make_runner()
        xml = _xml_with_elements("Wi-Fi", "Bluetooth", "General")
        assert runner._is_element_visible("Settings", xml) is False

    def test_visibility_check_is_case_insensitive(self):
        """Matching is case-insensitive to handle iOS label casing quirks."""
        runner = _make_runner()
        xml = _xml_with_elements("Settings", "wi-fi")

        assert runner._is_element_visible("settings", xml) is True
        assert runner._is_element_visible("SETTINGS", xml) is True
        assert runner._is_element_visible("WI-FI", xml) is True

    def test_partial_substring_is_matched(self):
        """Implementation uses substring matching — partial target matches too.

        'Account' should match the element labelled 'Account Settings'
        because the implementation checks ``needle in label``.
        """
        runner = _make_runner()
        xml = _xml_with_elements("Account Settings")
        assert runner._is_element_visible("Account", xml) is True

    def test_visibility_check_handles_empty_xml(self):
        """Empty element tree → False (nothing visible)."""
        runner = _make_runner()
        assert runner._is_element_visible("Settings", _xml_empty()) is False

    def test_visibility_check_handles_none_xml(self):
        """None tree must return False gracefully without raising."""
        runner = _make_runner()
        assert runner._is_element_visible("Settings", None) is False  # type: ignore[arg-type]

    def test_empty_target_always_returns_false(self):
        """An empty (or whitespace-only) target string returns False immediately."""
        runner = _make_runner()
        xml = _xml_with_elements("Settings")
        assert runner._is_element_visible("", xml) is False
        assert runner._is_element_visible("   ", xml) is False

    def test_returns_false_when_element_has_zero_width(self):
        """Zero width is treated as invisible — guard must return False."""
        runner = _make_runner()
        xml = (
            "<AppElement>"
            '<XCUIElementTypeCell label="Hidden" name="Hidden" '
            'enabled="true" visible="true" x="0" y="0" width="0" height="44" />'
            "</AppElement>"
        )
        assert runner._is_element_visible("Hidden", xml) is False

    def test_returns_false_when_element_has_zero_height(self):
        """Zero height is treated as invisible — guard must return False."""
        runner = _make_runner()
        xml = (
            "<AppElement>"
            '<XCUIElementTypeCell label="Hidden" name="Hidden" '
            'enabled="true" visible="true" x="0" y="0" width="200" height="0" />'
            "</AppElement>"
        )
        assert runner._is_element_visible("Hidden", xml) is False

    def test_invalid_xml_returns_false(self):
        """Malformed XML must return False rather than raising an exception."""
        runner = _make_runner()
        assert runner._is_element_visible("Settings", "<<<not xml>>>") is False

    def test_uses_name_attribute_as_fallback(self):
        """When ``label`` is absent, ``name`` attribute should be used instead."""
        runner = _make_runner()
        xml = (
            "<AppElement>"
            '<XCUIElementTypeCell name="Settings" '
            'enabled="true" visible="true" x="0" y="0" width="200" height="44" />'
            "</AppElement>"
        )
        assert runner._is_element_visible("Settings", xml) is True


# ---------------------------------------------------------------------------
# Guard 2: Scroll state-change detection
# ---------------------------------------------------------------------------


class TestScrollStateChangeDetection:
    """Unit tests for SoMAnnotator._screen_changed.

    Contract (80% overlap threshold using (label, y_bucket) signatures):
    - Returns False when >80% of signatures overlap (screen is unchanged).
    - Returns True when signatures differ significantly (screen changed).
    - Empty before- or after-tree → True (safe "changed" default).
    - Both trees empty (no labelled nodes) → True (safe default).
    - Parse failure on either side → True (safe default).
    """

    def test_detects_no_change_at_boundary(self):
        """Identical trees → False (scroll boundary, content did not move)."""
        annotator = SoMAnnotator()
        xml = _xml_with_elements("Wi-Fi", "Bluetooth", "General")
        assert annotator._screen_changed(xml, xml) is False

    def test_detects_change_when_new_elements_appear(self):
        """Completely different element labels → True."""
        annotator = SoMAnnotator()
        before = _xml_with_elements("Wi-Fi", "Bluetooth")
        after = _xml_with_elements("General", "Privacy", "Accessibility")
        assert annotator._screen_changed(before, after) is True

    def test_detects_change_when_positions_shift_significantly(self):
        """Same label but Y shifts by > 1 bucket (bucket = round(y/10)) → True."""
        annotator = SoMAnnotator()
        before = textwrap.dedent("""\
            <AppElement>
              <XCUIElementTypeCell label="General" enabled="true" visible="true"
                x="0" y="100" width="390" height="44" />
              <XCUIElementTypeCell label="Privacy" enabled="true" visible="true"
                x="0" y="144" width="390" height="44" />
            </AppElement>""")
        # After scroll: elements shifted up by ~400 points — different y_bucket
        after = textwrap.dedent("""\
            <AppElement>
              <XCUIElementTypeCell label="General" enabled="true" visible="true"
                x="0" y="56" width="390" height="44" />
              <XCUIElementTypeCell label="Privacy" enabled="true" visible="true"
                x="0" y="100" width="390" height="44" />
            </AppElement>""")
        # y_buckets: (100→10, 144→14) vs (56→6, 100→10) — only 1 of 2 overlap
        # overlap_ratio = 1/2 = 0.5 ≤ 0.80 → True (changed)
        assert annotator._screen_changed(before, after) is True

    def test_empty_before_tree_returns_true(self):
        """Empty before-tree with non-empty after-tree → True (safe default)."""
        annotator = SoMAnnotator()
        after = _xml_with_elements("Wi-Fi")
        # _xml_empty() has a root node labelled "App" which has y=0 so signature
        # set is non-empty ({"App", 0}).  A single-element after-tree will differ.
        # Use a truly label-free XML to get an empty signature set.
        truly_empty = "<AppElement></AppElement>"
        assert annotator._screen_changed(truly_empty, after) is True

    def test_empty_after_tree_returns_true(self):
        """Non-empty before-tree with no-label after-tree → True."""
        annotator = SoMAnnotator()
        before = _xml_with_elements("Wi-Fi")
        truly_empty = "<AppElement></AppElement>"
        assert annotator._screen_changed(before, truly_empty) is True

    def test_invalid_xml_returns_true(self):
        """Malformed XML on either side → True (conservative, safe default)."""
        annotator = SoMAnnotator()
        valid = _xml_with_elements("Settings")
        assert annotator._screen_changed("<<<bad>>>", valid) is True
        assert annotator._screen_changed(valid, "<<<bad>>>") is True

    def test_high_overlap_above_threshold_returns_false(self):
        """Same XML twice — 100% overlap — must return False."""
        annotator = SoMAnnotator()
        xml = _xml_with_elements("Notifications", "Sounds", "Do Not Disturb")
        assert annotator._screen_changed(xml, xml) is False

    def test_minor_addition_below_threshold_returns_false(self):
        """9 shared items + 1 new item → overlap = 9/10 = 90% > 80% → False.

        One new element scrolling into view should not register as a full
        page change — the majority of the screen is unchanged.
        """
        annotator = SoMAnnotator()
        shared_cells = "".join(
            f'<XCUIElementTypeCell label="Item{i}" name="Item{i}" '
            f'x="0" y="{i * 44}" width="390" height="44" '
            f'enabled="true" visible="true"/>'
            for i in range(9)
        )
        before = f"<AppElement>{shared_cells}</AppElement>"
        # After has same 9 items + one extra at the bottom
        extra = (
            '<XCUIElementTypeCell label="NewItem" name="NewItem" '
            'x="0" y="396" width="390" height="44" '
            'enabled="true" visible="true"/>'
        )
        after = f"<AppElement>{shared_cells}{extra}</AppElement>"
        # before signatures: 9 items; after signatures: 10 items
        # overlap = 9; denominator = max(9, 10) = 10; ratio = 0.9 > 0.80 → False
        assert annotator._screen_changed(before, after) is False

    def test_y_bucket_resolution(self):
        """Elements within the same 10-pt bucket are treated as the same position.

        Bucket = round(y / 10).  y=100 → bucket 10; y=104 → round(10.4) = 10.
        Both fall in the same bucket, so the signatures are identical → False.

        Note: y=109 would give round(10.9) = 11 — a different bucket — so we
        use y=104 here to stay firmly within bucket 10.
        """
        annotator = SoMAnnotator()
        # y=100 and y=104 both produce bucket round(100/10)=10 / round(104/10)=10
        before = (
            "<AppElement>"
            '<XCUIElementTypeCell label="X" enabled="true" visible="true" '
            'x="0" y="100" width="390" height="44" />'
            "</AppElement>"
        )
        after = (
            "<AppElement>"
            '<XCUIElementTypeCell label="X" enabled="true" visible="true" '
            'x="0" y="104" width="390" height="44" />'
            "</AppElement>"
        )
        # Both produce signature ("X", 10) → identical → unchanged → False
        assert annotator._screen_changed(before, after) is False


# ---------------------------------------------------------------------------
# Guard 3: MAX_CONSECUTIVE_SCROLLS constant
# ---------------------------------------------------------------------------


class TestMaxScrollCap:
    """Tests for the MAX_CONSECUTIVE_SCROLLS module constant and its enforcement."""

    def test_constant_is_defined(self):
        """MAX_CONSECUTIVE_SCROLLS must be importable from som_runner."""
        from specterqa.ios.som_runner import MAX_CONSECUTIVE_SCROLLS as cap  # noqa: F401

    def test_constant_is_positive_integer(self):
        assert isinstance(MAX_CONSECUTIVE_SCROLLS, int)
        assert MAX_CONSECUTIVE_SCROLLS > 0

    def test_constant_equals_5(self):
        assert MAX_CONSECUTIVE_SCROLLS == 5

    def test_caps_at_5_consecutive_scrolls(self):
        """run_step must abort after MAX_CONSECUTIVE_SCROLLS consecutive scrolls."""
        runner = _make_runner()
        driver, annotator = _wire_runner(runner)

        # Distinct screenshots on every call so the OLD prefix-based stuck detector
        # never fires — only the new guard-3 counter should terminate the loop.
        def _rotating_screenshots():
            colors = ["white", "gray", "silver", "gainsboro", "whitesmoke"] * 30
            for c in colors:
                yield (_tiny_png_b64(c), 390, 844)

        driver.screenshot.side_effect = _rotating_screenshots()
        # get_element_tree returns the same empty tree every time (guard 2 uses it)
        annotator.get_element_tree.return_value = _xml_with_elements("Wi-Fi", "Bluetooth")
        # _screen_changed always says "changed" so guard 2 never fires
        annotator._screen_changed.return_value = True

        mock_client = MagicMock()
        runner._client = mock_client
        mock_client.messages.create.return_value = _claude_resp(
            "ACTION: scroll\nDIRECTION: down\nREASONING: target not visible"
        )

        with patch("time.sleep"):
            result = runner.run_step("Find Settings", max_iterations=20)

        assert result["passed"] is False
        assert result["error"] is not None
        assert "scroll" in result["error"].lower() or "5" in result["error"]
        # Guard fires at exactly MAX_CONSECUTIVE_SCROLLS — loop breaks shortly after
        assert len(result["actions"]) <= MAX_CONSECUTIVE_SCROLLS + 1

    def test_counter_resets_on_non_scroll_action(self):
        """A tap between scrolls resets the counter so the cap never fires."""
        runner = _make_runner()
        driver, annotator = _wire_runner(runner)

        # Two distinct PNG payloads so the old prefix-based detector sees change
        png_a = _tiny_png_b64("white")
        png_b = _tiny_png_b64("black")

        screenshots = [(png_a if i % 2 == 0 else png_b, 390, 844) for i in range(60)]
        driver.screenshot.side_effect = screenshots

        elements = _make_elements("Settings", "General")
        annotator.annotate.return_value = (elements, png_a)
        annotator.elements_text.return_value = "[1] Cell Settings\n[2] Cell General"
        annotator.get_element_tree.return_value = _xml_empty()
        annotator._screen_changed.return_value = True

        cap = MAX_CONSECUTIVE_SCROLLS
        # (cap-1) scrolls → tap (resets counter) → (cap-1) more scrolls → done
        decisions = (
            [_claude_resp("ACTION: scroll\nDIRECTION: down\nREASONING: looking")] * (cap - 1)
            + [_claude_resp("ACTION: tap\nELEMENT: 1\nREASONING: found it")]
            + [_claude_resp("ACTION: scroll\nDIRECTION: down\nREASONING: more")] * (cap - 1)
            + [_claude_resp("ACTION: done\nREASONING: reached goal")]
        )
        mock_client = MagicMock()
        runner._client = mock_client
        mock_client.messages.create.side_effect = decisions

        with patch("time.sleep"):
            result = runner.run_step("Find Settings", max_iterations=30)

        # The tap reset the counter — guard 3 must NOT have fired
        assert result["passed"] is True

    def test_cap_logs_warning(self, caplog):
        """A WARNING must be emitted to the logger when the cap fires."""
        runner = _make_runner()
        driver, annotator = _wire_runner(runner)

        def _rotating():
            colors = ["white", "gray", "silver", "gainsboro", "whitesmoke"] * 10
            for c in colors:
                yield (_tiny_png_b64(c), 390, 844)

        driver.screenshot.side_effect = _rotating()
        annotator.get_element_tree.return_value = _xml_with_elements("Wi-Fi")
        annotator._screen_changed.return_value = True

        mock_client = MagicMock()
        runner._client = mock_client
        mock_client.messages.create.return_value = _claude_resp(
            "ACTION: scroll\nDIRECTION: down\nREASONING: still looking"
        )

        with caplog.at_level(logging.WARNING, logger="specterqa.ios.som_runner"):
            with patch("time.sleep"):
                runner.run_step("Find Settings", max_iterations=20)

        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("scroll" in m.lower() for m in warning_messages), (
            f"Expected a scroll-cap warning. Got: {warning_messages}"
        )


# ---------------------------------------------------------------------------
# Integration: guards fire (or stay silent) inside run_step
# ---------------------------------------------------------------------------


class TestScrollGuardsIntegration:
    """End-to-end run_step scenarios exercising the guards together."""

    def test_guard1_skips_scroll_when_element_visible(self):
        """Guard 1: element already in tree → scroll is skipped, loop continues."""
        runner = _make_runner()
        driver, annotator = _wire_runner(runner)

        # get_element_tree returns XML that contains the goal text
        annotator.get_element_tree.return_value = _xml_with_elements("Settings", "Wi-Fi")

        _tiny_png_b64()
        # Two distinct screenshots so the old stuck-detector doesn't fire
        driver.screenshot.side_effect = [
            (_tiny_png_b64("white"), 390, 844),
            (_tiny_png_b64("black"), 390, 844),
        ] * 10

        mock_client = MagicMock()
        runner._client = mock_client
        mock_client.messages.create.side_effect = [
            # Claude suggests scroll, but guard 1 skips it
            _claude_resp("ACTION: scroll\nDIRECTION: down\nREASONING: find Settings"),
            # Next iteration: done
            _claude_resp("ACTION: done\nREASONING: Settings was already visible"),
        ]

        with patch("time.sleep"):
            result = runner.run_step("Settings", max_iterations=5)

        # Guard 1 skipped the scroll → swipe never called
        driver.swipe.assert_not_called()
        assert result["passed"] is True

    def test_guard2_stops_when_screen_unchanged_after_scroll(self):
        """Guard 2: _screen_changed returns False → loop breaks with error."""
        runner = _make_runner()
        driver, annotator = _wire_runner(runner)

        annotator.get_element_tree.return_value = _xml_with_elements("Wi-Fi", "Bluetooth")
        annotator._screen_changed.return_value = False  # boundary reached

        # Use distinct screenshots so old detector doesn't fire before guard 2
        driver.screenshot.side_effect = [
            (_tiny_png_b64("white"), 390, 844),
            (_tiny_png_b64("black"), 390, 844),
        ] * 10

        mock_client = MagicMock()
        runner._client = mock_client
        mock_client.messages.create.return_value = _claude_resp("ACTION: scroll\nDIRECTION: down\nREASONING: scrolling")

        with patch("time.sleep"):
            result = runner.run_step("Privacy", max_iterations=10)

        assert result["passed"] is False
        assert result["error"] is not None
        assert "unchanged" in result["error"].lower() or "scroll" in result["error"].lower()

    def test_guard3_stops_after_max_consecutive_scrolls(self):
        """Guard 3: cap fires after MAX_CONSECUTIVE_SCROLLS back-to-back scrolls."""
        runner = _make_runner()
        driver, annotator = _wire_runner(runner)

        annotator.get_element_tree.return_value = _xml_empty()
        annotator._screen_changed.return_value = True  # guard 2 stays quiet

        def _rotating():
            colors = ["white", "gray", "silver", "gainsboro", "whitesmoke"] * 30
            for c in colors:
                yield (_tiny_png_b64(c), 390, 844)

        driver.screenshot.side_effect = _rotating()

        mock_client = MagicMock()
        runner._client = mock_client
        mock_client.messages.create.return_value = _claude_resp(
            "ACTION: scroll\nDIRECTION: down\nREASONING: keep going"
        )

        with patch("time.sleep"):
            result = runner.run_step("NeverFound", max_iterations=50)

        assert result["passed"] is False
        assert result["error"] is not None
        assert len(result["actions"]) <= MAX_CONSECUTIVE_SCROLLS + 1

    def test_normal_scroll_and_tap_passes(self):
        """Happy path: scroll reveals element → tap → done, no guard fires."""
        runner = _make_runner()
        driver, annotator = _wire_runner(runner)

        # get_element_tree returns empty tree on first call, then tree with target
        annotator.get_element_tree.side_effect = [
            _xml_empty(),  # pre-scroll Guard 1 check: not visible
            _xml_with_elements("Wi-Fi", "Settings"),  # post-scroll Guard 2 check
        ] + [_xml_with_elements("Wi-Fi", "Settings")] * 10

        annotator._screen_changed.return_value = True  # scroll changed the screen

        elements_with_target = _make_elements("Wi-Fi", "Settings")
        annotator.annotate.return_value = (elements_with_target, _tiny_png_b64())
        annotator.elements_text.return_value = "[1] Cell Wi-Fi\n[2] Cell Settings"

        png_a = _tiny_png_b64("white")
        png_b = _tiny_png_b64("black")
        driver.screenshot.side_effect = [
            (png_a, 390, 844),
            (png_b, 390, 844),  # iter 0: scroll
            (png_b, 390, 844),
            (png_a, 390, 844),  # iter 1: tap
            (png_a, 390, 844),  # iter 2: done
        ] + [(png_b, 390, 844)] * 10

        mock_client = MagicMock()
        runner._client = mock_client
        mock_client.messages.create.side_effect = [
            _claude_resp("ACTION: scroll\nDIRECTION: down\nREASONING: Settings not visible"),
            _claude_resp("ACTION: tap\nELEMENT: 2\nREASONING: Settings now visible"),
            _claude_resp("ACTION: done\nREASONING: opened settings"),
        ]

        with patch("time.sleep"):
            result = runner.run_step("Settings", max_iterations=10)

        assert result["passed"] is True
        driver.swipe.assert_called_once()  # exactly one scroll executed


# ---------------------------------------------------------------------------
# Smoke tests: existing behaviour that must pass TODAY and after guards land
# ---------------------------------------------------------------------------


class TestExistingRunnerSmoke:
    """Sanity checks against the current (post-guard) code.

    These tests verify the base parser and structural behaviour that the guards
    build on.  They must pass right now and continue passing after any further
    changes.
    """

    def test_runner_instantiates(self):
        runner = _make_runner()
        assert runner is not None

    def test_parse_response_scroll(self):
        runner = _make_runner()
        raw = "ACTION: scroll\nDIRECTION: down\nREASONING: target not visible"
        result = runner._parse_claude_response(raw)
        assert result["action"] == "scroll"
        assert result["direction"] == "down"

    def test_parse_response_tap(self):
        runner = _make_runner()
        raw = "ACTION: tap\nELEMENT: 3\nREASONING: Settings cell"
        result = runner._parse_claude_response(raw)
        assert result["action"] == "tap"
        assert result["element"] == 3

    def test_parse_response_done(self):
        runner = _make_runner()
        raw = "ACTION: done\nREASONING: goal achieved"
        result = runner._parse_claude_response(raw)
        assert result["action"] == "done"

    def test_parse_response_back(self):
        runner = _make_runner()
        raw = "ACTION: back\nREASONING: wrong screen"
        result = runner._parse_claude_response(raw)
        assert result["action"] == "back"

    def test_parse_response_type(self):
        runner = _make_runner()
        raw = "ACTION: type\nTEXT: hello world\nREASONING: filling search"
        result = runner._parse_claude_response(raw)
        assert result["action"] == "type"
        assert result["text"] == "hello world"

    def test_parse_response_unknown_defaults_to_wait(self):
        runner = _make_runner()
        raw = "ACTION: fly\nREASONING: ???"
        result = runner._parse_claude_response(raw)
        assert result["action"] == "wait"

    def test_parse_response_element_strips_noise(self):
        """Trailing text after the element number must be ignored."""
        runner = _make_runner()
        raw = "ACTION: tap\nELEMENT: 7 (Settings)\nREASONING: that one"
        result = runner._parse_claude_response(raw)
        assert result["element"] == 7

    def test_runner_raises_when_not_started(self):
        runner = _make_runner()
        with pytest.raises(SoMRunnerError, match="not started"):
            runner.run_step("anything")


class TestExistingAnnotatorSmoke:
    """Sanity checks for SoMAnnotator that must pass both before and after guards."""

    def test_parse_elements_basic(self):
        xml = _xml_with_elements("Wi-Fi", "Bluetooth", "General")
        annotator = SoMAnnotator()
        elements = annotator.parse_elements(xml)
        assert len(elements) == 3
        assert elements[0].label == "Wi-Fi"
        assert elements[1].label == "Bluetooth"
        assert elements[2].label == "General"

    def test_parse_elements_empty(self):
        annotator = SoMAnnotator()
        elements = annotator.parse_elements(_xml_empty())
        assert elements == []

    def test_elements_text_includes_label_and_index(self):
        xml = _xml_with_elements("Settings")
        annotator = SoMAnnotator()
        elements = annotator.parse_elements(xml)
        text = annotator.elements_text(elements)
        assert "Settings" in text
        assert "[1]" in text

    def test_uielement_center_properties(self):
        elem = UIElement(
            index=1,
            element_type="Cell",
            label="Test",
            value="",
            x=10.0,
            y=20.0,
            width=100.0,
            height=50.0,
        )
        assert elem.center_x == 60.0
        assert elem.center_y == 45.0

    def test_uielement_to_dict(self):
        elem = UIElement(
            index=2,
            element_type="Button",
            label="OK",
            value="",
            x=0.0,
            y=0.0,
            width=44.0,
            height=44.0,
        )
        d = elem.to_dict()
        assert d["index"] == 2
        assert d["label"] == "OK"
        assert d["center_x"] == 22.0
