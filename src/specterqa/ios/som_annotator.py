"""Set-of-Mark (SoM) Annotator for iOS Screenshots.

Overlays numbered labels on interactive UI elements detected from the
accessibility tree. Claude picks a number instead of guessing coordinates.

Element tree source (priority order):
  1. SpecterQA XCTest runner  — GET /source on our Swift runner (preferred)
  2. WebDriverAgent (WDA)     — GET /session/<id>/source (optional fallback,
     requires wda_url and session_id at construction time)

Research shows SoM prompting improves UI agent accuracy from ~50% to ~90%+
by eliminating coordinate prediction entirely.

INIT-2026-493 — SpecterQA SoM annotator.
INIT-2026-506 — XCTest runner /source integration.
INIT-2026-508 — Remove WDA fallback from SoM pipeline.
INIT-2026-509 — Restore WDA as optional fallback with timeout guard.
"""

from __future__ import annotations

import base64
import io
import json
import socket
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image, ImageDraw, ImageFont


@dataclass
class UIElement:
    """A single interactive UI element from the accessibility tree."""

    index: int           # SoM number (1, 2, 3, ...)
    element_type: str    # Button, Cell, StaticText, etc. (XCUIElementType prefix stripped)
    label: str           # Accessibility label
    value: str           # Current value (for text fields, switches)
    x: float             # Device-point x (top-left)
    y: float             # Device-point y (top-left)
    width: float         # Device-point width
    height: float        # Device-point height
    enabled: bool = True
    visible: bool = True

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2

    def to_dict(self) -> dict:
        """Serialise to a plain dict (JSON-safe)."""
        return {
            "index": self.index,
            "element_type": self.element_type,
            "label": self.label,
            "value": self.value,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "center_x": self.center_x,
            "center_y": self.center_y,
            "enabled": self.enabled,
            "visible": self.visible,
        }


# Element types the agent can meaningfully interact with.
_INTERACTIVE_TYPES: frozenset[str] = frozenset({
    "XCUIElementTypeButton",
    "XCUIElementTypeCell",
    "XCUIElementTypeLink",
    "XCUIElementTypeTextField",
    "XCUIElementTypeSecureTextField",
    "XCUIElementTypeSwitch",
    "XCUIElementTypeSlider",
    "XCUIElementTypeImage",
    "XCUIElementTypeStaticText",
    "XCUIElementTypeSearchField",
    "XCUIElementTypeSegmentedControl",
    "XCUIElementTypeTab",
    "XCUIElementTypeTabBar",
    "XCUIElementTypeNavigationBar",
    "XCUIElementTypePickerWheel",
    "XCUIElementTypeToggle",
})

# Minimum element dimension (device points) — filters out invisible sentinels.
_MIN_SIZE = 5

# Label fragments that indicate a purely-decorative system element (nav
# chevrons, overlay dimming layers).  Case-insensitive substring match.
_NOISE_LABEL_FRAGMENTS: tuple[str, ...] = (
    "chevron.forward",
    "chevron.backward",
    "chevron.up",
    "chevron.down",
    "dimmingoverlay",
    "dimmingview",
    "placard",
    "separator",
    "background",
)


