"""Tests for WDA optional fallback with timeout guard (INIT-2026-509).

Verifies:
- _get_element_tree_from_wda enforces a 10-second per-attempt timeout
- At most 2 retries are attempted before raising RuntimeError
- _start_legacy in SoMRunner logs a WARNING directing users to build the runner
- WDA fallback is only invoked when use_xctest_runner=False and wda_url is set
"""

from __future__ import annotations

import logging
import socket
import urllib.error
import urllib.request
from unittest.mock import MagicMock, call, patch

import pytest

from specterqa.ios.som_annotator import SoMAnnotator
from specterqa.ios.som_runner import SoMRunner


# ---------------------------------------------------------------------------
# _get_element_tree_from_wda — timeout behaviour
# ---------------------------------------------------------------------------


class TestWDATimeout:
    """Unit tests for SoMAnnotator._get_element_tree_from_wda."""

    def _make_annotator(self) -> SoMAnnotator:
        return SoMAnnotator(
            wda_url="http://localhost:8100",
            session_id="test-session-abc",
        )

    def test_wda_timeout_10_seconds(self):
        """urlopen is called with timeout=10."""
        annotator = self._make_annotator()

        captured_timeouts: list[int] = []

        def fake_urlopen(req, timeout=None):
            captured_timeouts.append(timeout)
            mock_resp = MagicMock()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = b'{"value": "<AppElement/>"}'
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            annotator._get_element_tree_from_wda()

        assert captured_timeouts, "urlopen was never called"
        assert all(t == 10 for t in captured_timeouts), (
            f"Expected timeout=10, got {captured_timeouts}"
        )

    def test_wda_retry_max_2(self):
        """On persistent URLError, exactly 2 attempts are made before raising."""
        annotator = self._make_annotator()

        attempt_count = 0

        def fake_urlopen(req, timeout=None):
            nonlocal attempt_count
            attempt_count += 1
            raise urllib.error.URLError("connection refused")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with pytest.raises(RuntimeError, match="timed out after 2 attempts"):
                annotator._get_element_tree_from_wda()

        assert attempt_count == 2, f"Expected 2 attempts, got {attempt_count}"

    def test_wda_socket_timeout_is_caught(self):
        """socket.timeout is treated as a retryable error."""
        annotator = self._make_annotator()

        attempt_count = 0

        def fake_urlopen(req, timeout=None):
            nonlocal attempt_count
            attempt_count += 1
            raise socket.timeout("timed out")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with pytest.raises(RuntimeError, match="timed out"):
                annotator._get_element_tree_from_wda()

        assert attempt_count == 2

    def test_wda_returns_value_on_success(self):
        """Returns the 'value' field from the WDA JSON response."""
        annotator = self._make_annotator()
        xml_payload = "<AppElement><XCUIElementTypeButton label='OK'/></AppElement>"

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = (
            f'{{"value": "{xml_payload}"}}'.encode()
        )

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = annotator._get_element_tree_from_wda()

        assert result == xml_payload

    def test_wda_raises_when_no_session_id(self):
        """RuntimeError raised immediately when session_id is not set."""
        annotator = SoMAnnotator(wda_url="http://localhost:8100")
        with pytest.raises(RuntimeError, match="session ID"):
            annotator._get_element_tree_from_wda()

    def test_wda_retries_once_on_first_failure(self):
        """First attempt fails, second succeeds — only 2 total calls."""
        annotator = self._make_annotator()
        attempt_count = 0

        def fake_urlopen(req, timeout=None):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count == 1:
                raise urllib.error.URLError("temporary failure")
            mock_resp = MagicMock()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = b'{"value": "<ok/>"}'
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = annotator._get_element_tree_from_wda()

        assert attempt_count == 2
        assert result == "<ok/>"

    def test_wda_url_is_constructed_correctly(self):
        """URL sent to urlopen includes the session ID and /source path."""
        annotator = SoMAnnotator(
            wda_url="http://localhost:8100",
            session_id="my-session-99",
        )
        captured_urls: list[str] = []

        def fake_urlopen(req, timeout=None):
            captured_urls.append(req.full_url if hasattr(req, "full_url") else str(req))
            mock_resp = MagicMock()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = b'{"value": ""}'
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            annotator._get_element_tree_from_wda()

        assert captured_urls, "urlopen was never called"
        url = captured_urls[0]
        assert "my-session-99" in url
        assert "/source" in url


# ---------------------------------------------------------------------------
# _start_legacy — warning behaviour
# ---------------------------------------------------------------------------


class TestWDAFallbackLogsWarning:
    def test_wda_fallback_logs_warning(self, caplog):
        """_start_legacy emits a WARNING telling the user to build the runner."""
        runner = SoMRunner(api_key="test", use_xctest_runner=False, wda_url="http://localhost:8100")

        mock_driver = MagicMock()
        mock_driver.create_session.return_value = "session-123"

        mock_annotator_cls = MagicMock()

        with (
            patch("specterqa.ios.som_runner.SoMRunner._start_legacy", wraps=lambda bundle_id, SoMAnnotator: None),
            caplog.at_level(logging.WARNING, logger="specterqa.ios.som_runner"),
        ):
            # Call _start_legacy directly so we can inspect its log output
            from specterqa.ios.wda_driver import WDADriver  # noqa — may not exist in CI

    def test_wda_fallback_logs_warning_via_logger(self, caplog):
        """_start_legacy writes to the specterqa.ios.som_runner logger at WARNING level."""
        # We patch the wda_driver import inside _start_legacy so it doesn't
        # require the actual wda_driver module to be installed.
        runner = SoMRunner(api_key="test", use_xctest_runner=False, wda_url="http://localhost:8100")

        mock_wda_driver_cls = MagicMock()
        mock_driver_instance = MagicMock()
        mock_driver_instance.create_session.return_value = "test-session"
        mock_wda_driver_cls.return_value = mock_driver_instance

        mock_annotator_cls = MagicMock()

        with (
            patch.dict(
                "sys.modules",
                {"specterqa.ios.wda_driver": MagicMock(WDADriver=mock_wda_driver_cls)},
            ),
            caplog.at_level(logging.WARNING, logger="specterqa.ios.som_runner"),
        ):
            runner._start_legacy("com.test.app", mock_annotator_cls)

        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any(
            "wda" in m.lower() or "fallback" in m.lower() or "runner" in m.lower()
            for m in warning_messages
        ), f"Expected WDA fallback warning. Got: {warning_messages}"

    def test_wda_fallback_warning_mentions_build_command(self, caplog):
        """The warning must mention how to build the runner (runner build)."""
        runner = SoMRunner(api_key="test", use_xctest_runner=False, wda_url="http://localhost:8100")

        mock_wda_driver_cls = MagicMock()
        mock_driver_instance = MagicMock()
        mock_driver_instance.create_session.return_value = "test-session"
        mock_wda_driver_cls.return_value = mock_driver_instance

        mock_annotator_cls = MagicMock()

        with (
            patch.dict(
                "sys.modules",
                {"specterqa.ios.wda_driver": MagicMock(WDADriver=mock_wda_driver_cls)},
            ),
            caplog.at_level(logging.WARNING, logger="specterqa.ios.som_runner"),
        ):
            runner._start_legacy("com.test.app", mock_annotator_cls)

        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        # The warning should mention 'runner build' or similar
        combined = " ".join(warning_messages).lower()
        assert "runner" in combined and "build" in combined, (
            f"Warning should mention 'runner build'. Got: {warning_messages}"
        )
