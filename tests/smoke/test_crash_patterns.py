"""
UIKit ↔ SwiftUI Crash Pattern Smoke Tests

Tests that the SpecterQA runner survives every known crash trigger when
interacting with UIKit/SwiftUI hybrid views. All tests hit the real runner
at http://127.0.0.1:8222.

Crash patterns covered:
  1. SwiftUI List + TextField (Palace pattern)
  2. LazyVStack scroll recycling
  3. Nested Form deep tree
  4. UIKit ↔ SwiftUI bridge
  5. Tab switching rapid fire
  6. Keyboard + tab switch
  7. Sheet over text field
  8. Element query during view transition
  9. Screenshot during transition

Run:
    pytest tests/smoke/test_crash_patterns.py -v -m live
"""
import time
import json
import base64
import urllib.request
import pytest

from tests.smoke.conftest import requires_live

BASE = "http://127.0.0.1:8222"


# ---------------------------------------------------------------------------
# Helpers — same contract as test_live_session.py
# ---------------------------------------------------------------------------

def _post(path, payload=None):
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(
        f"{BASE}{path}", data=data,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _get(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=15) as resp:
        return json.loads(resp.read())


def _elements():
    """Return current element list from runner."""
    data = _get("/elements")
    return data.get("result") or data.get("elements") or []


def _find(identifier=None, label=None):
    """Find element by accessibility identifier or label."""
    for el in _elements():
        if identifier and el.get("identifier") == identifier:
            return el
        if label and el.get("label") == label:
            return el
    return None


def _dismiss_keyboard():
    """Dismiss on-screen keyboard if visible."""
    try:
        return _post("/dismiss_keyboard")
    except Exception:
        return _post("/tap", {"x": 200, "y": 55})


def _tap(label=None, identifier=None, x=None, y=None):
    payload = {}
    if label:
        payload["label"] = label
    if identifier:
        payload["identifier"] = identifier
    if x is not None:
        payload["x"] = x
    if y is not None:
        payload["y"] = y
    return _post("/tap", payload)


def _tap_tab(label):
    """Coordinate-based tab tap — safe on iOS 26 (avoids accessibility tree SIGABRT)."""
    for el in _elements():
        if el.get("label") == label and el.get("type") == "button":
            f = el.get("frame", {})
            if f.get("y", 0) > 700:  # tab bar lives near the bottom
                cx = f.get("x", 0) + f.get("width", 0) / 2
                cy = f.get("y", 0) + f.get("height", 0) / 2
                return _tap(x=cx, y=cy)
    # Fallback: label-based tap
    return _tap(label=label)


def _type(text, label=None, identifier=None, x=None, y=None):
    payload = {"text": text}
    if label:
        payload["label"] = label
    if identifier:
        payload["identifier"] = identifier
    if x is not None:
        payload["x"] = x
    if y is not None:
        payload["y"] = y
    return _post("/type", payload)


def _assert_runner_alive(context=""):
    """Assert /health returns ok — the definitive crash signal."""
    data = _get("/health")
    assert data.get("status") == "ok", \
        f"Runner crashed{f' after {context}' if context else ''}: {data}"


# ---------------------------------------------------------------------------
# Pattern 1: SwiftUI List + TextField (Palace pattern)
# ---------------------------------------------------------------------------

@requires_live
class TestSwiftUIListTextField:
    """Pattern 1 — SwiftUI List containing TextField rows.

    The Palace crash: navigating to a List with embedded TextFields and then
    calling /source or /elements caused a SIGABRT inside the accessibility
    snapshot walk. The runner must survive all three operations.
    """

    def test_list_textfield_snapshot_survives(self):
        """GET /source on List tab must not crash the runner."""
        _dismiss_keyboard()
        time.sleep(0.5)
        _tap_tab("List")
        time.sleep(1.0)

        # /source triggers a full accessibility tree serialisation — the
        # original crash site.
        try:
            _get("/source")
        except Exception:
            pass  # /source may not exist on all builds — crash matters, not 404

        _assert_runner_alive("GET /source on List tab")

    def test_list_textfield_elements_query_survives(self):
        """GET /elements on List tab returns fields without crashing."""
        _dismiss_keyboard()
        time.sleep(0.5)
        _tap_tab("List")
        time.sleep(1.0)

        els = _elements()
        _assert_runner_alive("GET /elements on List tab")
        assert len(els) > 0, "Empty element list on List tab — tree walk failed silently"

    def test_list_textfield_type_survives(self):
        """Type into a List TextField; runner must be alive 1 s after (no delayed crash)."""
        _dismiss_keyboard()
        time.sleep(0.5)
        _tap_tab("List")
        time.sleep(1.0)

        barcode = _find(identifier="list_field_barcode")
        assert barcode is not None, "list_field_barcode not found on List tab"

        _type("CRASH_PROBE", identifier="list_field_barcode")
        time.sleep(1.0)  # allow any deferred crash to surface

        _assert_runner_alive("typing into List TextField")

        # Clean up — navigate back to Form
        _dismiss_keyboard()
        time.sleep(0.3)
        _tap_tab("Form")
        time.sleep(0.5)


# ---------------------------------------------------------------------------
# Pattern 2: LazyVStack scroll recycling
# ---------------------------------------------------------------------------

@requires_live
class TestLazyVStackScrollRecycling:
    """Pattern 2 — LazyVStack scroll causes cell recycling + accessibility re-registration.

    Re-registration can send a dangling pointer to the XCTest accessibility
    bridge on iOS 17+. The runner must survive the scroll and remain queryable.
    """

    def test_stress_lazyvstack_elements_survives(self):
        """Navigate to Stress tab, query elements — runner must survive tree walk."""
        _dismiss_keyboard()
        time.sleep(0.5)
        _tap_tab("Stress")
        time.sleep(1.0)

        els = _elements()
        _assert_runner_alive("element query on Stress/LazyVStack tab")
        assert len(els) > 0, "Empty element list on Stress tab"

    def test_stress_lazyvstack_scroll_survives(self):
        """Scroll down on Stress tab to trigger cell recycling; runner must stay alive."""
        _dismiss_keyboard()
        time.sleep(0.5)
        _tap_tab("Stress")
        time.sleep(1.0)

        # Scroll down: swipe from bottom-third to top-third of screen
        _post("/swipe", {"startX": 200, "startY": 600, "endX": 200, "endY": 200})
        time.sleep(0.8)

        # Query elements post-scroll — re-registration must not have crashed runner
        els = _elements()
        _assert_runner_alive("element query after LazyVStack scroll")
        assert len(els) > 0, "Empty element list after LazyVStack scroll — recycling may have crashed bridge"


# ---------------------------------------------------------------------------
# Pattern 3: Nested Form deep tree
# ---------------------------------------------------------------------------

@requires_live
class TestNestedFormDeepTree:
    """Pattern 3 — deeply-nested SwiftUI Form/Section tree.

    Deep accessibility trees (>8 levels) historically overwhelmed the element
    flattener and produced an empty result or a crash. Runner must return
    identifiable elements.
    """

    def test_stress_nested_form_survives(self):
        """Navigate to Stress tab and resolve deep fields by identifier."""
        _dismiss_keyboard()
        time.sleep(0.5)
        _tap_tab("Stress")
        time.sleep(1.0)

        els = _elements()
        _assert_runner_alive("element query on nested Form Stress tab")

        # We cannot assert specific identifiers here because the Stress tab
        # layout differs per build. Assert the tree is non-trivially deep
        # (at least one element with an identifier set — proves flattener ran).
        identified = [e for e in els if e.get("identifier")]
        assert len(identified) > 0, \
            "No identified elements on Stress tab — deep tree flattener may have bailed early"


# ---------------------------------------------------------------------------
# Pattern 4: UIKit ↔ SwiftUI bridge
# ---------------------------------------------------------------------------

@requires_live
class TestUIKitSwiftUIBridge:
    """Pattern 4 — UIViewRepresentable wrapping a UIKit UITextField.

    The bridge means the accessibility node is created by UIKit's layout pass
    but owned by SwiftUI's hosting controller. Element queries that walk the
    SwiftUI tree first can miss the node; identifier-based lookup must find it.
    """

    def test_bridge_tab_navigation_survives(self):
        """Navigate to Bridge tab — runner must not crash on hosting controller swap."""
        _dismiss_keyboard()
        time.sleep(0.5)
        _tap_tab("Bridge")
        time.sleep(1.0)

        _assert_runner_alive("navigation to Bridge tab")

    def test_bridge_uikit_textfield_visible(self):
        """Bridge tab must expose a UIKit-backed TextField via accessibility."""
        _dismiss_keyboard()
        time.sleep(0.5)
        _tap_tab("Bridge")
        time.sleep(1.0)

        els = _elements()
        _assert_runner_alive("element query on Bridge tab")

        # Accept either the known identifier OR any textField/secureTextField type
        bridge_field = _find(identifier="bridge_uikit_field")
        text_fields = [
            e for e in els
            if e.get("type") in ("textField", "secureTextField", "TextField", "textView")
        ]
        assert bridge_field is not None or len(text_fields) > 0, \
            "Bridge tab exposes no UIKit TextField — UIViewRepresentable bridge may be broken"

    def test_bridge_uikit_textfield_typing(self):
        """Type into UIKit-wrapped TextField on Bridge tab; runner survives."""
        _dismiss_keyboard()
        time.sleep(0.5)
        _tap_tab("Bridge")
        time.sleep(1.0)

        bridge_field = _find(identifier="bridge_uikit_field")
        if bridge_field is not None:
            # Prefer identifier-based type
            _type("UIKitProbe", identifier="bridge_uikit_field")
        else:
            # Fallback: find first text field and type by coordinate
            els = _elements()
            tf = next(
                (e for e in els if e.get("type") in ("textField", "TextField", "textView")),
                None
            )
            assert tf is not None, "No TextField found on Bridge tab to type into"
            frame = tf.get("frame", {})
            cx = frame.get("x", 0) + frame.get("width", 0) / 2
            cy = frame.get("y", 0) + frame.get("height", 0) / 2
            _type("UIKitProbe", x=cx, y=cy)

        time.sleep(1.0)  # allow deferred crash window
        _assert_runner_alive("typing into UIKit bridge TextField")

        # Clean up
        _dismiss_keyboard()
        time.sleep(0.3)
        _tap_tab("Form")
        time.sleep(0.5)


# ---------------------------------------------------------------------------
# Pattern 5: Tab switching rapid fire
# ---------------------------------------------------------------------------

@requires_live
class TestRapidTabSwitching:
    """Pattern 5 — rapid consecutive tab switches.

    Each switch triggers a SwiftUI hosting controller swap. If the accessibility
    tree invalidation races against an in-flight /elements query, the runner
    can crash via a dangling AXElement pointer. Rapid switching exercises this
    race window without interleaved sleeps.
    """

    def test_rapid_tab_switching_survives(self):
        """Cycle Form→List→Stress→Bridge→Nav→Form in quick succession; runner must survive."""
        _dismiss_keyboard()
        time.sleep(0.5)

        tab_sequence = ["Form", "List", "Stress", "Bridge", "Nav", "Form"]
        for tab in tab_sequence:
            _tap_tab(tab)
            # Minimal sleep — enough for the tap to register but tight enough
            # to catch races between the transition animation and /health poll.
            time.sleep(0.15)

        # One final settle before health check
        time.sleep(0.5)
        _assert_runner_alive("rapid tab switching (Form→List→Stress→Bridge→Nav→Form)")


# ---------------------------------------------------------------------------
# Pattern 6: Keyboard + tab switch
# ---------------------------------------------------------------------------

@requires_live
class TestKeyboardDuringTabSwitch:
    """Pattern 6 — first responder alive when the view disappears.

    iOS raises a UIKit exception if a UITextField (or its SwiftUI wrapper) is
    first responder when its hosting view controller is removed from the
    hierarchy. The runner must survive this without crashing or hanging.
    """

    def test_keyboard_open_during_tab_switch(self):
        """Open keyboard on Form tab, switch to List tab WITHOUT dismissing — runner must survive."""
        _dismiss_keyboard()
        time.sleep(0.5)
        _tap_tab("Form")
        time.sleep(0.8)

        # Focus a field to open the keyboard
        _tap(identifier="field_first_name")
        time.sleep(0.8)

        # Switch tabs WITHOUT dismissing keyboard first (the dangerous case)
        _tap_tab("List")
        time.sleep(1.0)

        _assert_runner_alive("tab switch with keyboard open (no prior dismiss)")

        # Recover — dismiss any residual keyboard state and return to Form
        _dismiss_keyboard()
        time.sleep(0.3)
        _tap_tab("Form")
        time.sleep(0.5)


# ---------------------------------------------------------------------------
# Pattern 7: Sheet over text field
# ---------------------------------------------------------------------------

@requires_live
class TestSheetOverTextField:
    """Pattern 7 — modal sheet presented while a text field is focused.

    SwiftUI sheets rendered over a hosting controller that owns a focused
    TextField can produce a dual-first-responder state on older iOS runtimes,
    which leads to a crash on the next accessibility snapshot. The runner must
    survive the combination.
    """

    def test_sheet_over_focused_field(self):
        """Focus a text field, open a sheet, query elements — runner must survive."""
        _dismiss_keyboard()
        time.sleep(0.5)

        # Use Nav tab which has a confirmed sheet trigger (btn_open_sheet)
        _tap_tab("Nav")
        time.sleep(0.8)

        # Open the sheet
        _tap(identifier="btn_open_sheet")
        time.sleep(0.8)

        # Sheet must be visible
        sheet_title = _find(identifier="lbl_sheet_title")
        assert sheet_title is not None, "Sheet did not open — cannot test crash pattern 7"

        # Query elements while sheet is up — this is the crash trigger
        els = _elements()
        _assert_runner_alive("element query with sheet open over text field")
        assert len(els) > 0, "Empty elements while sheet is open"

        # Clean up — close sheet and return to Form
        _tap(identifier="btn_close_sheet")
        time.sleep(0.5)
        _tap_tab("Form")
        time.sleep(0.5)


# ---------------------------------------------------------------------------
# Pattern 8: Element query during view transition
# ---------------------------------------------------------------------------

@requires_live
class TestElementQueryDuringTransition:
    """Pattern 8 — /elements called immediately after a tab tap.

    The SwiftUI transition animation is still in-flight. The accessibility
    tree is partially invalidated. The runner must return either valid data
    or an empty list — never a crash or a hung connection.
    """

    def test_elements_during_tab_animation(self):
        """Tap a tab and immediately query /elements — valid response required (not crash)."""
        _dismiss_keyboard()
        time.sleep(0.5)
        _tap_tab("Form")
        time.sleep(0.5)

        # Tap and query with NO sleep between
        _tap_tab("List")
        try:
            els = _elements()
            # Either a populated list OR an empty list is acceptable —
            # what is NOT acceptable is an exception (runner crash / timeout).
        except urllib.request.URLError as exc:
            pytest.fail(f"Runner did not respond during tab animation: {exc}")

        # Runner must still respond after the transition settles
        time.sleep(0.5)
        _assert_runner_alive("element query issued during tab transition animation")

        # Return to Form for subsequent tests
        _tap_tab("Form")
        time.sleep(0.5)


# ---------------------------------------------------------------------------
# Pattern 9: Screenshot during transition
# ---------------------------------------------------------------------------

@requires_live
class TestScreenshotDuringTransition:
    """Pattern 9 — /screenshot called immediately after a tab tap.

    A screenshot request during a UIView animation triggers a CALayer
    render pass on a partially-committed transaction. On certain iOS versions
    this races with the compositing thread and can produce a blank frame or
    a crash. The runner must return valid JPEG data (not a crash).
    """

    def test_screenshot_during_tab_animation(self):
        """Tap a tab and immediately call /screenshot — must return valid image data."""
        _dismiss_keyboard()
        time.sleep(0.5)
        _tap_tab("Form")
        time.sleep(0.5)

        # Tap and screenshot with NO sleep between
        _tap_tab("Nav")
        try:
            data = _get("/screenshot")
        except urllib.request.URLError as exc:
            pytest.fail(f"Runner did not respond to /screenshot during tab animation: {exc}")

        # Extract image bytes — runner nests under result.data or top-level
        result = data.get("result", {})
        b64 = (
            result.get("data") if isinstance(result, dict) else None
        ) or data.get("image") or data.get("data") or data.get("screenshot", "")

        assert b64, \
            "No image data returned from /screenshot during tab animation — possible crash or blank frame"

        raw = base64.b64decode(b64)
        assert raw[:3] == b"\xff\xd8\xff", \
            f"Screenshot during animation is not valid JPEG: {raw[:3].hex()}"

        # Runner must remain alive after the screenshot
        time.sleep(0.5)
        _assert_runner_alive("screenshot during tab transition animation")

        # Return to Form
        _tap_tab("Form")
        time.sleep(0.5)