class SoMAnnotator:
    """Annotates screenshots with numbered labels from the accessibility tree.

    The XCTest runner is the preferred source.  Provide ``runner_url`` (e.g.
    ``"http://localhost:8222"``); the annotator calls ``GET /source`` directly.

    When ``runner_url`` is not set and ``wda_url`` + ``session_id`` are
    provided, the annotator falls back to WebDriverAgent's ``/source``
    endpoint (with a 10-second timeout and 2-retry cap).

    Usage::

        # Preferred: XCTest runner
        annotator = SoMAnnotator(runner_url="http://localhost:8222")

        # Optional WDA fallback (legacy):
        annotator = SoMAnnotator(wda_url="http://localhost:8100",
                                 session_id="abc123")

        elements, annotated_b64 = annotator.annotate(screenshot_b64, img_w, img_h)

    Args:
        runner_url: Base URL of our XCTest runner (e.g.
            ``"http://localhost:8222"``).  Takes priority over WDA when set.
        wda_url: Base URL of a running WebDriverAgent instance (e.g.
            ``"http://localhost:8100"``).  Used only when ``runner_url`` is
            not configured.  Requires ``session_id``.
        session_id: WDA session ID returned by ``POST /session``.  Required
            when ``wda_url`` is provided.
    """

    def __init__(
        self,
        runner_url: Optional[str] = None,
        wda_url: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        self.runner_url = runner_url.rstrip("/") if runner_url else None
        self.wda_url = wda_url.rstrip("/") if wda_url else None
        self.session_id = session_id

    # ------------------------------------------------------------------
    # Network — fetch element tree
    # ------------------------------------------------------------------

    def get_element_tree(self) -> str:
        """Fetch the accessibility element tree.

        Source selection:
        1. XCTest runner ``GET /source`` — when ``runner_url`` is set.
        2. WDA ``GET /session/<id>/source`` — when ``wda_url`` + ``session_id``
           are set (10-second timeout, 2-retry cap).

        Returns:
            Raw XML string of the current UI hierarchy, compatible with
            :meth:`parse_elements`.

        Raises:
            RuntimeError: If neither runner nor WDA is configured, or if the
                request fails after all retries.
        """
        if self.runner_url:
            return self._get_element_tree_from_runner()
        if self.wda_url and self.session_id:
            return self._get_element_tree_from_wda()
        raise RuntimeError(
            "SoMAnnotator has no element tree source configured. "
            "Provide runner_url (XCTest runner) or wda_url + session_id (WDA fallback). "
            "Build the runner: specterqa-ios runner build"
        )

    def _get_element_tree_from_runner(self) -> str:
        """Fetch element tree from our XCTest runner's ``/source`` endpoint.

        The runner returns either:
        - JSON with an ``"xml"`` key: ``{"xml": "<AppElement ...>"}``
        - Plain XML text directly (if the runner version omits the wrapper)

        Returns:
            Raw XML string.

        Raises:
            RuntimeError: On network failure or empty response.
        """
        url = f"{self.runner_url}/source"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                raw = resp.read()
        except Exception as exc:
            raise RuntimeError(
                f"XCTest runner /source request failed at {url}: {exc}"
            ) from exc

        # The runner returns JSON (array of element dicts with children).
        # The annotator's parse_elements() expects WDA-style XML.
        # Convert the runner's JSON to XML so the existing parser works.
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Maybe it's already XML (older runner or WDA format).
            xml_source = raw.decode("utf-8", errors="replace").strip()
            if not xml_source:
                raise RuntimeError(
                    f"XCTest runner /source returned empty content. "
                    f"Raw response: {raw[:200]!r}"
                )
            return xml_source

        # JSON response — convert to XML for parse_elements().
        if isinstance(data, list):
            # Array of element trees — wrap in a root.
            xml_source = self._json_elements_to_xml(data)
        elif isinstance(data, dict):
            # Single element or {"xml": "..."} envelope.
            if "xml" in data or "value" in data:
                xml_source = data.get("xml") or data.get("value") or ""
            elif "error" in data:
                raise RuntimeError(f"Runner /source error: {data}")
            else:
                xml_source = self._json_elements_to_xml([data])
        else:
            xml_source = ""

        if not xml_source:
            raise RuntimeError(
                f"XCTest runner /source returned empty content. "
                f"Raw response: {raw[:200]!r}"
            )
        return xml_source

    @staticmethod
    def _json_elements_to_xml(elements: list[dict]) -> str:
        """Convert the runner's JSON element tree to WDA-style XML.

        The runner returns:
          [{"type": 42, "typeLabel": "application", "label": "Palace",
            "frame": {"x": 0, "y": 0, "width": 390, "height": 844},
            "enabled": true, "children": [...]}]

        parse_elements() expects:
          <XCUIElementTypeApplication label="Palace" x="0" y="0" width="390" height="844" enabled="true" visible="true">
            ...children...
          </XCUIElementTypeApplication>
        """
        def _convert(elem: dict) -> str:
            type_label = elem.get("typeLabel", "other")
            # Map to XCUIElementType tag names used by WDA XML.
            tag = f"XCUIElementType{type_label[0].upper()}{type_label[1:]}"

            frame = elem.get("frame", {})
            attrs = {
                "label": str(elem.get("label", "") or ""),
                "value": str(elem.get("value", "") or "") if elem.get("value") is not None and str(elem.get("value")) != "<null>" else "",
                "identifier": str(elem.get("identifier", "") or ""),
                "enabled": "true" if elem.get("enabled", True) else "false",
                "visible": "true",  # runner doesn't track visibility — assume visible
                "x": str(int(frame.get("x", 0))),
                "y": str(int(frame.get("y", 0))),
                "width": str(int(frame.get("width", 0))),
                "height": str(int(frame.get("height", 0))),
            }

            attr_str = " ".join(f'{k}="{_xml_escape(v)}"' for k, v in attrs.items() if v)
            children = elem.get("children", [])
            if children:
                child_xml = "".join(_convert(c) for c in children)
                return f"<{tag} {attr_str}>{child_xml}</{tag}>"
            return f"<{tag} {attr_str}/>"

        def _xml_escape(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

        return "".join(_convert(e) for e in elements)

    def _get_element_tree_from_wda(self) -> str:
        """Fetch element tree from WDA with timeout guard.

        Uses a 10-second per-attempt timeout and retries at most 2 times before
        giving up.  This prevents the annotator from hanging indefinitely when
        WDA becomes unresponsive mid-test.

        Returns:
            Raw XML string from WDA ``/session/<id>/source``.

        Raises:
            RuntimeError: When ``session_id`` is not set, or when all retries
                are exhausted due to network/timeout errors.
        """
        if not self.session_id:
            raise RuntimeError("No WDA session ID configured.")

        url = f"{self.wda_url}/session/{self.session_id}/source"
        max_retries = 2

        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                return data.get("value", "")
            except (urllib.error.URLError, socket.timeout) as exc:
                if attempt < max_retries - 1:
                    continue
                raise RuntimeError(
                    f"WDA /source timed out after {max_retries} attempts: {exc}"
                ) from exc

        # Unreachable — loop always returns or raises, but satisfies type checkers.
        raise RuntimeError("WDA fallback exhausted unexpectedly.")  # pragma: no cover

    # ------------------------------------------------------------------
    # Parsing — extract interactive elements
    # ------------------------------------------------------------------

    def parse_elements(self, xml_source: str) -> list[UIElement]:
        """Parse WDA XML source into a list of interactive ``UIElement`` objects.

        Filtering rules applied (all must pass):
        - Element type in ``_INTERACTIVE_TYPES``
        - ``visible == "true"``
        - ``enabled == "true"``
        - Has a non-empty ``label`` or ``value``
        - ``width`` and ``height`` both ``> _MIN_SIZE``
        - ``y >= 0`` and ``y < 1400 pt`` (rejects off-screen layout sentinels)
        - Not a *child redundancy*: StaticText/Image whose label is identical to
          its direct parent's label (e.g. Button "General" → child StaticText
          "General") — we keep the parent and drop the child.
        - Not a position duplicate: same label at nearly the same Y position
          (< 10 pt gap) but not yet caught by the parent-child check above.

        Args:
            xml_source: Raw XML string from WDA ``/source``.

        Returns:
            List of ``UIElement`` objects in document-order (top → bottom,
            left → right), with ``index`` starting at 1.
        """
        # Types that are almost always child-label duplicates of their parent
        # button/cell — only include if they have no interactive parent above.
        _SECONDARY_TYPES = frozenset({
            "XCUIElementTypeStaticText",
            "XCUIElementTypeImage",
        })

        root = ET.fromstring(xml_source)
        elements: list[UIElement] = []
        index = 1

        def _walk(node: ET.Element, parent_label: str = "") -> None:
            nonlocal index

            tag = node.tag
            if tag in _INTERACTIVE_TYPES:
                label = node.get("label") or node.get("name") or ""
                value = node.get("value") or ""
                enabled = node.get("enabled", "true").lower() == "true"
                visible = node.get("visible", "true").lower() == "true"

                try:
                    x = float(node.get("x", 0))
                    y = float(node.get("y", 0))
                    w = float(node.get("width", 0))
                    h = float(node.get("height", 0))
                except (TypeError, ValueError):
                    x = y = w = h = 0.0

                label_lower = label.lower()
                is_noise = any(f in label_lower for f in _NOISE_LABEL_FRAGMENTS)

                if (
                    visible
                    and enabled
                    and (label or value)
                    and w > _MIN_SIZE
                    and h > _MIN_SIZE
                    and y >= 0
                    and y < 1400  # well below any current device height
                    and not is_noise
                ):
                    # Drop secondary-type children whose label duplicates the
                    # parent (e.g. child StaticText "General" under Button
                    # "General") — they add visual noise without value.
                    child_redundant = (
                        tag in _SECONDARY_TYPES
                        and label
                        and label == parent_label
                    )

                    # Drop position duplicates (same label, nearly same Y).
                    pos_duplicate = any(
                        e.label == label and abs(e.y - y) < 10
                        for e in elements
                    )

                    if not child_redundant and not pos_duplicate:
                        elements.append(
                            UIElement(
                                index=index,
                                element_type=tag.replace("XCUIElementType", ""),
                                label=label,
                                value=value,
                                x=x,
                                y=y,
                                width=w,
                                height=h,
                                enabled=enabled,
                                visible=visible,
                            )
                        )
                        index += 1

            # Pass this node's label down so children can detect redundancy.
            this_label = node.get("label") or node.get("name") or ""
            for child in node:
                _walk(child, parent_label=this_label if this_label else parent_label)

        _walk(root)
        return elements

    # ------------------------------------------------------------------
    # Annotation — draw numbered badges on screenshot
    # ------------------------------------------------------------------

    def annotate_image(
        self,
        screenshot_b64: str,
        img_w: int,
        img_h: int,
        elements: list[UIElement],
        device_w: float = 390.0,
        device_h: float = 844.0,
    ) -> str:
        """Draw numbered badges on the screenshot image.

        Each interactive element gets:
        - A semi-transparent red bounding-box outline.
        - A filled red circle badge with a white number in the top-left corner.

        Coordinate mapping: device logical points → image pixels via linear
        scale factors ``(img_w / device_w, img_h / device_h)``.

        Args:
            screenshot_b64: Base-64 encoded PNG screenshot.
            img_w: Screenshot width in pixels.
            img_h: Screenshot height in pixels.
            elements: Parsed ``UIElement`` list from ``parse_elements()``.
            device_w: Device logical-point width (default 390 for iPhone 14/15).
            device_h: Device logical-point height (default 844 for iPhone 14/15).

        Returns:
            Base-64 encoded annotated PNG.
        """
        img_data = base64.b64decode(screenshot_b64)
        img = Image.open(io.BytesIO(img_data)).convert("RGBA")

        # Use actual image dimensions if the caller's values disagree with the
        # decoded image (handles retina where img_w may be 2x the device width).
        actual_w, actual_h = img.size
        if actual_w != img_w or actual_h != img_h:
            img_w, img_h = actual_w, actual_h

        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        scale_x = img_w / device_w
        scale_y = img_h / device_h

        # Font — fall back gracefully on non-macOS systems.
        font_paths = [
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/SFNSDisplay.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
        font = ImageFont.load_default()
        small_font = font
        for fp in font_paths:
            try:
                font = ImageFont.truetype(fp, 22)
                small_font = ImageFont.truetype(fp, 14)
                break
            except Exception:
                continue

        badge_size = 30

        for elem in elements:
            ix = int(elem.x * scale_x)
            iy = int(elem.y * scale_y)
            iw = max(int(elem.width * scale_x), 1)
            ih = max(int(elem.height * scale_y), 1)

            # Bounding box outline — semi-transparent red.
            draw.rectangle(
                [ix, iy, ix + iw, iy + ih],
                outline=(220, 40, 40, 180),
                width=2,
            )

            # Badge circle — placed at the top-left corner of the bounding box,
            # shifted slightly so it sits *on* the border rather than inside.
            bx = max(ix - 2, 0)
            by = max(iy - 2, 0)
            draw.ellipse(
                [bx, by, bx + badge_size, by + badge_size],
                fill=(220, 40, 40, 230),
            )

            # White number centred inside the badge.
            num_text = str(elem.index)
            try:
                text_bbox = draw.textbbox((0, 0), num_text, font=font)
                tw = text_bbox[2] - text_bbox[0]
                th = text_bbox[3] - text_bbox[1]
            except AttributeError:
                # Pillow < 9 fallback
                tw, th = draw.textsize(num_text, font=font)  # type: ignore[attr-defined]

            draw.text(
                (bx + (badge_size - tw) // 2, by + (badge_size - th) // 2 - 2),
                num_text,
                fill=(255, 255, 255, 255),
                font=font,
            )

        result = Image.alpha_composite(img, overlay).convert("RGB")
        buf = io.BytesIO()
        result.save(buf, format="PNG")
        return base64.standard_b64encode(buf.getvalue()).decode("ascii")

    # ------------------------------------------------------------------
    # High-level pipeline
    # ------------------------------------------------------------------

    def annotate(
        self,
        screenshot_b64: str,
        img_w: int,
        img_h: int,
        device_w: float = 390.0,
        device_h: float = 844.0,
    ) -> tuple[list[UIElement], str]:
        """Full pipeline: fetch tree → parse → annotate → return.

        Args:
            screenshot_b64: Base-64 encoded PNG screenshot.
            img_w: Screenshot width in pixels.
            img_h: Screenshot height in pixels.
            device_w: Device logical-point width.
            device_h: Device logical-point height.

        Returns:
            ``(elements, annotated_b64)`` — the list of ``UIElement`` objects
            (index-1 based, i.e. ``elements[0].index == 1``) and the
            base-64 annotated PNG string.
        """
        xml_source = self.get_element_tree()
        elements = self.parse_elements(xml_source)
        annotated = self.annotate_image(
            screenshot_b64, img_w, img_h, elements, device_w, device_h
        )
        return elements, annotated

    # ------------------------------------------------------------------
    # Scroll-stuck prevention helpers
    # ------------------------------------------------------------------

    def _screen_changed(self, before_tree: str, after_tree: str) -> bool:
        """Compare element trees before/after a scroll to detect content change.

        Guard 2: If the screen is effectively unchanged after a scroll the
        runner has hit a scroll boundary and should stop looping.

        Uses ``(label, y_bucket)`` tuples for a fast, position-aware comparison.
        Empty trees are treated as "changed" (safer default — avoids false stops
        when the tree cannot be fetched).

        Args:
            before_tree: Raw XML element tree captured before the scroll.
            after_tree: Raw XML element tree captured after the scroll.

        Returns:
            ``False`` when > 80 % of ``(label, y_bucket)`` pairs overlap —
            i.e. the screen is effectively unchanged.
            ``True`` (changed) in all other cases, including parse failures.
        """
        def _extract_signatures(xml_source: str) -> set[tuple[str, int]]:
            sigs: set[tuple[str, int]] = set()
            try:
                root = ET.fromstring(xml_source)
            except Exception:
                return sigs
            for node in root.iter():
                label = (node.get("label") or node.get("name") or "").strip()
                if not label:
                    continue
                try:
                    y = float(node.get("y", 0))
                except (TypeError, ValueError):
                    y = 0.0
                sigs.add((label, round(y / 10)))
            return sigs

        before_sigs = _extract_signatures(before_tree)
        after_sigs = _extract_signatures(after_tree)

        # Empty trees → treat as changed (safe default).
        if not before_sigs or not after_sigs:
            return True

        overlap = len(before_sigs & after_sigs)
        # Use the larger set as the denominator so a partially-loaded tree
        # doesn't produce a misleadingly high overlap ratio.
        denominator = max(len(before_sigs), len(after_sigs))
        overlap_ratio = overlap / denominator

        return overlap_ratio <= 0.80  # True = changed; False = same

    # ------------------------------------------------------------------
    # Text formatting for Claude context
    # ------------------------------------------------------------------

    def elements_text(self, elements: list[UIElement]) -> str:
        """Format the element list as a compact text block for Claude's prompt.

        Example output::

            [1] Button "Wi-Fi" (195, 104)
            [2] Cell "Bluetooth" (195, 148)
            [3] Cell "General" value="1" (195, 258)

        Args:
            elements: List of ``UIElement`` objects.

        Returns:
            Newline-joined string — one element per line.
        """
        lines: list[str] = []
        for e in elements:
            parts = [f"[{e.index}]", e.element_type]
            if e.label:
                parts.append(f'"{e.label}"')
            if e.value:
                parts.append(f'value="{e.value}"')
            parts.append(f"({e.center_x:.0f}, {e.center_y:.0f})")
            lines.append(" ".join(parts))
        return "\n".join(lines)

    def elements_json(self, elements: list[UIElement]) -> str:
        """Serialise the element list to a compact JSON string."""
        return json.dumps([e.to_dict() for e in elements], separators=(",", ":"))
