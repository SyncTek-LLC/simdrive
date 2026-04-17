"""Regression test for Example Reader dogfood Issue 1 (v13.1.0 → v13.1.1).

Bug: AXHTTPServer.stop() called shutdown() but NOT server_close(), leaving the
TCP socket in a half-closed state. A second ios_start_session with backend="ax"
would fail with [Errno 48] Address already in use.

Fix: call server_close() immediately after shutdown() so the OS reclaims the
listening socket before stop() returns.

This test exercises the real Python socket lifecycle — no mocks.
"""
from __future__ import annotations

import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock

import pytest

from specterqa.ios.backends.ax_backend import AXHTTPServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _can_bind_httpserver(port: int) -> bool:
    """Return True if a new HTTPServer can bind to localhost:port.

    Uses HTTPServer directly (same code path as AXHTTPServer.start()) so
    SO_REUSEADDR semantics are identical.  Returns False if [Errno 48].
    """
    class _Silent(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

    try:
        s = HTTPServer(("localhost", port), _Silent)
        s.server_close()
        return True
    except OSError:
        return False


def _make_mock_backend() -> MagicMock:
    """Return a minimal AXBackend mock sufficient for AXHTTPServer to start."""
    backend = MagicMock()
    backend.health.return_value = {"status": "ok"}
    return backend


# ---------------------------------------------------------------------------
# Regression tests
# ---------------------------------------------------------------------------

TEST_PORT = 18222  # high non-conflicting port; self-contained


class TestAXHTTPServerPortRelease:
    """Example Reader dogfood Issue 1 — port must be free after stop()."""

    def test_port_is_released_after_stop(self):
        """stop() must call server_close() so a new HTTPServer can bind the same port."""
        backend = _make_mock_backend()
        server = AXHTTPServer(backend, port=TEST_PORT)

        server.start()
        time.sleep(0.05)  # let the background thread bind and begin serving
        server.stop()

        assert _can_bind_httpserver(TEST_PORT), (
            "Port is still bound after AXHTTPServer.stop() — "
            "server_close() was likely not called after shutdown()."
        )

    def test_server_can_restart_on_same_port_three_times(self):
        """Simulate three consecutive ios_start_session / ios_stop_session cycles.

        Before the fix, the second iteration raised [Errno 48] Address already in use.
        """
        backend = _make_mock_backend()

        for i in range(3):
            server = AXHTTPServer(backend, port=TEST_PORT)
            try:
                server.start()
                time.sleep(0.05)
            except OSError as exc:
                pytest.fail(
                    f"Iteration {i}: AXHTTPServer.start() raised {exc!r} — "
                    "port was not released by the previous stop() call."
                )
            finally:
                server.stop()

    def test_source_calls_server_close_after_shutdown(self):
        """Static guard: ax_backend.py source must contain server_close() after shutdown().

        This ensures the fix is present even if the live socket test is flaky
        under specific OS scheduling.
        """
        from pathlib import Path

        src_path = (
            Path(__file__).parent.parent.parent
            / "src"
            / "specterqa"
            / "ios"
            / "backends"
            / "ax_backend.py"
        )
        src = src_path.read_text()

        # Locate the stop() method and verify server_close appears before _server = None
        stop_idx = src.find("def stop(self):")
        assert stop_idx != -1, "AXHTTPServer.stop() not found in ax_backend.py"

        stop_block = src[stop_idx : stop_idx + 300]
        assert "server_close()" in stop_block, (
            "AXHTTPServer.stop() does not call server_close() — "
            "port 8222 will not be released cleanly between sessions."
        )
        # Verify ordering: shutdown comes before server_close
        shutdown_pos = stop_block.find("shutdown()")
        close_pos = stop_block.find("server_close()")
        assert shutdown_pos < close_pos, (
            "server_close() must appear AFTER shutdown() inside stop()"
        )
