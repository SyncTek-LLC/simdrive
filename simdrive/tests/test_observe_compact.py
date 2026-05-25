"""Tests for the PR A token-efficiency knobs on ``observe.observe()``.

Covers the four new parameters introduced under [internal-tracker]:
* ``compact=True`` — slim mark dict via ``Mark.to_compact_dict()``
* ``confidence_floor`` — drop marks below the requested band
* ``mark_limit`` — cap the returned list to top-N by (band, area)
* ``capture_observability`` — append per-mark band-derivation breadcrumbs

These tests stub out the simulator and Vision OCR so we exercise the
filtering / serialization logic without a running device.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from simdrive import observe
from simdrive.som import Mark
from simdrive.window import WindowBounds


def _make_png(path: Path, w: int = 100, h: int = 200) -> None:
    Image.new("RGB", (w, h), (123, 0, 0)).save(path)


def _high_mark(mid: int, text: str = "Login", x: int = 10, y: int = 10, w: int = 60, h: int = 20) -> Mark:
    """A mark whose text passes the dictionary fence + raw conf >= 0.85."""
    return Mark(id=mid, x=x, y=y, w=w, h=h, text=text, confidence=0.95)


def _medium_mark(mid: int, text: str = "Welcome back", x: int = 10, y: int = 50, w: int = 60, h: int = 20) -> Mark:
    """A mark whose dictionary check passes but raw conf is < 0.85."""
    return Mark(id=mid, x=x, y=y, w=w, h=h, text=text, confidence=0.5)


def _low_mark(mid: int, text: str = "Xyzzy Plough Krrng", x: int = 10, y: int = 90, w: int = 60, h: int = 20) -> Mark:
    """A mark whose dictionary check fails — clamped to band='low'."""
    return Mark(id=mid, x=x, y=y, w=w, h=h, text=text, confidence=0.99)


def _stub_observe(tmp_path: Path, marks: list[Mark], **kwargs):
    """Run observe.observe() with sim/OCR stubbed out and a fixed window."""
    def fake_screenshot(udid, dest_path):
        _make_png(dest_path)
        return dest_path

    def fake_annotate(src, marks_arg, dest):
        _make_png(dest)

    with patch("simdrive.observe.sim.screenshot", side_effect=fake_screenshot), \
         patch("simdrive.observe.som.detect_marks", return_value=list(marks)), \
         patch("simdrive.observe.som.annotate", side_effect=fake_annotate), \
         patch("simdrive.observe.get_bounds", return_value=WindowBounds(0, 0, 100, 200)):
        return observe.observe("UDID", tmp_path, annotate=True, **kwargs)


# ---------------------------------------------------------------------------
# Mark.to_compact_dict — pure unit test, no observe() pipeline
# ---------------------------------------------------------------------------


def test_to_compact_dict_returns_exactly_six_keys():
    m = _high_mark(7)
    d = m.to_compact_dict()
    assert set(d.keys()) == {"id", "stable_id", "text", "center", "bbox", "confidence_band"}


def test_to_compact_dict_drops_diagnostic_fields():
    """Compact dict MUST NOT include raw_confidence / clamped confidence / stable_id_loose."""
    d = _high_mark(1).to_compact_dict()
    for forbidden in ("raw_confidence", "confidence", "stable_id_loose"):
        assert forbidden not in d


def test_to_compact_dict_values_match_to_dict():
    m = _high_mark(3, text="Submit", x=12, y=34, w=56, h=78)
    full = m.to_dict()
    compact = m.to_compact_dict()
    assert compact["id"] == full["id"]
    assert compact["stable_id"] == full["stable_id"]
    assert compact["text"] == full["text"]
    assert compact["bbox"] == full["bbox"]
    assert compact["center"] == full["center"]
    assert compact["confidence_band"] == full["confidence_band"]


def test_to_compact_dict_is_json_serializable():
    json.dumps(_high_mark(1).to_compact_dict())


# ---------------------------------------------------------------------------
# observe(compact=True) — Observation.to_dict integration
# ---------------------------------------------------------------------------


def test_observe_compact_emits_compact_marks(tmp_path):
    obs = _stub_observe(tmp_path, [_high_mark(1), _high_mark(2, text="Cancel", y=40)], compact=True)
    d = obs.to_dict()
    assert d["marks"], "compact=True should still emit marks"
    for md in d["marks"]:
        assert set(md.keys()) == {"id", "stable_id", "text", "center", "bbox", "confidence_band"}


def test_observe_compact_false_keeps_legacy_payload(tmp_path):
    """Default compact=False must preserve the full diagnostic mark dict."""
    obs = _stub_observe(tmp_path, [_high_mark(1)])
    d = obs.to_dict()
    legacy_keys = {"id", "stable_id", "stable_id_loose", "bbox", "center", "text",
                   "confidence", "raw_confidence", "confidence_band"}
    assert legacy_keys.issubset(set(d["marks"][0].keys()))


# ---------------------------------------------------------------------------
# confidence_floor filtering
# ---------------------------------------------------------------------------


def test_confidence_floor_high_drops_med_and_low(tmp_path):
    marks = [_high_mark(1), _medium_mark(2), _low_mark(3)]
    obs = _stub_observe(tmp_path, marks, confidence_floor="high")
    bands = sorted({m.confidence_band for m in obs.marks})
    assert bands == ["high"]
    assert len(obs.marks) == 1
    assert obs.marks[0].id == 1


def test_confidence_floor_med_keeps_high_and_medium(tmp_path):
    marks = [_high_mark(1), _medium_mark(2), _low_mark(3)]
    obs = _stub_observe(tmp_path, marks, confidence_floor="med")
    ids = sorted(m.id for m in obs.marks)
    assert ids == [1, 2]


def test_confidence_floor_medium_alias_matches_med(tmp_path):
    """`confidence_floor="medium"` must behave identically to the "med" shorthand."""
    marks = [_high_mark(1), _medium_mark(2), _low_mark(3)]
    obs = _stub_observe(tmp_path, marks, confidence_floor="medium")
    ids = sorted(m.id for m in obs.marks)
    assert ids == [1, 2]


def test_confidence_floor_low_keeps_all(tmp_path):
    marks = [_high_mark(1), _medium_mark(2), _low_mark(3)]
    obs = _stub_observe(tmp_path, marks, confidence_floor="low")
    assert len(obs.marks) == 3


def test_confidence_floor_none_is_default_keep_all(tmp_path):
    marks = [_high_mark(1), _medium_mark(2), _low_mark(3)]
    obs = _stub_observe(tmp_path, marks)
    assert len(obs.marks) == 3


def test_confidence_floor_invalid_raises(tmp_path):
    with pytest.raises(ValueError, match="confidence_floor"):
        _stub_observe(tmp_path, [_high_mark(1)], confidence_floor="extreme")


# ---------------------------------------------------------------------------
# mark_limit truncation
# ---------------------------------------------------------------------------


def test_mark_limit_truncates_to_top_n(tmp_path):
    # Three marks; mark_limit=2 should drop the lowest-ranked one.
    marks = [_high_mark(1), _medium_mark(2), _low_mark(3)]
    obs = _stub_observe(tmp_path, marks, mark_limit=2)
    assert len(obs.marks) == 2
    # Highest band (high) must always survive; low must be dropped before med.
    kept_ids = {m.id for m in obs.marks}
    assert 1 in kept_ids
    assert 3 not in kept_ids


def test_mark_limit_zero_returns_empty(tmp_path):
    obs = _stub_observe(tmp_path, [_high_mark(1)], mark_limit=0)
    assert obs.marks == []


def test_mark_limit_larger_than_marks_is_no_op(tmp_path):
    marks = [_high_mark(1), _medium_mark(2)]
    obs = _stub_observe(tmp_path, marks, mark_limit=99)
    assert len(obs.marks) == 2


def test_mark_limit_applied_after_floor(tmp_path):
    """floor='high', mark_limit=1 should pick the 1 high mark, not a medium one."""
    marks = [_high_mark(1, text="Login"), _medium_mark(2), _medium_mark(3, y=120),
             _high_mark(4, text="Submit", y=140)]
    obs = _stub_observe(tmp_path, marks, confidence_floor="high", mark_limit=1)
    assert len(obs.marks) == 1
    assert obs.marks[0].confidence_band == "high"


def test_mark_limit_negative_raises(tmp_path):
    with pytest.raises(ValueError, match="mark_limit"):
        _stub_observe(tmp_path, [_high_mark(1)], mark_limit=-1)


def test_mark_limit_tiebreak_prefers_larger_area(tmp_path):
    """When two marks share a band, the larger-area one wins the limit slot."""
    big = _high_mark(1, text="Login", w=200, h=80)
    small = _high_mark(2, text="Help", x=10, y=120, w=40, h=20)
    obs = _stub_observe(tmp_path, [big, small], mark_limit=1)
    assert len(obs.marks) == 1
    assert obs.marks[0].id == 1


# ---------------------------------------------------------------------------
# capture_observability — per-mark derivation breadcrumbs
# ---------------------------------------------------------------------------


def test_capture_observability_off_by_default_no_field(tmp_path):
    obs = _stub_observe(tmp_path, [_high_mark(1)])
    assert "_observability" not in obs.to_dict()


def test_capture_observability_emits_field(tmp_path):
    obs = _stub_observe(tmp_path, [_high_mark(1), _low_mark(2)], capture_observability=True)
    d = obs.to_dict()
    assert "_observability" in d
    assert len(d["_observability"]) == 2


def test_capture_observability_length_matches_returned_marks(tmp_path):
    """The `_observability` array must align 1:1 with `marks` after filtering."""
    marks = [_high_mark(1), _medium_mark(2), _low_mark(3)]
    obs = _stub_observe(
        tmp_path, marks,
        confidence_floor="high",
        capture_observability=True,
    )
    d = obs.to_dict()
    assert len(d["marks"]) == 1
    assert len(d["_observability"]) == 1
    assert d["_observability"][0]["mark_id"] == d["marks"][0]["id"]


def test_capture_observability_entry_shape(tmp_path):
    obs = _stub_observe(tmp_path, [_low_mark(7)], capture_observability=True)
    entry = obs.to_dict()["_observability"][0]
    assert set(entry.keys()) == {
        "mark_id", "raw_confidence", "clamped_confidence",
        "confidence_band", "dictionary_check", "reason",
    }
    assert entry["mark_id"] == 7
    assert entry["dictionary_check"] == "failed"
    assert entry["confidence_band"] == "low"


def test_capture_observability_with_compact(tmp_path):
    """capture_observability and compact=True must compose cleanly."""
    obs = _stub_observe(
        tmp_path,
        [_high_mark(1), _low_mark(2)],
        compact=True,
        capture_observability=True,
    )
    d = obs.to_dict()
    # Marks are compact (6 keys) but _observability still present.
    assert all(set(m.keys()) == {"id", "stable_id", "text", "center", "bbox", "confidence_band"}
               for m in d["marks"])
    assert len(d["_observability"]) == 2


# ---------------------------------------------------------------------------
# Backward-compatibility — defaults preserve legacy behavior
# ---------------------------------------------------------------------------


def test_defaults_match_legacy_payload(tmp_path):
    """A call with no new args must produce the same top-level keys as before PR A."""
    obs = _stub_observe(tmp_path, [_high_mark(1)])
    d = obs.to_dict()
    expected = {
        "screenshot_path", "annotated_path", "screenshot_size_pixels",
        "window_bounds_macos", "captured_at", "marks", "recent_logs",
    }
    assert set(d.keys()) == expected


# ---------------------------------------------------------------------------
# Server-wire integration for the *device* path. The device branch of
# tool_observe builds its Observation dict manually (does NOT call
# observe.observe()), so the filtering helpers had to be mirrored inline.
# These tests pin that the device branch honors the same four knobs.
# ---------------------------------------------------------------------------


_ONE_PX_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806"
    "0000001f15c4890000000a49444154789c6260000000020001"
    "e221bc330000000049454e44ae426082"
)


def _make_device_obs_session(tmp_path):
    """Helper: build a device-target Session with a mocked WdaClient."""
    from unittest.mock import MagicMock
    from simdrive import session as session_mod
    from simdrive.sim import Device

    wda = MagicMock()
    wda._session_id = "open"
    wda.screenshot_any.return_value = _ONE_PX_PNG
    d = Device(udid="DEVICE-FILT-TEST", name="Test", os_version="26.0", state="active")
    workdir = tmp_path / "sessions" / "devfilt"
    workdir.mkdir(parents=True, exist_ok=True)
    s = session_mod.Session(
        session_id="devfilt",
        device=d,
        workdir=workdir,
        target="device",
        wda_client=wda,
    )
    session_mod._SESSIONS["devfilt"] = s
    return s, wda


def _device_marks_three_bands() -> list[dict]:
    """3 dict-shape marks (1 high, 1 med, 1 low) as annotate_device_screenshot returns."""
    return [
        {"id": 1, "stable_id": "s1", "text": "High", "center": (10, 10),
         "bbox": (0, 0, 50, 30), "w": 50, "h": 30,
         "confidence_band": "high", "raw_confidence": 0.95, "clamped_confidence": 0.95,
         "dictionary_check": "passed"},
        {"id": 2, "stable_id": "s2", "text": "Mid",  "center": (20, 20),
         "bbox": (10, 10, 60, 40), "w": 50, "h": 30,
         "confidence_band": "med", "raw_confidence": 0.75, "clamped_confidence": 0.75,
         "dictionary_check": "passed"},
        {"id": 3, "stable_id": "s3", "text": "Low",  "center": (30, 30),
         "bbox": (20, 20, 70, 50), "w": 50, "h": 30,
         "confidence_band": "low", "raw_confidence": 0.42, "clamped_confidence": 0.42,
         "dictionary_check": "failed"},
    ]


def test_tool_observe_device_confidence_floor_drops_low_bands(tmp_path):
    s, wda = _make_device_obs_session(tmp_path)
    from simdrive import server
    fake_marks = _device_marks_three_bands()
    with patch("simdrive.wda.som_device.annotate_device_screenshot",
               return_value=(fake_marks, None)):
        result = server.tool_observe({
            "session_id": "devfilt",
            "confidence_floor": "high",
        })
    emitted_ids = [m["id"] for m in result["marks"]]
    assert emitted_ids == [1], "only the high-band mark should survive floor='high'"
    # Session.last_marks holds the UNFILTERED set so the resolver isn't degraded.
    assert {m["id"] for m in s.last_marks} == {1, 2, 3}


def test_tool_observe_device_compact_drops_diagnostic_keys(tmp_path):
    _make_device_obs_session(tmp_path)
    from simdrive import server
    fake_marks = _device_marks_three_bands()
    with patch("simdrive.wda.som_device.annotate_device_screenshot",
               return_value=(fake_marks, None)):
        result = server.tool_observe({"session_id": "devfilt", "compact": True})
    for m in result["marks"]:
        assert set(m.keys()) <= {"id", "stable_id", "text", "center", "bbox", "confidence_band"}
        assert "raw_confidence" not in m
        assert "dictionary_check" not in m


def test_tool_observe_device_mark_limit_caps_results(tmp_path):
    _make_device_obs_session(tmp_path)
    from simdrive import server
    fake_marks = _device_marks_three_bands()
    with patch("simdrive.wda.som_device.annotate_device_screenshot",
               return_value=(fake_marks, None)):
        result = server.tool_observe({"session_id": "devfilt", "mark_limit": 1})
    assert len(result["marks"]) == 1
    # Top-by-(band, area): id=1 (high) wins over id=2 (med) and id=3 (low).
    assert result["marks"][0]["id"] == 1


def test_tool_observe_device_capture_observability_emits_per_mark(tmp_path):
    _make_device_obs_session(tmp_path)
    from simdrive import server
    fake_marks = _device_marks_three_bands()
    with patch("simdrive.wda.som_device.annotate_device_screenshot",
               return_value=(fake_marks, None)):
        result = server.tool_observe({
            "session_id": "devfilt",
            "confidence_floor": "med",
            "capture_observability": True,
        })
    assert "_observability" in result
    # One entry per *emitted* mark — floor='med' keeps ids 1 & 2.
    obs_ids = sorted(e["mark_id"] for e in result["_observability"])
    assert obs_ids == [1, 2]
    # Diagnostic fields present in observability even when compact dropped them.
    for entry in result["_observability"]:
        assert entry["dictionary_check"] in {"passed", "failed"}
        assert isinstance(entry["raw_confidence"], float)
