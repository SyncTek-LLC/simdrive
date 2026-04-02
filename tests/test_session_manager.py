"""Tests for SessionManager — iOS Simulator lifecycle management for SpecterQA v3.

TDD Phase — INIT-2026-500 (SpecterQA iOS Headless Driver).

These tests are written BEFORE implementation exists and remain importable even
when the implementation module is absent.

Module under test (to be created by CodeAtlas):
  specterqa/ios/session_manager.py  —  TestSession, SessionManager

Spec:
  - TestSession wraps a cloned simulator UDID and the XCTest runner process.
  - SessionManager.start() clones a simulator, boots it headlessly, installs
    the app bundle, launches the XCTest runner via xcodebuild, and polls /health.
  - SessionManager.stop()  shuts the runner down and deletes the cloned sim.

All tests mock subprocess.run and requests so no real simulator is required.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard — tests remain importable without implementation.
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.session_manager import (  # type: ignore[import]
        SessionManager,
        SessionManagerError,
        TestSession,
    )
    _MODULE_AVAILABLE = True
except ImportError:
    _MODULE_AVAILABLE = False
    SessionManager = None       # type: ignore[assignment,misc]
    SessionManagerError = Exception  # type: ignore[assignment,misc]
    TestSession = None          # type: ignore[assignment,misc]

needs_module = pytest.mark.skipif(
    not _MODULE_AVAILABLE,
    reason="specterqa.ios.session_manager not yet implemented",
)

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

_CLONE_UDID = "CLONE-1234-ABCD-5678-EFGH"
_SOURCE_UDID = "SOURCE-UDID-0001"
_BUNDLE_ID = "com.example.TestApp"
_APP_PATH = "/tmp/TestApp.app"
_RUNNER_DERIVED_DATA = "/tmp/specterqa-runner-build"


def _ok_proc(stdout: str = "", returncode: int = 0) -> MagicMock:
    """Return a mock CompletedProcess that looks successful."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = ""
    return proc


def _make_manager(**kwargs) -> "SessionManager":
    """Construct a SessionManager with sensible test defaults."""
    defaults = dict(
        bundle_id=_BUNDLE_ID,
        app_path=_APP_PATH,
        runner_derived_data=_RUNNER_DERIVED_DATA,
        port=8222,
        headless=True,
    )
    defaults.update(kwargs)
    return SessionManager(**defaults)


# ---------------------------------------------------------------------------
# TestSession dataclass / object contract
# ---------------------------------------------------------------------------


@needs_module
class TestTestSessionContract:
    """TestSession must expose the attributes the rest of the system depends on."""

    def test_has_udid(self):
        session = TestSession(udid=_CLONE_UDID, port=8222, source_udid=_SOURCE_UDID)
        assert session.udid == _CLONE_UDID

    def test_has_port(self):
        session = TestSession(udid=_CLONE_UDID, port=8222, source_udid=_SOURCE_UDID)
        assert session.port == 8222

    def test_has_source_udid(self):
        session = TestSession(udid=_CLONE_UDID, port=8222, source_udid=_SOURCE_UDID)
        assert session.source_udid == _SOURCE_UDID

    def test_base_url_uses_port(self):
        session = TestSession(udid=_CLONE_UDID, port=9000, source_udid=_SOURCE_UDID)
        assert "9000" in session.base_url

    def test_base_url_is_localhost(self):
        session = TestSession(udid=_CLONE_UDID, port=8222, source_udid=_SOURCE_UDID)
        assert "localhost" in session.base_url or "127.0.0.1" in session.base_url


# ---------------------------------------------------------------------------
# test_clone_creates_simulator
# ---------------------------------------------------------------------------


@needs_module
class TestCloneCreateSimulator:
    """SessionManager._clone() invokes simctl clone with the correct args."""

    def test_clone_calls_simctl_clone(self):
        manager = _make_manager()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok_proc(stdout=_CLONE_UDID + "\n")
            udid = manager._clone(source_udid=_SOURCE_UDID, name="SpecterQA-Clone")

        # simctl clone <source_udid> <name>
        args = mock_run.call_args_list[0][0][0]
        assert "simctl" in args
        assert "clone" in args
        assert _SOURCE_UDID in args

    def test_clone_returns_new_udid(self):
        manager = _make_manager()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok_proc(stdout=_CLONE_UDID + "\n")
            udid = manager._clone(source_udid=_SOURCE_UDID, name="SpecterQA-Clone")
        assert udid == _CLONE_UDID

    def test_clone_raises_on_failure(self):
        manager = _make_manager()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok_proc(returncode=1, stdout="")
            with pytest.raises((SessionManagerError, subprocess.CalledProcessError, RuntimeError, Exception)):
                manager._clone(source_udid=_SOURCE_UDID, name="SpecterQA-Clone")

    def test_clone_includes_custom_name(self):
        manager = _make_manager()
        custom_name = "SpecterQA-Test-Session-42"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok_proc(stdout=_CLONE_UDID)
            manager._clone(source_udid=_SOURCE_UDID, name=custom_name)
        args = mock_run.call_args_list[0][0][0]
        assert custom_name in args


