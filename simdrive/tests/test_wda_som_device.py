"""Tests for simdrive.wda.som_device — device SoM annotation via WDA /source.

Validates:
  1. WdaClient.source() hits GET /session/<id>/source and returns the XML value.
  2. annotate_device_screenshot produces marks matching som.Mark.to_dict() shape.
  3. Exclusion rules: invisible, empty-text, zero-area, out-of-bounds, nested dups.
  4. Error paths: malformed XML, WDA HTTP error, empty XML → marks=[], no raise.
  5. Point-to-pixel coordinate scaling.
  6. stable_id matches what som.Mark computes directly.
  7. server.py annotate=True wires annotate_device_screenshot; annotate=False skips it.
  8. Annotated PNG is written at expected path (no double-draw).
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import httpx
import pytest


# ── minimal valid PNG (1×1 transparent pixel) ─────────────────────────────────

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


# ── XML fixtures ──────────────────────────────────────────────────────────────

_SIMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<XCUIElementTypeApplication name="Palace" label="Palace" enabled="true" visible="true" x="0" y="0" width="440" height="956">
  <XCUIElementTypeWindow x="0" y="0" width="440" height="956" visible="true">
    <XCUIElementTypeButton name="Catalog" label="Catalog" x="0" y="893" width="110" height="63" enabled="true" visible="true"/>
    <XCUIElementTypeButton name="My Books" label="My Books" x="110" y="893" width="110" height="63" enabled="true" visible="true"/>
    <XCUIElementTypeButton name="" label="" value="" x="220" y="893" width="110" height="63" enabled="true" visible="true"/>
  </XCUIElementTypeWindow>
</XCUIElementTypeApplication>"""

_INVISIBLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<XCUIElementTypeApplication name="App" label="App" enabled="true" visible="true" x="0" y="0" width="440" height="956">
  <XCUIElementTypeButton name="Hidden" label="Hidden" x="0" y="0" width="100" height="50" visible="false"/>
  <XCUIElementTypeButton name="Visible" label="Visible" x="0" y="100" width="100" height="50" visible="true"/>
</XCUIElementTypeApplication>"""

_ZERO_AREA_XML = """<?xml version="1.0" encoding="UTF-8"?>
<XCUIElementTypeApplication name="App" label="App" enabled="true" visible="true" x="0" y="0" width="440" height="956">
  <XCUIElementTypeButton name="Zero Width" x="10" y="10" width="0" height="50" visible="true"/>
  <XCUIElementTypeButton name="Zero Height" x="10" y="10" width="100" height="0" visible="true"/>
  <XCUIElementTypeButton name="Normal" x="10" y="10" width="100" height="50" visible="true"/>
</XCUIElementTypeApplication>"""

_OUT_OF_BOUNDS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<XCUIElementTypeApplication name="App" label="App" enabled="true" visible="true" x="0" y="0" width="440" height="956">
  <XCUIElementTypeButton name="OOB" x="500" y="1000" width="100" height="50" visible="true"/>
  <XCUIElementTypeButton name="InBounds" x="10" y="10" width="100" height="50" visible="true"/>
</XCUIElementTypeApplication>"""

_NESTED_DUP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<XCUIElementTypeApplication name="App" label="App" enabled="true" visible="true" x="0" y="0" width="440" height="956">
  <XCUIElementTypeCell name="Catalog" x="0" y="800" width="440" height="100" visible="true">
    <XCUIElementTypeButton name="Catalog" x="10" y="810" width="100" height="40" visible="true"/>
  </XCUIElementTypeCell>
  <XCUIElementTypeButton name="My Books" x="0" y="900" width="100" height="50" visible="true"/>
</XCUIElementTypeApplication>"""

_SCALE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<XCUIElementTypeApplication name="App" label="App" enabled="true" visible="true" x="0" y="0" width="440" height="956">
  <XCUIElementTypeButton name="Tap Me" x="100" y="200" width="110" height="50" visible="true"/>
</XCUIElementTypeApplication>"""

_MALFORMED_XML = "<<<not valid xml>>>"
_EMPTY_XML = ""


# ── WdaClient.source() tests ──────────────────────────────────────────────────


def _make_transport(responses: list) -> httpx.MockTransport:
    queue = list(responses)

    def _handler(request: httpx.Request) -> httpx.Response:
        if not queue:
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")
        status, body = queue.pop(0)
        if isinstance(body, dict):
            content = json.dumps(body).encode()
            headers = {"content-type": "application/json"}
        else:
            content = str(body).encode()
            headers = {"content-type": "text/plain"}
        return httpx.Response(status, content=content, headers=headers)

    return httpx.MockTransport(_handler)


