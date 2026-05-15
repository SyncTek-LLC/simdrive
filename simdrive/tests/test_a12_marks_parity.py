"""TestAtlas — simdrive 1.0.0a12 marks parity tests (F-007 + F-008).

F-007: Resolver (_resolve_target_xy) must accept dict marks (as stored in
       Session.last_marks for target=device) without raising AttributeError.

F-008: Device marks (from annotate_device_screenshot) must be in screenshot
       pixel space — coordinates must equal logical-point values × point_scale,
       and must stay pixel-consistent across multiple consecutive observe calls.

All 10 tests MUST FAIL on feat/v17-claude-native HEAD (resolver calls
m.stable_id on dict objects; no normalization layer) and MUST PASS after
fix/simdrive-a12-marks-parity is merged.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Canonical 9-key mark set (from som.Mark.to_dict) ─────────────────────────
_CANONICAL_MARK_KEYS = frozenset({
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

# ── Minimal 1×1 PNG (PIL-readable) ───────────────────────────────────────────
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

# ── Canonical dict mark fixture ───────────────────────────────────────────────
# Matches the shape produced by som.Mark.to_dict() / annotate_device_screenshot.
# bbox = [x, y, w, h]; center = [cx, cy].
_DICT_MARK: dict = {
    "id": 1,
    "stable_id": "abc123def456",
    "stable_id_loose": "def456abc123",
    "bbox": [100, 200, 50, 30],
    "center": [125, 215],
    "text": "Foo",
    "confidence": 1.0,
    "raw_confidence": 1.0,
    "confidence_band": "high",
}


# ── Session factory helpers ───────────────────────────────────────────────────


def _make_session(
    tmp_path: Path,
    target: str = "simulator",
    session_id: str = "a12test",
    last_marks: list | None = None,
    screenshot_w: int = 1320,
    screenshot_h: int = 2868,
) -> object:
    """Build a minimal Session and insert it into the global registry."""
    from simdrive import session as session_mod
    from simdrive.sim import Device

    d = Device(udid="TEST-A12-MARKS", name="Test Device", os_version="18.0", state="active")
    workdir = tmp_path / "sessions" / session_id
    workdir.mkdir(parents=True, exist_ok=True)
    s = session_mod.Session(
        session_id=session_id,
        device=d,
        workdir=workdir,
        target=target,
        last_screenshot_w=screenshot_w,
        last_screenshot_h=screenshot_h,
    )
    if last_marks is not None:
        s.last_marks = last_marks
    session_mod._SESSIONS[session_id] = s
    return s


@pytest.fixture(autouse=True)
def _cleanup():
    from simdrive import session as session_mod
    yield
    for sid in ("a12test", "a12sim", "a12dev"):
        session_mod._SESSIONS.pop(sid, None)


# ── XML builder for F-008 device tests ───────────────────────────────────────


def _button_xml(name: str = "My Books", x: int = 110, y: int = 893,
                w: int = 110, h: int = 63) -> str:
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <AppiumAUT>
          <XCUIElementTypeApplication name="App" x="0" y="0" width="440" height="956"
                                      visible="true" enabled="true">
            <XCUIElementTypeButton name="{name}" x="{x}" y="{y}" width="{w}" height="{h}"
                                   visible="true" enabled="true" />
          </XCUIElementTypeApplication>
        </AppiumAUT>
    """)


def _mock_wda(source_xml: str) -> MagicMock:
    wda = MagicMock()
    wda.source.return_value = source_xml
    return wda


# ═══════════════════════════════════════════════════════════════════════════════
# F-007 — Resolver works for dict marks
# ═══════════════════════════════════════════════════════════════════════════════


