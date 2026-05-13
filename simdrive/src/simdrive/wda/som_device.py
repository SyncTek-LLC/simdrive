"""Device SoM annotation via WDA /source (XCUI accessibility tree).

Builds marks[] for a real-device `observe(annotate=True)` call by walking
the XCUIElement accessibility tree returned by ``GET /session/<sid>/source``.

Design notes
------------
* Coordinates in /source are in LOGICAL POINTS (UIKit coordinate space).
  Multiply by `point_scale` to get PIXEL coordinates that match the PNG
  resolution.  E.g. a 3x device (iPhone 15 Pro) has point_scale=3.0 so
  element at point (100, 200) maps to pixel (300, 600).

* Mark shape is IDENTICAL to the simulator-OCR path (som.Mark.to_dict()).
  The stable_id / stable_id_loose hashing reuses Mark directly so device
  and sim marks are comparable across cross-session recordings.

* Walk is O(n): the ElementTree is parsed once; no re-parsing per element.

* Annotated PNG is written as ``<screenshot_stem>_annotated.png`` alongside
  the raw screenshot.  Calling this function twice will overwrite the same
  file (no double-draw).

XML attributes read from each XCUIElement node
-----------------------------------------------
  type     — element type string (e.g. "XCUIElementTypeButton")
  name     — accessibility name (primary text source)
  label    — accessibility label
  value    — element value (e.g. text field content)
  visible  — "true" | "false"  — exclude when "false"
  enabled  — "true" | "false"  — informational only (not used for exclusion)
  x, y     — top-left in POINTS
  width    — element width in POINTS
  height   — element height in POINTS

Exclusion rules (in order)
--------------------------
  1. visible != "true"  → skip
  2. element type IN {XCUIElementTypeApplication, XCUIElementTypeWindow} → skip
     (root containers span the full screen and add noise for agent tap resolution)
  3. bbox area >= 70% of screen area (catches XCUIElementTypeOther wrappers that
     cover the whole screen — they match app-name text and poison tap resolution)
  4. name, label, value all empty/whitespace  → skip
  5. width == 0 or height == 0  → skip
  6. bbox_pixels entirely outside screenshot bounds  → skip
  7. Nested duplicates: if a child element has identical text to its direct
     parent AND an identical or fully-contained bbox, emit only the child
     (deepest wins).
"""
from __future__ import annotations

import hashlib
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from ..som import Mark, annotate as _draw_annotated

_log = logging.getLogger(__name__)

# Element types that are always excluded — they are root containers that span
# the full screen and add noise to tap-target resolution for agent drivers.
_CONTAINER_TYPES = frozenset({
    "XCUIElementTypeApplication",
    "XCUIElementTypeWindow",
})

# Fraction of screen area at or above which an element is treated as a
# full-screen container and excluded.  70% covers the typical "Other" wrapper
# that XCUITest inserts for scroll views / root content views.
_CONTAINER_AREA_RATIO = 0.70


def _pick_text(el: ET.Element) -> str:
    """Return the best non-empty text for an element.

    Priority: name > label > value.  Strip whitespace; return "" when all
    are empty so the caller can apply the exclusion rule uniformly.
    """
    for attr in ("name", "label", "value"):
        t = (el.get(attr) or "").strip()
        if t:
            return t
    return ""


def _el_bbox_pixels(
    el: ET.Element,
    point_scale: float,
    img_w: int,
    img_h: int,
) -> Optional[tuple[int, int, int, int]]:
    """Return (x, y, w, h) in pixels, or None if the element must be excluded.

    Exclusion reasons returned as None:
      - zero-area (width or height is 0 in points)
      - bbox entirely outside the screenshot bounds after scaling
    """
    try:
        # Coordinates from /source are in POINTS (UIKit logical space).
        # Multiply by point_scale to convert to pixels matching the PNG.
        # Example: iPhone 15 Pro has point_scale=3.0 (3x Retina display);
        # a button at point (110, 893, 110, 63) becomes pixel
        # (330, 2679, 330, 189) — matching the actual PNG resolution.
        x_pt = float(el.get("x") or 0)
        y_pt = float(el.get("y") or 0)
        w_pt = float(el.get("width") or 0)
        h_pt = float(el.get("height") or 0)
    except (ValueError, TypeError):
        return None

    # Exclude zero-area elements (invisible or collapsed).
    if w_pt <= 0 or h_pt <= 0:
        return None

    x = int(x_pt * point_scale)
    y = int(y_pt * point_scale)
    w = int(w_pt * point_scale)
    h = int(h_pt * point_scale)

    # Exclude elements whose bbox is entirely outside the screenshot bounds.
    if x >= img_w or y >= img_h or x + w <= 0 or y + h <= 0:
        return None

    return (x, y, w, h)