def _make_client(responses: list):
    from simdrive.wda.client import WdaClient
    client = WdaClient(host="localhost", port=8100)
    client._replace_transport(_make_transport(responses))
    return client


def test_source_returns_xml_string():
    """WdaClient.source() must return the value field as a string."""
    xml = _SIMPLE_XML
    client = _make_client([(200, {"value": xml})])
    client._session_id = "test-sid"
    result = client.source()
    assert "Catalog" in result
    assert result == xml


def test_source_requires_open_session():
    """source() without session must raise wda_session_not_open."""
    from simdrive.errors import SimdriveError
    client = _make_client([])
    with pytest.raises(SimdriveError) as exc:
        client.source()
    assert exc.value.code == "wda_session_not_open"


def test_source_raises_on_http_error():
    """source() must propagate WDA HTTP errors."""
    from simdrive.errors import SimdriveError
    client = _make_client([(500, "server error")])
    client._session_id = "test-sid"
    with pytest.raises(SimdriveError) as exc:
        client.source()
    assert exc.value.code == "wda_http_error"


# ── annotate_device_screenshot tests ─────────────────────────────────────────


def _make_wda_mock(xml: str) -> MagicMock:
    wda = MagicMock()
    wda.source.return_value = xml
    return wda


def _write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_ONE_PX_PNG)


def test_basic_marks_produced(tmp_path):
    """annotate_device_screenshot returns marks for elements with text."""
    png = tmp_path / "observe-001.png"
    _write_png(png)
    wda = _make_wda_mock(_SIMPLE_XML)

    from simdrive.wda.som_device import annotate_device_screenshot
    marks, _ = annotate_device_screenshot(png, (440, 956), wda, point_scale=1.0)

    texts = {m["text"] for m in marks}
    # "Catalog" and "My Books" must appear; empty-text button must be excluded.
    # Root XCUIElementTypeApplication "Palace" is not a leaf with unique text below it,
    # but it has text "Palace" and no child has the same text, so it may appear.
    assert "Catalog" in texts
    assert "My Books" in texts
    # Empty-text button (name="" label="" value="") must be excluded.
    assert "" not in texts


def test_mark_shape_matches_som_dict(tmp_path):
    """Each mark dict must have all required keys matching som.Mark.to_dict()."""
    png = tmp_path / "observe-002.png"
    _write_png(png)
    wda = _make_wda_mock(_SCALE_XML)

    from simdrive.wda.som_device import annotate_device_screenshot
    marks, _ = annotate_device_screenshot(png, (440, 956), wda, point_scale=1.0)

    required_keys = {
        "id", "stable_id", "stable_id_loose", "bbox", "center",
        "text", "confidence", "raw_confidence", "confidence_band",
    }
    assert marks, "expected at least one mark"
    for m in marks:
        assert required_keys <= set(m.keys()), f"missing keys: {required_keys - set(m.keys())}"
        assert isinstance(m["id"], int)
        assert isinstance(m["stable_id"], str) and len(m["stable_id"]) == 12
        assert isinstance(m["stable_id_loose"], str) and len(m["stable_id_loose"]) == 12
        assert isinstance(m["bbox"], list) and len(m["bbox"]) == 4
        assert isinstance(m["center"], list) and len(m["center"]) == 2
        assert m["confidence_band"] in ("low", "medium", "high")


def test_invisible_elements_excluded(tmp_path):
    """Elements with visible=false must not appear in marks."""
    png = tmp_path / "observe-003.png"
    _write_png(png)
    wda = _make_wda_mock(_INVISIBLE_XML)

    from simdrive.wda.som_device import annotate_device_screenshot
    marks, _ = annotate_device_screenshot(png, (440, 956), wda, point_scale=1.0)

    texts = {m["text"] for m in marks}
    assert "Hidden" not in texts, "visible=false element must be excluded"
    assert "Visible" in texts


def test_zero_area_excluded(tmp_path):
    """Elements with width=0 or height=0 must not appear."""
    png = tmp_path / "observe-004.png"
    _write_png(png)
    wda = _make_wda_mock(_ZERO_AREA_XML)

    from simdrive.wda.som_device import annotate_device_screenshot
    marks, _ = annotate_device_screenshot(png, (440, 956), wda, point_scale=1.0)

    texts = {m["text"] for m in marks}
    assert "Zero Width" not in texts
    assert "Zero Height" not in texts
    assert "Normal" in texts


