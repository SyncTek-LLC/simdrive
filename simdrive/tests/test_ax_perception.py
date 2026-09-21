"""Hermetic tests for INIT-2026-641 Wave 2 — AX-primary perception in `ax.py`.

Covers the DFS element walk, the content-group-relative coordinate transform
(the spike's verified correction: scale against `iOSContentGroup`, not the
raw `AXWindow`), and the reactivate-retry loop for the spike's live finding
("AXWindows returned empty mid-session, process still alive, recovered by one
osascript activate").

Every AX call is faked via monkeypatch on `ax._attr`/`ax._children`/`ax._frame`
— no pyobjc, no booted simulator. The live counterpart proving this is wired to
the real host API is `tests/test_ax_live_acceptance.py` (marked `live`).
"""
from __future__ import annotations

import pytest

from simdrive import ax


class FE:
    """Fake AXUIElement: role/title/value/desc/enabled/frame/children."""

    def __init__(self, role="", title=None, value=None, desc=None,
                 enabled=None, frame=None, children=None):
        self.role = role
        self.title = title
        self.value = value
        self.desc = desc
        self.enabled = enabled
        self.frame = frame
        self.children = children or []


def _patch_ax(monkeypatch):
    def fake_attr(e, name):
        return {
            "AXRole": e.role,
            "AXTitle": e.title,
            "AXValue": e.value,
            "AXDescription": e.desc,
            "AXPlaceholderValue": None,
            "AXEnabled": e.enabled,
        }.get(name)

    monkeypatch.setattr(ax, "_attr", fake_attr)
    monkeypatch.setattr(ax, "_children", lambda e: e.children)
    monkeypatch.setattr(ax, "_frame", lambda e: e.frame)


# ---------------------------------------------------------------------------
# walk_interactive_elements
# ---------------------------------------------------------------------------


def test_walk_collects_button_with_label_and_enabled_state(monkeypatch):
    _patch_ax(monkeypatch)
    btn = FE(role="AXButton", title="Continue", enabled=False,
             frame={"x": 10, "y": 20, "width": 100, "height": 30})
    root = FE(role="AXGroup", frame={"x": 0, "y": 0, "width": 200, "height": 200},
              children=[btn])

    out = ax.walk_interactive_elements(root)

    assert len(out) == 1
    el = out[0]
    assert el["role"] == "button"
    assert el["label"] == "Continue"
    assert el["enabled"] is False
    assert el["frame"] == {"x": 10, "y": 20, "width": 100, "height": 30}


def test_walk_normalizes_text_field_role_and_prefers_value_over_title(monkeypatch):
    _patch_ax(monkeypatch)
    field = FE(role="AXTextField", value="name@example.com",
               frame={"x": 0, "y": 0, "width": 300, "height": 40})
    out = ax.walk_interactive_elements(field)
    assert out[0]["role"] == "text_field"
    assert out[0]["label"] == "name@example.com"


def test_walk_skips_unlabeled_generic_containers(monkeypatch):
    _patch_ax(monkeypatch)
    root = FE(role="AXGroup", frame={"x": 0, "y": 0, "width": 10, "height": 10})
    out = ax.walk_interactive_elements(root)
    assert out == []


def test_walk_includes_static_text_and_recurses_into_children(monkeypatch):
    _patch_ax(monkeypatch)
    child = FE(role="AXStaticText", title="RefSource.",
               frame={"x": 5, "y": 5, "width": 90, "height": 20})
    root = FE(role="AXGroup", frame={"x": 0, "y": 0, "width": 400, "height": 400},
              children=[child])
    out = ax.walk_interactive_elements(root)
    assert len(out) == 1
    assert out[0]["role"] == "static_text"
    assert out[0]["label"] == "RefSource."


