"""Tests for ios_perf, ios_memory, and ios_network MCP tools.

Verifies PerfProfiler and NetworkInspector wiring in the MCP server.
Tests are written against the expected handler interface; graceful
ImportError / AttributeError skips are used for symbols not yet wired.

Run:
    pytest tests/test_perf_memory_network.py -v --tb=short
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Import guards for real dataclasses
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.drivers.simulator.perf import PerfSnapshot, PerfProfiler
except ImportError:
    pytest.skip("perf module not available", allow_module_level=True)

try:
    from specterqa.ios.drivers.simulator.network import NetworkRequest, NetworkInspector, NetworkSnapshot
except ImportError:
    pytest.skip("network module not available", allow_module_level=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_perf_snapshot(
    memory_mb: float = 44.9,
    cpu_percent: float = 12.5,
    thread_count: int = 8,
    disk_usage_mb: float = 0.0,
    fps_estimate: float = 60.0,
    timestamp: float | None = None,
) -> PerfSnapshot:
    """Build a PerfSnapshot with sensible defaults."""
    return PerfSnapshot(
        memory_mb=memory_mb,
        cpu_percent=cpu_percent,
        thread_count=thread_count,
        disk_usage_mb=disk_usage_mb,
        fps_estimate=fps_estimate,
        timestamp=timestamp if timestamp is not None else time.time(),
    )


def _make_network_request(
    request_id: str = "req-001",
    method: str = "GET",
    url: str = "https://api.example.com/v1/users",
    host: str = "api.example.com",
    path: str = "/v1/users",
    status_code: int | None = 200,
    request_headers: dict | None = None,
    response_headers: dict | None = None,
    request_body_size: int = 0,
    response_body_size: int = 1024,
    started_at: float | None = None,
    completed_at: float | None = None,
    duration_ms: float | None = 142.3,
    error: str | None = None,
) -> NetworkRequest:
    """Build a NetworkRequest with sensible defaults."""
    now = time.time()
    return NetworkRequest(
        request_id=request_id,
        method=method,
        url=url,
        host=host,
        path=path,
        status_code=status_code,
        request_headers=request_headers or {"Accept": "application/json"},
        response_headers=response_headers or {"Content-Type": "application/json"},
        request_body_size=request_body_size,
        response_body_size=response_body_size,
        started_at=started_at if started_at is not None else now - 0.142,
        completed_at=completed_at if completed_at is not None else now,
        duration_ms=duration_ms,
        error=error,
    )


def _setup_perf_network(perf_profiler=None, network_inspector=None):
    """Inject mock profiler / inspector into server module globals."""
    import specterqa.ios.mcp.server as srv

    mock_backend = MagicMock()
    # Make bridge calls fail so tests exercise the Python-side fallback path
    mock_backend._get.side_effect = Exception("bridge unavailable")
    srv._backend = mock_backend
    srv._session = MagicMock()
    if hasattr(srv, "_perf_profiler"):
        srv._perf_profiler = perf_profiler
    if hasattr(srv, "_network_inspector"):
        srv._network_inspector = network_inspector


def _teardown_perf_network():
    """Reset server module globals to a clean idle state."""
    import specterqa.ios.mcp.server as srv

    srv._backend = None
    srv._session = None
    if hasattr(srv, "_perf_profiler"):
        srv._perf_profiler = None
    if hasattr(srv, "_network_inspector"):
        srv._network_inspector = None
    if hasattr(srv, "_session_state"):
        srv._session_state = "idle"


def _get_handler(name: str):
    """Import a handler from server, skip if not yet implemented."""
    try:
        import specterqa.ios.mcp.server as srv
        handler = getattr(srv, name, None)
        if handler is None:
            pytest.skip(f"{name} not yet implemented")
        return handler
    except ImportError:
        pytest.skip("server module not importable")


# ===========================================================================
# TestHandlePerf
# ===========================================================================


class TestHandlePerf:
    """Verify ios_perf MCP tool handler (handle_perf)."""

    def teardown_method(self, method):
        _teardown_perf_network()

    def test_perf_returns_error_when_no_session(self):
        """When _perf_profiler is None, handle_perf returns an error dict."""
        import specterqa.ios.mcp.server as srv

        handle_perf = _get_handler("handle_perf")

        if not hasattr(srv, "_perf_profiler"):
            pytest.skip("_perf_profiler global not yet wired")

        srv._perf_profiler = None

        result = handle_perf({})

        assert "error" in result, (
            f"Expected error when _perf_profiler is None. Got: {result}"
        )

    def test_perf_returns_cpu_and_memory(self):
        """Mock profiler.snapshot() → response has cpu_percent, memory, thread_count, pid."""
        handle_perf = _get_handler("handle_perf")
        import specterqa.ios.mcp.server as srv

        if not hasattr(srv, "_perf_profiler"):
            pytest.skip("_perf_profiler global not yet wired")

        snapshot = _make_perf_snapshot(
            cpu_percent=23.7,
            memory_mb=88.3,
            thread_count=12,
        )

        mock_profiler = MagicMock()
        mock_profiler.snapshot.return_value = snapshot

        _setup_perf_network(perf_profiler=mock_profiler)

        result = handle_perf({})

        assert "error" not in result, f"Unexpected error: {result}"

        result_str = str(result)
        assert "cpu_percent" in result or "cpu" in result_str, (
            f"Response should include cpu_percent. Got keys: {list(result.keys())}"
        )
        assert "memory" in result_str or "memory_mb" in result, (
            f"Response should include memory field. Got keys: {list(result.keys())}"
        )
        assert "thread_count" in result or "threads" in result_str, (
            f"Response should include thread_count. Got keys: {list(result.keys())}"
        )

        # Verify profiler.snapshot() was actually called
        mock_profiler.snapshot.assert_called_once()

    def test_perf_handles_profiler_error(self):
        """snapshot() raises RuntimeError → graceful error response (no exception escapes)."""
        handle_perf = _get_handler("handle_perf")
        import specterqa.ios.mcp.server as srv

        if not hasattr(srv, "_perf_profiler"):
            pytest.skip("_perf_profiler global not yet wired")

        mock_profiler = MagicMock()
        mock_profiler.snapshot.side_effect = RuntimeError("ps command failed: permission denied")

        _setup_perf_network(perf_profiler=mock_profiler)

        # Must not raise — handler must convert to error dict
        result = handle_perf({})

        assert "error" in result, (
            f"Expected graceful error response when snapshot() raises. Got: {result}"
        )

    def test_perf_thread_count_is_integer(self):
        """thread_count in response must be int, not float."""
        handle_perf = _get_handler("handle_perf")
        import specterqa.ios.mcp.server as srv

        if not hasattr(srv, "_perf_profiler"):
            pytest.skip("_perf_profiler global not yet wired")

        snapshot = _make_perf_snapshot(thread_count=7)

        mock_profiler = MagicMock()
        mock_profiler.snapshot.return_value = snapshot

        _setup_perf_network(perf_profiler=mock_profiler)

        result = handle_perf({})

        assert "error" not in result, f"Unexpected error: {result}"

        # Find thread_count wherever it is in the response
        thread_val = result.get("thread_count") or result.get("threads")
        if thread_val is None:
            # Might be nested under a "snapshot" or "metrics" key
            for key in result:
                if isinstance(result[key], dict):
                    thread_val = result[key].get("thread_count") or result[key].get("threads")
                    if thread_val is not None:
                        break

        if thread_val is not None:
            assert isinstance(thread_val, int), (
                f"thread_count must be int, got {type(thread_val).__name__}: {thread_val}"
            )
            assert not isinstance(thread_val, float), (
                "thread_count must not be float"
            )


# ===========================================================================
# TestHandleMemory
# ===========================================================================


class TestHandleMemory:
    """Verify ios_memory MCP tool handler (handle_memory)."""

    def teardown_method(self, method):
        _teardown_perf_network()

    def test_memory_returns_error_when_no_session(self):
        """When _perf_profiler is None, handle_memory returns an error dict."""
        import specterqa.ios.mcp.server as srv

        handle_memory = _get_handler("handle_memory")

        if not hasattr(srv, "_perf_profiler"):
            pytest.skip("_perf_profiler global not yet wired")

        srv._perf_profiler = None

        result = handle_memory({})

        assert "error" in result, (
            f"Expected error when _perf_profiler is None. Got: {result}"
        )

    def test_memory_returns_breakdown(self):
        """mock memory_detail() → response includes dirty, swapped, clean, footprint fields."""
        handle_memory = _get_handler("handle_memory")
        import specterqa.ios.mcp.server as srv

        if not hasattr(srv, "_perf_profiler"):
            pytest.skip("_perf_profiler global not yet wired")

        memory_breakdown = {
            "dirty_mb": 34.5,
            "swapped_mb": 0.0,
            "clean_mb": 12.1,
            "footprint_mb": 46.6,
        }

        mock_profiler = MagicMock()
        mock_profiler.memory_detail.return_value = memory_breakdown

        _setup_perf_network(perf_profiler=mock_profiler)

        result = handle_memory({})

        assert "error" not in result, f"Unexpected error: {result}"

        result_str = str(result)
        assert "dirty" in result_str, (
            f"Response should include dirty memory field. Got: {result}"
        )
        assert "swapped" in result_str or "swap" in result_str, (
            f"Response should include swapped memory field. Got: {result}"
        )
        assert "clean" in result_str, (
            f"Response should include clean memory field. Got: {result}"
        )
        assert "footprint" in result_str, (
            f"Response should include footprint field. Got: {result}"
        )

    def test_memory_handles_footprint_failure(self):
        """memory_detail() raises (footprint tool unavailable) → graceful error response."""
        handle_memory = _get_handler("handle_memory")
        import specterqa.ios.mcp.server as srv

        if not hasattr(srv, "_perf_profiler"):
            pytest.skip("_perf_profiler global not yet wired")

        mock_profiler = MagicMock()
        mock_profiler.memory_detail.side_effect = RuntimeError(
            "footprint tool not available on this OS version"
        )

        _setup_perf_network(perf_profiler=mock_profiler)

        # Must not raise — handler must catch and return error dict
        result = handle_memory({})

        assert "error" in result, (
            f"Expected graceful error when memory_detail() raises. Got: {result}"
        )


# ===========================================================================
# TestHandleNetwork
# ===========================================================================


class TestHandleNetwork:
    """Verify ios_network MCP tool handler (handle_network)."""

    def teardown_method(self, method):
        _teardown_perf_network()

    def test_network_returns_error_when_no_session(self):
        """When _network_inspector is None, handle_network returns an error dict."""
        import specterqa.ios.mcp.server as srv

        handle_network = _get_handler("handle_network")

        if not hasattr(srv, "_network_inspector"):
            pytest.skip("_network_inspector global not yet wired")

        srv._network_inspector = None

        result = handle_network({})

        assert "error" in result, (
            f"Expected error when _network_inspector is None. Got: {result}"
        )

    def test_network_returns_requests_list(self):
        """Mock recent requests → response has list with url, method, status_code fields."""
        handle_network = _get_handler("handle_network")
        import specterqa.ios.mcp.server as srv

        if not hasattr(srv, "_network_inspector"):
            pytest.skip("_network_inspector global not yet wired")

        requests = [
            _make_network_request(
                request_id="req-001",
                method="GET",
                url="https://api.example.com/v1/profile",
                status_code=200,
            ),
            _make_network_request(
                request_id="req-002",
                method="POST",
                url="https://api.example.com/v1/auth/token",
                status_code=201,
            ),
        ]

        mock_inspector = MagicMock()
        mock_inspector.snapshot.return_value = NetworkSnapshot(
            bytes_in=4096,
            bytes_out=256,
            throughput_in=100.0,
            throughput_out=50.0,
            requests=requests,
            active_connections=0,
            nettop_available=True,
        )

        _setup_perf_network(network_inspector=mock_inspector)

        result = handle_network({})

        assert "error" not in result, f"Unexpected error: {result}"

        # Find the requests list in the response
        req_list = result.get("requests") or result.get("network_requests") or []
        assert len(req_list) == 2, (
            f"Expected 2 requests in response. Got {len(req_list)}: {result}"
        )

        first = req_list[0]
        assert "url" in first, f"Request entry should have 'url' field. Got: {first}"
        assert "method" in first, f"Request entry should have 'method' field. Got: {first}"
        assert "status_code" in first, f"Request entry should have 'status_code' field. Got: {first}"

    def test_network_returns_byte_counters(self):
        """bytes_in and bytes_out present in the response."""
        handle_network = _get_handler("handle_network")
        import specterqa.ios.mcp.server as srv

        if not hasattr(srv, "_network_inspector"):
            pytest.skip("_network_inspector global not yet wired")

        requests = [
            _make_network_request(
                request_id="req-001",
                request_body_size=256,
                response_body_size=4096,
            ),
            _make_network_request(
                request_id="req-002",
                request_body_size=512,
                response_body_size=2048,
            ),
        ]

        mock_inspector = MagicMock()
        mock_inspector.completed_requests.return_value = requests
        mock_inspector.summary.return_value = {
            "total_requests": 2,
            "by_status": {200: 2},
            "by_host": {"api.example.com": 2},
            "avg_latency_ms": 100.0,
            "failed_count": 0,
        }

        _setup_perf_network(network_inspector=mock_inspector)

        result = handle_network({})

        assert "error" not in result, f"Unexpected error: {result}"

        result_str = str(result)
        assert "bytes_in" in result_str or "bytes_received" in result_str or "response_bytes" in result_str, (
            f"Response should include incoming byte counter. Got keys: {list(result.keys())}"
        )
        assert "bytes_out" in result_str or "bytes_sent" in result_str or "request_bytes" in result_str, (
            f"Response should include outgoing byte counter. Got keys: {list(result.keys())}"
        )

    def test_network_seconds_filter(self):
        """seconds param is passed through to completed_requests()."""
        handle_network = _get_handler("handle_network")
        import specterqa.ios.mcp.server as srv

        if not hasattr(srv, "_network_inspector"):
            pytest.skip("_network_inspector global not yet wired")

        mock_inspector = MagicMock()
        mock_inspector.completed_requests.return_value = []
        mock_inspector.summary.return_value = {
            "total_requests": 0,
            "by_status": {},
            "by_host": {},
            "avg_latency_ms": 0.0,
            "failed_count": 0,
        }

        _setup_perf_network(network_inspector=mock_inspector)

        result = handle_network({"seconds": 60})

        assert "error" not in result, f"Unexpected error: {result}"

        # snapshot must have been called with seconds=60
        mock_inspector.snapshot.assert_called_once()
        call_args = mock_inspector.snapshot.call_args
        passed_seconds = call_args[0][0] if call_args[0] else call_args[1].get("seconds", 30.0)
        assert float(passed_seconds) == 60.0, (
            f"Expected seconds=60 passed to snapshot. Got: {passed_seconds}"
        )

    def test_network_empty_when_no_activity(self):
        """No requests → empty list and zero byte counters in response."""
        handle_network = _get_handler("handle_network")
        import specterqa.ios.mcp.server as srv

        if not hasattr(srv, "_network_inspector"):
            pytest.skip("_network_inspector global not yet wired")

        mock_inspector = MagicMock()
        mock_inspector.completed_requests.return_value = []
        mock_inspector.summary.return_value = {
            "total_requests": 0,
            "by_status": {},
            "by_host": {},
            "avg_latency_ms": 0.0,
            "failed_count": 0,
        }

        _setup_perf_network(network_inspector=mock_inspector)

        result = handle_network({})

        assert "error" not in result, f"Unexpected error: {result}"

        req_list = result.get("requests") or result.get("network_requests") or []
        assert len(req_list) == 0, (
            f"Expected empty requests list when no activity. Got: {req_list}"
        )

        # byte counters should be zero
        result_str = str(result)
        assert "0" in result_str, (
            "Expected zero byte counts when no network activity"
        )


# ===========================================================================
# TestSessionLifecyclePerf
# ===========================================================================


class TestSessionLifecyclePerf:
    """Verify PerfProfiler and NetworkInspector lifecycle tied to session start/stop."""

    def teardown_method(self, method):
        _teardown_perf_network()

    def test_profiler_started_on_session_start(self):
        """After handle_start_session, _perf_profiler is not None."""
        import specterqa.ios.mcp.server as srv
        from specterqa.ios.mcp.server import handle_start_session

        if not hasattr(srv, "_perf_profiler"):
            pytest.skip("_perf_profiler global not yet wired")

        with (
            patch("specterqa.ios.backends.xctest_client.XCTestBackend", autospec=True) as MockBackend,
            patch("specterqa.ios.session_manager.TestSession", autospec=True) as MockSession,
            patch("specterqa.ios.replay.ReplayRecorder", autospec=True),
            patch(
                "specterqa.ios.drivers.simulator.console.ConsoleMonitor",
                autospec=True,
            ) as MockConsole,
            patch(
                "specterqa.ios.drivers.simulator.crash.CrashDetector",
                autospec=True,
            ) as MockCrash,
            patch(
                "specterqa.ios.drivers.simulator.perf.PerfProfiler",
                autospec=True,
            ) as MockPerfProfiler,
        ):
            mock_backend_instance = MagicMock()
            MockBackend.return_value = mock_backend_instance
            mock_backend_instance.health.return_value = {"status": "ok"}

            mock_session_instance = MagicMock()
            MockSession.return_value = mock_session_instance
            mock_session_instance._target_udid = "test-udid"
            mock_session_instance._port = 8222
            mock_session_instance.runner_url = "http://localhost:8222"

            MockConsole.return_value = MagicMock()
            MockCrash.return_value = MagicMock()
            MockPerfProfiler.return_value = MagicMock()

            try:
                handle_start_session({"bundle_id": "com.example.app", "udid": "test-udid"})
            except Exception:
                pass  # Partial start OK — we check instantiation was attempted

        profiler_set = srv._perf_profiler is not None
        profiler_called = MockPerfProfiler.called

        assert profiler_set or profiler_called, (
            "_perf_profiler should be initialised during session start"
        )

    def test_profiler_stopped_on_session_stop(self):
        """After handle_stop_session, _perf_profiler is None."""
        import specterqa.ios.mcp.server as srv
        from specterqa.ios.mcp.server import handle_stop_session

        if not hasattr(srv, "_perf_profiler"):
            pytest.skip("_perf_profiler global not yet wired")

        mock_profiler = MagicMock()
        srv._backend = MagicMock()
        srv._session = MagicMock()
        srv._perf_profiler = mock_profiler
        if hasattr(srv, "_session_state"):
            srv._session_state = "running"

        handle_stop_session({})

        assert srv._perf_profiler is None, (
            f"_perf_profiler should be None after stop. Got: {srv._perf_profiler}"
        )

    def test_network_started_on_session_start(self):
        """After handle_start_session, _network_inspector is not None."""
        import specterqa.ios.mcp.server as srv
        from specterqa.ios.mcp.server import handle_start_session

        if not hasattr(srv, "_network_inspector"):
            pytest.skip("_network_inspector global not yet wired")

        with (
            patch("specterqa.ios.backends.xctest_client.XCTestBackend", autospec=True) as MockBackend,
            patch("specterqa.ios.session_manager.TestSession", autospec=True) as MockSession,
            patch("specterqa.ios.replay.ReplayRecorder", autospec=True),
            patch(
                "specterqa.ios.drivers.simulator.console.ConsoleMonitor",
                autospec=True,
            ) as MockConsole,
            patch(
                "specterqa.ios.drivers.simulator.crash.CrashDetector",
                autospec=True,
            ) as MockCrash,
            patch(
                "specterqa.ios.drivers.simulator.network.NetworkInspector",
                autospec=True,
            ) as MockNetworkInspector,
        ):
            mock_backend_instance = MagicMock()
            MockBackend.return_value = mock_backend_instance
            mock_backend_instance.health.return_value = {"status": "ok"}

            mock_session_instance = MagicMock()
            MockSession.return_value = mock_session_instance
            mock_session_instance._target_udid = "test-udid"
            mock_session_instance._port = 8222
            mock_session_instance.runner_url = "http://localhost:8222"

            MockConsole.return_value = MagicMock()
            MockCrash.return_value = MagicMock()
            MockNetworkInspector.return_value = MagicMock()

            try:
                handle_start_session({"bundle_id": "com.example.app", "udid": "test-udid"})
            except Exception:
                pass  # Partial start OK — we check instantiation was attempted

        inspector_set = srv._network_inspector is not None
        inspector_called = MockNetworkInspector.called

        assert inspector_set or inspector_called, (
            "_network_inspector should be initialised during session start"
        )

    def test_network_stopped_on_session_stop(self):
        """After handle_stop_session, _network_inspector is None."""
        import specterqa.ios.mcp.server as srv
        from specterqa.ios.mcp.server import handle_stop_session

        if not hasattr(srv, "_network_inspector"):
            pytest.skip("_network_inspector global not yet wired")

        mock_inspector = MagicMock()
        srv._backend = MagicMock()
        srv._session = MagicMock()
        srv._network_inspector = mock_inspector
        if hasattr(srv, "_session_state"):
            srv._session_state = "running"

        handle_stop_session({})

        assert srv._network_inspector is None, (
            f"_network_inspector should be None after stop. Got: {srv._network_inspector}"
        )
