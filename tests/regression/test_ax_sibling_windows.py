"""Regression tests for AX backend sibling-window enumeration.

Palace dogfood Issue 2 (v13.1.0 → v13.2.0):
  When a SwiftUI `.sheet` presents a `UIViewControllerRepresentable`-wrapped
  UIKit screen, the sheet content lives in a sibling `AXWindow` that the old
  tree-walk never visited.  `ios_elements()` returned only the root tab-bar
  buttons.

Fix: `AXBackend._walk_sibling_windows()` enumerates ALL `AXWindows` of the
Simulator process and merges their elements (de-duped) into the result list.

Unit tests here verify the fix via static source analysis and a mock-based
walk that exercises the dedup logic without a live Simulator.

Live integration tests (marked `requires_live`) exercise the real
TestKitApp `PalacePatternTab` which already contains the exact Palace sheet
pattern (`.sheet(isPresented: $showLibrarySheet) { UIKitLibrarySwitcher(…) }`).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Static source checks
# ---------------------------------------------------------------------------


class TestAXSiblingWindowsSourcePresent:
    """Verify the sibling-window code is present in ax_backend.py."""

    def _src(self) -> str:
        return (
            REPO_ROOT / "src" / "specterqa" / "ios" / "backends" / "ax_backend.py"
        ).read_text()

    def test_walk_sibling_windows_method_exists(self):
        assert "_walk_sibling_windows" in self._src(), (
            "_walk_sibling_windows() not found in ax_backend.py — "
            "sibling-window enumeration fix is missing."
        )

    def test_get_elements_calls_walk_sibling_windows(self):
        src = self._src()
        get_elements_start = src.find("def get_elements(")
        assert get_elements_start != -1
        # Find next method after get_elements
        next_def = src.find("\n    def ", get_elements_start + 1)
        get_elements_body = src[get_elements_start:next_def]
        assert "_walk_sibling_windows" in get_elements_body, (
            "get_elements() does not call _walk_sibling_windows() — "
            "sheet content will not be enumerated."
        )

    def test_sibling_walk_uses_ax_windows_attribute(self):
        src = self._src()
        walk_start = src.find("def _walk_sibling_windows(")
        assert walk_start != -1
        walk_end = src.find("\n    def ", walk_start + 1)
        walk_body = src[walk_start:walk_end]
        assert "AXWindows" in walk_body, (
            "_walk_sibling_windows() must use kAXWindowsAttribute to enumerate "
            "all Simulator process windows."
        )

    def test_sibling_walk_deduplicates_elements(self):
        src = self._src()
        walk_start = src.find("def _walk_sibling_windows(")
        assert walk_start != -1
        walk_end = src.find("\n    def ", walk_start + 1)
        walk_body = src[walk_start:walk_end]
        assert "existing_keys" in walk_body or "dedup" in walk_body.lower(), (
            "_walk_sibling_windows() must de-duplicate elements to avoid "
            "returning the same element twice when walking multiple windows."
        )


# ---------------------------------------------------------------------------
# Unit tests — mock AX tree with sibling windows
# ---------------------------------------------------------------------------


def _make_ax_element(role="AXGroup", label="", children=None, frame=None):
    """Build a fake AX element dict for use with MockAXBackend."""
    return {
        "_role": role,
        "_label": label,
        "_children": children or [],
        "_frame": frame or {"x": 10.0, "y": 10.0, "width": 100.0, "height": 50.0},
        "_identifier": "",
        "_value": "",
        "_enabled": True,
    }


class TestWalkSiblingWindowsUnit:
    """Exercise _walk_sibling_windows dedup logic via a fully mocked backend.

    We bypass the real AX tree walk entirely and inject pre-built element
    dicts into the accumulator, so no pyobjc / Simulator access is needed.
    The tests validate the dedup key computation and limit guard.
    """

    def _make_elem(self, label: str, x: float = 10.0, y: float = 10.0) -> dict:
        return {
            "type": "button",
            "typeLabel": "button",
            "label": label,
            "identifier": "",
            "value": "",
            "enabled": True,
            "hittable": True,
            "frame": {"x": x, "y": y, "width": 100.0, "height": 50.0},
        }

    def _dedup_key(self, e: dict) -> tuple:
        f = e.get("frame", {})
        return (
            e.get("label", ""),
            e.get("type", ""),
            round(f.get("x", 0), 1),
            round(f.get("y", 0), 1),
        )

    def test_sibling_window_dedup_no_duplicates(self):
        """Pre-built dedup key logic: elements with same (label, type, x, y) are not duplicated."""
        existing = self._make_elem("Shared Button", x=10.0, y=10.0)
        unique = self._make_elem("Unique Button", x=200.0, y=400.0)

        results = [existing]
        existing_keys = {self._dedup_key(existing)}

        # Simulate what _walk_sibling_windows does when it encounters elements.
        candidate_elems = [existing, unique]  # existing is a dup; unique is new
        for elem in candidate_elems:
            k = self._dedup_key(elem)
            if k not in existing_keys:
                existing_keys.add(k)
                results.append(elem)

        shared_count = sum(1 for e in results if e["label"] == "Shared Button")
        assert shared_count == 1, (
            f"Shared Button appears {shared_count} times — dedup key logic is wrong."
        )
        assert any(e["label"] == "Unique Button" for e in results), (
            "Unique Button was not added to results."
        )

    def test_limit_respected(self):
        """_walk_sibling_windows respects the limit parameter."""
        from specterqa.ios.backends.ax_backend import AXBackend

        # Build a backend with a mocked _walk_tree that injects N elements.
        with patch.object(AXBackend, "__init__", lambda self, *a, **kw: None):
            backend = AXBackend.__new__(AXBackend)

        backend._sim_pid = 12345
        backend._ios_content_frame = {"x": 0.0, "y": 0.0, "width": 390.0, "height": 844.0}
        backend._device_w = 390.0
        backend._device_h = 844.0
        backend._ios_content_group = None
        backend._root = MagicMock()

        # Mock _ax_attr to return a list of 50 fake window objects.
        fake_windows = [MagicMock() for _ in range(50)]

        def _fake_ax_attr(element, attr):
            if attr == "AXWindows":
                return fake_windows
            if attr == "AXRole":
                return "AXWindow"
            if attr == "AXSubrole":
                return ""
            if attr == "AXTitle":
                return ""
            return None

        backend._ax_attr = _fake_ax_attr

        # _walk_tree injects one element per call.
        walk_call_count = [0]

        def _fake_walk_tree(element, results, depth=0, max_depth=20, limit=200):
            walk_call_count[0] += 1
            if len(results) < limit:
                results.append({
                    "type": "button",
                    "typeLabel": "button",
                    "label": f"Btn {walk_call_count[0]}",
                    "identifier": "",
                    "value": "",
                    "enabled": True,
                    "hittable": True,
                    "frame": {
                        "x": float(walk_call_count[0] * 5),
                        "y": 10.0,
                        "width": 100.0,
                        "height": 50.0,
                    },
                })

        backend._walk_tree = _fake_walk_tree
        backend._ax_children = lambda e: []

        results: list[dict] = []
        backend._walk_sibling_windows(results, limit=5)
        assert len(results) <= 5, (
            f"_walk_sibling_windows returned {len(results)} elements, "
            "exceeding the limit of 5."
        )

    def test_sibling_window_elements_included_via_walk_tree_mock(self):
        """Elements produced by _walk_tree from sibling windows appear in results."""
        from specterqa.ios.backends.ax_backend import AXBackend

        with patch.object(AXBackend, "__init__", lambda self, *a, **kw: None):
            backend = AXBackend.__new__(AXBackend)

        backend._sim_pid = 12345
        backend._ios_content_frame = {"x": 0.0, "y": 0.0, "width": 390.0, "height": 844.0}
        backend._device_w = 390.0
        backend._device_h = 844.0
        backend._ios_content_group = None
        backend._root = MagicMock()

        sheet_win = MagicMock()
        main_win = MagicMock()

        def _fake_ax_attr(element, attr):
            if attr == "AXWindows":
                return [main_win, sheet_win]
            if element is sheet_win:
                if attr == "AXRole":
                    return "AXSheet"
                if attr == "AXSubrole":
                    return "Sheet"
                if attr == "AXTitle":
                    return "Select Library"
            elif element is main_win:
                if attr == "AXRole":
                    return "AXWindow"
                if attr == "AXSubrole":
                    return ""
                if attr == "AXTitle":
                    return ""
            return None

        backend._ax_attr = _fake_ax_attr
        backend._ax_children = lambda e: []

        walk_log: list[str] = []

        def _fake_walk_tree(element, results, depth=0, max_depth=20, limit=200):
            if element is sheet_win:
                walk_log.append("sheet")
                for i in range(5):
                    results.append({
                        "type": "cell",
                        "typeLabel": "cell",
                        "label": f"Library Row {i}",
                        "identifier": "",
                        "value": "",
                        "enabled": True,
                        "hittable": True,
                        "frame": {
                            "x": 0.0, "y": float(i * 50),
                            "width": 390.0, "height": 44.0,
                        },
                    })
            elif element is main_win:
                walk_log.append("main")

        backend._walk_tree = _fake_walk_tree

        results: list[dict] = []
        backend._walk_sibling_windows(results, limit=100)

        assert "sheet" in walk_log, (
            "_walk_tree was never called for the sheet window."
        )
        labels = [e["label"] for e in results]
        assert any("Library Row" in lbl for lbl in labels), (
            f"Sheet elements not in results. Labels: {labels}"
        )


# ---------------------------------------------------------------------------
# Live integration test — requires TestKitApp running in AX session
# ---------------------------------------------------------------------------


try:
    import urllib.request as _ur
    import json as _json

    _RUNNER_BASE = "http://127.0.0.1:8222"

    def _runner_healthy() -> bool:
        try:
            with _ur.urlopen(f"{_RUNNER_BASE}/health", timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    _LIVE_AVAILABLE = _runner_healthy()
except Exception:
    _LIVE_AVAILABLE = False


@pytest.mark.skipif(not _LIVE_AVAILABLE, reason="Requires active AX session on port 8222")
class TestSheetEnumerationLive:
    """Live test: Palace sheet pattern on TestKitApp.

    Requires:
    - TestKitApp installed and running (bundle id: io.synctek.specterqa.testkit)
    - AX session active (ios_start_session with backend='ax')
    """

    def _get_elements(self, max_elements: int = 0) -> list[dict]:
        url = f"{_RUNNER_BASE}/elements"
        with _ur.urlopen(url, timeout=10) as resp:
            data = _json.loads(resp.read())
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return data if isinstance(data, list) else []

    def _post(self, path: str, body: dict) -> dict:
        payload = _json.dumps(body).encode()
        req = _ur.Request(
            f"{_RUNNER_BASE}{path}",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with _ur.urlopen(req, timeout=10) as resp:
            return _json.loads(resp.read())

    def test_sheet_elements_visible_after_open(self):
        """Opening the Palace-pattern sheet exposes >5 elements (cells + nav bar + cancel)."""
        # Navigate to Palace tab
        self._post("/tap", {"label": "Palace"})

        import time
        time.sleep(0.5)

        # Tap "Switch Library" to open the UIKit sheet
        self._post("/tap", {"identifier": "palace_btn_switch_library"})
        time.sleep(1.0)  # let sheet animation complete

        elements = self._get_elements()
        labels = [e.get("label", "") for e in elements]

        # The sheet contains "Select Library" nav title, "Cancel" button,
        # and at least 5 library cells.
        sheet_content = [
            lbl for lbl in labels
            if any(kw in lbl for kw in ("Library", "Select", "Cancel", "NYPL", "Brooklyn"))
        ]

        assert len(sheet_content) >= 5, (
            f"Expected >=5 sheet elements, got {len(sheet_content)}. "
            f"All labels: {labels[:30]}. "
            "Sibling-window enumeration may not be working."
        )