# ---------------------------------------------------------------------------
# test_boot_headless
# ---------------------------------------------------------------------------


@needs_module
class TestBootHeadless:
    """SessionManager._boot() boots without launching Simulator.app."""

    def test_boot_calls_simctl_boot(self):
        manager = _make_manager(headless=True)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok_proc()
            manager._boot(udid=_CLONE_UDID)
        args_list = [call[0][0] for call in mock_run.call_args_list]
        boot_calls = [a for a in args_list if "boot" in a]
        assert boot_calls, "Expected at least one simctl boot call"

    def test_boot_headless_does_not_open_simulator_app(self):
        """Headless boot must NOT invoke 'open -a Simulator'."""
        manager = _make_manager(headless=True)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok_proc()
            manager._boot(udid=_CLONE_UDID)
        for c in mock_run.call_args_list:
            cmd = c[0][0]
            assert not ("open" in cmd and "Simulator" in " ".join(cmd)), \
                "Headless boot must not open Simulator.app"

    def test_boot_ignores_already_booted_error(self):
        """'Unable to boot device in current state: Booted' is not fatal."""
        manager = _make_manager()
        already_booted = _ok_proc(returncode=1)
        already_booted.stderr = "Unable to boot device in current state: Booted"
        with patch("subprocess.run", return_value=already_booted):
            # Should not raise
            manager._boot(udid=_CLONE_UDID)

    def test_boot_raises_on_real_failure(self):
        manager = _make_manager()
        fail_proc = _ok_proc(returncode=1)
        fail_proc.stderr = "Simulator not found"
        with patch("subprocess.run", return_value=fail_proc):
            with pytest.raises((SessionManagerError, RuntimeError, Exception)):
                manager._boot(udid=_CLONE_UDID)

    def test_boot_passes_udid(self):
        manager = _make_manager()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok_proc()
            manager._boot(udid=_CLONE_UDID)
        all_args = " ".join(
            str(a) for c in mock_run.call_args_list for a in c[0][0]
        )
        assert _CLONE_UDID in all_args


# ---------------------------------------------------------------------------
# test_install_app
# ---------------------------------------------------------------------------


@needs_module
class TestInstallApp:
    """SessionManager._install() calls simctl install with app path."""

    def test_install_calls_simctl_install(self):
        manager = _make_manager(app_path=_APP_PATH)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok_proc()
            manager._install(udid=_CLONE_UDID)
        args_list = [call[0][0] for call in mock_run.call_args_list]
        install_calls = [a for a in args_list if "install" in a]
        assert install_calls

    def test_install_passes_app_path(self):
        manager = _make_manager(app_path=_APP_PATH)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok_proc()
            manager._install(udid=_CLONE_UDID)
        all_args = " ".join(
            str(a) for c in mock_run.call_args_list for a in c[0][0]
        )
        assert _APP_PATH in all_args

    def test_install_passes_udid(self):
        manager = _make_manager(app_path=_APP_PATH)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok_proc()
            manager._install(udid=_CLONE_UDID)
        all_args = " ".join(
            str(a) for c in mock_run.call_args_list for a in c[0][0]
        )
        assert _CLONE_UDID in all_args

    def test_install_raises_on_failure(self):
        manager = _make_manager(app_path=_APP_PATH)
        with patch("subprocess.run", return_value=_ok_proc(returncode=1)):
            with pytest.raises((SessionManagerError, RuntimeError, Exception)):
                manager._install(udid=_CLONE_UDID)


# ---------------------------------------------------------------------------
# test_deploy_runner
# ---------------------------------------------------------------------------