def _contains(outer: tuple[int, int, int, int], inner: tuple[int, int, int, int]) -> bool:
    """Return True if outer fully contains inner (bbox tuple is x,y,w,h)."""
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return ox <= ix and oy <= iy and (ox + ow) >= (ix + iw) and (oy + oh) >= (iy + ih)


def _walk_tree(
    root: ET.Element,
    point_scale: float,
    img_w: int,
    img_h: int,
) -> list[tuple[str, tuple[int, int, int, int]]]:
    """Walk the XCUI element tree depth-first, collecting (text, bbox_px) pairs.

    Returns a flat list in document order (top → children).  Duplicate
    suppression (parent/child same-text) happens in a second pass so this
    walk stays O(n) with no backtracking.

    Only elements with visible=true, non-empty text, and non-zero area are
    collected; all other exclusion rules are applied inline.
    """
    collected: list[tuple[str, tuple[int, int, int, int]]] = []
    screen_area = img_w * img_h

    # Iterative DFS to avoid Python recursion limits on deep trees.
    stack: list[ET.Element] = [root]
    while stack:
        el = stack.pop()
        # Push children in reverse order so left-most child is processed first.
        for child in reversed(list(el)):
            stack.append(child)

        # Leaf filter 1: skip root container types (Application, Window).
        # WDA /source XML uses the tag name as the element type identifier
        # (e.g. <XCUIElementTypeButton ...>); a type="" attribute may also be
        # present in some WDA versions — check both.
        el_type = el.get("type", "") or el.tag or ""
        if el_type in _CONTAINER_TYPES:
            continue

        # Visibility gate: skip invisible elements.
        if el.get("visible", "true").lower() != "true":
            continue

        text = _pick_text(el)
        if not text:
            continue

        bbox = _el_bbox_pixels(el, point_scale, img_w, img_h)
        if bbox is None:
            continue

        # Leaf filter 2: skip elements whose area covers >= 70% of the screen
        # (e.g. XCUIElementTypeOther wrappers that span the whole viewport).
        bx, by, bw, bh = bbox
        if screen_area > 0 and (bw * bh) >= _CONTAINER_AREA_RATIO * screen_area:
            continue

        collected.append((text, bbox))

    return collected


def _deduplicate(
    entries: list[tuple[str, tuple[int, int, int, int]]],
) -> list[tuple[str, tuple[int, int, int, int]]]:
    """Remove parent/child duplicate text entries.

    When a child element has the same text as a parent and the parent's bbox
    fully contains the child's bbox, keep only the child (deepest wins).

    Strategy: for each entry, check whether any *later* entry in document
    order has the same text and a bbox that is contained by the current
    entry's bbox.  If so, skip the current entry (it is the parent).

    O(n^2) in the worst case, but XCUI trees are typically shallow (<500
    elements visible on screen) so this is negligible.
    """
    result: list[tuple[str, tuple[int, int, int, int]]] = []
    n = len(entries)
    for i, (text_i, bbox_i) in enumerate(entries):
        # Is there a later entry with the same text that is contained inside us?
        dominated = False
        for j in range(i + 1, n):
            text_j, bbox_j = entries[j]
            if text_j == text_i and _contains(bbox_i, bbox_j):
                dominated = True
                break
        if not dominated:
            result.append((text_i, bbox_i))
    return result


