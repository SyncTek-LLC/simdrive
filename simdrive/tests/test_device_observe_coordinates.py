"""Device observe: one coordinate space, allow_ax honoured, WDA-less fallback.

Marks from a device observe are screenshot pixels (what tap pixel_x/pixel_y
takes). The first observe of a session used to emit WDA points because the
px/pt scale was only cached after the first tap.
"""
from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

from simdrive import server, session, som
from simdrive.sim import Device

UDID = "31471BBD-6889-5DAC-9497-BCD565AB1CD6"

# One tab-bar button at (110, 880) size 110x36 points on a 440x956 pt screen.
_SOURCE = """<?xml version="1.0" encoding="UTF-8"?>
<XCUIElementTypeApplication type="XCUIElementTypeApplication" name="Palace" visible="true"
    x="0" y="0" width="440" height="956">
  <XCUIElementTypeButton type="XCUIElementTypeButton" name="My Books" label="My Books"
      visible="true" enabled="true" x="110" y="880" width="110" height="36"/>
</XCUIElementTypeApplication>
"""


def _png(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


def _wda():
    wda = MagicMock()
    wda.screenshot_any.return_value = _png(1320, 2868)
    wda.window_size_points.return_value = (440, 956)
    wda.source.return_value = _SOURCE
    return wda


@pytest.fixture
def device_session(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_entitlement_gate", lambda: None)
    server._SCALE_CACHE_BY_UDID.clear()
    s = session.Session(
        session_id="obs-dev", device=Device(udid=UDID, name="Phone", os_version="26", state="active"),
        workdir=tmp_path, target="device", wda_client=_wda(),
    )
    session._SESSIONS[s.session_id] = s
    yield s
    session._SESSIONS.pop(s.session_id, None)
    server._SCALE_CACHE_BY_UDID.clear()


def _my_books(result):
    [m] = [m for m in result["marks"] if m.get("text") == "My Books"]
    return m


def test_first_device_observe_emits_pixel_marks(device_session):
    """The first observe of a session (no tap yet) emits pixels, not points."""
    assert device_session.pixel_per_point_scale is None
    out = server.tool_observe({"session_id": device_session.session_id})
    m = _my_books(out)
    assert m["center"] == [495, 2694]
    assert out["coordinate_space"] == "pixels"
    assert out["screenshot_size_pixels"] == [1320, 2868]


def test_repeated_device_observes_agree(device_session):
    first = _my_books(server.tool_observe({"session_id": device_session.session_id}))
    second = _my_books(server.tool_observe({"session_id": device_session.session_id}))
    assert first["center"] == second["center"]
    assert first["bbox"] == second["bbox"]


def test_tap_by_text_after_first_observe_taps_point_center(device_session):
    server.tool_observe({"session_id": device_session.session_id})
    server.tool_tap({"session_id": device_session.session_id, "text": "My Books"})
    x, y = device_session.wda_client.tap.call_args.args
    assert (round(x), round(y)) == (165, 898)


def test_allow_ax_false_uses_ocr_not_wda_source(device_session, monkeypatch):
    ocr = [som.Mark(id=1, x=300, y=2600, w=390, h=110, text="My Books", confidence=0.95)]
    monkeypatch.setattr(som, "detect_marks", lambda path: ocr)
    out = server.tool_observe({"session_id": device_session.session_id, "allow_ax": False})
    device_session.wda_client.source.assert_not_called()
    assert out["resolution_method"] == "ocr"
    assert [m["text"] for m in out["marks"]] == ["My Books"]


def test_allow_ax_default_reads_wda_source(device_session):
    out = server.tool_observe({"session_id": device_session.session_id})
    device_session.wda_client.source.assert_called()
    assert out["resolution_method"] == "wda_ax"


def test_observe_without_wda_uses_hardware_udid_screenshot(tmp_path, monkeypatch):
    """A WDA-less device session observes via libimobiledevice with the hardware UDID."""
    monkeypatch.setattr(server, "_entitlement_gate", lambda: None)
    monkeypatch.setenv("WDA_REGISTRY_DIR", str(tmp_path / "wda"))
    s = session.Session(
        session_id="obs-nowda", device=Device(udid=UDID, name="Phone", os_version="", state="active"),
        workdir=tmp_path, target="device", wda_client=None,
        hardware_udid="00008150-00142D540A87801C",
    )
    session._SESSIONS[s.session_id] = s
    seen = {}
    shot = tmp_path / "shot.png"
    shot.write_bytes(_png(1320, 2868))

    def fake_observe(udid, out_dir, **kw):
        seen["udid"], seen["target"] = udid, kw.get("target")
        return SimpleNamespace(screenshot_path=shot, screenshot_w=1320, screenshot_h=2868,
                               marks=[], to_dict=lambda: {"marks": []})

    monkeypatch.setattr("simdrive.observe.observe", fake_observe)
    try:
        server.tool_observe({"session_id": s.session_id})
    finally:
        session._SESSIONS.pop(s.session_id, None)
    assert seen == {"udid": "00008150-00142D540A87801C", "target": "device"}