def test_out_of_bounds_excluded(tmp_path):
    """Elements whose bbox is entirely outside screenshot bounds must be excluded."""
    png = tmp_path / "observe-005.png"
    _write_png(png)
    wda = _make_wda_mock(_OUT_OF_BOUNDS_XML)

    from simdrive.wda.som_device import annotate_device_screenshot
    marks, _ = annotate_device_screenshot(png, (440, 956), wda, point_scale=1.0)

    texts = {m["text"] for m in marks}
    assert "OOB" not in texts
    assert "InBounds" in texts


def test_nested_duplicate_emits_child(tmp_path):
    """When parent and child have identical text, only the child (deepest) is emitted."""
    png = tmp_path / "observe-006.png"
    _write_png(png)
    wda = _make_wda_mock(_NESTED_DUP_XML)

    from simdrive.wda.som_device import annotate_device_screenshot
    marks, _ = annotate_device_screenshot(png, (440, 956), wda, point_scale=1.0)

    catalog_marks = [m for m in marks if m["text"] == "Catalog"]
    assert len(catalog_marks) == 1, "exactly one Catalog mark must be emitted (child only)"
    # Child bbox: x=10,y=810,w=100,h=40 — must match the smaller (child) element
    m = catalog_marks[0]
    assert m["bbox"][2] == 100, f"expected child width=100, got {m['bbox'][2]}"
    assert m["bbox"][3] == 40, f"expected child height=40, got {m['bbox'][3]}"


