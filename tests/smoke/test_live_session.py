"""
Live Simulator Smoke Tests — the real quality gate.

These tests run against a REAL iOS simulator with the TestKitApp installed
and a SpecterQA session active. They catch every class of bug that mock
tests missed: runner crashes, focus transfer, cache staleness, screenshot
size, element-based tap, typing into specific fields.

Run:
    # First: boot sim, install TestKitApp, start session
    pytest tests/smoke/ -v -m live

Bundle ID: io.synctek.specterqa.testkit
"""
import time
import json
import urllib.request
import pytest

BASE = "http://127.0.0.1:8222"


def _post(path, payload=None):
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(f"{BASE}{path}", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _get(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=15) as resp:
        return json.loads(resp.read())


def _elements():
    """Get current element list from runner."""
    data = _get("/elements")
    # Runner returns elements under "result" key (list of dicts)
    return data.get("result") or data.get("elements") or []


def _find(identifier=None, label=None):
    """Find element by identifier or label."""
    for el in _elements():
        if identifier and el.get("identifier") == identifier:
            return el
        if label and el.get("label") == label:
            return el
    return None


def _tap(label=None, identifier=None, x=None, y=None):
    payload = {}
    if label: payload["label"] = label
    if identifier: payload["identifier"] = identifier
    if x is not None: payload["x"] = x
    if y is not None: payload["y"] = y
    return _post("/tap", payload)


def _type(text, label=None, identifier=None):
    payload = {"text": text}
    if label: payload["label"] = label
    if identifier: payload["identifier"] = identifier
    return _post("/type", payload)


# Import the skip marker from conftest
from tests.smoke.conftest import requires_live


@requires_live
class TestSingleFieldTyping:
    """Scenario 1: tap a field, type, verify text appears."""

    def test_tap_and_type_first_name(self):
        _tap(identifier="field_first_name")
        time.sleep(0.5)
        _type("Alice", identifier="field_first_name")
        time.sleep(0.5)
        el = _find(identifier="field_first_name")
        assert el is not None, "field_first_name not found"
        assert "Alice" in str(el.get("value", "")), \
            f"Expected 'Alice' in value, got {el.get('value')!r}"


@requires_live
class TestMultiFieldForm:
    """Scenario 2: THE regression test — multi-field form preserves each field's value."""

    def test_two_fields_typed_independently(self):
        # Type into First Name
        _type("Alice", identifier="field_first_name")
        time.sleep(0.5)

        # Type into Last Name (must NOT overwrite First Name)
        _type("Smith", identifier="field_last_name")
        time.sleep(0.5)

        # Verify BOTH fields
        first = _find(identifier="field_first_name")
        last = _find(identifier="field_last_name")
        assert first is not None, "field_first_name not found"
        assert last is not None, "field_last_name not found"
        assert "Alice" in str(first.get("value", "")), \
            f"First Name overwritten: {first.get('value')!r}"
        assert "Smith" in str(last.get("value", "")), \
            f"Last Name not set: {last.get('value')!r}"


@requires_live
class TestSecureField:
    """Scenario 3: SecureField typing (the Palace password field bug)."""

    def test_secure_field_accepts_text(self):
        _type("s3cret!", identifier="field_password")
        time.sleep(0.5)
        el = _find(identifier="field_password")
        assert el is not None, "field_password not found"
        # SecureField masks value but should NOT show placeholder
        val = str(el.get("value", ""))
        assert val != "" and val != "Password", \
            f"SecureField still shows placeholder: {val!r}"


@requires_live
class TestTabNavigation:
    """Scenario 4: tab switch tests element cache refresh."""

    def test_cache_refreshes_after_tab_switch(self):
        # Dismiss keyboard first — it covers the tab bar
        _tap(x=200, y=55)  # tap nav bar area
        time.sleep(0.5)

        # Navigate to Nav tab
        _tap(label="Nav")
        time.sleep(1.0)
        el = _find(identifier="lbl_nav_title")
        assert el is not None, "Nav tab title not found — cache not refreshed"

        # Navigate back to Form tab
        _tap(label="Form")
        time.sleep(1.0)
        el = _find(identifier="field_first_name")
        assert el is not None, "Form tab field not found after return"


@requires_live
class TestSheetDismiss:
    """Scenario 5: open and dismiss a half-sheet."""

    def test_sheet_lifecycle(self):
        # Dismiss keyboard, then navigate to Nav tab
        _tap(x=200, y=55)
        time.sleep(0.5)
        _tap(label="Nav")
        time.sleep(0.5)

        _tap(identifier="btn_open_sheet")
        time.sleep(0.8)
        el = _find(identifier="lbl_sheet_title")
        assert el is not None, "Sheet didn't open"

        _tap(identifier="btn_close_sheet")
        time.sleep(0.5)
        el = _find(identifier="lbl_sheet_title")
        assert el is None, "Sheet didn't close"

        # Go back to form tab for other tests
        _tap(label="Form")
        time.sleep(0.5)


@requires_live
class TestScreenshotSize:
    """Scenario 6: screenshot must be JPEG and under 1MB."""

    def test_screenshot_is_jpeg_under_1mb(self):
        import base64
        data = _get("/screenshot")
        # Runner nests image under result.data
        result = data.get("result", {}) if isinstance(data.get("result"), dict) else {}
        b64 = result.get("data") or data.get("image") or data.get("data") or data.get("screenshot", "")
        assert b64, "No image data in screenshot response"
        raw = base64.b64decode(b64)
        assert raw[:3] == b'\xff\xd8\xff', f"Not JPEG: {raw[:3].hex()}"
        assert len(raw) < 1_000_000, f"Screenshot too large: {len(raw)} bytes"


@requires_live
class TestElementList:
    """Scenario 7: element list has required structure."""

    def test_elements_have_required_fields(self):
        els = _elements()
        assert len(els) > 0, "Empty element list"
        for el in els[:10]:
            assert "label" in el or "identifier" in el, f"Element missing label/id: {el}"
            assert "type" in el, f"Element missing type: {el}"


@requires_live
class TestObservability:
    """Scenario 8: perf and logs via XCTest bridge."""

    def test_perf_returns_real_data(self):
        data = _get("/perf")
        # Must have real values, not zeros
        rss = data.get("memory_rss_bytes") or data.get("memory_rss_mb") or 0
        assert rss > 0, f"RSS is zero — bridge not working: {data}"

    def test_health_endpoint(self):
        data = _get("/health")
        assert data.get("status") == "ok", f"Health check failed: {data}"


@requires_live
class TestFormSubmitEndToEnd:
    """Scenario 9: full form fill → submit → verify result label."""

    def test_fill_and_submit(self):
        _type("Alice", identifier="field_first_name")
        time.sleep(0.3)
        _type("Smith", identifier="field_last_name")
        time.sleep(0.3)
        _type("mypass", identifier="field_password")
        time.sleep(0.3)

        # Dismiss keyboard before tapping Submit (it may be covered)
        _tap(x=200, y=55)
        time.sleep(0.3)
        _tap(identifier="btn_submit")
        time.sleep(1.0)

        result = _find(identifier="lbl_result")
        assert result is not None, "Result label not found"
        # StaticText uses label for display text, not value
        val = str(result.get("label", "") or result.get("value", ""))
        assert "Alice" in val, f"First name missing from result: {val!r}"
        assert "Smith" in val, f"Last name missing from result: {val!r}"
        assert "set" in val, f"Password not confirmed: {val!r}"
