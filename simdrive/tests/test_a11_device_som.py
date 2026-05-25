"""test engineering — simdrive 1.0.0a11 device SoM tests (F-002).

All 14 tests in this file are expected to FAIL on feat/v17-claude-native HEAD
3a22bd4 (no ``simdrive/wda/som_device.py`` module, no ``WdaClient.source()``
method). They must all PASS after engineering lands ``fix/simdrive-a11-device-som``.

XML fixtures are built inline with stdlib ``xml.etree.ElementTree`` so no
disk-based fixtures are needed — tests are fully hermetic.
"""
from __future__ import annotations

import io
import logging
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

# ── helpers: tiny PNG fixture ─────────────────────────────────────────────────

# 1×1 transparent PNG — smallest valid PNG PIL can open.
_ONE_PX_PNG = bytes.fromhex(
    "89504e470d0a1a0a"
    "0000000d49484452"
    "00000001"
    "00000001"
    "08060000001f15c489"
    "0000000a49444154"
    "789c6260000000020001"
    "e221bc33"
    "0000000049454e44ae426082"
)


# ── helpers: XML fixtures ─────────────────────────────────────────────────────


def _single_button_xml(
    name: str = "My Books",
    x: int = 110,
    y: int = 893,
    w: int = 110,
    h: int = 63,
    visible: str = "true",
    enabled: str = "true",
) -> str:
    """Return minimal XCUITest /source XML with one XCUIElementTypeButton."""
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <AppiumAUT>
          <XCUIElementTypeApplication name="Palace" x="0" y="0" width="440" height="956" visible="true" enabled="true">
            <XCUIElementTypeButton name="{name}" x="{x}" y="{y}" width="{w}" height="{h}" visible="{visible}" enabled="{enabled}" />
          </XCUIElementTypeApplication>
        </AppiumAUT>
    """)


def _two_elements_xml(
    el1_visible: str = "true",
    el2_visible: str = "false",
) -> str:
    """Return XML with one visible button and one potentially-invisible element."""
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <AppiumAUT>
          <XCUIElementTypeApplication name="Palace" x="0" y="0" width="440" height="956" visible="true" enabled="true">
            <XCUIElementTypeButton name="Visible Button" x="10" y="50" width="100" height="44" visible="{el1_visible}" enabled="true" />
            <XCUIElementTypeButton name="Hidden Button" x="10" y="150" width="100" height="44" visible="{el2_visible}" enabled="true" />
          </XCUIElementTypeApplication>
        </AppiumAUT>
    """)


def _no_text_xml() -> str:
    """Return XML with element that has no name/label/value."""
    return textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <AppiumAUT>
          <XCUIElementTypeApplication name="Palace" x="0" y="0" width="440" height="956" visible="true" enabled="true">
            <XCUIElementTypeButton name="" x="10" y="50" width="100" height="44" visible="true" enabled="true" />
          </XCUIElementTypeApplication>
        </AppiumAUT>
    """)


def _zero_area_xml() -> str:
    """Return XML with a zero-width element."""
    return textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <AppiumAUT>
          <XCUIElementTypeApplication name="Palace" x="0" y="0" width="440" height="956" visible="true" enabled="true">
            <XCUIElementTypeButton name="Ghost" x="10" y="50" width="0" height="44" visible="true" enabled="true" />
          </XCUIElementTypeApplication>
        </AppiumAUT>
    """)


def _out_of_bounds_xml(screen_width_pts: int = 440) -> str:
    """Return XML with an element outside the screenshot's point-coordinate bounds."""
    # x=2000 pts * 1.0 scale = 2000 px, which is way outside a 1320 px screen
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <AppiumAUT>
          <XCUIElementTypeApplication name="Palace" x="0" y="0" width="{screen_width_pts}" height="956" visible="true" enabled="true">
            <XCUIElementTypeButton name="Off Screen" x="2000" y="50" width="100" height="44" visible="true" enabled="true" />
          </XCUIElementTypeApplication>
        </AppiumAUT>
    """)


def _nested_duplicate_xml() -> str:
    """Parent container + child button, both at similar bbox, same name."""
    return textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <AppiumAUT>
          <XCUIElementTypeApplication name="Palace" x="0" y="0" width="440" height="956" visible="true" enabled="true">
            <XCUIElementTypeOther name="My Books" x="110" y="893" width="110" height="63" visible="true" enabled="true">
              <XCUIElementTypeButton name="My Books" x="110" y="893" width="110" height="63" visible="true" enabled="true" />
            </XCUIElementTypeOther>
          </XCUIElementTypeApplication>
        </AppiumAUT>
    """)


