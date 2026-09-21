"""Hermetic tests for INIT-2026-641 Wave 2 — AX-primary wiring in
``simdrive.observe.observe()``: visible degradation, reactivate/retry, and
the multi-sim/headless routing guard.

Every AX call is monkeypatched on the ``simdrive.observe.ax`` module — no
pyobjc, no booted simulator. The live counterpart is
``tests/test_ax_live_acceptance.py`` (marked ``live``).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from PIL import Image

from simdrive import ax, observe
from simdrive.som import Mark
from simdrive.window import WindowBounds


def _make_png(path: Path, w: int = 1320, h: int = 2868) -> None:
    Image.new("RGB", (w, h), (10, 10, 10)).save(path)


def _fake_screenshot(udid, dest_path):
    _make_png(dest_path)
    return dest_path


_OCR_MARKS = [Mark(id=1, x=560, y=1660, w=195, h=41, text="Continue", confidence=0.9)]
_AX_ELEMENTS = [
    {"role": "button", "label": "Continue", "enabled": True, "bbox": [532, 1650, 384, 51]},
]


def test_ax_unavailable_falls_back_to_ocr_visibly(tmp_path):
    """AX unavailable (no permission / no sim window) — must still return
    marks via OCR, with a field the calling agent can read that distinguishes
    this from a clean AX read. Never merely 'marks came back'.
    """
    with patch("simdrive.observe.sim.screenshot", side_effect=_fake_screenshot), \
         patch("simdrive.observe.som.detect_marks", return_value=list(_OCR_MARKS)), \
         patch("simdrive.observe.get_bounds", return_value=WindowBounds(0, 0, 100, 200)), \
         patch("simdrive.observe.ax.is_available", return_value=False), \
         patch("simdrive.observe.ax.observe_pixel_elements") as mock_ax:
        obs = observe.observe("UDID", tmp_path, annotate=False, device_name="iPhone 17 Pro Max")

    assert obs.resolution_method == "ocr"
    assert obs.degraded is True
    assert obs.degraded_reason == "ax_unavailable"
    assert not mock_ax.called
    assert len(obs.marks) == 1
    d = obs.to_dict()
    assert d["resolution_method"] == "ocr"
    assert d["degraded"] is True


def test_ax_available_merges_and_reports_clean_resolution(tmp_path):
    with patch("simdrive.observe.sim.screenshot", side_effect=_fake_screenshot), \
         patch("simdrive.observe.som.detect_marks", return_value=list(_OCR_MARKS)), \
         patch("simdrive.observe.get_bounds", return_value=WindowBounds(0, 0, 100, 200)), \
         patch("simdrive.observe.ax.is_available", return_value=True), \
         patch(
             "simdrive.observe.ax.observe_pixel_elements",
             return_value={
                 "elements": _AX_ELEMENTS,
                 "resolution_method": "ax",
                 "reactivated": False,
             },
         ):
        obs = observe.observe("UDID", tmp_path, annotate=False, device_name="iPhone 17 Pro Max")

    assert obs.resolution_method == "ax"
    assert obs.degraded is False
    assert obs.degraded_reason is None
    assert len(obs.marks) == 1
    m = obs.marks[0]
    assert m.source == "ax"
    assert m.role == "button"
    assert (m.x, m.y, m.w, m.h) == (532, 1650, 384, 51)


def test_ax_reactivated_is_visible_as_degraded_with_reason(tmp_path):
    """Section 3's 'critically, degradation is visible' requirement: a
    successful reactivate/retry recovery is NOT reported as a clean read —
    an agent debugging flakiness later must see it happened.
    """
    with patch("simdrive.observe.sim.screenshot", side_effect=_fake_screenshot), \
         patch("simdrive.observe.som.detect_marks", return_value=list(_OCR_MARKS)), \
         patch("simdrive.observe.get_bounds", return_value=WindowBounds(0, 0, 100, 200)), \
         patch("simdrive.observe.ax.is_available", return_value=True), \
         patch(
             "simdrive.observe.ax.observe_pixel_elements",
             return_value={
                 "elements": _AX_ELEMENTS,
                 "resolution_method": "ax_reactivated",
                 "reactivated": True,
             },
         ):
        obs = observe.observe("UDID", tmp_path, annotate=False, device_name="iPhone 17 Pro Max")

    assert obs.resolution_method == "ax_reactivated"
    assert obs.degraded is True
    assert obs.degraded_reason == "ax_reactivated"
    assert len(obs.marks) == 1


def test_ax_retry_exhausted_falls_back_to_ocr_not_agent_error(tmp_path):
    """observe() must never raise to the agent because host AX failed —
    fall back to the OCR marks already computed, and mark the response
    degraded with a reason distinguishable from a plain 'unavailable'.
    """
    with patch("simdrive.observe.sim.screenshot", side_effect=_fake_screenshot), \
         patch("simdrive.observe.som.detect_marks", return_value=list(_OCR_MARKS)), \
         patch("simdrive.observe.get_bounds", return_value=WindowBounds(0, 0, 100, 200)), \
         patch("simdrive.observe.ax.is_available", return_value=True), \
         patch(
             "simdrive.observe.ax.observe_pixel_elements",
             side_effect=ax.AXError("No on-screen Simulator window"),
         ):
        obs = observe.observe("UDID", tmp_path, annotate=False, device_name="iPhone 17 Pro Max")

    assert obs.resolution_method == "ocr"
    assert obs.degraded is True
    assert obs.degraded_reason.startswith("ax_exhausted")
    # OCR marks (computed before the AX attempt) still came through.
    assert len(obs.marks) == 1
    assert obs.marks[0].source == "ocr"


def test_ax_unexpected_exception_never_propagates_to_caller(tmp_path):
    """A completely unanticipated AX-layer exception must also fall back,
    not crash observe() — the architecture doc's 'never raise to the agent'
    guarantee applies to every AX failure shape, not just AXError.
    """
    with patch("simdrive.observe.sim.screenshot", side_effect=_fake_screenshot), \
         patch("simdrive.observe.som.detect_marks", return_value=list(_OCR_MARKS)), \
         patch("simdrive.observe.get_bounds", return_value=WindowBounds(0, 0, 100, 200)), \
         patch("simdrive.observe.ax.is_available", return_value=True), \
         patch(
             "simdrive.observe.ax.observe_pixel_elements",
             side_effect=RuntimeError("pyobjc exploded"),
         ):
        obs = observe.observe("UDID", tmp_path, annotate=False, device_name="iPhone 17 Pro Max")

    assert obs.degraded is True
    assert obs.degraded_reason.startswith("ax_error")
    assert len(obs.marks) == 1


def test_multi_sim_or_headless_guard_never_touches_ax_entry_points(tmp_path):
    """The explicit single-sim/GUI guard: when the caller (server.py) has
    already decided AX must not run (multi-sim fleet, or a headless
    session), `allow_ax=False` must mean `ax.is_available()` and
    `ax.observe_pixel_elements()` are NEVER called — not called-and-caught.
    This is the routing decision itself, not a try/except fallback.
    """
    def _boom(*a, **kw):
        raise AssertionError("AX entry point must not be called when allow_ax=False")

    with patch("simdrive.observe.sim.screenshot", side_effect=_fake_screenshot), \
         patch("simdrive.observe.som.detect_marks", return_value=list(_OCR_MARKS)), \
         patch("simdrive.observe.get_bounds", return_value=WindowBounds(0, 0, 100, 200)), \
         patch("simdrive.observe.ax.is_available", side_effect=_boom), \
         patch("simdrive.observe.ax.observe_pixel_elements", side_effect=_boom):
        obs = observe.observe(
            "UDID", tmp_path, annotate=False,
            device_name="iPhone 17 Pro Max", allow_ax=False,
        )

    assert obs.resolution_method == "ocr"
    assert obs.degraded is True
    assert obs.degraded_reason == "ax_routing_disabled_multi_sim_or_headless"


def test_no_device_name_skips_ax_entirely_legacy_callers_unaffected(tmp_path):
    """Every pre-Wave-2 caller doesn't pass `device_name` — AX must never be
    attempted, and the observation must look exactly like the pre-Wave-2
    contract (resolution_method='ocr', degraded=False, no reason).
    """
    def _boom(*a, **kw):
        raise AssertionError("AX must not be attempted without device_name")

    with patch("simdrive.observe.sim.screenshot", side_effect=_fake_screenshot), \
         patch("simdrive.observe.som.detect_marks", return_value=list(_OCR_MARKS)), \
         patch("simdrive.observe.get_bounds", return_value=WindowBounds(0, 0, 100, 200)), \
         patch("simdrive.observe.ax.is_available", side_effect=_boom), \
         patch("simdrive.observe.ax.observe_pixel_elements", side_effect=_boom):
        obs = observe.observe("UDID", tmp_path, annotate=False)

    assert obs.resolution_method == "ocr"
    assert obs.degraded is False
    assert obs.degraded_reason is None


def test_device_target_never_attempts_host_ax(tmp_path):
    """Host AX is simulator-only; a device-target observation must not
    attempt it even if a device_name happens to be passed.
    """
    def _boom(*a, **kw):
        raise AssertionError("host AX must not be attempted for target=device")

    def fake_dev_screenshot(udid, dest_path):
        _make_png(dest_path)
        return dest_path

    with patch("simdrive.device.screenshot", side_effect=fake_dev_screenshot), \
         patch("simdrive.observe.som.detect_marks", return_value=list(_OCR_MARKS)), \
         patch("simdrive.observe.get_bounds", return_value=None), \
         patch("simdrive.observe.ax.is_available", side_effect=_boom), \
         patch("simdrive.observe.ax.observe_pixel_elements", side_effect=_boom):
        obs = observe.observe(
            "UDID", tmp_path, annotate=False, target="device",
            device_name="iPhone 17 Pro Max",
        )

    assert obs.resolution_method == "ocr"
    assert obs.degraded is False


def test_ax_merge_collapse_is_reported_as_degraded(tmp_path):
    """A merge that erases most of the screen must not read as a clean AX read.

    The original defect: on a Palace catalog the AX scroll-view element
    consumed every OCR mark inside its bbox, so observe returned 5 marks
    where OCR alone found 43 — and reported degraded=false. The merge rule
    now prevents that, but an agent cannot tell a sparse screen from a
    collapsed one, so the canary makes the next container-shaped regression
    announce itself rather than look healthy.
    """
    many_ocr = [
        Mark(id=i, x=10 * i, y=100 + 10 * i, w=40, h=12, text=f"title {i}",
             confidence=1.0, raw_confidence=1.0, source="ocr")
        for i in range(1, 21)
    ]

    with patch("simdrive.observe.sim.screenshot", side_effect=_fake_screenshot), \
         patch("simdrive.observe.som.detect_marks", return_value=many_ocr), \
         patch("simdrive.observe.get_bounds", return_value=WindowBounds(0, 0, 100, 200)), \
         patch("simdrive.observe.ax.is_available", return_value=True), \
         patch("simdrive.observe.som.merge_ax_and_ocr", return_value=many_ocr[:2]), \
         patch(
             "simdrive.observe.ax.observe_pixel_elements",
             return_value={
                 "elements": _AX_ELEMENTS,
                 "resolution_method": "ax",
                 "reactivated": False,
             },
         ):
        obs = observe.observe("UDID", tmp_path, annotate=False,
                              device_name="iPhone 17 Pro Max")

    assert obs.degraded is True, "a 20 -> 2 collapse must not report as healthy"
    assert "ax_merge_collapsed" in (obs.degraded_reason or ""), obs.degraded_reason


def test_normal_ax_merge_does_not_trip_the_collapse_canary(tmp_path):
    """Folding a label's fragments together is the merge working, not a collapse."""
    ocr = [
        Mark(id=i, x=10 * i, y=100 + 10 * i, w=40, h=12, text=f"title {i}",
             confidence=1.0, raw_confidence=1.0, source="ocr")
        for i in range(1, 21)
    ]

    with patch("simdrive.observe.sim.screenshot", side_effect=_fake_screenshot), \
         patch("simdrive.observe.som.detect_marks", return_value=ocr), \
         patch("simdrive.observe.get_bounds", return_value=WindowBounds(0, 0, 100, 200)), \
         patch("simdrive.observe.ax.is_available", return_value=True), \
         patch("simdrive.observe.som.merge_ax_and_ocr", return_value=ocr[:18]), \
         patch(
             "simdrive.observe.ax.observe_pixel_elements",
             return_value={
                 "elements": _AX_ELEMENTS,
                 "resolution_method": "ax",
                 "reactivated": False,
             },
         ):
        obs = observe.observe("UDID", tmp_path, annotate=False,
                              device_name="iPhone 17 Pro Max")

    assert obs.degraded is False, f"20 -> 18 is a normal merge; got {obs.degraded_reason}"


def test_merge_receives_the_screenshot_dimensions(tmp_path):
    """The container size check needs the screen size — it must be threaded in."""
    captured = {}

    def fake_merge(ocr_marks, ax_elements, **kwargs):
        captured.update(kwargs)
        return list(ocr_marks)

    with patch("simdrive.observe.sim.screenshot", side_effect=_fake_screenshot), \
         patch("simdrive.observe.som.detect_marks", return_value=list(_OCR_MARKS)), \
         patch("simdrive.observe.get_bounds", return_value=WindowBounds(0, 0, 100, 200)), \
         patch("simdrive.observe.ax.is_available", return_value=True), \
         patch("simdrive.observe.som.merge_ax_and_ocr", side_effect=fake_merge), \
         patch(
             "simdrive.observe.ax.observe_pixel_elements",
             return_value={
                 "elements": _AX_ELEMENTS,
                 "resolution_method": "ax",
                 "reactivated": False,
             },
         ):
        observe.observe("UDID", tmp_path, annotate=False,
                        device_name="iPhone 17 Pro Max")

    assert "screen_size" in captured, "merge must receive screen_size"
    assert captured["screen_size"] is not None