def test_tap_by_stable_id_resolves_on_dict_marks(tmp_path):
    """_resolve_target_xy must resolve stable_id on dict marks without AttributeError.

    On HEAD, som.find_by_stable_id calls m.stable_id on a dict, raising
    AttributeError. After the fix, it reads m["stable_id"] or adapts.
    """
    _make_session(tmp_path, last_marks=[_DICT_MARK])

    from simdrive.server import _resolve_target_xy
    from simdrive import session as session_mod

    s = session_mod.get("a12test")
    # Must not raise AttributeError: 'dict' object has no attribute 'stable_id'
    cx, cy, _how, _mark = _resolve_target_xy(s, {"stable_id": "abc123def456"})

    assert (cx, cy) == (125, 215), (
        f"Expected center (125, 215), got ({cx}, {cy})"
    )


def test_tap_by_stable_id_loose_resolves_on_dict_marks(tmp_path):
    """_resolve_target_xy must resolve stable_id_loose on dict marks without AttributeError."""
    _make_session(tmp_path, last_marks=[_DICT_MARK])

    from simdrive.server import _resolve_target_xy
    from simdrive import session as session_mod

    s = session_mod.get("a12test")
    cx, cy, _how, _mark = _resolve_target_xy(s, {"stable_id_loose": "def456abc123"})

    assert (cx, cy) == (125, 215), (
        f"Expected center (125, 215), got ({cx}, {cy})"
    )


def test_tap_by_text_resolves_on_dict_marks(tmp_path):
    """_resolve_target_xy must resolve text= on dict marks without AttributeError.

    som.find_by_text accesses m.text and m.confidence as attributes on HEAD,
    which fails for dict marks.
    """
    _make_session(tmp_path, last_marks=[_DICT_MARK])

    from simdrive.server import _resolve_target_xy
    from simdrive import session as session_mod

    s = session_mod.get("a12test")
    cx, cy, _how, _mark = _resolve_target_xy(s, {"text": "Foo"})

    assert (cx, cy) == (125, 215), (
        f"Expected center (125, 215) for text='Foo', got ({cx}, {cy})"
    )


def test_tap_by_mark_id_resolves_on_dict_marks(tmp_path):
    """_resolve_target_xy must resolve mark= (integer ID) on dict marks.

    som.find_by_mark_id accesses m.id as attribute on HEAD; fails for dicts.
    """
    _make_session(tmp_path, last_marks=[_DICT_MARK])

    from simdrive.server import _resolve_target_xy
    from simdrive import session as session_mod

    s = session_mod.get("a12test")
    cx, cy, _how, _mark = _resolve_target_xy(s, {"mark": 1})

    assert (cx, cy) == (125, 215), (
        f"Expected center (125, 215) for mark=1, got ({cx}, {cy})"
    )


