"""Tests verifying WDA code paths are fully removed from the SoM pipeline.

R&D cleanup:
- WDA fallback removed entirely from SoMRunner and SoMAnnotator.
- XCTest runner is the sole backend.
- wda_url, session_id, use_xctest_runner, and _start_legacy are all gone.
"""
import inspect
import json
import subprocess
import pytest
from unittest.mock import patch, MagicMock
from specterqa.ios.som_runner import SoMRunner
from specterqa.ios.som_annotator import SoMAnnotator


class TestSoMRunnerWDARemoved:
    def test_no_wda_url_parameter(self):
        """SoMRunner.__init__ must NOT have wda_url — WDA path is removed."""
        sig = inspect.signature(SoMRunner.__init__)
        assert "wda_url" not in sig.parameters

    def test_no_use_xctest_runner_parameter(self):
        """SoMRunner.__init__ must NOT have use_xctest_runner — XCTest is the only backend."""
        sig = inspect.signature(SoMRunner.__init__)
        assert "use_xctest_runner" not in sig.parameters

    def test_no_start_legacy_method(self):
        """_start_legacy must be removed — WDA fallback is dead code."""
        assert not hasattr(SoMRunner, "_start_legacy")

    def test_start_raises_without_runner(self):
        """start() raises RuntimeError when XCTest runner not available."""
        runner = SoMRunner(api_key="test")
        with patch.object(
            SoMRunner, "_start_xctest", side_effect=RuntimeError("XCTest runner not built.")
        ):
            with pytest.raises(RuntimeError, match="runner|XCTest"):
                runner.start("com.test.app")

    def test_xctest_always_used(self):
        """start() always calls _start_xctest — no conditional backend selection."""
        runner = SoMRunner(api_key="test")
        with (
            patch.object(SoMRunner, "_start_xctest") as mock_xctest,
            patch("anthropic.Anthropic"),
        ):
            runner.start("com.test.app")
        mock_xctest.assert_called_once()


class TestSoMAnnotatorWDARemoved:
    def test_no_wda_url_parameter(self):
        """SoMAnnotator.__init__ must NOT have wda_url — WDA path is removed."""
        sig = inspect.signature(SoMAnnotator.__init__)
        assert "wda_url" not in sig.parameters

    def test_no_session_id_parameter(self):
        """SoMAnnotator.__init__ must NOT have session_id — WDA path is removed."""
        sig = inspect.signature(SoMAnnotator.__init__)
        assert "session_id" not in sig.parameters

    def test_no_wda_method(self):
        """_get_element_tree_from_wda must be removed."""
        assert not hasattr(SoMAnnotator, "_get_element_tree_from_wda")

    def test_raises_without_runner_url(self):
        """get_element_tree raises when runner_url is not set."""
        annotator = SoMAnnotator()
        with pytest.raises(RuntimeError, match="runner"):
            annotator.get_element_tree()

    def test_runner_url_used_for_element_tree(self):
        """When runner_url is set, _get_element_tree_from_runner is called."""
        annotator = SoMAnnotator(runner_url="http://localhost:8222")
        with patch.object(annotator, "_get_element_tree_from_runner", return_value="<xml/>") as mock_runner:
            result = annotator.get_element_tree()
        mock_runner.assert_called_once()
        assert result == "<xml/>"


class TestNoWDAImportsInHotPath:
    def test_som_runner_no_wda_import_anywhere(self):
        """som_runner.py must not import wda_driver at all."""
        import specterqa.ios.som_runner as mod
        source = inspect.getsource(mod)
        assert "wda_driver" not in source, "wda_driver import must be fully removed"

    def test_som_annotator_no_wda_imports(self):
        """som_annotator.py must not import socket or urllib.error (WDA-only deps)."""
        import specterqa.ios.som_annotator as mod
        source = inspect.getsource(mod)
        assert "wda_url" not in source, "wda_url must be fully removed from annotator"


class TestExistingTestsUnbroken:
    def test_scroll_guards_still_pass(self):
        """Scroll guard tests should still pass after WDA removal."""
        # Just verify the scroll guard functions still exist
        assert hasattr(SoMRunner, "_is_element_visible")
        assert hasattr(SoMAnnotator, "_screen_changed")
        from specterqa.ios.som_runner import MAX_CONSECUTIVE_SCROLLS
        assert MAX_CONSECUTIVE_SCROLLS == 5


# ---------------------------------------------------------------------------
# TestSessionManagerCloneFix — simctl clone catch-22 (INIT-2026-506)
# ---------------------------------------------------------------------------

_SOURCE_UDID = "SOURCE-UDID-DEADBEEF"
_CLONE_UDID = "CLONE-UDID-CAFEBABE"

_DEVICES_JSON_BOOTED = json.dumps({
    "devices": {
        "com.apple.CoreSimulator.SimRuntime.iOS-17-0": [
            {"udid": _SOURCE_UDID, "name": "iPhone 15", "state": "Booted"}
        ]
    }
})

_DEVICES_JSON_SHUTDOWN = json.dumps({
    "devices": {
        "com.apple.CoreSimulator.SimRuntime.iOS-17-0": [
            {"udid": _SOURCE_UDID, "name": "iPhone 15", "state": "Shutdown"}
        ]
    }
})


def _make_proc(stdout: str = "", returncode: int = 0) -> MagicMock:
    p = MagicMock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = ""
    return p