def test_walk_respects_maxdepth():
    # Build a chain deeper than maxdepth; walk must not recurse forever / crash.
    leaf = FE(role="AXButton", title="deep", frame={"x": 0, "y": 0, "width": 1, "height": 1})
    node = leaf
    for _ in range(5):
        node = FE(role="AXGroup", frame={"x": 0, "y": 0, "width": 1, "height": 1}, children=[node])

    def fake_attr(e, name):
        return {"AXRole": e.role, "AXTitle": e.title, "AXEnabled": None}.get(name)

    import unittest.mock as mock
    with mock.patch.object(ax, "_attr", fake_attr), \
         mock.patch.object(ax, "_children", lambda e: e.children), \
         mock.patch.object(ax, "_frame", lambda e: e.frame):
        out = ax.walk_interactive_elements(node, maxdepth=2)
    assert all(e["label"] != "deep" for e in out)


# ---------------------------------------------------------------------------
# Coordinate transform — the spike's verified content-group-relative math
# ---------------------------------------------------------------------------


def test_content_group_scale_is_screenshot_width_over_group_width():
    group_frame = {"x": 509.0, "y": 117.5, "width": 431.5, "height": 937.5}
    scale = ax.content_group_scale(group_frame, screenshot_w=1320, screenshot_h=2868)
    assert scale == pytest.approx(1320 / 431.5)


def test_content_group_scale_rejects_zero_width_frame():
    with pytest.raises(ax.AXError):
        ax.content_group_scale({"x": 0, "y": 0, "width": 0, "height": 100}, 100, 100)


def test_ax_frame_to_pixel_bbox_matches_spike_verified_transform():
    """Reproduces the spike's own numbers: content group at (509, 117.5),
    width 431.5pt scaling to a 1320px-wide screenshot (scale ~3.0592), and the
    'Continue' button frame from tree_dump.json.
    """
    group_frame = {"x": 509.0, "y": 117.5, "width": 431.5, "height": 937.5}
    scale = ax.content_group_scale(group_frame, screenshot_w=1320, screenshot_h=2868)
    continue_frame = {
        "x": 532.5363636363636, "y": 627.4372384937238,
        "width": 384.4272727272728, "height": 50.99372384937237,
    }
    x, y, w, h = ax.ax_frame_to_pixel_bbox(continue_frame, group_frame, scale)
    # Relative-to-content-group offset, scaled — NOT relative to the raw
    # AXWindow frame (which would shift y by the ~52pt toolbar + bezel).
    expected_x = round((continue_frame["x"] - group_frame["x"]) * scale)
    expected_y = round((continue_frame["y"] - group_frame["y"]) * scale)
    assert x == expected_x
    assert y == expected_y
    assert w > 0 and h > 0


def test_ax_frame_to_pixel_bbox_uses_content_group_not_raw_window():
    """The corrected transform (spike finding #2): using the raw AXWindow
    frame instead of the content-group frame shifts every y coordinate by the
    window's toolbar height. Prove the function's output depends on which
    frame is passed as the group, not a hardcoded window offset.
    """
    window_frame = {"x": 482.0, "y": 38.0, "width": 485.0, "height": 1035.0}
    group_frame = {"x": 509.0, "y": 117.5, "width": 431.5, "height": 937.5}
    elem_frame = {"x": 532.5, "y": 627.4, "width": 384.4, "height": 51.0}
    scale = ax.content_group_scale(group_frame, 1320, 2868)

    via_group = ax.ax_frame_to_pixel_bbox(elem_frame, group_frame, scale)
    via_window = ax.ax_frame_to_pixel_bbox(elem_frame, window_frame, scale)
    assert via_group != via_window


# ---------------------------------------------------------------------------
# observe_pixel_elements — reactivate/retry + fallback-worthy failures
# ---------------------------------------------------------------------------


_GROUP_FRAME = {"x": 509.0, "y": 117.5, "width": 431.5, "height": 937.5}
_BTN = FE(role="AXButton", title="Continue", enabled=True,
          frame={"x": 532.5, "y": 627.4, "width": 384.4, "height": 51.0})
_GROUP = FE(role="AXGroup", frame=_GROUP_FRAME, children=[_BTN])
_WINDOW = FE(role="AXWindow", frame={"x": 482.0, "y": 38.0, "width": 485.0, "height": 1035.0},
             children=[_GROUP])