def annotate_device_screenshot(
    screenshot_path: Path,
    screenshot_size_pixels: tuple[int, int],
    wda: object,
    point_scale: float,
) -> tuple[list[dict], Optional[Path]]:
    """Build marks[] for a real-device observation.

    Parameters
    ----------
    screenshot_path:
        Path to the raw PNG screenshot already written to disk.
    screenshot_size_pixels:
        (width, height) of the screenshot in pixels.
    wda:
        A ``WdaClient`` instance with an open session.  Must support
        ``wda.source() -> str`` (returns UTF-8 XML from WDA /source).
    point_scale:
        Pixels-per-point scale factor for the device display.  A 3x Retina
        device (e.g. iPhone 15 Pro) uses point_scale=3.0.  Pass 1.0 when
        unknown — coordinates will be in points, which is still usable for
        stable_id hashing; bbox values will be wrong only in magnitude.

    Returns
    -------
    (marks, annotated_path)
        ``marks`` is a list of dicts matching som.Mark.to_dict() exactly.
        ``annotated_path`` is a Path to the annotated PNG (numbered red
        boxes drawn over the screenshot), or None when no marks were found
        or an error occurred.
    """
    img_w, img_h = screenshot_size_pixels

    # Step 1: Fetch the XCUI accessibility tree via WDA /source.
    try:
        xml_str: str = wda.source()  # type: ignore[attr-defined]
    except Exception as exc:
        _log.warning(
            "device SoM: WDA /source failed — falling back to marks=[]. "
            "Error: %s",
            exc,
        )
        return [], None

    if not isinstance(xml_str, str):
        _log.warning(
            "device SoM: WDA /source returned non-string (%r) — falling back to marks=[].",
            type(xml_str).__name__,
        )
        return [], None

    if not xml_str.strip():
        _log.warning("device SoM: WDA /source returned empty XML.")
        return [], None

    # Step 2: Parse the XML (once — the tree is walked exactly once below).
    try:
        root = ET.fromstring(xml_str)
    except (ET.ParseError, TypeError) as exc:
        _log.warning(
            "device SoM: WDA /source XML is malformed — falling back to marks=[]. "
            "Error: %s",
            exc,
        )
        return [], None

    # Step 3: Walk the tree and collect candidate (text, bbox_px) pairs.
    entries = _walk_tree(root, point_scale, img_w, img_h)
    if not entries:
        return [], None

    # Step 4: Remove parent/child duplicates (same text, parent contains child).
    entries = _deduplicate(entries)

    # Step 5: Sort top-to-bottom, left-to-right (reading order), matching sim path.
    entries.sort(key=lambda e: (e[1][1] // 40, e[1][0]))

    # Step 6: Build Mark objects (reuses som.Mark for stable_id hashing — this
    # ensures device marks and sim marks produce identical stable_id values when
    # the same element appears on both, enabling cross-session recordings).
    marks_objs: list[Mark] = []
    for idx, (text, (x, y, w, h)) in enumerate(entries):
        m = Mark(
            id=idx + 1,
            x=x,
            y=y,
            w=w,
            h=h,
            text=text,
            # Device elements from XCUI are ground-truth accessibility labels —
            # we assign high raw_confidence (1.0) to distinguish them from OCR
            # results.  The Mark dataclass will still compute confidence_band
            # via the dictionary gate; UI labels like "Catalog" are dictionary
            # words and will surface as "high".
            confidence=1.0,
            raw_confidence=1.0,
        )
        marks_objs.append(m)

    if not marks_objs:
        return [], None

    # Step 7: Draw annotated PNG.  Written to <stem>_annotated.png alongside
    # the raw screenshot.  Calling twice overwrites the same file (no double-draw).
    annotated_path: Optional[Path] = None
    try:
        out_path = screenshot_path.parent / (screenshot_path.stem + "_annotated.png")
        _draw_annotated(screenshot_path, marks_objs, out_path)
        annotated_path = out_path
    except Exception as exc:
        _log.warning("device SoM: failed to draw annotated PNG — %s", exc)

    return [m.to_dict() for m in marks_objs], annotated_path
