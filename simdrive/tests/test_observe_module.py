"""Unit tests for ``simdrive.observe`` — screenshot + SoM + logs pipeline.

These tests stub out ``sim.screenshot``, ``som.detect_marks``, and
``window.get_bounds`` so we exercise the orchestrator without touching a
running simulator or display.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from PIL import Image

from simdrive import observe
from simdrive.som import Mark
from simdrive.window import WindowBounds


def _make_png(path: Path, w: int = 100, h: int = 200) -> None:
    Image.new("RGB", (w, h), (123, 0, 0)).save(path)


def test_observe_writes_sidecar_json(tmp_path):
    """observe() should drop a sibling .json next to the .png with the structured payload."""
    def fake_screenshot(udid, dest_path):
        _make_png(dest_path, 50, 80)
        return dest_path

    with patch("simdrive.observe.sim.screenshot", side_effect=fake_screenshot), \
         patch("simdrive.observe.som.detect_marks", return_value=[]), \
         patch("simdrive.observe.get_bounds", return_value=WindowBounds(0, 0, 50, 80)):
        obs = observe.observe("UDID", tmp_path, annotate=True)

    # PNG written
    assert obs.screenshot_path.exists()
    # Sidecar JSON exists next to the PNG with same stem
    sidecar = obs.screenshot_path.with_suffix(".json")
    assert sidecar.exists()
    assert obs.screenshot_w == 50
    assert obs.screenshot_h == 80
    assert obs.marks == []


def test_observe_with_marks_writes_annotated_png(tmp_path):
    def fake_screenshot(udid, dest_path):
        _make_png(dest_path)
        return dest_path

    marks = [Mark(id=1, x=10, y=10, w=20, h=10, text="OK", confidence=0.9)]

    def fake_annotate(src, marks_arg, dest):
        # Write a stub annotated PNG so the path is real.
        _make_png(dest)

    with patch("simdrive.observe.sim.screenshot", side_effect=fake_screenshot), \
         patch("simdrive.observe.som.detect_marks", return_value=marks), \
         patch("simdrive.observe.som.annotate", side_effect=fake_annotate), \
         patch("simdrive.observe.get_bounds", return_value=WindowBounds(0, 0, 100, 200)):
        obs = observe.observe("UDID", tmp_path, annotate=True)

    assert obs.annotated_path is not None
    assert obs.annotated_path.exists()
    assert obs.marks == marks


def test_observe_annotate_false_still_returns_marks(tmp_path):
    """F#7 contract: annotate=False skips annotation *rendering* but detect_marks
    is still called so text-targeting agents always receive marks.
    annotated_path stays None; marks are returned normally."""
    def fake_screenshot(udid, dest_path):
        _make_png(dest_path)
        return dest_path

    marks = [Mark(id=1, x=5, y=5, w=30, h=10, text="Submit", confidence=0.95)]
    with patch("simdrive.observe.sim.screenshot", side_effect=fake_screenshot), \
         patch("simdrive.observe.som.detect_marks", return_value=marks) as mock_marks, \
         patch("simdrive.observe.get_bounds", return_value=WindowBounds(0, 0, 100, 200)):
        obs = observe.observe("UDID", tmp_path, annotate=False)
    # detect_marks IS called — marks must be available for text targeting.
    assert mock_marks.called
    assert obs.marks == marks
    # Annotation drawing is skipped — no SoM overlay written.
    assert obs.annotated_path is None


def test_observe_captures_logs_sim(tmp_path):
    def fake_screenshot(udid, dest_path):
        _make_png(dest_path)
        return dest_path

    with patch("simdrive.observe.sim.screenshot", side_effect=fake_screenshot), \
         patch("simdrive.observe.som.detect_marks", return_value=[]), \
         patch("simdrive.observe.sim.get_log_tail", return_value="log line 1\nlog line 2"), \
         patch("simdrive.observe.get_bounds", return_value=WindowBounds(0, 0, 100, 200)):
        obs = observe.observe("UDID", tmp_path, annotate=False, capture_logs=True)
    assert obs.recent_logs is not None
    assert "log line 1" in obs.recent_logs


def test_observe_log_capture_failure_is_swallowed(tmp_path):
    """A log-capture exception must not break the observation — set logs to a marker string."""
    def fake_screenshot(udid, dest_path):
        _make_png(dest_path)
        return dest_path

    with patch("simdrive.observe.sim.screenshot", side_effect=fake_screenshot), \
         patch("simdrive.observe.som.detect_marks", return_value=[]), \
         patch("simdrive.observe.sim.get_log_tail", side_effect=RuntimeError("log proc died")), \
         patch("simdrive.observe.get_bounds", return_value=WindowBounds(0, 0, 100, 200)):
        obs = observe.observe("UDID", tmp_path, annotate=False, capture_logs=True)
    assert obs.recent_logs is not None
    assert "log capture failed" in obs.recent_logs


def test_observe_bounds_failure_yields_none_bounds(tmp_path):
    """If get_bounds() raises (no Simulator window), bounds=None — not a failure."""
    def fake_screenshot(udid, dest_path):
        _make_png(dest_path)
        return dest_path

    with patch("simdrive.observe.sim.screenshot", side_effect=fake_screenshot), \
         patch("simdrive.observe.som.detect_marks", return_value=[]), \
         patch("simdrive.observe.get_bounds", side_effect=RuntimeError("no window")):
        obs = observe.observe("UDID", tmp_path, annotate=False)
    assert obs.window_bounds is None


def test_observe_device_target_routes_to_device_screenshot(tmp_path):
    """target='device' must call device.screenshot, not sim.screenshot."""
    def fake_dev_screenshot(udid, dest_path):
        _make_png(dest_path)
        return dest_path

    with patch("simdrive.observe.sim.screenshot") as mock_sim_ss, \
         patch("simdrive.device.screenshot", side_effect=fake_dev_screenshot) as mock_dev, \
         patch("simdrive.observe.som.detect_marks", return_value=[]), \
         patch("simdrive.observe.get_bounds", return_value=None):
        obs = observe.observe("UDID", tmp_path, annotate=False, target="device")
    assert mock_dev.called
    assert not mock_sim_ss.called
    assert obs.screenshot_path.exists()


def test_observe_device_logs_route(tmp_path):
    """capture_logs=True + target=device routes to device.get_log_tail."""
    def fake_dev_screenshot(udid, dest_path):
        _make_png(dest_path)
        return dest_path

    with patch("simdrive.device.screenshot", side_effect=fake_dev_screenshot), \
         patch("simdrive.device.get_log_tail", return_value="device log"), \
         patch("simdrive.observe.som.detect_marks", return_value=[]), \
         patch("simdrive.observe.get_bounds", return_value=None):
        obs = observe.observe("UDID", tmp_path, annotate=False, capture_logs=True, target="device")
    assert obs.recent_logs == "device log"


def test_observation_to_dict_roundtrip(tmp_path):
    """Observation.to_dict produces JSON-safe primitives only."""
    import json

    raw = tmp_path / "raw.png"
    _make_png(raw)
    obs = observe.Observation(
        screenshot_path=raw,
        annotated_path=None,
        screenshot_w=100,
        screenshot_h=200,
        window_bounds=WindowBounds(1, 2, 3, 4),
        captured_at=1.0,
        marks=[Mark(id=1, x=0, y=0, w=10, h=10, text="ok", confidence=0.9)],
        recent_logs="hi",
    )
    d = obs.to_dict()
    # Must be JSON-serializable
    json.dumps(d)
    assert d["screenshot_size_pixels"] == [100, 200]
    assert d["window_bounds_macos"]["x"] == 1
    assert len(d["marks"]) == 1
    assert d["recent_logs"] == "hi"


def test_observation_to_dict_handles_none_paths_and_bounds(tmp_path):
    obs = observe.Observation(
        screenshot_path=tmp_path / "x.png",
        annotated_path=None,
        screenshot_w=10,
        screenshot_h=20,
        window_bounds=None,
        captured_at=2.0,
    )
    d = obs.to_dict()
    assert d["annotated_path"] is None
    assert d["window_bounds_macos"] is None
    assert d["marks"] == []