def test_observe_pixel_elements_success_first_try(monkeypatch):
    _patch_ax(monkeypatch)
    monkeypatch.setattr(ax, "select_window", lambda device_name, auto_raise=False: _WINDOW)
    monkeypatch.setattr(ax, "_resolve_content_group", lambda window: _GROUP)
    raise_calls = []
    monkeypatch.setattr(ax, "raise_window", lambda d: raise_calls.append(d) or True)

    result = ax.observe_pixel_elements("iPhone 17 Pro Max", 1320, 2868)

    assert result["resolution_method"] == "ax"
    assert result["reactivated"] is False
    assert raise_calls == []
    assert len(result["elements"]) == 1
    assert result["elements"][0]["role"] == "button"
    assert result["elements"][0]["bbox"][2] > 0


def test_observe_pixel_elements_reactivates_after_empty_window(monkeypatch):
    """Reproduces the spike's live finding: AXWindows returns empty mid-session
    with the process alive; one raise_window()/select_window() retry recovers.
    """
    _patch_ax(monkeypatch)
    calls = {"n": 0}

    def fake_select(device_name, auto_raise=False):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ax.AXError("No on-screen Simulator window")
        return _WINDOW

    raise_calls = []
    monkeypatch.setattr(ax, "select_window", fake_select)
    monkeypatch.setattr(ax, "_resolve_content_group", lambda window: _GROUP)
    monkeypatch.setattr(ax, "raise_window", lambda d: raise_calls.append(d) or True)
    monkeypatch.setattr(ax.time, "sleep", lambda s: None)

    result = ax.observe_pixel_elements("iPhone 17 Pro Max", 1320, 2868, reactivate_retries=1)

    assert result["resolution_method"] == "ax_reactivated"
    assert result["reactivated"] is True
    assert raise_calls == ["iPhone 17 Pro Max"]
    assert len(result["elements"]) == 1


def test_observe_pixel_elements_raises_after_retry_budget_exhausted(monkeypatch):
    """Retry budget spent, AX still can't resolve content — must raise AXError
    so the caller (observe.py) can fall back to OCR. Never silently return
    empty elements as if that were a legitimate zero-control screen.
    """
    _patch_ax(monkeypatch)

    def always_fails(device_name, auto_raise=False):
        raise ax.AXError("No on-screen Simulator window")

    raise_calls = []
    monkeypatch.setattr(ax, "select_window", always_fails)
    monkeypatch.setattr(ax, "raise_window", lambda d: raise_calls.append(d) or True)
    monkeypatch.setattr(ax.time, "sleep", lambda s: None)

    with pytest.raises(ax.AXError):
        ax.observe_pixel_elements("iPhone 17 Pro Max", 1320, 2868, reactivate_retries=1)

    # Exactly one retry attempted (reactivate_retries=1): one raise_window call.
    assert raise_calls == ["iPhone 17 Pro Max"]


def test_observe_pixel_elements_retries_on_empty_content_group(monkeypatch):
    """Empty subtree (content group resolves but has zero walkable elements)
    is the same failure shape as a raised AXError — must trigger the retry
    loop, not be silently treated as a real zero-control screen.
    """
    _patch_ax(monkeypatch)
    empty_group = FE(role="AXGroup", frame=_GROUP_FRAME, children=[])
    resolve_calls = {"n": 0}

    def fake_resolve(window):
        resolve_calls["n"] += 1
        return empty_group if resolve_calls["n"] == 1 else _GROUP

    raise_calls = []
    monkeypatch.setattr(ax, "select_window", lambda d, auto_raise=False: _WINDOW)
    monkeypatch.setattr(ax, "_resolve_content_group", fake_resolve)
    monkeypatch.setattr(ax, "raise_window", lambda d: raise_calls.append(d) or True)
    monkeypatch.setattr(ax.time, "sleep", lambda s: None)

    result = ax.observe_pixel_elements("iPhone 17 Pro Max", 1320, 2868, reactivate_retries=1)

    assert result["resolution_method"] == "ax_reactivated"
    assert len(raise_calls) == 1
