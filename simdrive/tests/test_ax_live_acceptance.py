"""INIT-2026-641 Wave 2 — live AX-primary perception acceptance test.

The Chairman's stated acceptance test (test plan section 2), executed
through the shipped API surface — ``observe.observe()`` /
``som.merge_ax_and_ocr()`` — not raw ``ax.py`` internals. Turns the spike's
manual probe (``scratchpad/ax-spike/probe.py``) into a repeatable assertion.

Marked `live` — skipped in normal pytest runs (`-m "not live"` CI gate).
Requires the booted iPhone 17 Pro Max (UDID
49E36F58-38D6-4D5C-9C38-EBDC809F9213) with `io.synctek.refsource` installed
and its sign-in screen on screen. Run explicitly:

    /opt/homebrew/opt/python@3.11/bin/python3.11 -m pytest \
        tests/test_ax_live_acceptance.py -v -m live

Host AX is inherently flakier than a hermetic mock can capture (this is
Wave 2's whole reason for a permanent OCR fallback, not a one-time bridge):
this test SKIPS (not fails) when the fixed target isn't in the expected
state or AX cannot resolve content at all after its own reactivate-retry
budget — a skip with a clear reason is honest; a false failure from wrong
app/host state or transient host-AX staleness is not. Once AX resolves,
every assertion below is a hard, non-skippable pass/fail: this test exists
specifically to prove AX perception, not to explain away a failure of it.
"""
from __future__ import annotations

import time

import pytest

from simdrive import act, ax, observe, sim

UDID = "49E36F58-38D6-4D5C-9C38-EBDC809F9213"
DEVICE_NAME = "iPhone 17 Pro Max"
BUNDLE_ID = "io.synctek.refsource"

# live-measurement.md's OCR-only glyph reading for "Continue" — the acceptance
# bar is that the AX-path bbox is NOT this (or near enough to be the same box).
GLYPH_ONLY_CONTINUE_BOX = (562, 1709, 191, 41)


def _booted_target() -> bool:
    for d in sim.list_devices():
        if d.udid == UDID and d.is_booted:
            return True
    return False


@pytest.fixture
def live_observe_dir(tmp_path):
    return tmp_path / "ax_acceptance_observations"


@pytest.mark.live
def test_ax_acceptance_refsource_signin(live_observe_dir):
    if not _booted_target():
        pytest.skip(f"target device {UDID} is not booted — live-only acceptance check")
    if not ax.is_available():
        pytest.skip(
            "host AX is not available (permission not granted, or no on-screen "
            "Simulator window for this device) — cannot run the AX acceptance "
            "check without host AX; this is a precondition skip, not an AX-vs-OCR "
            "result"
        )

    obs = observe.observe(
        UDID, live_observe_dir, annotate=True, target="simulator",
        device_name=DEVICE_NAME,
    )
    d = obs.to_dict()

    if d["resolution_method"] not in ("ax", "ax_reactivated"):
        pytest.skip(
            f"AX perception did not resolve for this run "
            f"(resolution_method={d['resolution_method']!r}, "
            f"degraded_reason={d.get('degraded_reason')!r}) — host AX is a live, "
            "occasionally-flaky dependency (see module docstring); this is a "
            "precondition skip, not a failed assertion about the AX code path "
            "itself. Re-run once the Simulator window is stable."
        )

    marks = d["marks"]
    by_text = {m["text"].strip(): m for m in marks}

    # 1. Continue reports role=button with the control's rect, not glyph bounds.
    assert "Continue" in by_text, f"no 'Continue' mark in {sorted(by_text)}"
    continue_mark = by_text["Continue"]
    assert continue_mark["role"] == "button"
    assert continue_mark["source"] == "ax"
    gx, gy, gw, gh = GLYPH_ONLY_CONTINUE_BOX
    bx, by_, bw, bh = continue_mark["bbox"]
    assert (bw, bh) != (gw, gh), "Continue bbox equals the OCR glyph-only box"
    assert bw > gw * 1.3, (
        f"Continue bbox {continue_mark['bbox']} is not meaningfully larger than "
        f"the glyph-only box {GLYPH_ONLY_CONTINUE_BOX}"
    )

    # 2. The email field is exactly ONE text-field-role mark, not a label mark
    #    plus a separate hint-text mark — counted by role, not by string match.
    text_field_marks = [m for m in marks if m["role"] == "text_field"]
    assert len(text_field_marks) == 1, (
        f"expected exactly 1 text_field mark, got {len(text_field_marks)}: "
        f"{text_field_marks}"
    )

    # 3. Disabled state is visible before typing (fresh sign-in screen: empty field).
    assert continue_mark["enabled"] is False, (
        "Continue.enabled must be False on a fresh sign-in screen with an "
        f"empty email field; got {continue_mark['enabled']!r} — is the field "
        "pre-filled from a prior run? Relaunch the app to reset."
    )

    # 4. The headline is ONE element, not banded low.
    assert "Welcome to RefSource." in by_text, sorted(by_text)
    headline = by_text["Welcome to RefSource."]
    assert headline["confidence_band"] != "low"
    assert headline["source"] == "ax"

    # 5. Type into the email field via the tool surface, re-observe, and assert
    #    Continue transitions to enabled=True.
    fx, fy = text_field_marks[0]["center"]
    act.tap(fx, fy, d["screenshot_size_pixels"][0], d["screenshot_size_pixels"][1], udid=UDID)
    time.sleep(0.8)
    act.type_text("ax-acceptance@example.com", udid=UDID)
    time.sleep(0.8)

    obs2 = observe.observe(
        UDID, live_observe_dir, annotate=True, target="simulator",
        device_name=DEVICE_NAME,
    )
    d2 = obs2.to_dict()
    by_text_2 = {m["text"].strip(): m for m in d2["marks"]}
    assert "Continue" in by_text_2
    assert by_text_2["Continue"]["enabled"] is True, (
        "Continue.enabled must flip to True once the email field has text; "
        f"resolution_method={d2['resolution_method']!r}"
    )

    # 6. Tap Continue by mark center; the coordinate transform's output point
    #    must fall inside the AX button's TRUE frame — not merely inside the
    #    OCR glyph box, which would hide a systematic offset (architecture
    #    doc correction #1).
    window = ax.select_window(DEVICE_NAME, auto_raise=False)
    group = ax._resolve_content_group(window) or window
    group_frame = ax._frame(group)
    scale = ax.content_group_scale(
        group_frame, d2["screenshot_size_pixels"][0], d2["screenshot_size_pixels"][1],
    )
    ax_button = next(
        (el for el in ax.walk_interactive_elements(group)
         if el["role"] == "button" and el["label"].strip() == "Continue"),
        None,
    )
    assert ax_button is not None, "ground-truth AX 'Continue' button not found for cross-check"
    true_bx, true_by, true_bw, true_bh = ax.ax_frame_to_pixel_bbox(
        ax_button["frame"], group_frame, scale,
    )
    cx, cy = by_text_2["Continue"]["center"]
    assert true_bx <= cx <= true_bx + true_bw, "tap x falls outside the true AX button frame"
    assert true_by <= cy <= true_by + true_bh, "tap y falls outside the true AX button frame"