@needs_module
class TestDeployRunner:
    """SessionManager._deploy_runner() launches xcodebuild test-without-building."""

    def test_deploy_runner_uses_xcodebuild(self):
        manager = _make_manager()
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock(pid=12345)
            manager._deploy_runner(udid=_CLONE_UDID, port=8222)
        assert mock_popen.called
        cmd = mock_popen.call_args[0][0]
        assert "xcodebuild" in cmd

    def test_deploy_runner_uses_test_without_building(self):
        manager = _make_manager()
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock(pid=12345)
            manager._deploy_runner(udid=_CLONE_UDID, port=8222)
        cmd = mock_popen.call_args[0][0]
        assert "test-without-building" in cmd

    def test_deploy_runner_passes_udid_as_destination(self):
        manager = _make_manager()
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock(pid=12345)
            manager._deploy_runner(udid=_CLONE_UDID, port=8222)
        cmd_str = " ".join(mock_popen.call_args[0][0])
        assert _CLONE_UDID in cmd_str

    def test_deploy_runner_sets_port_env_var(self):
        """SPECTERQA_PORT must be passed in the environment."""
        manager = _make_manager()
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock(pid=12345)
            manager._deploy_runner(udid=_CLONE_UDID, port=8222)
        # env may be in kwargs or positional
        call_kwargs = mock_popen.call_args.kwargs if mock_popen.call_args.kwargs else {}
        env = call_kwargs.get("env", {})
        assert env.get("SPECTERQA_PORT") == "8222" or "SPECTERQA_PORT" in str(mock_popen.call_args)

    def test_deploy_runner_returns_process(self):
        manager = _make_manager()
        mock_proc = MagicMock(pid=99)
        with patch("subprocess.Popen", return_value=mock_proc):
            result = manager._deploy_runner(udid=_CLONE_UDID, port=8222)
        assert result is not None


# ---------------------------------------------------------------------------
# test_wait_for_health
# ---------------------------------------------------------------------------


@needs_module
class TestWaitForHealth:
    """SessionManager._wait_for_health() polls GET /health until ready."""

    def test_health_check_polls_correct_url(self):
        manager = _make_manager()
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"status": "ok"}'
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp
            manager._wait_for_health(port=8222, timeout=5)
        url_called = str(mock_urlopen.call_args_list[0])
        assert "8222" in url_called
        assert "health" in url_called

    def test_health_check_returns_on_ok(self):
        manager = _make_manager()
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"status": "ok"}'
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp
            # Should not raise or block
            manager._wait_for_health(port=8222, timeout=5)

    def test_health_check_retries_on_connection_error(self):
        """Retries on connection refused, eventually succeeds."""
        import urllib.error
        manager = _make_manager()

        ok_resp = MagicMock()
        ok_resp.read.return_value = b'{"status": "ok"}'
        ok_resp.__enter__ = lambda s: s
        ok_resp.__exit__ = MagicMock(return_value=False)

        side_effects = [
            urllib.error.URLError("Connection refused"),
            urllib.error.URLError("Connection refused"),
            ok_resp,
        ]

        with patch("urllib.request.urlopen", side_effect=side_effects):
            with patch("time.sleep"):  # Speed up polling
                manager._wait_for_health(port=8222, timeout=10)

    def test_health_check_raises_on_timeout(self):
        """Raises SessionManagerError when timeout expires."""
        import urllib.error
        manager = _make_manager()
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            with patch("time.sleep"):
                with patch("time.monotonic", side_effect=[0.0, 0.5, 1.0, 999.0]):
                    with pytest.raises((SessionManagerError, TimeoutError, RuntimeError, Exception)):
                        manager._wait_for_health(port=8222, timeout=1)


# ---------------------------------------------------------------------------
# test_cleanup_deletes_clone
# ---------------------------------------------------------------------------


@needs_module
class TestCleanupDeletesClone:
    """SessionManager._cleanup() shuts down and deletes the cloned simulator."""

    def test_cleanup_shuts_down_simulator(self):
        manager = _make_manager()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok_proc()
            manager._cleanup(udid=_CLONE_UDID)
        all_cmds = [c[0][0] for c in mock_run.call_args_list]
        shutdown_calls = [cmd for cmd in all_cmds if "shutdown" in cmd]
        assert shutdown_calls, "Expected simctl shutdown call"

    def test_cleanup_deletes_simulator(self):
        manager = _make_manager()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok_proc()
            manager._cleanup(udid=_CLONE_UDID)
        all_cmds = [c[0][0] for c in mock_run.call_args_list]
        delete_calls = [cmd for cmd in all_cmds if "delete" in cmd]
        assert delete_calls, "Expected simctl delete call"

    def test_cleanup_passes_clone_udid(self):
        manager = _make_manager()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok_proc()
            manager._cleanup(udid=_CLONE_UDID)
        all_args = " ".join(
            str(a) for c in mock_run.call_args_list for a in c[0][0]
        )
        assert _CLONE_UDID in all_args

    def test_cleanup_shutdown_before_delete(self):
        """simctl shutdown must be called before simctl delete."""
        manager = _make_manager()
        call_order = []

        def track_call(cmd, **_kwargs):
            if "shutdown" in cmd:
                call_order.append("shutdown")
            elif "delete" in cmd:
                call_order.append("delete")
            return _ok_proc()

        with patch("subprocess.run", side_effect=track_call):
            manager._cleanup(udid=_CLONE_UDID)

        if len(call_order) >= 2:
            assert call_order.index("shutdown") < call_order.index("delete"), \
                "shutdown must come before delete"

    def test_cleanup_tolerates_shutdown_failure(self):
        """Shutdown failure (sim already gone) must not prevent delete."""
        manager = _make_manager()
        call_order = []

        def side_effect(cmd, **_kwargs):
            if "shutdown" in cmd:
                call_order.append("shutdown")
                return _ok_proc(returncode=1)  # shutdown fails — sim already off
            if "delete" in cmd:
                call_order.append("delete")
                return _ok_proc()
            return _ok_proc()

        with patch("subprocess.run", side_effect=side_effect):
            manager._cleanup(udid=_CLONE_UDID)  # Must not raise

        assert "delete" in call_order, "delete must still be called even if shutdown fails"


