"""Hermetic tests for INIT-2026-641 Wave 2 — AX-primary perception.

Covers:
  * item 5.4 — the dictionary-gate fence in Mark._compute_band()/
    _clamped_confidence() must not apply to source="ax" (ground-truth) marks.
  * som.merge_ax_and_ocr() — AX elements overlay/replace overlapping OCR
    marks with the real control rect + role + enabled state; unmatched AX
    elements become new marks; unmatched OCR marks are preserved unchanged.

No simulator, no host AX, no Vision OCR — every input is a plain dict/Mark
constructed by hand. Live acceptance against a real device is
`tests/test_ax_live_acceptance.py` (marked `live`).
"""
from __future__ import annotations

from simdrive.som import Mark, merge_ax_and_ocr


# ---------------------------------------------------------------------------
# Item 5.4 — fence-skip for ground-truth (AX) marks
# ---------------------------------------------------------------------------


def test_ax_source_mark_is_always_high_band_even_with_gibberish_text():
    """A ground-truth AX mark must not be clamped to 'low' by the dictionary
    fence — the fence exists to catch OCR misreads, and an AX-sourced mark's
    text came from the app itself, not a probabilistic read.
    """
    m = Mark(
        id=1, x=0, y=0, w=50, h=20,
        text="Sary of the Canadan liothest",  # would fail the dictionary fence
        confidence=1.0, source="ax", role="AXStaticText", enabled=True,
    )
    assert m.confidence_band == "high"
    assert m.confidence == 1.0  # unclamped
    # english_like still independently reports the (failing) dictionary check —
    # item 4.1's whole point is these two must not be conflated.
    assert m.english_like is False


def test_ocr_source_mark_gibberish_still_clamped_to_low():
    """Unchanged pre-existing behavior: an OCR mark with non-English text is
    still clamped to 'low', proving the fence-skip is source-scoped, not a
    global relaxation of the gate.
    """
    m = Mark(
        id=1, x=0, y=0, w=50, h=20,
        text="Sary of the Canadan liothest",
        confidence=1.0,  # source defaults to "ocr"
    )
    assert m.confidence_band == "low"
    assert m.confidence <= 0.3


def test_ax_source_mark_low_raw_confidence_still_high_band():
    """AX ground truth has no OCR engine score to speak of; raw_confidence
    is conventionally 1.0 for AX marks, and band is 'high' regardless of the
    raw value — the AX walk never manufactures a low raw_confidence, but the
    band computation itself must not depend on the >=0.85 raw threshold for
    source="ax" marks.
    """
    m = Mark(
        id=1, x=0, y=0, w=50, h=20, text="Continue",
        confidence=0.1, source="ax", role="AXButton", enabled=False,
    )
    assert m.confidence_band == "high"


# ---------------------------------------------------------------------------
# som.merge_ax_and_ocr
# ---------------------------------------------------------------------------


def _ocr(id_, x, y, w, h, text, confidence=0.9):
    return Mark(id=id_, x=x, y=y, w=w, h=h, text=text, confidence=confidence)


def _ax_el(role, label, bbox, enabled=True):
    return {"role": role, "label": label, "enabled": enabled, "bbox": list(bbox)}


def test_merge_replaces_overlapping_ocr_mark_with_ax_control_rect():
    """The Chairman's acceptance case: OCR sees a tight glyph box for
    "Continue"; AX sees the real button control rect. The merged mark must
    carry the AX rect (materially larger than the glyph box), role="button",
    and source="ax" — never the glyph-only bbox.
    """
    glyph_bbox = (562, 1660, 191, 41)  # tight OCR glyph box, sitting inside the control
    control_bbox = (532, 1650, 384, 51)  # the real AX button control rect, much wider
    ocr_marks = [_ocr(1, *glyph_bbox, "Continue")]
    ax_elements = [_ax_el("button", "Continue", control_bbox, enabled=True)]

    merged = merge_ax_and_ocr(ocr_marks, ax_elements)

    assert len(merged) == 1
    m = merged[0]
    assert m.source == "ax"
    assert m.role == "button"
    assert m.enabled is True
    assert (m.x, m.y, m.w, m.h) == control_bbox
    # Must not equal or nearly equal the OCR glyph-only box.
    assert (m.x, m.y, m.w, m.h) != glyph_bbox
    assert m.w > glyph_bbox[2] * 1.5