def test_tap_unresolvable_returns_clear_error(tmp_path):
    """Unresolvable stable_id must raise SimdriveError (target_not_found), not AttributeError/KeyError.

    On HEAD, the AttributeError from dict attribute access leaks out instead of
    the structured SimdriveError with code='target_not_found'.
    """
    _make_session(tmp_path, last_marks=[_DICT_MARK])

    from simdrive.server import _resolve_target_xy
    from simdrive import session as session_mod
    from simdrive.errors import SimdriveError

    s = session_mod.get("a12test")

    with pytest.raises(SimdriveError) as exc_info:
        _resolve_target_xy(s, {"stable_id": "nonexistent000000"})

    err = exc_info.value
    assert err.code == "target_not_found", (
        f"Expected code='target_not_found', got {err.code!r}"
    )
    # The missing key must appear in the message so the agent knows what it tried.
    assert "nonexistent000000" in err.message, (
        f"Expected missing key in message, got: {err.message!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# F-008 — Coordinate-system invariant: device marks are always in pixel space
# ═══════════════════════════════════════════════════════════════════════════════


def test_device_marks_always_pixels_not_points(tmp_path):
    """annotate_device_screenshot must return bbox in pixels (points × point_scale).

    Fixture: element at logical point (x=110, y=893, w=110, h=63) on a 3x device.
    Expected pixel bbox: [110*3, 893*3, 110*3, 63*3] = [330, 2679, 330, 189].
    """
    from simdrive.wda.som_device import annotate_device_screenshot

    screenshot_path = tmp_path / "shot.png"
    screenshot_path.write_bytes(_ONE_PX_PNG)

    xml = _button_xml(name="My Books", x=110, y=893, w=110, h=63)
    wda = _mock_wda(xml)

    marks, _ann = annotate_device_screenshot(
        screenshot_path=screenshot_path,
        screenshot_size_pixels=(1320, 2868),
        wda=wda,
        point_scale=3.0,
    )

    assert len(marks) == 1, f"Expected 1 mark, got {len(marks)}"
    m = marks[0]
    assert m["bbox"] == [330, 2679, 330, 189], (
        f"bbox must be points × 3.0 = [330, 2679, 330, 189], got {m['bbox']}"
    )


def test_device_marks_bbox_within_screenshot_bounds(tmp_path):
    """Every mark's bbox must lie within the screenshot dimensions (within 5 px tolerance)."""
    from simdrive.wda.som_device import annotate_device_screenshot

    screenshot_path = tmp_path / "shot.png"
    screenshot_path.write_bytes(_ONE_PX_PNG)

    xml = _button_xml(name="My Books", x=110, y=893, w=110, h=63)
    wda = _mock_wda(xml)

    screenshot_size_pixels = (1320, 2868)
    marks, _ann = annotate_device_screenshot(
        screenshot_path=screenshot_path,
        screenshot_size_pixels=screenshot_size_pixels,
        wda=wda,
        point_scale=3.0,
    )

    tolerance = 5
    sw, sh = screenshot_size_pixels
    for m in marks:
        bx, by, bw, bh = m["bbox"]
        assert bx + bw <= sw + tolerance, (
            f"Mark right edge ({bx + bw}) exceeds screenshot width ({sw}): bbox={m['bbox']}"
        )
        assert by + bh <= sh + tolerance, (
            f"Mark bottom edge ({by + bh}) exceeds screenshot height ({sh}): bbox={m['bbox']}"
        )


def test_consecutive_observes_same_screen_same_units(tmp_path):
    """Two annotate_device_screenshot calls with identical inputs must produce identical bboxes.

    Catches the 'first call pixels, second call points' alternation bug where
    a mutable state flag flips units on every invocation.
    """
    from simdrive.wda.som_device import annotate_device_screenshot

    screenshot_path = tmp_path / "shot.png"
    screenshot_path.write_bytes(_ONE_PX_PNG)

    xml = _button_xml(name="Catalog", x=50, y=100, w=200, h=44)
    wda = _mock_wda(xml)

    marks_1, _ann1 = annotate_device_screenshot(
        screenshot_path=screenshot_path,
        screenshot_size_pixels=(1320, 2868),
        wda=wda,
        point_scale=3.0,
    )

    marks_2, _ann2 = annotate_device_screenshot(
        screenshot_path=screenshot_path,
        screenshot_size_pixels=(1320, 2868),
        wda=wda,
        point_scale=3.0,
    )

    assert len(marks_1) == len(marks_2), (
        f"Call 1 returned {len(marks_1)} marks, call 2 returned {len(marks_2)}"
    )
    for i, (m1, m2) in enumerate(zip(marks_1, marks_2)):
        assert m1["bbox"] == m2["bbox"], (
            f"Mark[{i}] bbox differs between calls: {m1['bbox']} vs {m2['bbox']}. "
            "Units must stay consistent (pixels) across consecutive annotate calls."
        )
        assert m1["center"] == m2["center"], (
            f"Mark[{i}] center differs between calls: {m1['center']} vs {m2['center']}"
        )


def test_sim_and_device_marks_same_keys(tmp_path):
    """Both sim (Mark.to_dict) and device (annotate_device_screenshot) marks must share the 9 canonical keys."""
    from simdrive.som import Mark
    from simdrive.wda.som_device import annotate_device_screenshot

    # Build a sim mark via Mark.to_dict()
    sim_mark = Mark(id=1, x=100, y=200, w=50, h=30, text="Done", confidence=0.95)
    sim_dict = sim_mark.to_dict()

    # Build a device mark via annotate_device_screenshot
    screenshot_path = tmp_path / "shot.png"
    screenshot_path.write_bytes(_ONE_PX_PNG)

    xml = _button_xml(name="Done", x=100, y=200, w=50, h=30)
    wda = _mock_wda(xml)

    device_marks, _ = annotate_device_screenshot(
        screenshot_path=screenshot_path,
        screenshot_size_pixels=(1320, 2868),
        wda=wda,
        point_scale=1.0,
    )

    assert len(device_marks) >= 1, f"Expected at least 1 device mark, got {device_marks}"
    device_dict = device_marks[0]

    sim_keys = frozenset(sim_dict.keys())
    device_keys = frozenset(device_dict.keys())

    assert sim_keys == _CANONICAL_MARK_KEYS, (
        f"Sim mark keys {sim_keys} != canonical {_CANONICAL_MARK_KEYS}"
    )
    assert device_keys == _CANONICAL_MARK_KEYS, (
        f"Device mark keys {device_keys} != canonical {_CANONICAL_MARK_KEYS}"
    )
    assert sim_keys == device_keys, (
        f"Sim keys {sim_keys} != device keys {device_keys}. "
        "Both paths must produce the same 9-key mark shape."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-target
# ═══════════════════════════════════════════════════════════════════════════════


def test_resolver_works_uniformly_on_sim_and_device_marks(tmp_path):
    """_resolve_target_xy must return the same (x, y) for sim and device dict marks with identical shape.

    Both sessions carry a mark with text='Foo' and center=[125, 215].
    The resolver must find it equivalently regardless of session target.
    This test enforces that the fix is not target-specific (e.g. only fixing device
    path but not simulator dict marks if they appear).
    """
    from simdrive import session as session_mod
    from simdrive.sim import Device

    # Shared mark fixture — same center for both targets.
    mark = {
        "id": 1,
        "stable_id": "aabbccddee11",
        "stable_id_loose": "112233445566",
        "bbox": [100, 200, 50, 30],
        "center": [125, 215],
        "text": "Foo",
        "confidence": 1.0,
        "raw_confidence": 1.0,
        "confidence_band": "high",
    }

    def _make(target: str, session_id: str) -> object:
        d = Device(udid=f"TEST-{target.upper()}", name=target, os_version="18.0", state="active")
        workdir = tmp_path / session_id
        workdir.mkdir(parents=True, exist_ok=True)
        s = session_mod.Session(
            session_id=session_id,
            device=d,
            workdir=workdir,
            target=target,
            last_screenshot_w=1320,
            last_screenshot_h=2868,
        )
        s.last_marks = [mark]
        session_mod._SESSIONS[session_id] = s
        return s

    s_sim = _make("simulator", "a12sim")
    s_dev = _make("device", "a12dev")

    from simdrive.server import _resolve_target_xy

    cx_sim, cy_sim, _how_sim, _m_sim = _resolve_target_xy(s_sim, {"text": "Foo"})
    cx_dev, cy_dev, _how_dev, _m_dev = _resolve_target_xy(s_dev, {"text": "Foo"})

    assert (cx_sim, cy_sim) == (125, 215), (
        f"Sim resolver returned ({cx_sim}, {cy_sim}), expected (125, 215)"
    )
    assert (cx_dev, cy_dev) == (125, 215), (
        f"Device resolver returned ({cx_dev}, {cy_dev}), expected (125, 215)"
    )
    assert (cx_sim, cy_sim) == (cx_dev, cy_dev), (
        "Resolver must return identical coords for sim and device marks with identical shape."
    )