def test_point_scale_applied(tmp_path):
    """Pixel bbox must equal point coords × point_scale."""
    png = tmp_path / "observe-007.png"
    # Use a large screenshot so scaled coords are in bounds.
    from PIL import Image
    img = Image.new("RGB", (1320, 2868), "white")
    img.save(png)

    wda = _make_wda_mock(_SCALE_XML)
    from simdrive.wda.som_device import annotate_device_screenshot
    marks, _ = annotate_device_screenshot(png, (1320, 2868), wda, point_scale=3.0)

    assert marks, "expected at least one mark"
    m = next(m for m in marks if m["text"] == "Tap Me")
    # point coords: x=100, y=200, w=110, h=50 → pixels with scale=3: 300, 600, 330, 150
    assert m["bbox"] == [300, 600, 330, 150], (
        f"expected [300,600,330,150], got {m['bbox']}"
    )
    assert m["center"] == [300 + 330 // 2, 600 + 150 // 2]


def test_stable_id_matches_som_mark_directly(tmp_path):
    """stable_id from annotate_device_screenshot must match som.Mark.stable_id."""
    from simdrive.som import Mark

    png = tmp_path / "observe-008.png"
    _write_png(png)
    wda = _make_wda_mock(_SCALE_XML)

    from simdrive.wda.som_device import annotate_device_screenshot
    marks, _ = annotate_device_screenshot(png, (440, 956), wda, point_scale=1.0)

    assert marks
    m_dict = marks[0]
    # Reconstruct a Mark with same coords to verify the hash is identical.
    x, y, w, h = m_dict["bbox"]
    ref = Mark(id=1, x=x, y=y, w=w, h=h, text=m_dict["text"], confidence=1.0)
    assert m_dict["stable_id"] == ref.stable_id
    assert m_dict["stable_id_loose"] == ref.stable_id_loose


def test_malformed_xml_returns_empty(tmp_path):
    """Malformed XML from WDA /source must return marks=[], not raise."""
    png = tmp_path / "observe-009.png"
    _write_png(png)
    wda = _make_wda_mock(_MALFORMED_XML)

    from simdrive.wda.som_device import annotate_device_screenshot
    marks, annotated = annotate_device_screenshot(png, (440, 956), wda, point_scale=1.0)
    assert marks == []
    assert annotated is None


def test_wda_source_error_returns_empty(tmp_path):
    """WDA /source HTTP error must return marks=[], not raise."""
    from simdrive.errors import SimdriveError

    png = tmp_path / "observe-010.png"
    _write_png(png)
    wda = MagicMock()
    wda.source.side_effect = SimdriveError(
        code="wda_http_error",
        message="WDA failure",
        details={},
    )

    from simdrive.wda.som_device import annotate_device_screenshot
    marks, annotated = annotate_device_screenshot(png, (440, 956), wda, point_scale=1.0)
    assert marks == []
    assert annotated is None


def test_empty_xml_returns_empty(tmp_path):
    """Empty XML string from WDA /source must return marks=[]."""
    png = tmp_path / "observe-011.png"
    _write_png(png)
    wda = _make_wda_mock(_EMPTY_XML)

    from simdrive.wda.som_device import annotate_device_screenshot
    marks, annotated = annotate_device_screenshot(png, (440, 956), wda, point_scale=1.0)
    assert marks == []
    assert annotated is None


def test_annotated_png_written(tmp_path):
    """When marks are found, annotated PNG must be written to <stem>_annotated.png."""
    from PIL import Image
    # Use a proper-sized image so drawing doesn't fail.
    png = tmp_path / "observe-012.png"
    img = Image.new("RGB", (440, 956), "white")
    img.save(png)
    wda = _make_wda_mock(_SCALE_XML)

    from simdrive.wda.som_device import annotate_device_screenshot
    marks, annotated_path = annotate_device_screenshot(
        png, (440, 956), wda, point_scale=1.0,
    )

    assert marks, "expected at least one mark"
    assert annotated_path is not None
    expected = png.parent / (png.stem + "_annotated.png")
    assert annotated_path == expected
    assert annotated_path.exists()


def test_annotated_png_no_double_draw(tmp_path):
    """Calling annotate_device_screenshot twice overwrites the same file (no dup)."""
    from PIL import Image
    png = tmp_path / "observe-013.png"
    img = Image.new("RGB", (440, 956), "white")
    img.save(png)
    wda = _make_wda_mock(_SCALE_XML)

    from simdrive.wda.som_device import annotate_device_screenshot
    _, path1 = annotate_device_screenshot(png, (440, 956), wda, point_scale=1.0)
    wda2 = _make_wda_mock(_SCALE_XML)
    _, path2 = annotate_device_screenshot(png, (440, 956), wda2, point_scale=1.0)

    assert path1 == path2, "both calls must write to the same annotated path"


def test_annotate_false_skips_som(tmp_path):
    """annotate=False in tool_observe must skip annotate_device_screenshot entirely."""
    from simdrive import session as session_mod
    from simdrive.sim import Device

    d = Device(udid="SOM-TEST-UDID", name="Test Device", os_version="26", state="active")
    workdir = tmp_path / "sessions" / "somtest"
    workdir.mkdir(parents=True, exist_ok=True)
    s = session_mod.Session(
        session_id="somtest", device=d, workdir=workdir, target="device",
    )
    session_mod._SESSIONS["somtest"] = s

    mock_wda = MagicMock()
    mock_wda.screenshot_any.return_value = _ONE_PX_PNG

    called = {"annotate": False}

    def _fake_annotate(*args, **kwargs):
        called["annotate"] = True
        return [], None

    try:
        with patch("simdrive.wda.registry.load", return_value={"host": "localhost", "port": 8100}), \
             patch("simdrive.wda.client.WdaClient") as mock_cls, \
             patch("simdrive.wda.som_device.annotate_device_screenshot", side_effect=_fake_annotate):
            mock_cls.return_value = mock_wda
            from simdrive.server import tool_observe
            result = tool_observe({"session_id": "somtest", "annotate": False})

        assert not called["annotate"], "annotate_device_screenshot must NOT be called when annotate=False"
        assert result["marks"] == []
        assert result["annotated_path"] is None
    finally:
        session_mod._SESSIONS.pop("somtest", None)


def test_annotate_true_calls_som(tmp_path):
    """annotate=True (default) must call annotate_device_screenshot and populate marks."""
    from simdrive import session as session_mod
    from simdrive.sim import Device

    d = Device(udid="SOM-TEST-UDID-2", name="Test Device", os_version="26", state="active")
    workdir = tmp_path / "sessions" / "somtest2"
    workdir.mkdir(parents=True, exist_ok=True)
    s = session_mod.Session(
        session_id="somtest2", device=d, workdir=workdir, target="device",
    )
    session_mod._SESSIONS["somtest2"] = s

    mock_wda = MagicMock()
    mock_wda.screenshot_any.return_value = _ONE_PX_PNG

    fake_mark = {
        "id": 1, "stable_id": "aabbcc112233", "stable_id_loose": "ddeeff445566",
        "bbox": [0, 0, 1, 1], "center": [0, 0],
        "text": "Catalog", "confidence": 1.0, "raw_confidence": 1.0,
        "confidence_band": "high",
    }

    try:
        with patch("simdrive.wda.registry.load", return_value={"host": "localhost", "port": 8100}), \
             patch("simdrive.wda.client.WdaClient") as mock_cls, \
             patch("simdrive.wda.som_device.annotate_device_screenshot",
                   return_value=([fake_mark], None)) as mock_annotate:
            mock_cls.return_value = mock_wda
            from simdrive.server import tool_observe
            result = tool_observe({"session_id": "somtest2", "annotate": True})

        mock_annotate.assert_called_once()
        assert result["marks"] == [fake_mark]
        assert s.last_marks == [fake_mark]
    finally:
        session_mod._SESSIONS.pop("somtest2", None)