def test_merge_collapses_label_plus_hint_fragments_into_one_field_mark():
    """Pre-fix defect: OCR splits an email field into a label mark ("Email
    address") and a separate hint-text mark ("name@example.com"). AX exposes
    the field as ONE text-field element whose control rect covers the hint
    text's region (but not the separate label above it). After merge, exactly
    one mark carries role="text_field", and the hint fragment is absorbed
    into it rather than surviving as a second peer mark.
    """
    label_bbox = (532, 545, 87, 15)          # "Email address" static label, above the field
    hint_bbox = (540, 565, 300, 40)          # OCR's placeholder-text fragment
    field_bbox = (532, 564, 384, 43)         # AX text field control rect (overlaps hint)

    ocr_marks = [
        _ocr(1, *label_bbox, "Email address"),
        _ocr(2, *hint_bbox, "name@example.com"),
    ]
    ax_elements = [
        _ax_el("static_text", "Email address", label_bbox),
        _ax_el("text_field", "name@example.com", field_bbox),
    ]

    merged = merge_ax_and_ocr(ocr_marks, ax_elements)

    field_marks = [m for m in merged if m.role == "text_field"]
    assert len(field_marks) == 1
    assert field_marks[0].source == "ax"
    # No leftover OCR-only mark still sitting inside the field's own bbox.
    assert not any(
        m is not field_marks[0]
        and m.x >= field_bbox[0] and m.y >= field_bbox[1]
        and m.x + m.w <= field_bbox[0] + field_bbox[2]
        and m.y + m.h <= field_bbox[1] + field_bbox[3]
        for m in merged
    )


def test_merge_collapses_two_line_headline_ocr_fragments_into_one_mark():
    """OCR often splits a wrapped two-line headline into two marks; AX sees
    the label as a single AXStaticText spanning both lines. After merge,
    exactly one mark should cover that headline, banded 'high'.
    """
    line1_bbox = (532, 341, 215, 45)
    line2_bbox = (532, 386, 200, 48)
    headline_bbox = (532, 341, 215, 94)  # AX's single spanning frame

    ocr_marks = [
        _ocr(1, *line1_bbox, "Welcome to"),
        _ocr(2, *line2_bbox, "RefSource."),
    ]
    ax_elements = [_ax_el("static_text", "Welcome to RefSource.", headline_bbox)]

    merged = merge_ax_and_ocr(ocr_marks, ax_elements)

    headline_marks = [m for m in merged if m.text == "Welcome to RefSource."]
    assert len(headline_marks) == 1
    assert headline_marks[0].confidence_band == "high"
    assert headline_marks[0].source == "ax"


def test_merge_keeps_unmatched_ocr_marks_as_ocr_sourced():
    """An OCR mark with no overlapping AX element (e.g. rendered texture text
    AX doesn't expose) passes through unchanged, still source='ocr'.
    """
    ocr_marks = [_ocr(1, 10, 10, 40, 12, "v1.2.3 build 456")]
    merged = merge_ax_and_ocr(ocr_marks, [])
    assert len(merged) == 1
    assert merged[0].source == "ocr"
    assert merged[0].text == "v1.2.3 build 456"


def test_merge_adds_ax_only_elements_with_no_ocr_counterpart():
    """An AX control OCR never rendered as recognizable text (e.g. an icon-only
    button with an accessibility label) becomes its own new mark.
    """
    ax_elements = [_ax_el("button", "Settings", (10, 10, 40, 40))]
    merged = merge_ax_and_ocr([], ax_elements)
    assert len(merged) == 1
    assert merged[0].source == "ax"
    assert merged[0].role == "button"
    assert merged[0].text == "Settings"


def test_merge_skips_unlabeled_unknown_role_ax_elements():
    """Generic AXGroup/AXGenericElement containers with no role we recognize
    and no label add no signal — including them would flood the mark list
    with noise. They must not appear as marks.
    """
    ax_elements = [_ax_el("unknown", "", (0, 0, 500, 900))]
    merged = merge_ax_and_ocr([], ax_elements)
    assert merged == []


def test_merge_output_is_renumbered_in_reading_order():
    """Merged marks get fresh sequential ids in top-to-bottom reading order,
    matching detect_marks' own convention — a caller should never see gaps
    or an id collision across OCR-origin and AX-origin marks.
    """
    ocr_marks = [_ocr(5, 10, 500, 40, 12, "footer text")]
    ax_elements = [_ax_el("button", "Continue", (10, 10, 40, 12))]
    merged = merge_ax_and_ocr(ocr_marks, ax_elements)
    ids = [m.id for m in merged]
    assert ids == sorted(ids)
    assert ids == list(range(1, len(merged) + 1))