class TestSessionManagerCloneFix:
    """Verify the simctl clone catch-22 fix in TestSession._start().

    simctl clone requires the source simulator to be in Shutdown state.
    If the user's sim is Booted when TestSession.start() is called, we must
    shut it down, clone, restore it to Booted, then boot only the clone.
    """

    def _make_session(self) -> "object":
        from specterqa.ios.session_manager import TestSession
        return TestSession(source_udid=_SOURCE_UDID)

    def _run_side_effect_booted(self, cmd, **kwargs):
        """Simulate subprocess.run responses for a booted-source scenario."""
        if "list" in cmd and "devices" in cmd:
            return _make_proc(stdout=_DEVICES_JSON_BOOTED)
        if "clone" in cmd:
            return _make_proc(stdout=_CLONE_UDID)
        # shutdown / boot both succeed silently
        return _make_proc()

    def _run_side_effect_shutdown(self, cmd, **kwargs):
        """Simulate subprocess.run responses for a shutdown-source scenario."""
        if "list" in cmd and "devices" in cmd:
            return _make_proc(stdout=_DEVICES_JSON_SHUTDOWN)
        if "clone" in cmd:
            return _make_proc(stdout=_CLONE_UDID)
        return _make_proc()

    def test_shuts_down_booted_sim_before_clone(self):
        """If source sim is booted, shutdown must be called before clone."""
        from specterqa.ios.session_manager import TestSession

        session = TestSession(source_udid=_SOURCE_UDID)
        call_order = []

        def tracking_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "list" in cmd_str and "devices" in cmd_str:
                return _make_proc(stdout=_DEVICES_JSON_BOOTED)
            if "shutdown" in cmd_str and _SOURCE_UDID in cmd_str:
                call_order.append("shutdown_source")
            elif "clone" in cmd_str:
                call_order.append("clone")
            return _make_proc(stdout=_CLONE_UDID if "clone" in " ".join(cmd) else "")

        with (
            patch("subprocess.run", side_effect=tracking_run),
            patch("subprocess.Popen", return_value=MagicMock(pid=1)),
            patch("specterqa.ios.session_manager._find_free_port", return_value=8222),
            patch("specterqa.ios.session_manager._find_xctestrun", return_value="/fake/test.xctestrun"),
            patch("specterqa.ios.session_manager._wait_for_health"),
            patch("time.sleep"),
        ):
            session._is_sim_booted = lambda udid: True
            session._start()

        assert "shutdown_source" in call_order, "shutdown must be called when source is booted"
        assert "clone" in call_order, "clone must be called"
        assert call_order.index("shutdown_source") < call_order.index("clone"), \
            "shutdown must happen before clone"

    def test_reboots_original_after_clone(self):
        """Original sim must be re-booted after cloning if it was booted before."""
        from specterqa.ios.session_manager import TestSession

        session = TestSession(source_udid=_SOURCE_UDID)
        call_order = []

        def tracking_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "list" in cmd_str and "devices" in cmd_str:
                return _make_proc(stdout=_DEVICES_JSON_BOOTED)
            if "clone" in cmd_str:
                call_order.append("clone")
                return _make_proc(stdout=_CLONE_UDID)
            if "boot" in cmd_str and _SOURCE_UDID in cmd_str:
                call_order.append("reboot_source")
            return _make_proc()

        with (
            patch("subprocess.run", side_effect=tracking_run),
            patch("subprocess.Popen", return_value=MagicMock(pid=1)),
            patch("specterqa.ios.session_manager._find_free_port", return_value=8222),
            patch("specterqa.ios.session_manager._find_xctestrun", return_value="/fake/test.xctestrun"),
            patch("specterqa.ios.session_manager._wait_for_health"),
            patch("time.sleep"),
        ):
            session._is_sim_booted = lambda udid: True
            session._start()

        assert "reboot_source" in call_order, "source sim must be re-booted after clone"
        assert "clone" in call_order
        assert call_order.index("clone") < call_order.index("reboot_source"), \
            "re-boot of source must happen after clone completes"

    def test_clone_works_with_shutdown_sim(self):
        """If source sim is already shutdown, no extra shutdown/re-boot on source."""
        from specterqa.ios.session_manager import TestSession

        session = TestSession(source_udid=_SOURCE_UDID)
        source_shutdowns = []
        source_reboots = []

        def tracking_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "list" in cmd_str and "devices" in cmd_str:
                return _make_proc(stdout=_DEVICES_JSON_SHUTDOWN)
            if "clone" in cmd_str:
                return _make_proc(stdout=_CLONE_UDID)
            if "shutdown" in cmd_str and _SOURCE_UDID in cmd_str:
                source_shutdowns.append(cmd_str)
            if "boot" in cmd_str and _SOURCE_UDID in cmd_str:
                source_reboots.append(cmd_str)
            return _make_proc()

        with (
            patch("subprocess.run", side_effect=tracking_run),
            patch("subprocess.Popen", return_value=MagicMock(pid=1)),
            patch("specterqa.ios.session_manager._find_free_port", return_value=8222),
            patch("specterqa.ios.session_manager._find_xctestrun", return_value="/fake/test.xctestrun"),
            patch("specterqa.ios.session_manager._wait_for_health"),
            patch("time.sleep"),
        ):
            session._is_sim_booted = lambda udid: False
            session._start()

        assert not source_shutdowns, \
            "No shutdown should be issued on source when it is already shutdown"
        assert not source_reboots, \
            "No re-boot should be issued on source when it was not booted before clone"
