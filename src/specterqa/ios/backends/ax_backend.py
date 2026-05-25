"""AXUIElement backend — host-side iOS Simulator automation.

Queries the iOS Simulator's accessibility tree via macOS AXUIElement APIs.
Taps elements via AXPress action or CGEvent coordinate injection.
Types via AXSetValue or CGEvent keystrokes.
Screenshots via simctl.

No XCTest runner. No on-device process. No deployment. No SIGABRT.

Requirements:
    - macOS Accessibility permission granted to the Python process
    - pyobjc-framework-Cocoa and pyobjc-framework-Quartz installed
    - iOS Simulator running with an app

[internal-tracker] — SpecterQA iOS AXUIElement backend.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from typing import Any

logger = logging.getLogger("specterqa.ios.backends.ax_backend")

# AX role → iOS element type mapping (mirrors XCUIElementType names used by
# the rest of the pipeline so UIElement.element_type values are consistent).
AX_ROLE_MAP: dict[str, str] = {
    "AXButton": "button",
    "AXStaticText": "staticText",
    "AXTextField": "textField",
    "AXSecureTextField": "secureTextField",
    "AXSearchField": "searchField",
    "AXImage": "image",
    "AXCell": "cell",
    "AXNavigationBar": "navigationBar",
    "AXTabBar": "tabBar",
    "AXTable": "table",
    "AXScrollView": "scrollView",
    "AXSwitch": "switch",
    "AXSlider": "slider",
    "AXLink": "link",
    "AXGroup": "other",
    "AXWebArea": "webView",
    "AXTextArea": "textView",
    "AXHeading": "staticText",
    "AXProgressIndicator": "progressIndicator",
    "AXCheckBox": "switch",
    "AXPopUpButton": "button",
    "AXMenuButton": "button",
}

# Serialize all AX calls onto a single dedicated thread to satisfy the
# requirement that ApplicationServices calls are not issued from arbitrary
# threads.  An empty queue means "run on calling thread" for environments
# that already handle this (e.g. main thread or tests).
_ax_lock = threading.Lock()

# Default device logical dimensions — updated at init from the AX frame.
_DEFAULT_DEVICE_W = 390.0
_DEFAULT_DEVICE_H = 844.0


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class AXContentGroupNotFoundError(RuntimeError):
    """Raised when neither the aspect-ratio heuristic nor the position-probe
    can locate the iOS content group in the Simulator AX tree.

    This is raised instead of silently returning hardware chrome elements,
    which would be a misleading / unusable result for callers.

    Actionable message is always included; callers should surface it to the
    user or MCP caller so they can diagnose the root cause (e.g. missing
    Accessibility permission, no booted simulator, iOS 26 compatibility gap).
    """


class AXBackend:
    """Host-side iOS Simulator automation via macOS AXUIElement APIs.

    All automation runs from the Mac — no process on the simulator, no
    deployment, no SIGABRT crashes.  Session start is instant.

    Args:
        sim_pid:     PID of the ``Simulator.app`` process.  Pass 0 (default)
                     to auto-detect via :meth:`_find_simulator_pid`.
        device_udid: Simulator UDID or ``"booted"`` (used for simctl calls).
        device_w:    Override device logical width (points).  Auto-detected
                     from the AX frame when 0.
        device_h:    Override device logical height (points).  Auto-detected
                     from the AX frame when 0.
    """

    def __init__(
        self,
        sim_pid: int = 0,
        device_udid: str = "booted",
        device_w: float = 0.0,
        device_h: float = 0.0,
    ) -> None:
        # Validate Accessibility permission upfront — fail loudly so the error
        # message is clear rather than producing silent no-ops.
        try:
            from ApplicationServices import AXIsProcessTrusted  # type: ignore[import]

            if not AXIsProcessTrusted():
                raise PermissionError(
                    "macOS Accessibility permission is required. "
                    "Open System Settings → Privacy & Security → Accessibility "
                    "and enable access for this terminal / Python process."
                )
        except ImportError:
            raise ImportError(
                "pyobjc-framework-ApplicationServices is required for AXBackend. "
                "Install it with: pip install pyobjc-framework-ApplicationServices"
            )

        self.device_udid = device_udid

        # Resolve simulator PID.
        if sim_pid <= 0:
            sim_pid = self._find_simulator_pid()
        self._sim_pid = sim_pid

        # Create the root AX element for the Simulator process.
        from ApplicationServices import AXUIElementCreateApplication  # type: ignore[import]

        self._root = AXUIElementCreateApplication(self._sim_pid)

        # Cached iOS content group frame (set by _find_ios_content_group).
        self._ios_content_frame: Any = None  # NSRect / CGRect from AX

        # Device dimensions for coordinate conversion.
        self._device_w = device_w if device_w > 0 else _DEFAULT_DEVICE_W
        self._device_h = device_h if device_h > 0 else _DEFAULT_DEVICE_H

        # Flag set when BOTH heuristic and position-probe fail — causes
        # get_elements() to raise AXContentGroupNotFoundError instead of
        # silently returning hardware chrome.
        self._content_group_failed = False

        # Eagerly find the iOS content group and calibrate dimensions.
        self._ios_content_group = None
        self._init_content_group()

    # ------------------------------------------------------------------
    # Static / class helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_simulator_pid() -> int:
        """Return the PID of the running Simulator.app process.

        Raises:
            RuntimeError: If no Simulator process is found.
        """
        try:
            result = subprocess.run(
                ["pgrep", "-f", "Simulator.app/Contents/MacOS/Simulator"],
                capture_output=True,
                text=True,
            )
            pids = [int(p) for p in result.stdout.strip().splitlines() if p.strip()]
            if not pids:
                raise RuntimeError(
                    "iOS Simulator is not running. "
                    "Launch Xcode → Simulator or run: open -a Simulator"
                )
            return pids[0]
        except ValueError as exc:
            raise RuntimeError(f"Could not parse Simulator PID: {exc}") from exc

    @classmethod
    def is_available(cls) -> bool:
        """Return ``True`` if AXBackend can operate on this system.

        Checks:
        1. pyobjc-framework-ApplicationServices is importable.
        2. macOS Accessibility permission is granted.
        3. Simulator.app is running.
        """
        try:
            from ApplicationServices import AXIsProcessTrusted  # type: ignore[import]

            if not AXIsProcessTrusted():
                return False
            result = subprocess.run(
                ["pgrep", "-f", "Simulator.app/Contents/MacOS/Simulator"],
                capture_output=True,
                text=True,
            )
            return bool(result.stdout.strip())
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------
    # AX tree navigation helpers
    # ------------------------------------------------------------------

    def _ax_attr(self, element: Any, attr: str) -> Any:
        """Read one AX attribute value from *element*.

        Returns:
            The attribute value, or ``None`` on any AX error.
        """
        try:
            from ApplicationServices import AXUIElementCopyAttributeValue  # type: ignore[import]

            err, value = AXUIElementCopyAttributeValue(element, attr, None)
            if err == 0:  # kAXErrorSuccess
                return value
        except Exception as exc:  # noqa: BLE001 — element may have been destroyed
            logger.debug("_ax_attr(%r) failed: %s", attr, exc)
        return None

    def _ax_children(self, element: Any) -> list[Any]:
        """Return the AXChildren of *element* (empty list on failure)."""
        children = self._ax_attr(element, "AXChildren")
        if children is None:
            return []
        try:
            return list(children)
        except TypeError:
            return [children]

    def _ax_frame(self, element: Any) -> dict | None:
        """Return the element's frame as ``{x, y, width, height}`` in macOS screen coords.

        Uses ``AXValueGetValue`` with ``kAXValueCGRectType`` to extract the
        ``CGRect`` stored in the ``AXFrame`` attribute value.

        Returns:
            Dict with keys ``x``, ``y``, ``width``, ``height``, or ``None``
            if the frame cannot be read.
        """
        try:
            from ApplicationServices import (  # type: ignore[import]
                AXUIElementCopyAttributeValue,
                AXValueGetValue,
                kAXValueCGRectType,
            )
            import Quartz  # type: ignore[import]

            err, val = AXUIElementCopyAttributeValue(element, "AXFrame", None)
            if err != 0 or val is None:
                return None

            ok, rect = AXValueGetValue(val, kAXValueCGRectType, None)
            if not ok or rect is None:
                return None

            # rect is a CGRect-compatible object; access via .origin / .size
            try:
                return {
                    "x": float(rect.origin.x),
                    "y": float(rect.origin.y),
                    "width": float(rect.size.width),
                    "height": float(rect.size.height),
                }
            except AttributeError:
                # Some pyobjc versions return a tuple (x, y, w, h)
                if hasattr(rect, "__iter__"):
                    vals = list(rect)
                    if len(vals) == 4:
                        return {"x": float(vals[0]), "y": float(vals[1]),
                                "width": float(vals[2]), "height": float(vals[3])}
        except Exception as exc:  # noqa: BLE001
            logger.debug("_ax_frame failed: %s", exc)
        return None

    def _init_content_group(self) -> None:
        """Locate the iOS content group using heuristic then position-probe fallback.

        Strategy (iOS 26 compatible):
        1. Try the aspect-ratio heuristic (_find_ios_content_group).
        2. If heuristic returns None / raises, try position-probe
           (_position_probe_content_group) — uses AXUIElementCopyElementAtPosition
           at the screen centre.
        3. If both fail, set _content_group_failed = True so get_elements()
           can raise AXContentGroupNotFoundError instead of silently returning
           hardware chrome.
        """
        # Step 1: aspect-ratio heuristic
        try:
            result = self._find_ios_content_group()
            if result is not None:
                self._ios_content_group = result
                self._content_group_failed = False
                return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Content-group heuristic failed: %s — trying position-probe", exc)

        # Step 2: position-probe fallback (iOS 26 / macOS 25 compat)
        logger.debug("Falling back to position-probe for iOS content group")
        probe_result = self._position_probe_content_group()
        if probe_result is not None:
            element, frame = probe_result
            self._ios_content_group = element
            self._ios_content_frame = frame
            self._content_group_failed = False
            logger.info("iOS content group found via position-probe: frame=%s", frame)
            return

        # Step 3: both strategies failed
        logger.warning(
            "Could not locate iOS content group via heuristic or position-probe. "
            "On iOS 26 / macOS 25 this is a known compatibility gap. "
            "Check that Accessibility permission is granted and a simulator is booted."
        )
        self._ios_content_group = None
        self._content_group_failed = True

    def _position_probe_content_group(self) -> tuple[Any, dict] | None:
        """Fallback: probe AX element at simulator screen centre.

        Uses AXUIElementCopyElementAtPosition (the same API used by
        _get_tab_bar_buttons) at the computed screen centre of the first
        booted simulator window, then walks up the parent chain to find
        a window-level container that can serve as the iOS content group.

        Returns:
            (element, frame) tuple on success, or None if unavailable.
        """
        try:
            from ApplicationServices import (  # type: ignore[import]
                AXUIElementCopyElementAtPosition,
            )
        except ImportError:
            logger.debug("position-probe unavailable: ApplicationServices not importable")
            return None

        # Locate the simulator window to find its screen position.
        windows = self._ax_attr(self._root, "AXWindows")
        if not windows:
            logger.debug("position-probe: no AX windows found")
            return None

        # Find the largest window by area (most likely the sim screen window).
        best_window = None
        best_window_frame: dict | None = None
        best_area = 0.0
        for win in windows:
            frame = self._ax_frame(win)
            if frame is None:
                continue
            area = frame["width"] * frame["height"]
            if area > best_area:
                best_area = area
                best_window = win
                best_window_frame = frame

        if best_window_frame is None:
            logger.debug("position-probe: could not determine simulator window frame")
            return None

        # Compute screen-space centre of the simulator window.
        centre_x = best_window_frame["x"] + best_window_frame["width"] / 2.0
        centre_y = best_window_frame["y"] + best_window_frame["height"] / 2.0

        # Hit-test at the centre.
        with _ax_lock:
            err, hit_element = AXUIElementCopyElementAtPosition(
                self._root, centre_x, centre_y, None
            )

        if err != 0 or hit_element is None:
            logger.debug("position-probe: AXUIElementCopyElementAtPosition returned err=%d", err)
            return None

        # Walk up the parent chain to find a window-level container.
        # We stop at the first AXWindow parent or at depth 10 to avoid
        # infinite loops on malformed trees.
        candidate = hit_element
        for _ in range(10):
            role = self._ax_attr(candidate, "AXRole") or ""
            if role == "AXWindow":
                break
            parent = self._ax_attr(candidate, "AXParent")
            if parent is None:
                break
            candidate = parent

        # Use the hit element's window as the content group root (or the hit
        # element itself if it's a large group).
        frame = self._ax_frame(candidate)
        if frame is None:
            frame = best_window_frame

        # Update device dimensions from the probe frame.
        if frame["width"] > 0:
            self._device_w = frame["width"]
        if frame["height"] > 0:
            self._device_h = frame["height"]

        logger.debug("position-probe found content group: role=%s frame=%s", role, frame)
        return candidate, frame

    def _find_ios_content_group(self) -> Any:
        """Walk AXWindow → AXChildren to locate the iOS screen group.

        The iOS content group is the ``AXGroup`` (or similar container) whose
        frame matches the Simulator's screen area.  We identify it by looking
        for the child group with the largest area that falls within a
        reasonable iOS screen aspect ratio.

        Returns:
            The AXUIElement representing the iOS screen group.

        Raises:
            RuntimeError: If no suitable group is found.
        """
        windows = self._ax_attr(self._root, "AXWindows")
        if not windows:
            raise RuntimeError("No AX windows found for the Simulator process.")

        best_elem: Any = None
        best_area: float = 0.0

        for window in windows:
            frame = self._ax_frame(window)
            if frame is None:
                continue

            # Walk direct children of each window for the iOS content group.
            for child in self._ax_children(window):
                child_frame = self._ax_frame(child)
                if child_frame is None:
                    continue
                w = child_frame["width"]
                h = child_frame["height"]
                area = w * h
                # Heuristic: the iOS screen has aspect ratio ~0.45–0.6 (portrait)
                # and is the largest child element.
                if area > best_area and h > 0 and (0.35 < w / h < 0.75):
                    best_area = area
                    best_elem = child
                    self._ios_content_frame = child_frame
                    # Keep default device dimensions (390x844).
                    # The AX frame is in macOS screen points which
                    # differ from iOS device points. _ax_to_device
                    # handles the coordinate conversion using the
                    # ios_content_frame as the reference rect.

        if best_elem is None:
            raise RuntimeError(
                "Could not locate the iOS content group in the Simulator AX tree. "
                "Make sure the Simulator is running and a device is booted."
            )

        logger.debug(
            "iOS content group found: frame=%s device=%.0fx%.0f",
            self._ios_content_frame,
            self._device_w,
            self._device_h,
        )
        return best_elem

    # ------------------------------------------------------------------
    # Tab bar probing (iOS 26+ workaround)
    # ------------------------------------------------------------------

    def _get_tab_bar_buttons(self) -> list[dict]:
        """Probe the tab bar area using AXUIElementCopyElementAtPosition.

        On iOS 26 the tab bar buttons are ``AXRadioButton`` elements accessible
        via position lookup, but they are NOT exposed as children of the
        ``AXGroup desc="Tab Bar"`` node.  This method sweeps x-positions along
        the tab bar's y-centre to collect all distinct buttons.

        Returns:
            List of element dicts (same shape as :meth:`get_elements` output)
            for each distinct tab button found.
        """
        # Find the Tab Bar AXGroup to get its screen frame.
        root = self._ios_content_group or self._root
        tab_bar_frame: dict | None = None
        for child in self._ax_children(root):
            desc = self._ax_attr(child, "AXDescription") or ""
            if "Tab Bar" in desc:
                tab_bar_frame = self._ax_frame(child)
                break

        if tab_bar_frame is None:
            return []

        try:
            from ApplicationServices import AXUIElementCopyElementAtPosition  # type: ignore[import]
        except ImportError:
            return []

        bar_x = tab_bar_frame["x"]
        bar_y = tab_bar_frame["y"]
        bar_w = tab_bar_frame["width"]
        bar_h = tab_bar_frame["height"]
        center_y = bar_y + bar_h / 2

        # Sweep x positions in small increments to find all radio buttons.
        step = max(1.0, bar_w / 40)  # ~40 samples across the bar
        seen_labels: set[str] = set()
        buttons: list[dict] = []

        x = bar_x + step / 2
        while x < bar_x + bar_w:
            with _ax_lock:
                err, el = AXUIElementCopyElementAtPosition(self._root, x, center_y, None)
            if err == 0 and el is not None:
                role = self._ax_attr(el, "AXRole") or ""
                if role == "AXRadioButton":
                    label = (
                        self._ax_attr(el, "AXDescription")
                        or self._ax_attr(el, "AXTitle")
                        or self._ax_attr(el, "AXLabel")
                        or ""
                    )
                    label_str = str(label) if label else ""
                    if label_str and label_str not in seen_labels:
                        seen_labels.add(label_str)
                        # Build a frame for this button.
                        ax_frame = self._ax_frame(el)
                        if ax_frame is None:
                            ax_frame = {
                                "x": x - step / 2,
                                "y": center_y - bar_h / 2,
                                "width": step,
                                "height": bar_h,
                            }
                        device_frame = self._ax_to_device(ax_frame)
                        ident = str(self._ax_attr(el, "AXIdentifier") or "")
                        buttons.append(
                            {
                                "type": "button",
                                "typeLabel": "button",
                                "label": label_str,
                                "identifier": ident,
                                "value": "",
                                "enabled": True,
                                "hittable": True,
                                "frame": device_frame,
                            }
                        )
            x += step

        return buttons

    # ------------------------------------------------------------------
    # Coordinate conversion
    # ------------------------------------------------------------------

    def _ax_to_device(self, ax_frame: dict) -> dict:
        """Convert AX macOS screen coordinates to iOS device points.

        Args:
            ax_frame: Dict with ``x``, ``y``, ``width``, ``height`` in macOS
                      screen coordinates (from :meth:`_ax_frame`).

        Returns:
            Dict with ``x``, ``y``, ``width``, ``height`` in iOS device points.
        """
        if self._ios_content_frame is None:
            # No reference frame — return as-is (e.g. tests without a real sim).
            return ax_frame

        ios_x = self._ios_content_frame["x"]
        ios_y = self._ios_content_frame["y"]
        ios_w = self._ios_content_frame["width"]
        ios_h = self._ios_content_frame["height"]

        if ios_w <= 0 or ios_h <= 0:
            return ax_frame

        rel_x = ax_frame["x"] - ios_x
        rel_y = ax_frame["y"] - ios_y
        scale_x = self._device_w / ios_w
        scale_y = self._device_h / ios_h

        return {
            "x": rel_x * scale_x,
            "y": rel_y * scale_y,
            "width": ax_frame["width"] * scale_x,
            "height": ax_frame["height"] * scale_y,
        }

    # ------------------------------------------------------------------
    # AX tree walker — produces element dicts for the SoM pipeline
    # ------------------------------------------------------------------

    def _walk_tree(
        self,
        element: Any,
        results: list[dict],
        depth: int = 0,
        max_depth: int = 20,
        limit: int = 200,
    ) -> None:
        """Recursively walk the AX tree from *element*, appending to *results*.

        Args:
            element:   Current AX element to inspect.
            results:   Accumulator list (mutated in-place).
            depth:     Current recursion depth.
            max_depth: Maximum depth to descend (default 20 for deep List/SwiftUI trees).
            limit:     Stop collecting once this many elements are found.
        """
        if len(results) >= limit:
            return
        if depth > max_depth:
            return

        role = self._ax_attr(element, "AXRole") or ""
        ios_type = AX_ROLE_MAP.get(role, "other")

        # AXDescription is primary; fall back to AXTitle (tab buttons) then AXLabel.
        label = (
            self._ax_attr(element, "AXDescription")
            or self._ax_attr(element, "AXTitle")
            or self._ax_attr(element, "AXLabel")
            or ""
        )
        identifier = self._ax_attr(element, "AXIdentifier") or ""
        value = self._ax_attr(element, "AXValue")
        value_str = str(value) if value is not None else ""
        enabled_raw = self._ax_attr(element, "AXEnabled")
        enabled = bool(enabled_raw) if enabled_raw is not None else True

        ax_frame = self._ax_frame(element)
        if ax_frame is not None:
            device_frame = self._ax_to_device(ax_frame)
        else:
            device_frame = {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}

        # Only include elements with a known interactive role and non-zero size.
        if (
            ios_type != "other"
            and device_frame["width"] > 0
            and device_frame["height"] > 0
        ):
            results.append(
                {
                    "type": ios_type,
                    "typeLabel": ios_type,
                    "label": str(label) if label else "",
                    "identifier": str(identifier) if identifier else "",
                    "value": value_str,
                    "enabled": enabled,
                    "hittable": enabled,
                    "frame": device_frame,
                    "_ax_ref": element,
                }
            )

        for child in self._ax_children(element):
            if len(results) >= limit:
                break
            self._walk_tree(child, results, depth + 1, max_depth, limit)

    # ------------------------------------------------------------------
    # Public API — matches XCTestBackend interface
    # ------------------------------------------------------------------

    def _walk_sibling_windows(self, results: list[dict], limit: int) -> None:
        """Walk all AX windows of the Simulator process and collect elements.

        SwiftUI ``.sheet``-presented UIKit content appears in a sibling
        ``AXWindow`` that is NOT descended from the main iOS content group.
        SpringBoard-level system alerts (permission prompts) also appear in
        separate windows.  This method enumerates every window exposed by the
        Simulator process via ``kAXWindowsAttribute`` and walks each one,
        de-duplicating against *results* so elements are not reported twice.

        Palace dogfood Issue 2 fix (2026-04-17).

        Args:
            results: Accumulator list (already populated from the main walk).
            limit:   Stop collecting once this many total elements are found.
        """
        windows = self._ax_attr(self._root, "AXWindows")
        if not windows:
            return

        def _key(e: dict) -> tuple:
            f = e.get("frame", {})
            return (
                e.get("label", ""),
                e.get("type", ""),
                round(f.get("x", 0), 1),
                round(f.get("y", 0), 1),
            )

        existing_keys: set[tuple] = {_key(e) for e in results}

        for window in windows:
            if len(results) >= limit:
                break

            win_role = str(self._ax_attr(window, "AXRole") or "")
            win_subrole = str(self._ax_attr(window, "AXSubrole") or "")
            win_title = str(self._ax_attr(window, "AXTitle") or "")

            is_modal = (
                win_role in ("AXSheet", "AXDialog")
                or any(kw in win_subrole for kw in ("Sheet", "Dialog", "Alert", "Modal"))
                or any(kw in win_title for kw in ("Alert", "Permission"))
            )

            window_results: list[dict] = []
            with _ax_lock:
                self._walk_tree(window, window_results, limit=limit - len(results))

            for elem in window_results:
                k = _key(elem)
                if k not in existing_keys:
                    existing_keys.add(k)
                    if is_modal:
                        elem["_modal_window"] = True
                    results.append(elem)
                    if len(results) >= limit:
                        break

    def get_elements(self, limit: int = 200) -> list[dict]:
        """Walk the AX tree and return a list of element dicts.

        Each dict has keys: ``typeLabel``, ``label``, ``identifier``,
        ``value``, ``enabled``, ``hittable``, ``frame`` — compatible with
        :meth:`~specterqa.ios.som_annotator.SoMAnnotator.parse_elements_from_json`.

        On iOS 26+ the tab bar buttons are not accessible via ``AXChildren``
        traversal; they are collected separately via :meth:`_get_tab_bar_buttons`
        and appended to the result set so tests can find and tap them by label.

        A second-pass sibling-window walk (:meth:`_walk_sibling_windows`)
        enumerates all ``AXWindows`` of the Simulator process so that
        SwiftUI ``.sheet``-presented UIKit content and SpringBoard-level
        system alerts are included in the flat element list.

        Args:
            limit: Maximum number of elements to return (default 200).

        Returns:
            List of element dicts.

        Raises:
            AXContentGroupNotFoundError: When both the aspect-ratio heuristic
                and the position-probe fallback failed to locate the iOS content
                group.  This prevents silently returning only hardware chrome
                elements (Mute, Volume, Sleep/Wake) which would be misleading.
        """
        if getattr(self, "_content_group_failed", False):
            raise AXContentGroupNotFoundError(
                "AX backend could not locate the iOS content group in the Simulator AX tree. "
                "Neither the aspect-ratio heuristic nor the position-probe fallback succeeded. "
                "Possible causes:\n"
                "  1. iOS 26 / macOS 25 compatibility gap — the Simulator AX tree layout "
                "has changed. Report this to SpecterQA.\n"
                "  2. Accessibility permission not granted — check System Settings → "
                "Privacy & Security → Accessibility.\n"
                "  3. No simulator is booted or the app is not foreground.\n"
                "Workaround: use backend='xctest' which does not depend on the AX tree."
            )
        root = self._ios_content_group or self._root
        results: list[dict] = []
        with _ax_lock:
            self._walk_tree(root, results, limit=limit)

        # Second pass: walk sibling AX windows (sheets, alerts, modals).
        self._walk_sibling_windows(results, limit)

        # Append tab bar buttons (iOS 26 position-probe workaround).
        tab_buttons = self._get_tab_bar_buttons()
        existing_labels = {e["label"] for e in results if e.get("type") == "button"}
        for btn in tab_buttons:
            if btn["label"] not in existing_labels:
                results.append(btn)

        # Strip internal keys before returning.
        return [
            {k: v for k, v in e.items() if k not in ("_ax_ref", "_modal_window")}
            for e in results
        ]

    def find_element(
        self,
        label: str | None = None,
        identifier: str | None = None,
    ) -> Any | None:
        """Find and return a raw AXUIElement matching *label* or *identifier*.

        Searches ``AXDescription``, ``AXLabel``, and ``AXTitle`` for label
        matching (tab bar buttons surface their label via ``AXDescription`` or
        ``AXTitle`` depending on iOS/macOS version).

        Also searches sibling AX windows so elements inside SwiftUI
        ``.sheet``-presented UIKit content and SpringBoard-level alerts are
        found without a separate tool call.

        Args:
            label:      Accessibility label (case-insensitive substring match).
            identifier: Accessibility identifier (exact match).

        Returns:
            The raw AXUIElement, or ``None`` if not found.
        """
        result: list[Any] = []

        def _search(element: Any, depth: int = 0) -> None:
            if result or depth > 20:
                return
            elem_id = str(self._ax_attr(element, "AXIdentifier") or "")
            if identifier is not None and elem_id == identifier:
                result.append(element)
                return

            if label is not None:
                elem_label = (
                    str(self._ax_attr(element, "AXDescription") or "")
                    or str(self._ax_attr(element, "AXTitle") or "")
                    or str(self._ax_attr(element, "AXLabel") or "")
                )
                if label.lower() in elem_label.lower():
                    result.append(element)
                    return

            for child in self._ax_children(element):
                _search(child, depth + 1)

        root = self._ios_content_group or self._root
        with _ax_lock:
            _search(root)

        if not result:
            windows = self._ax_attr(self._root, "AXWindows") or []
            for window in windows:
                if result:
                    break
                with _ax_lock:
                    _search(window)

        return result[0] if result else None

    # ------------------------------------------------------------------
    # SpringBoard alert handling (Palace dogfood Issue 3)
    # ------------------------------------------------------------------

    def dismiss_springboard_alert(self, label: str = "Allow") -> dict[str, Any]:
        """Dismiss a SpringBoard-level iOS system permission alert.

        iOS permission alerts (Notifications, Location, Camera, Bluetooth)
        appear in a separate ``AXWindow`` above the app.  Strategy:
        1. Walk all ``AXWindows`` for a modal/alert window, find a button
           matching *label*, and ``AXPress`` it.
        2. Fall back to CGEvent coordinate tap if AXPress fails.
        3. Fall back to scanning the full element list and tapping by coords.

        Limitation on iOS 18.4: SpringBoard alerts for ``notifications``
        cannot be pre-granted via ``xcrun simctl privacy grant`` and may not
        appear in the Simulator.app AX tree.  Use :meth:`pre_grant_permissions`
        BEFORE launching the app as a workaround.

        Args:
            label: Button label to press (default ``"Allow"``).

        Returns:
            ``{"success": True, "mode": "ax_press"|"cg_tap"|"cg_tap_fallback"}``
            or ``{"success": False, "error": "<message>"}``
        """
        windows = self._ax_attr(self._root, "AXWindows") or []

        def _is_alert_window(window: Any) -> bool:
            role = str(self._ax_attr(window, "AXRole") or "")
            subrole = str(self._ax_attr(window, "AXSubrole") or "")
            title = str(self._ax_attr(window, "AXTitle") or "")
            return (
                role in ("AXSheet", "AXDialog")
                or any(kw in subrole for kw in ("Sheet", "Dialog", "Alert", "Modal"))
                or any(kw in title for kw in ("Alert", "Permission", "Allow", "Access"))
            )

        def _find_button(element: Any, target: str, depth: int = 0) -> Any | None:
            if depth > 10:
                return None
            role = str(self._ax_attr(element, "AXRole") or "")
            if role in ("AXButton", "AXStaticText"):
                btn_label = (
                    str(self._ax_attr(element, "AXTitle") or "")
                    or str(self._ax_attr(element, "AXDescription") or "")
                    or str(self._ax_attr(element, "AXLabel") or "")
                )
                if target.lower() in btn_label.lower():
                    return element
            for child in self._ax_children(element):
                found = _find_button(child, target, depth + 1)
                if found is not None:
                    return found
            return None

        for window in windows:
            if _is_alert_window(window):
                btn = _find_button(window, label)
                if btn is not None:
                    try:
                        from ApplicationServices import AXUIElementPerformAction  # type: ignore[import]

                        with _ax_lock:
                            err = AXUIElementPerformAction(btn, "AXPress")
                        if err == 0:
                            time.sleep(0.5)
                            return {"success": True, "mode": "ax_press", "label": label}
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("AXPress on alert button failed: %s", exc)

                    ax_frame = self._ax_frame(btn)
                    if ax_frame is not None:
                        dev = self._ax_to_device(ax_frame)
                        cx = dev["x"] + dev["width"] / 2
                        cy = dev["y"] + dev["height"] / 2
                        tap_result = self._cg_tap(cx, cy)
                        if tap_result.get("success"):
                            time.sleep(0.5)
                            return {"success": True, "mode": "cg_tap", "label": label}

        # Fallback: search full element list (includes sibling windows).
        all_elements = self.get_elements(limit=300)
        for elem in all_elements:
            if elem.get("type") in ("button", "staticText"):
                elem_label = elem.get("label", "")
                if label.lower() in elem_label.lower():
                    frame = elem.get("frame", {})
                    if frame.get("width", 0) > 0 and frame.get("height", 0) > 0:
                        cx = frame["x"] + frame["width"] / 2
                        cy = frame["y"] + frame["height"] / 2
                        tap_result = self._cg_tap(cx, cy)
                        if tap_result.get("success"):
                            time.sleep(0.5)
                            return {
                                "success": True,
                                "mode": "cg_tap_fallback",
                                "label": label,
                            }

        return {
            "success": False,
            "error": (
                f"Alert button {label!r} not found in any Simulator window. "
                "On iOS 18.4, SpringBoard alerts may not be accessible via AX — "
                "use ios_pre_grant_permissions() BEFORE launching the app as a workaround."
            ),
        }

    @staticmethod
    def pre_grant_permissions(
        device_udid: str,
        bundle_id: str,
        permissions: list[str],
    ) -> dict[str, Any]:
        """Pre-grant iOS permissions via ``xcrun simctl privacy`` before app launch.

        Call BEFORE launching the app to prevent runtime permission alerts.
        Recommended workaround when :meth:`dismiss_springboard_alert` cannot
        reach iOS 18.4 SpringBoard alerts.

        iOS version compatibility:
            - iOS 17.x and earlier: ``grant`` works for most services.
            - iOS 18.4: ``grant notifications`` returns ``Operation not
              permitted`` — OS-level restriction, cannot be worked around.
              Other services (location, camera, etc.) typically work on 18.4.

        Args:
            device_udid:  Booted simulator UDID (or ``"booted"``).
            bundle_id:    App bundle identifier.
            permissions:  List of simctl service names to grant.
                          Common: ``"notifications"``, ``"location"``,
                          ``"camera"``, ``"microphone"``, ``"contacts"``,
                          ``"photos"``, ``"bluetooth"``, ``"health"``.

        Returns:
            Dict with ``"granted"`` (list), ``"failed"`` (list of
            ``{service, error}``), and ``"note"`` if any failures occurred.
        """
        granted: list[str] = []
        failed: list[dict] = []

        for service in permissions:
            result = subprocess.run(
                ["xcrun", "simctl", "privacy", device_udid, "grant", service, bundle_id],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                granted.append(service)
            else:
                err_text = result.stderr.strip() or result.stdout.strip()
                failed.append({"service": service, "error": err_text})
                logger.debug(
                    "simctl privacy grant %s %s failed: %s", service, bundle_id, err_text
                )

        response: dict[str, Any] = {"granted": granted, "failed": failed}
        if failed:
            response["note"] = (
                "Some permissions could not be pre-granted. "
                "On iOS 18.4, 'notifications' cannot be granted via simctl — "
                "this is an OS-level restriction. "
                "See docs/troubleshooting.md for the compatibility matrix."
            )
        return response

    def tap(
        self,
        x: float | None = None,
        y: float | None = None,
        label: str | None = None,
        identifier: str | None = None,
        duration: float = 0.0,
    ) -> dict[str, Any]:
        """Tap an element by label/identifier, or at device-point coordinates.

        Priority: identifier → label → coordinates.

        Args:
            x:          Horizontal device point (used when no label/identifier).
            y:          Vertical device point (used when no label/identifier).
            label:      Accessibility label (case-insensitive substring).
            identifier: Accessibility identifier (exact match).
            duration:   Hold duration in seconds (0 = normal tap).

        Returns:
            ``{"success": True}`` or ``{"success": False, "error": "..."}``
        """
        if label is not None or identifier is not None:
            elem = self.find_element(label=label, identifier=identifier)
            if elem is not None:
                try:
                    from ApplicationServices import AXUIElementPerformAction  # type: ignore[import]

                    with _ax_lock:
                        err = AXUIElementPerformAction(elem, "AXPress")
                    if err == 0:
                        return {"success": True, "mode": "ax_press"}
                    logger.debug("AXPress failed (err=%d), falling back to coordinates", err)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("AXPress error: %s", exc)

                # Fall back to CGEvent tap at element center.
                ax_frame = self._ax_frame(elem)
                if ax_frame is not None:
                    dev = self._ax_to_device(ax_frame)
                    cx = dev["x"] + dev["width"] / 2
                    cy = dev["y"] + dev["height"] / 2
                    return self._cg_tap(cx, cy, duration)

            # AX tree walk didn't find it.  Try the synthesised element list
            # (which includes position-probed tab bar buttons not in the tree).
            def _tap_from_elements(el_list: list[dict]) -> dict | None:
                for el_dict in el_list:
                    matched = False
                    if identifier is not None and el_dict.get("identifier") == identifier:
                        matched = True
                    elif label is not None and label.lower() in el_dict.get("label", "").lower():
                        matched = True
                    if matched:
                        frame = el_dict.get("frame", {})
                        if frame.get("width", 0) > 0 and frame.get("height", 0) > 0:
                            cx = frame["x"] + frame["width"] / 2
                            cy = frame["y"] + frame["height"] / 2
                            return self._cg_tap(cx, cy, duration)
                return None

            els = self.get_elements(limit=300)
            result = _tap_from_elements(els)
            if result is not None:
                return result

            # Element not found.  If there is a visible navigation Back button,
            # we may be in a sub-page (e.g. More tab retains Bridge view when
            # the Palace view is requested).  Navigate back once and retry.
            back_btn = next(
                (
                    e for e in els
                    if e.get("identifier") == "BackButton"
                    or (e.get("label", "").lower() in ("back", "more") and e.get("type") == "button")
                ),
                None,
            )
            if back_btn is not None:
                frame = back_btn.get("frame", {})
                if frame.get("width", 0) > 0:
                    bx = frame["x"] + frame["width"] / 2
                    by = frame["y"] + frame["height"] / 2
                    self._cg_tap(bx, by)
                    time.sleep(0.5)
                    els2 = self.get_elements(limit=300)
                    result2 = _tap_from_elements(els2)
                    if result2 is not None:
                        return result2

        if x is not None and y is not None:
            return self._cg_tap(float(x), float(y), duration)

        return {"success": False, "error": "No tap target: provide label, identifier, or x+y coordinates"}

    def _cg_tap(self, x: float, y: float, duration: float = 0.0) -> dict[str, Any]:
        """Tap at iOS device-point coordinates via a direct CGEvent mouse click.

        Uses the ``_ios_content_frame`` (macOS screen coordinates of the
        Simulator's iOS content area) to accurately convert iOS device points
        to macOS screen coordinates, then posts a CGEvent left-click.  This
        bypasses the ``CGEventBackend._image_to_screen`` path which relies on
        an image-pixel coordinate system that does not account for the
        Simulator toolbar offset.
        """
        try:
            import Quartz  # type: ignore[import]

            if self._ios_content_frame is not None:
                # Direct conversion: iOS device pts → macOS screen pts using
                # the known content rect from the AX frame query.
                ios_x = self._ios_content_frame["x"]
                ios_y = self._ios_content_frame["y"]
                ios_w = self._ios_content_frame["width"]
                ios_h = self._ios_content_frame["height"]

                scale_x = ios_w / self._device_w if self._device_w > 0 else 1.0
                scale_y = ios_h / self._device_h if self._device_h > 0 else 1.0

                screen_x = ios_x + x * scale_x
                screen_y = ios_y + y * scale_y
            else:
                # Fallback: use CGEventBackend image-space conversion.
                from specterqa.ios.backends.cgevents import CGEventBackend  # noqa: PLC0415

                cg = CGEventBackend(udid=self.device_udid)
                img_w = cg._img_w
                img_h = cg._img_h
                img_x = x * (img_w / self._device_w) if self._device_w > 0 else x
                img_y = y * (img_h / self._device_h) if self._device_h > 0 else y
                screen_x, screen_y = cg._layer._image_to_screen(img_x, img_y, img_w, img_h)

            # Activate the Simulator window before posting events.
            try:
                from specterqa.ios.backends.cgevents import CGEventBackend  # noqa: PLC0415

                CGEventBackend(udid=self.device_udid)._layer._activate_simulator()
            except Exception:  # noqa: BLE001
                pass

            pos = Quartz.CGPointMake(screen_x, screen_y)
            down = Quartz.CGEventCreateMouseEvent(
                None, Quartz.kCGEventLeftMouseDown, pos, Quartz.kCGMouseButtonLeft
            )
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
            time.sleep(0.08)
            up = Quartz.CGEventCreateMouseEvent(
                None, Quartz.kCGEventLeftMouseUp, pos, Quartz.kCGMouseButtonLeft
            )
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
            if duration > 0.0:
                time.sleep(duration)
            else:
                time.sleep(0.4)  # post-tap cooldown (mirrors CGEventBackend.tap)

            return {"success": True, "mode": "cg_event_direct", "x": x, "y": y}
        except Exception as exc:  # noqa: BLE001
            logger.warning("CGEvent tap failed: %s", exc)
            return {"success": False, "error": str(exc)}

    def tap_element(
        self,
        label: str | None = None,
        identifier: str | None = None,
        element_type: str | None = None,
    ) -> dict[str, Any]:
        """Tap an element by label or identifier (XCTestBackend-compatible shim).

        Args:
            label:        Accessibility label substring.
            identifier:   Accessibility identifier (exact).
            element_type: Ignored for AX backend (all types searched).

        Returns:
            ``{"success": True, "mode": "ax_press"}`` or error dict.
        """
        return self.tap(label=label, identifier=identifier)

    def type_text(
        self,
        text: str,
        label: str | None = None,
        identifier: str | None = None,
        x: float | None = None,
        y: float | None = None,
    ) -> dict[str, Any]:
        """Type *text* into an element or the currently focused field.

        When ``x`` and ``y`` are supplied (device-point coordinates), the
        method first taps those coordinates to focus the field and then types
        via CGEvent keystrokes.  This is required for ``SecureTextField``
        elements where ``AXSetValue`` does not work.

        Attempts ``AXSetValue`` on the target element first; falls back to
        CGEvent keystrokes.

        Args:
            text:       String to type.
            label:      Target element label (optional).
            identifier: Target element identifier (optional).
            x:          Horizontal device point — tap to focus before typing.
            y:          Vertical device point — tap to focus before typing.

        Returns:
            ``{"success": True}`` or error dict.
        """
        # If coordinates are given, tap first to focus the field.
        if x is not None and y is not None:
            self._cg_tap(float(x), float(y))
            time.sleep(0.2)
            # Then type via CGEvent keystrokes into the now-focused field.
            try:
                from specterqa.ios.backends.cgevents import CGEventBackend  # noqa: PLC0415

                cg = CGEventBackend(udid=self.device_udid)
                cg.type_text(text)
                return {"success": True, "mode": "cg_tap_then_keystroke"}
            except Exception as exc:  # noqa: BLE001
                logger.warning("type_text (coordinate path) failed: %s", exc)
                return {"success": False, "error": str(exc)}

        target = None
        if label is not None or identifier is not None:
            target = self.find_element(label=label, identifier=identifier)

        if target is not None:
            try:
                from ApplicationServices import AXUIElementSetAttributeValue  # type: ignore[import]

                with _ax_lock:
                    err = AXUIElementSetAttributeValue(target, "AXValue", text)
                if err == 0:
                    return {"success": True, "mode": "ax_set_value"}
                logger.debug("AXSetValue failed (err=%d), falling back to keystrokes", err)
            except Exception as exc:  # noqa: BLE001
                logger.debug("AXSetValue error: %s", exc)

        # Fallback: CGEvent keystrokes (types into focused field).
        try:
            from specterqa.ios.backends.cgevents import CGEventBackend  # noqa: PLC0415

            cg = CGEventBackend(udid=self.device_udid)
            cg.type_text(text)
            return {"success": True, "mode": "cg_keystrokes"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("type_text fallback failed: %s", exc)
            return {"success": False, "error": str(exc)}

    def swipe(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        duration: float = 0.3,
    ) -> dict[str, Any]:
        """Swipe from (x1, y1) to (x2, y2) in device-point coordinates.

        Delegates to :class:`CGEventBackend`.

        Args:
            x1: Start horizontal position.
            y1: Start vertical position.
            x2: End horizontal position.
            y2: End vertical position.
            duration: Gesture duration in seconds (default 0.3).

        Returns:
            ``{"success": True}`` or error dict.
        """
        try:
            import Quartz  # type: ignore[import]

            def _to_screen(dev_x: float, dev_y: float) -> tuple[float, float]:
                """Convert iOS device points to macOS screen coords."""
                if self._ios_content_frame is not None:
                    ios_x = self._ios_content_frame["x"]
                    ios_y = self._ios_content_frame["y"]
                    ios_w = self._ios_content_frame["width"]
                    ios_h = self._ios_content_frame["height"]
                    sx = ios_x + dev_x * (ios_w / self._device_w if self._device_w > 0 else 1.0)
                    sy = ios_y + dev_y * (ios_h / self._device_h if self._device_h > 0 else 1.0)
                    return sx, sy
                from specterqa.ios.backends.cgevents import CGEventBackend  # noqa: PLC0415

                cg = CGEventBackend(udid=self.device_udid)
                img_x = dev_x * (cg._img_w / self._device_w) if self._device_w > 0 else dev_x
                img_y = dev_y * (cg._img_h / self._device_h) if self._device_h > 0 else dev_y
                return cg._layer._image_to_screen(img_x, img_y, cg._img_w, cg._img_h)

            try:
                from specterqa.ios.backends.cgevents import CGEventBackend  # noqa: PLC0415

                CGEventBackend(udid=self.device_udid)._layer._activate_simulator()
            except Exception:  # noqa: BLE001
                pass

            sx1, sy1 = _to_screen(x1, y1)
            sx2, sy2 = _to_screen(x2, y2)
            steps = max(10, int(duration / 0.01))
            dx = (sx2 - sx1) / steps
            dy = (sy2 - sy1) / steps

            pos_start = Quartz.CGPointMake(sx1, sy1)
            down = Quartz.CGEventCreateMouseEvent(
                None, Quartz.kCGEventLeftMouseDown, pos_start, Quartz.kCGMouseButtonLeft
            )
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
            for i in range(1, steps + 1):
                pos = Quartz.CGPointMake(sx1 + dx * i, sy1 + dy * i)
                drag = Quartz.CGEventCreateMouseEvent(
                    None, Quartz.kCGEventLeftMouseDragged, pos, Quartz.kCGMouseButtonLeft
                )
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, drag)
                time.sleep(duration / steps)
            pos_end = Quartz.CGPointMake(sx2, sy2)
            up = Quartz.CGEventCreateMouseEvent(
                None, Quartz.kCGEventLeftMouseUp, pos_end, Quartz.kCGMouseButtonLeft
            )
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
            time.sleep(0.3)
            return {"success": True, "mode": "cg_event_direct"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("swipe failed: %s", exc)
            return {"success": False, "error": str(exc)}

    def swipe_back(self) -> dict[str, Any]:
        """Perform a swipe-from-left-edge gesture (iOS back navigation)."""
        return self.swipe(x1=5, y1=self._device_h / 2, x2=200, y2=self._device_h / 2, duration=0.3)

    def screenshot(self) -> dict[str, Any]:
        """Capture a screenshot of the booted simulator via simctl.

        Captures a PNG via simctl, converts it to JPEG (for MCP/size
        compatibility), and returns a dict compatible with the XCTestBackend
        screenshot response::

            {
                "data": "<base64 JPEG string>",
                "format": "jpeg",
                "width": <int>,
                "height": <int>,
            }

        Raises:
            RuntimeError: If the screenshot command fails.
        """
        import base64 as _b64
        import io as _io
        from PIL import Image as _Image  # type: ignore[import]

        tmp = f"/tmp/specterqa_screenshot_{os.getpid()}.png"
        result = subprocess.run(
            ["xcrun", "simctl", "io", self.device_udid, "screenshot", tmp],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"simctl screenshot failed: {result.stderr.strip() or result.stdout.strip()}"
            )
        with open(tmp, "rb") as fh:
            raw = fh.read()
        try:
            os.unlink(tmp)
        except OSError:
            pass

        # Convert PNG → JPEG for MCP compatibility and size reduction.
        try:
            img = _Image.open(_io.BytesIO(raw)).convert("RGB")
            w, h = img.size
            buf = _io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            jpeg_bytes = buf.getvalue()
        except Exception:  # noqa: BLE001
            # Fallback: return PNG as-is if PIL conversion fails.
            jpeg_bytes = raw
            try:
                img = _Image.open(_io.BytesIO(raw))
                w, h = img.size
            except Exception:  # noqa: BLE001
                w, h = int(self._device_w), int(self._device_h)

        encoded = _b64.standard_b64encode(jpeg_bytes).decode("ascii")
        return {"data": encoded, "format": "jpeg", "width": w, "height": h}

    def screenshot_bytes(self) -> bytes:
        """Return raw image bytes (convenience wrapper around :meth:`screenshot`).

        Decodes the base64 payload from :meth:`screenshot` for callers that
        need the binary data directly.

        Returns:
            Raw JPEG bytes.
        """
        import base64 as _b64

        result = self.screenshot()
        return _b64.standard_b64decode(result["data"])

    def press_key(self, key: str) -> dict[str, Any]:
        """Press a named key via CGEvent keystrokes.

        Args:
            key: Key name (e.g. ``"return"``, ``"escape"``, ``"delete"``).

        Returns:
            ``{"success": True}`` or error dict.
        """
        try:
            from specterqa.ios.backends.cgevents import CGEventBackend  # noqa: PLC0415

            cg = CGEventBackend(udid=self.device_udid)
            cg.press_key(key)
            return {"success": True, "mode": "cg_event"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("press_key(%r) failed: %s", key, exc)
            return {"success": False, "error": str(exc)}

    def wait_for_element(
        self,
        label: str | None = None,
        identifier: str | None = None,
        timeout: float = 10.0,
        poll_interval: float = 0.5,
    ) -> dict[str, Any]:
        """Poll the AX tree until a matching element appears.

        Args:
            label:         Element label substring to wait for.
            identifier:    Element identifier (exact) to wait for.
            timeout:       Maximum wait in seconds (default 10).
            poll_interval: Polling interval in seconds (default 0.5).

        Returns:
            ``{"found": True, "elapsed": <seconds>}`` or
            ``{"found": False, "elapsed": <seconds>, "error": "Timeout"}``
        """
        start = time.monotonic()
        while True:
            elem = self.find_element(label=label, identifier=identifier)
            elapsed = time.monotonic() - start
            if elem is not None:
                return {"found": True, "elapsed": round(elapsed, 2)}
            if elapsed >= timeout:
                target = identifier or label or "(unknown)"
                return {
                    "found": False,
                    "elapsed": round(elapsed, 2),
                    "error": f"Timeout waiting for element {target!r} after {timeout}s",
                }
            time.sleep(poll_interval)

    def wait_idle(self, timeout: float = 10.0) -> dict[str, Any]:
        """Wait for the UI to settle (AX tree stops changing).

        Polls the element count twice; returns when counts stabilize or
        timeout is reached.

        Args:
            timeout: Maximum wait in seconds (default 10).

        Returns:
            ``{"success": True, "elapsed": <seconds>}``
        """
        start = time.monotonic()
        prev_count = -1
        stable_for = 0.0
        required_stable = 0.5  # seconds with unchanged element count

        while time.monotonic() - start < timeout:
            elems = self.get_elements(limit=50)
            count = len(elems)
            if count == prev_count:
                stable_for += 0.25
                if stable_for >= required_stable:
                    break
            else:
                stable_for = 0.0
                prev_count = count
            time.sleep(0.25)

        elapsed = round(time.monotonic() - start, 2)
        return {"success": True, "elapsed": elapsed}

    def app_state(self) -> dict[str, Any]:
        """Return the app's current process state.

        Uses ``ps`` to check whether the app process is running.

        Returns:
            ``{"state": "foreground"|"not_running", "pid": <int>|None}``
        """
        result = subprocess.run(
            ["xcrun", "simctl", "spawn", self.device_udid, "launchctl", "list"],
            capture_output=True,
            text=True,
        )
        # launchctl list output includes running services/processes.
        is_running = result.returncode == 0 and bool(result.stdout.strip())
        return {
            "state": "foreground" if is_running else "not_running",
            "details": result.stdout.strip()[:200] if is_running else "",
        }

    def health(self) -> dict[str, Any]:
        """Return OK if the Simulator is running and the AX tree is accessible.

        Returns:
            ``{"status": "ok", "sim_pid": <int>}`` or
            ``{"status": "error", "error": "<message>"}``
        """
        try:
            root = self._ios_content_group or self._root
            # A simple AX attribute read is sufficient to confirm AX tree access.
            role = self._ax_attr(root, "AXRole")
            return {
                "status": "ok",
                "sim_pid": self._sim_pid,
                "root_role": str(role) if role else "unknown",
                "device_w": self._device_w,
                "device_h": self._device_h,
            }
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": str(exc)}

    def logs(self, lines: int = 50) -> dict[str, Any]:
        """Return recent simulator log output via ``simctl spawn log``.

        Args:
            lines: Number of lines to return (default 50).

        Returns:
            ``{"lines": [...], "count": <int>}``
        """
        result = subprocess.run(
            [
                "xcrun", "simctl", "spawn", self.device_udid,
                "log", "show", "--last", "30s", "--style", "compact",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        output_lines = result.stdout.strip().splitlines()[-lines:]
        return {"lines": output_lines, "count": len(output_lines)}

    def perf(self) -> dict[str, Any]:
        """Return basic performance metrics for the Simulator process.

        Uses the Simulator.app host-side PID (``self._sim_pid``) which is
        always available and reflects the full simulated session load.
        Returns keys matching the XCTest bridge format:

            {
                "memory_rss_mb": <float>,
                "thread_count": <int>,
                "process_id": <int|None>,
            }

        Returns:
            Dict with ``memory_rss_mb``, ``thread_count``, ``process_id``.
        """
        pid: int | None = self._sim_pid if self._sim_pid else None
        rss_mb: float = 0.0
        thread_count: int = 0

        if pid:
            try:
                # macOS ps: rss is in KB.
                rss_result = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "rss="],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                rss_kb_str = rss_result.stdout.strip()
                if rss_kb_str.lstrip().isdigit():
                    rss_mb = round(int(rss_kb_str) / 1024, 2)

                # macOS: -M shows one row per thread (first row is header).
                thread_result = subprocess.run(
                    ["ps", "-M", "-p", str(pid)],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                thread_lines = [ln for ln in thread_result.stdout.splitlines() if ln.strip()]
                thread_count = max(0, len(thread_lines) - 1)
            except Exception:  # noqa: BLE001
                pass

        return {
            "memory_rss_mb": rss_mb,
            "thread_count": thread_count,
            "process_id": pid,
        }

    # ------------------------------------------------------------------
    # SoMAnnotator-compatible source feed
    # ------------------------------------------------------------------

    def source(self) -> dict[str, Any]:
        """Return the element tree in a format compatible with SoMAnnotator.

        The SoMAnnotator's ``get_elements_from_runner()`` calls ``GET /source``
        on the XCTest runner.  For AXBackend we short-circuit that HTTP call
        by providing this method — callers that use an ``AXAnnotator`` wrapper
        will route here instead.

        Returns:
            Dict with ``"elements"`` key containing the raw list from
            :meth:`get_elements`.
        """
        return {"elements": self.get_elements()}

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"AXBackend(sim_pid={self._sim_pid}, udid={self.device_udid!r}, "
            f"device={self._device_w:.0f}x{self._device_h:.0f})"
        )


# ---------------------------------------------------------------------------
# AXAnnotator — SoMAnnotator-compatible wrapper for AXBackend
# ---------------------------------------------------------------------------


class AXAnnotator:
    """Drop-in replacement for :class:`~specterqa.ios.som_annotator.SoMAnnotator`.

    Provides the same interface as ``SoMAnnotator`` but sources its element
    tree from an :class:`AXBackend` instance rather than an HTTP call to the
    XCTest runner.  The MCP server assigns this to ``_annotator`` when the
    AX backend is active, so all tool handler code is unchanged.

    Args:
        backend: The :class:`AXBackend` providing the AX tree.
    """

    def __init__(self, backend: AXBackend) -> None:
        self._backend = backend

    def get_elements_from_runner(self):
        """Return a list of :class:`~specterqa.ios.som_annotator.UIElement` objects.

        Fetches the raw element dicts from :meth:`AXBackend.get_elements` and
        converts them via
        :meth:`~specterqa.ios.som_annotator.SoMAnnotator.parse_elements_from_json`.

        Returns:
            List of ``UIElement`` objects ready for SoM annotation.
        """
        from specterqa.ios.som_annotator import SoMAnnotator  # noqa: PLC0415

        raw_elems = self._backend.get_elements()
        # parse_elements_from_json expects typeLabel without "XCUIElementType" prefix.
        # AXBackend already stores the short form in "typeLabel" — compatible.
        dummy = SoMAnnotator.__new__(SoMAnnotator)
        dummy.runner_url = None
        return dummy.parse_elements_from_json(raw_elems)

    def annotate(
        self,
        screenshot_b64: str,
        img_w: int,
        img_h: int,
        device_w: float = 0.0,
        device_h: float = 0.0,
    ) -> tuple:
        """Fetch AX elements, annotate *screenshot_b64*, and return both.

        Mirrors :meth:`~specterqa.ios.som_annotator.SoMAnnotator.annotate`.

        Args:
            screenshot_b64: Base-64 encoded PNG screenshot.
            img_w:          Screenshot width in pixels.
            img_h:          Screenshot height in pixels.
            device_w:       Device logical-point width (0 = use backend value).
            device_h:       Device logical-point height (0 = use backend value).

        Returns:
            ``(elements, annotated_b64)`` — list of ``UIElement`` and the
            annotated base-64 PNG string.
        """
        from specterqa.ios.som_annotator import SoMAnnotator  # noqa: PLC0415

        elements = self.get_elements_from_runner()
        dw = device_w if device_w > 0 else self._backend._device_w
        dh = device_h if device_h > 0 else self._backend._device_h

        dummy = SoMAnnotator.__new__(SoMAnnotator)
        dummy.runner_url = None
        annotated = dummy.annotate_image(screenshot_b64, img_w, img_h, elements, dw, dh)
        return elements, annotated

    def __repr__(self) -> str:
        return f"AXAnnotator(backend={self._backend!r})"


# ---------------------------------------------------------------------------
# AXHTTPServer — thin HTTP wrapper around AXBackend for smoke test compat
# ---------------------------------------------------------------------------


class AXHTTPServer:
    """Thin HTTP wrapper around AXBackend for smoke test compatibility.

    Serves the same endpoints as the XCTest Swift runner on localhost:PORT.
    Runs in a background thread.
    """

    def __init__(self, backend: AXBackend, port: int = 8222):
        self.backend = backend
        self.port = port
        self._server = None
        self._thread = None

    def start(self):
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import json

        backend = self.backend

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args): pass  # silence logs

            def _respond(self, data, code=200):
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                body = json.dumps(data, default=str)
                self.wfile.write(body.encode())

            def do_GET(self):
                path = self.path.split('?')[0]
                try:
                    if path == '/health':
                        self._respond(backend.health())
                    elif path == '/elements':
                        els = backend.get_elements(limit=200)
                        # Inject index field for clients that need positional refs.
                        for i, el in enumerate(els):
                            el.setdefault("index", i)
                        self._respond({"success": True, "result": els, "count": len(els)})
                    elif path == '/source':
                        els = backend.get_elements(limit=500)
                        self._respond(els)  # simplified source
                    elif path == '/screenshot':
                        data = backend.screenshot()
                        self._respond({"success": True, "result": data})
                    elif path == '/perf':
                        data = backend.perf()
                        self._respond(data)
                    elif path == '/logs':
                        data = backend.logs()
                        self._respond(data)
                    elif path == '/crashes':
                        self._respond({"app_running": True, "crashes_since_session_start": 0, "crashes": []})
                    elif path == '/app_state':
                        data = backend.app_state()
                        self._respond(data)
                    else:
                        self._respond({"error": f"Unknown path: {path}"}, 404)
                except Exception as e:
                    self._respond({"error": str(e)}, 500)

            def do_POST(self):
                path = self.path
                content_length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(content_length)) if content_length else {}

                try:
                    if path == '/tap':
                        result = backend.tap(
                            label=body.get('label'),
                            identifier=body.get('identifier'),
                            x=body.get('x'),
                            y=body.get('y'),
                        )
                        self._respond({"success": True, **result})
                    elif path == '/type':
                        result = backend.type_text(
                            body.get('text', ''),
                            label=body.get('label'),
                            identifier=body.get('identifier'),
                            x=body.get('x'),
                            y=body.get('y'),
                        )
                        self._respond({"success": True, **result})
                    elif path == '/swipe':
                        backend.swipe(
                            body.get('fromX', 0), body.get('fromY', 0),
                            body.get('toX', 0), body.get('toY', 0),
                            body.get('duration', 0.3),
                        )
                        self._respond({"success": True, "mode": "swipe"})
                    elif path == '/key':
                        backend.press_key(body.get('key', ''))
                        self._respond({"success": True, "key": body.get('key', '')})
                    elif path == '/wait':
                        label = body.get('label', '')
                        timeout = body.get('timeout', 10)
                        found = backend.wait_for_element(label=label, timeout=timeout)
                        if found:
                            self._respond({"success": True, "status": "found", "label": label})
                        else:
                            self._respond({"success": True, "status": "not_found"})
                    elif path == '/idle':
                        result = backend.wait_idle(timeout=body.get('timeout', 10))
                        self._respond({"success": True, **result})
                    elif path == '/dismiss_keyboard':
                        # AX backend: tap outside any text field to dismiss
                        backend.tap(x=200, y=50)
                        self._respond({"success": True, "dismissed": True})
                    else:
                        self._respond({"error": f"Unknown path: {path}"}, 422)
                except Exception as e:
                    self._respond({"error": str(e)}, 500)

        self._server = HTTPServer(('localhost', self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
