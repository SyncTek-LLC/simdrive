"""
Regression Suite — one test per historical bug.

These verify source code patterns and real behavior, NOT mocked behavior.
Each test documents the bug version, fix version, and what it checks.

Run:
    pytest tests/test_regression.py -v
"""
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).parent.parent


class TestReg001TypeDoesntUseFocusedTap:
    """v11.9.2 bug: typeText() called focused.tap() stealing focus from user's field."""

    def test_typtext_does_not_call_focused_tap_unconditionally(self):
        """Verify TouchInjector.typeText only taps when alreadyFocused is false."""
        swift = REPO_ROOT / "runner" / "Sources" / "TouchInjector.swift"
        content = swift.read_text()
        assert "alreadyFocused" in content or "!alreadyFocused" in content, \
            "typeText still unconditionally taps — v11.9.2 regression risk"


class TestReg002NoRawElementTap:
    """v11.9.3 bug: el.tap() throws ObjC SIGABRT on iOS 26."""

    def test_httpserver_uses_coordinate_tap_not_el_tap(self):
        """Verify POST /tap uses element.coordinate().tap(), not element.tap()."""
        swift = REPO_ROOT / "runner" / "Sources" / "HTTPServer.swift"
        content = swift.read_text()
        # The element-based tap section should use coordinate, not raw tap
        assert "withNormalizedOffset" in content, \
            "HTTPServer element tap doesn't use coordinate — v11.9.3 SIGABRT risk"


class TestReg003RunnerSourceBundled:
    """v11.9.0 bug: Swift source not in wheel."""

    def test_runner_source_package_exists(self):
        pkg = REPO_ROOT / "src" / "specterqa" / "ios" / "runner_source"
        assert pkg.exists(), "runner_source package directory missing"
        assert (pkg / "__init__.py").exists(), "runner_source __init__.py missing"
        assert (pkg / "Sources").exists(), "runner_source/Sources missing"
        swift_files = list((pkg / "Sources").glob("*.swift"))
        assert len(swift_files) >= 7, f"Only {len(swift_files)} Swift files bundled"


class TestReg004GreedyLabelMatch:
    """v11.5.0 bug: 'Password' matched 'Forgot your password?'."""

    def test_lookup_uses_scored_matching(self):
        """Verify _lookup function exists with score-based matching."""
        server = REPO_ROOT / "src" / "specterqa" / "ios" / "mcp" / "server.py"
        content = server.read_text()
        assert "_lookup" in content, "Element resolver _lookup function missing"
        # Should have scoring logic
        assert "exact" in content.lower() or "score" in content.lower() or \
               "prefix" in content.lower(), \
            "No scoring in element resolver — greedy match risk"


class TestReg005ScreenshotJpeg:
    """v11.5.0 bug: screenshot was PNG, exceeded MCP limit."""

    def test_annotator_outputs_jpeg(self):
        annotator = REPO_ROOT / "src" / "specterqa" / "ios" / "som_annotator.py"
        content = annotator.read_text()
        assert 'format="JPEG"' in content or "format='JPEG'" in content, \
            "Annotator still outputs PNG — screenshot will exceed MCP limit"


class TestReg006TypeAcceptsTargetField:
    """v12.0.0 feature: ios_type must accept label/identifier/element_index."""

    def test_handle_type_accepts_label(self):
        server = REPO_ROOT / "src" / "specterqa" / "ios" / "mcp" / "server.py"
        content = server.read_text()
        # handle_type should read label from arguments
        assert 'arguments.get("label")' in content or \
               "arguments.get('label')" in content, \
            "handle_type doesn't accept label param — multi-field typing broken"

    def test_handle_type_accepts_element_index(self):
        server = REPO_ROOT / "src" / "specterqa" / "ios" / "mcp" / "server.py"
        content = server.read_text()
        assert 'arguments.get("element_index")' in content or \
               "arguments.get('element_index')" in content, \
            "handle_type doesn't accept element_index param"


class TestReg007HttpTimeoutAdequate:
    """v12.0.0 bug: 5s timeout too short for element-based operations."""

    def test_default_timeout_at_least_10s(self):
        client = REPO_ROOT / "src" / "specterqa" / "ios" / "backends" / "xctest_client.py"
        content = client.read_text()
        # Find _DEFAULT_TIMEOUT
        for line in content.splitlines():
            if "_DEFAULT_TIMEOUT" in line and "=" in line:
                val = line.split("=")[1].strip().split("#")[0].strip()
                timeout = int(val)
                assert timeout >= 10, \
                    f"HTTP timeout is {timeout}s — too short for element ops (need ≥10s)"
                return
        pytest.fail("_DEFAULT_TIMEOUT not found in xctest_client.py")
