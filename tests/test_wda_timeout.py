"""Tests for XCTest runner network calls — timeout and retry behaviour.

Originally tested the removed WDA fallback path. Updated for v11.2.1 to
cover the current XCTest runner interface (INIT-2026-509 cleanup).

Verifies:
- _fetch_runner_source_raw enforces a 15-second per-attempt timeout
- _get_element_tree_from_runner raises RuntimeError on network failure
- _get_element_tree_from_runner returns XML from JSON response
- SoMAnnotator raises RuntimeError when runner_url is not configured
- SoMRunner no longer exposes legacy WDA parameters (already verified in
  test_wda_removal.py — included here for cross-file discoverability)
"""

from __future__ import annotations

import inspect
import socket
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from specterqa.ios.som_annotator import SoMAnnotator
from specterqa.ios.som_runner import SoMRunner


# ---------------------------------------------------------------------------
# _fetch_runner_source_raw — timeout behaviour
# ---------------------------------------------------------------------------


class TestXCTestRunnerTimeout:
    """Unit tests for SoMAnnotator._fetch_runner_source_raw / _get_element_tree_from_runner."""

    def _make_annotator(self) -> SoMAnnotator:
        return SoMAnnotator(runner_url="http://localhost:8222")

    def test_runner_timeout_15_seconds(self):
        """urlopen is called with timeout=15 when fetching /source."""
        annotator = self._make_annotator()

        captured_timeouts: list[int] = []

        def fake_urlopen(url, timeout=None):
            captured_timeouts.append(timeout)
            mock_resp = MagicMock()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = b'[{"label": "Button", "x": 10, "y": 20, "width": 100, "height": 44}]'
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            annotator._fetch_runner_source_raw()

        assert captured_timeouts, "urlopen was never called"
        assert all(t == 15 for t in captured_timeouts), f"Expected timeout=15, got {captured_timeouts}"

    def test_runner_raises_on_urlerror(self):
        """RuntimeError is raised when /source returns a URLError."""
        annotator = self._make_annotator()

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
            with pytest.raises(RuntimeError, match="XCTest runner /source request failed"):
                annotator._fetch_runner_source_raw()

    def test_runner_raises_on_socket_timeout(self):
        """RuntimeError is raised when /source times out."""
        annotator = self._make_annotator()

        with patch("urllib.request.urlopen", side_effect=socket.timeout("timed out")):
            with pytest.raises(RuntimeError, match="XCTest runner /source request failed"):
                annotator._fetch_runner_source_raw()

    def test_runner_raises_without_runner_url(self):
        """RuntimeError raised immediately when runner_url is not set."""
        annotator = SoMAnnotator()
        with pytest.raises(RuntimeError, match="runner"):
            annotator.get_element_tree()

    def test_runner_returns_xml_from_json_list(self):
        """JSON list response from runner is converted to XML string."""
        annotator = self._make_annotator()
        json_payload = b'[{"label": "OK", "x": 50, "y": 100, "width": 80, "height": 44}]'

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = json_payload

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = annotator.get_element_tree()

        assert result, "Expected non-empty XML from runner JSON"

    def test_runner_returns_xml_field_directly(self):
        """JSON dict response with 'xml' key is returned as-is."""
        annotator = self._make_annotator()
        xml_content = "<AppElement><XCUIElementTypeButton label='OK'/></AppElement>"
        json_payload = f'{{"xml": "{xml_content}"}}'.encode()

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = json_payload

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = annotator.get_element_tree()

        assert result == xml_content

    def test_runner_url_is_constructed_correctly(self):
        """URL sent to urlopen includes /source path at the configured runner_url."""
        annotator = SoMAnnotator(runner_url="http://localhost:8222")
        captured_urls: list[str] = []

        def fake_urlopen(url, timeout=None):
            captured_urls.append(url)
            mock_resp = MagicMock()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = b'[{"label": "X", "x": 0, "y": 0, "width": 10, "height": 10}]'
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            annotator._fetch_runner_source_raw()

        assert captured_urls, "urlopen was never called"
        url = captured_urls[0]
        assert "localhost:8222" in url
        assert "/source" in url


# ---------------------------------------------------------------------------
# SoMRunner — legacy WDA params must be absent (cross-file guard)
# ---------------------------------------------------------------------------


class TestSoMRunnerLegacyParamsAbsent:
    """Guard: confirm WDA removal is complete. Mirrors test_wda_removal.py."""

    def test_no_wda_url_parameter(self):
        """SoMRunner.__init__ must NOT have wda_url."""
        sig = inspect.signature(SoMRunner.__init__)
        assert "wda_url" not in sig.parameters

    def test_no_use_xctest_runner_parameter(self):
        """SoMRunner.__init__ must NOT have use_xctest_runner."""
        sig = inspect.signature(SoMRunner.__init__)
        assert "use_xctest_runner" not in sig.parameters

    def test_no_start_legacy_method(self):
        """_start_legacy must be absent."""
        assert not hasattr(SoMRunner, "_start_legacy")