def _disabled_button_xml() -> str:
    """Single button that is disabled (enabled=false)."""
    return textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <AppiumAUT>
          <XCUIElementTypeApplication name="Palace" x="0" y="0" width="440" height="956" visible="true" enabled="true">
            <XCUIElementTypeButton name="Disabled Button" x="10" y="50" width="100" height="44" visible="true" enabled="false" />
          </XCUIElementTypeApplication>
        </AppiumAUT>
    """)


# ── helpers: mock WdaClient ───────────────────────────────────────────────────


def _mock_wda(source_xml: str) -> MagicMock:
    """Return a mock WdaClient whose .source() returns the given XML string."""
    wda = MagicMock()
    wda.source.return_value = source_xml
    return wda


# ── helpers: session factory ──────────────────────────────────────────────────


def _make_device_session(tmp_path: Path, udid: str = "TEST-A11-SOM-DEVICE") -> object:
    """Create an in-memory device Session and register it in the global registry."""
    from simdrive import session as session_mod
    from simdrive.sim import Device

    d = Device(udid=udid, name="Moes Max", os_version="26.3.1", state="active")
    workdir = tmp_path / "sessions" / "somtest"
    workdir.mkdir(parents=True, exist_ok=True)
    s = session_mod.Session(
        session_id="somtest",
        device=d,
        workdir=workdir,
        target="device",
    )
    session_mod._SESSIONS["somtest"] = s
    return s


@pytest.fixture(autouse=True)
def _cleanup_sessions():
    from simdrive import session as session_mod
    yield
    session_mod._SESSIONS.pop("somtest", None)


# ═════════════════════════════════════════════════════════════════════════════
# 1. single_button_emits_one_mark
# ═════════════════════════════════════════════════════════════════════════════


def test_single_button_emits_one_mark(tmp_path):
    """One visible button in the XML → exactly one mark with correct pixel bbox."""
    from simdrive.wda.som_device import annotate_device_screenshot

    screenshot_path = tmp_path / "shot.png"
    screenshot_path.write_bytes(_ONE_PX_PNG)

    xml = _single_button_xml(name="My Books", x=110, y=893, w=110, h=63)
    wda = _mock_wda(xml)

    marks, ann_path = annotate_device_screenshot(
        screenshot_path=screenshot_path,
        screenshot_size_pixels=(1320, 2868),
        wda=wda,
        point_scale=3.0,
    )

    assert len(marks) == 1, f"Expected 1 mark, got {len(marks)}: {marks}"
    m = marks[0]
    # bbox = [x*scale, y*scale, w*scale, h*scale]
    # = [110*3, 893*3, 110*3, 63*3] = [330, 2679, 330, 189]
    assert m["bbox"] == [330, 2679, 330, 189], f"bbox mismatch: {m['bbox']}"
    assert "My Books" in m["text"], f"text mismatch: {m['text']}"
    # stable_id must be 12-char hex (blake2b digest_size=6 → 12 hex chars)
    assert len(m["stable_id"]) == 12, f"stable_id length wrong: {m['stable_id']!r}"
    assert all(c in "0123456789abcdef" for c in m["stable_id"]), \
        f"stable_id not hex: {m['stable_id']!r}"


# ═════════════════════════════════════════════════════════════════════════════
# 2. point_to_pixel_multiplication
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("scale,expected_x,expected_w", [
    (1.0, 110, 110),
    (2.0, 220, 220),
    (3.0, 330, 330),
])
def test_point_to_pixel_multiplication(tmp_path, scale, expected_x, expected_w):
    """bbox values must always be pts * point_scale regardless of scale factor."""
    from simdrive.wda.som_device import annotate_device_screenshot

    screenshot_path = tmp_path / "shot.png"
    screenshot_path.write_bytes(_ONE_PX_PNG)

    xml = _single_button_xml(name="My Books", x=110, y=893, w=110, h=63)
    wda = _mock_wda(xml)

    # Use a large enough screen to avoid out-of-bounds exclusion
    screen_w = int(440 * scale)
    screen_h = int(956 * scale)

    marks, _ = annotate_device_screenshot(
        screenshot_path=screenshot_path,
        screenshot_size_pixels=(screen_w, screen_h),
        wda=wda,
        point_scale=scale,
    )

    assert len(marks) == 1
    m = marks[0]
    assert m["bbox"][0] == expected_x, f"scale={scale}: x={m['bbox'][0]} != {expected_x}"
    assert m["bbox"][2] == expected_w, f"scale={scale}: w={m['bbox'][2]} != {expected_w}"


# ═════════════════════════════════════════════════════════════════════════════
# 3. invisible_element_excluded
# ═════════════════════════════════════════════════════════════════════════════


def test_invisible_element_excluded(tmp_path):
    """Element with visible='false' must not produce a mark."""
    from simdrive.wda.som_device import annotate_device_screenshot

    screenshot_path = tmp_path / "shot.png"
    screenshot_path.write_bytes(_ONE_PX_PNG)

    xml = _two_elements_xml(el1_visible="true", el2_visible="false")
    wda = _mock_wda(xml)

    marks, _ = annotate_device_screenshot(
        screenshot_path=screenshot_path,
        screenshot_size_pixels=(1320, 2868),
        wda=wda,
        point_scale=3.0,
    )

    assert len(marks) == 1, f"Expected 1 mark (visible only), got {len(marks)}"
    assert "Visible Button" in marks[0]["text"]


# ═════════════════════════════════════════════════════════════════════════════
# 4. disabled_element_excluded_or_included
# ═════════════════════════════════════════════════════════════════════════════


def test_disabled_element_included(tmp_path):
    """Disabled (enabled=false) but visible elements ARE included in marks.

    Rationale: disabled buttons are still visible and tappable via WDA even
    if the app ignores the tap. Agents need to see them to describe the UI
    state accurately. Exclusion of disabled would hide 'greyed-out' submit
    buttons that are informative.
    """
    from simdrive.wda.som_device import annotate_device_screenshot

    screenshot_path = tmp_path / "shot.png"
    screenshot_path.write_bytes(_ONE_PX_PNG)

    xml = _disabled_button_xml()
    wda = _mock_wda(xml)

    marks, _ = annotate_device_screenshot(
        screenshot_path=screenshot_path,
        screenshot_size_pixels=(1320, 2868),
        wda=wda,
        point_scale=3.0,
    )

    assert len(marks) == 1, (
        f"Expected disabled button to be INCLUDED; got {len(marks)} marks. "
        "If engineering decided to EXCLUDE disabled: change assertion to `== 0` "
        "and update this docstring."
    )
    assert "Disabled Button" in marks[0]["text"]


# ═════════════════════════════════════════════════════════════════════════════
# 5. empty_text_element_excluded
# ═════════════════════════════════════════════════════════════════════════════


def test_empty_text_element_excluded(tmp_path):
    """Element with empty name/label/value must produce no mark."""
    from simdrive.wda.som_device import annotate_device_screenshot

    screenshot_path = tmp_path / "shot.png"
    screenshot_path.write_bytes(_ONE_PX_PNG)

    wda = _mock_wda(_no_text_xml())

    marks, _ = annotate_device_screenshot(
        screenshot_path=screenshot_path,
        screenshot_size_pixels=(1320, 2868),
        wda=wda,
        point_scale=3.0,
    )

    assert marks == [], f"Expected no marks for empty-text element, got {marks}"


# ═════════════════════════════════════════════════════════════════════════════
# 6. zero_area_element_excluded
# ═════════════════════════════════════════════════════════════════════════════


def test_zero_area_element_excluded(tmp_path):
    """Element with width=0 or height=0 must produce no mark."""
    from simdrive.wda.som_device import annotate_device_screenshot

    screenshot_path = tmp_path / "shot.png"
    screenshot_path.write_bytes(_ONE_PX_PNG)

    wda = _mock_wda(_zero_area_xml())

    marks, _ = annotate_device_screenshot(
        screenshot_path=screenshot_path,
        screenshot_size_pixels=(1320, 2868),
        wda=wda,
        point_scale=3.0,
    )

    assert marks == [], f"Expected no marks for zero-area element, got {marks}"


# ═════════════════════════════════════════════════════════════════════════════
# 7. outside_screenshot_bounds_excluded
# ═════════════════════════════════════════════════════════════════════════════


def test_outside_screenshot_bounds_excluded(tmp_path):
    """Element whose pixel bbox starts outside screenshot width must be excluded."""
    from simdrive.wda.som_device import annotate_device_screenshot

    screenshot_path = tmp_path / "shot.png"
    screenshot_path.write_bytes(_ONE_PX_PNG)

    # x=2000 pts * 1.0 scale = 2000 px, screen is only 1320 px wide
    wda = _mock_wda(_out_of_bounds_xml())

    marks, _ = annotate_device_screenshot(
        screenshot_path=screenshot_path,
        screenshot_size_pixels=(1320, 2868),
        wda=wda,
        point_scale=1.0,
    )

    assert marks == [], f"Expected out-of-bounds element excluded, got {marks}"


# ═════════════════════════════════════════════════════════════════════════════
# 8. nested_duplicate_deepest_wins
# ═════════════════════════════════════════════════════════════════════════════


def test_nested_duplicate_deepest_wins(tmp_path):
    """Parent XCUIElementTypeOther + child XCUIElementTypeButton, same name/bbox.

    The deepest (most specific) element wins — one mark, tagged as the button.
    """
    from simdrive.wda.som_device import annotate_device_screenshot

    screenshot_path = tmp_path / "shot.png"
    screenshot_path.write_bytes(_ONE_PX_PNG)

    wda = _mock_wda(_nested_duplicate_xml())

    marks, _ = annotate_device_screenshot(
        screenshot_path=screenshot_path,
        screenshot_size_pixels=(1320, 2868),
        wda=wda,
        point_scale=3.0,
    )

    assert len(marks) == 1, f"Expected exactly 1 mark (deepest wins), got {len(marks)}"
    m = marks[0]
    assert "My Books" in m["text"]
    # The surviving mark should come from the Button (leaf) not the Other (container)
    element_type = m.get("element_type", "")
    if element_type:
        assert "Button" in element_type, (
            f"Expected Button element_type, got {element_type!r}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 9. wda_source_http_error_returns_empty_marks
# ═════════════════════════════════════════════════════════════════════════════


def test_wda_source_http_error_returns_empty_marks(tmp_path, caplog):
    """WdaClient.source() raising httpx.HTTPError → ([], None), warning logged."""
    from simdrive.wda.som_device import annotate_device_screenshot

    screenshot_path = tmp_path / "shot.png"
    screenshot_path.write_bytes(_ONE_PX_PNG)

    wda = MagicMock()
    wda.source.side_effect = httpx.HTTPError("connection refused")

    with caplog.at_level(logging.WARNING):
        marks, ann_path = annotate_device_screenshot(
            screenshot_path=screenshot_path,
            screenshot_size_pixels=(1320, 2868),
            wda=wda,
            point_scale=3.0,
        )

    assert marks == [], f"Expected empty marks on HTTP error, got {marks}"
    assert ann_path is None, f"Expected None annotated_path on HTTP error, got {ann_path}"
    # A warning must be logged (not an exception)
    assert any("warn" in r.levelname.lower() or r.levelno >= logging.WARNING
               for r in caplog.records), \
        "Expected a WARNING log entry when WDA source() raises HTTPError"


# ═════════════════════════════════════════════════════════════════════════════
# 10. wda_source_malformed_xml_returns_empty_marks
# ═════════════════════════════════════════════════════════════════════════════


def test_wda_source_malformed_xml_returns_empty_marks(tmp_path, caplog):
    """Malformed XML from WdaClient.source() → ([], None), warning logged."""
    from simdrive.wda.som_device import annotate_device_screenshot

    screenshot_path = tmp_path / "shot.png"
    screenshot_path.write_bytes(_ONE_PX_PNG)

    wda = _mock_wda("<not valid xml")  # deliberately invalid

    with caplog.at_level(logging.WARNING):
        marks, ann_path = annotate_device_screenshot(
            screenshot_path=screenshot_path,
            screenshot_size_pixels=(1320, 2868),
            wda=wda,
            point_scale=3.0,
        )

    assert marks == [], f"Expected empty marks on malformed XML, got {marks}"
    assert ann_path is None
    assert any(r.levelno >= logging.WARNING for r in caplog.records), \
        "Expected a WARNING log entry when XML parse fails"


# ═════════════════════════════════════════════════════════════════════════════
# 11. wda_source_empty_xml_returns_empty_marks
# ═════════════════════════════════════════════════════════════════════════════


def test_wda_source_empty_xml_returns_empty_marks(tmp_path):
    """Empty container XML <AppiumAUT/> → ([], None), no exception."""
    from simdrive.wda.som_device import annotate_device_screenshot

    screenshot_path = tmp_path / "shot.png"
    screenshot_path.write_bytes(_ONE_PX_PNG)

    wda = _mock_wda("<AppiumAUT/>")

    marks, ann_path = annotate_device_screenshot(
        screenshot_path=screenshot_path,
        screenshot_size_pixels=(1320, 2868),
        wda=wda,
        point_scale=3.0,
    )

    assert marks == [], f"Expected empty marks for empty XML, got {marks}"
    assert ann_path is None


# ═════════════════════════════════════════════════════════════════════════════
# 12. tool_observe_device_annotate_true_populates_marks
# ═════════════════════════════════════════════════════════════════════════════


def test_tool_observe_device_annotate_true_populates_marks(tmp_path):
    """tool_observe on device session with annotate=True populates marks list."""
    _make_device_session(tmp_path)

    mock_client = MagicMock()
    mock_client.screenshot_any.return_value = _ONE_PX_PNG
    mock_client.source.return_value = _single_button_xml(name="My Books", x=10, y=20, w=100, h=44)

    fake_marks = [
        {
            "id": 1,
            "stable_id": "aabbccddeeff",
            "stable_id_loose": "112233445566",
            "bbox": [30, 60, 300, 132],
            "center": [180, 126],
            "text": "My Books",
            "confidence": 1.0,
            "raw_confidence": 1.0,
            "confidence_band": "high",
        }
    ]
    fake_annotated = tmp_path / "annotated.png"
    fake_annotated.write_bytes(_ONE_PX_PNG)

    def _fake_annotate_device_screenshot(screenshot_path, screenshot_size_pixels, wda, point_scale):
        return fake_marks, fake_annotated

    with patch("simdrive.wda.registry.load", return_value={"host": "localhost", "port": 8100}), \
         patch("simdrive.wda.client.WdaClient") as mock_cls, \
         patch("simdrive.wda.som_device.annotate_device_screenshot",
               side_effect=_fake_annotate_device_screenshot):
        mock_cls.return_value = mock_client

        from simdrive.server import tool_observe
        result = tool_observe({"session_id": "somtest", "annotate": True})

    assert len(result["marks"]) > 0, f"Expected marks, got {result['marks']}"
    assert result["annotated_path"] is not None, \
        "Expected annotated_path to be set when annotate=True"


# ═════════════════════════════════════════════════════════════════════════════
# 13. tool_observe_device_annotate_false_skips_som
# ═════════════════════════════════════════════════════════════════════════════


def test_tool_observe_device_annotate_false_skips_som(tmp_path):
    """tool_observe with annotate=False → marks==[], annotated_path is None, source NOT called.

    This test verifies the fast-path skip of som_device when annotate=False.
    It intentionally imports som_device to confirm the module exists (fails on HEAD
    where som_device is not yet implemented) and then validates that tool_observe
    does NOT call into it when annotate=False.
    """
    # Verify the module exists — this import fails on HEAD (no som_device yet).
    from simdrive.wda import som_device  # noqa: F401 — import for existence check

    _make_device_session(tmp_path)

    mock_client = MagicMock()
    mock_client.screenshot_any.return_value = _ONE_PX_PNG

    with patch("simdrive.wda.registry.load", return_value={"host": "localhost", "port": 8100}), \
         patch("simdrive.wda.client.WdaClient") as mock_cls:
        mock_cls.return_value = mock_client

        from simdrive.server import tool_observe
        result = tool_observe({"session_id": "somtest", "annotate": False})

    assert result["marks"] == [], f"Expected empty marks when annotate=False, got {result['marks']}"
    assert result["annotated_path"] is None, \
        "Expected annotated_path=None when annotate=False"
    # source() must NOT have been called at all
    mock_client.source.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════
# 14. mark_shape_matches_sim_keys
# ═════════════════════════════════════════════════════════════════════════════

# Canonical mark keys as produced by simdrive.som.Mark.to_dict()
_SIM_MARK_KEYS = frozenset({
    "id",
    "stable_id",
    "stable_id_loose",
    "bbox",
    "center",
    "text",
    "confidence",
    "raw_confidence",
    "confidence_band",
})


def test_mark_shape_matches_sim_keys(tmp_path):
    """Each mark dict from annotate_device_screenshot must contain at least the sim mark keys."""
    from simdrive.wda.som_device import annotate_device_screenshot

    screenshot_path = tmp_path / "shot.png"
    screenshot_path.write_bytes(_ONE_PX_PNG)

    xml = _single_button_xml(name="My Books", x=110, y=893, w=110, h=63)
    wda = _mock_wda(xml)

    marks, _ = annotate_device_screenshot(
        screenshot_path=screenshot_path,
        screenshot_size_pixels=(1320, 2868),
        wda=wda,
        point_scale=3.0,
    )

    assert len(marks) == 1, f"Expected 1 mark for shape check, got {len(marks)}"
    m = marks[0]
    mark_keys = frozenset(m.keys())
    missing = _SIM_MARK_KEYS - mark_keys
    assert not missing, (
        f"Mark is missing keys required for sim parity: {missing}. "
        f"Got keys: {mark_keys}"
    )