# ---------------------------------------------------------------------------
# test_full_lifecycle
# ---------------------------------------------------------------------------


@needs_module
class TestFullLifecycle:
    """SessionManager.start() → health check → stop() → cleanup integration."""

    def test_start_returns_test_session(self):
        manager = _make_manager()
        with (
            patch.object(manager, "_clone", return_value=_CLONE_UDID),
            patch.object(manager, "_boot"),
            patch.object(manager, "_install"),
            patch.object(manager, "_deploy_runner", return_value=MagicMock(pid=1)),
            patch.object(manager, "_wait_for_health"),
        ):
            session = manager.start(source_udid=_SOURCE_UDID)

        assert session is not None
        assert session.udid == _CLONE_UDID

    def test_start_calls_lifecycle_in_order(self):
        """clone → boot → install → deploy → wait_for_health."""
        manager = _make_manager()
        call_order = []

        def track(name):
            def _inner(*args, **kwargs):
                call_order.append(name)
                if name == "_deploy_runner":
                    return MagicMock(pid=1)
            return _inner

        with (
            patch.object(manager, "_clone", side_effect=lambda **kw: (call_order.append("clone") or _CLONE_UDID)),
            patch.object(manager, "_boot", side_effect=track("boot")),
            patch.object(manager, "_install", side_effect=track("install")),
            patch.object(manager, "_deploy_runner", side_effect=track("deploy_runner")),
            patch.object(manager, "_wait_for_health", side_effect=track("wait_for_health")),
        ):
            manager.start(source_udid=_SOURCE_UDID)

        # Verify ordering constraints
        assert call_order.index("boot") > call_order.index("clone")
        assert call_order.index("install") > call_order.index("boot")

    def test_stop_calls_cleanup_with_clone_udid(self):
        manager = _make_manager()
        with (
            patch.object(manager, "_clone", return_value=_CLONE_UDID),
            patch.object(manager, "_boot"),
            patch.object(manager, "_install"),
            patch.object(manager, "_deploy_runner", return_value=MagicMock(pid=1)),
            patch.object(manager, "_wait_for_health"),
        ):
            session = manager.start(source_udid=_SOURCE_UDID)

        with patch.object(manager, "_cleanup") as mock_cleanup:
            manager.stop(session)
        mock_cleanup.assert_called_once_with(udid=_CLONE_UDID)

    def test_stop_terminates_runner_process(self):
        manager = _make_manager()
        mock_proc = MagicMock(pid=12345)
        with (
            patch.object(manager, "_clone", return_value=_CLONE_UDID),
            patch.object(manager, "_boot"),
            patch.object(manager, "_install"),
            patch.object(manager, "_deploy_runner", return_value=mock_proc),
            patch.object(manager, "_wait_for_health"),
        ):
            session = manager.start(source_udid=_SOURCE_UDID)

        with patch.object(manager, "_cleanup"):
            manager.stop(session)

        mock_proc.terminate.assert_called()

    def test_clone_failure_raises_before_boot(self):
        manager = _make_manager()
        with (
            patch.object(manager, "_clone", side_effect=RuntimeError("clone failed")),
            patch.object(manager, "_boot") as mock_boot,
        ):
            with pytest.raises(Exception):
                manager.start(source_udid=_SOURCE_UDID)
        mock_boot.assert_not_called()

    def test_health_timeout_cleans_up_clone(self):
        """If health check times out, the cloned simulator must still be deleted."""
        manager = _make_manager()
        with (
            patch.object(manager, "_clone", return_value=_CLONE_UDID),
            patch.object(manager, "_boot"),
            patch.object(manager, "_install"),
            patch.object(manager, "_deploy_runner", return_value=MagicMock(pid=1)),
            patch.object(manager, "_wait_for_health", side_effect=TimeoutError("timed out")),
            patch.object(manager, "_cleanup") as mock_cleanup,
        ):
            with pytest.raises(Exception):
                manager.start(source_udid=_SOURCE_UDID)
        mock_cleanup.assert_called_once_with(udid=_CLONE_UDID)
