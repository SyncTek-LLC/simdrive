"""Tests verifying WDA fallback behaviour in SoM pipeline (INIT-2026-508/509).

INIT-2026-508 removed WDA as the primary backend.
INIT-2026-509 restored it as an *optional* fallback with:
  - A clear warning directing users to build the runner
  - 10-second timeout per attempt
  - Maximum 2 retries

The XCTest runner remains the primary (preferred) backend.
WDA is only used when use_xctest_runner=False AND wda_url is provided.
Neither WDA URL nor session_id are mandatory — they are optional fallback params.
"""
import inspect
import json
import subprocess
import pytest
from unittest.mock import patch, MagicMock, call
from specterqa.ios.som_runner import SoMRunner
from specterqa.ios.som_annotator import SoMAnnotator


class TestSoMRunnerWDAFallback:
    def test_wda_url_parameter_is_optional(self):
        """SoMRunner.__init__ accepts optional wda_url (None by default)."""
        sig = inspect.signature(SoMRunner.__init__)
        assert "wda_url" in sig.parameters
        assert sig.parameters["wda_url"].default is None

    def test_start_legacy_method_exists(self):
        """_start_legacy must exist for the WDA fallback path."""
        assert hasattr(SoMRunner, "_start_legacy")

    def test_start_raises_without_runner_or_wda(self):
        """start() raises RuntimeError when runner not available and no WDA URL.

        Patches _start_xctest to simulate the XCTest runner not being built,
        and wda_url is not provided, so no fallback is available.
        """
        runner = SoMRunner(api_key="test")
        with patch.object(
            SoMRunner, "_start_xctest", side_effect=RuntimeError("XCTest runner not built. Run: specterqa-ios runner build")
        ):
            with pytest.raises(RuntimeError, match="runner"):
                runner.start("com.test.app")

    def test_start_raises_with_use_xctest_false_and_no_wda(self):
        """Explicitly disabling xctest runner without wda_url raises error."""
        runner = SoMRunner(api_key="test", use_xctest_runner=False)
        with pytest.raises(RuntimeError, match="runner"):
            runner.start("com.test.app")

    def test_start_uses_legacy_when_xctest_disabled_and_wda_provided(self):
        """When use_xctest_runner=False and wda_url is set, _start_legacy is called."""
        runner = SoMRunner(api_key="test", use_xctest_runner=False, wda_url="http://localhost:8100")
        with (
            patch.object(SoMRunner, "_start_legacy") as mock_legacy,
            patch("anthropic.Anthropic"),
        ):
            runner.start("com.test.app")
        mock_legacy.assert_called_once()

    def test_xctest_takes_priority_over_wda(self):
        """When use_xctest_runner=True, _start_xctest is called even if wda_url is set."""
        runner = SoMRunner(api_key="test", use_xctest_runner=True, wda_url="http://localhost:8100")
        with (
            patch.object(SoMRunner, "_start_xctest") as mock_xctest,
            patch("anthropic.Anthropic"),
        ):
            runner.start("com.test.app")
        mock_xctest.assert_called_once()


class TestSoMAnnotatorWDAFallback:
    def test_wda_url_parameter_is_optional(self):
        """SoMAnnotator.__init__ accepts optional wda_url (None by default)."""
        sig = inspect.signature(SoMAnnotator.__init__)
        assert "wda_url" in sig.parameters
        assert sig.parameters["wda_url"].default is None

    def test_session_id_parameter_is_optional(self):
        """SoMAnnotator.__init__ accepts optional session_id (None by default)."""
        sig = inspect.signature(SoMAnnotator.__init__)
        assert "session_id" in sig.parameters
        assert sig.parameters["session_id"].default is None

    def test_wda_method_exists(self):
        """_get_element_tree_from_wda must exist for optional fallback."""
        assert hasattr(SoMAnnotator, "_get_element_tree_from_wda")

    def test_raises_without_runner_url_or_wda(self):
        """get_element_tree raises when neither runner_url nor wda params are set."""
        annotator = SoMAnnotator()
        with pytest.raises(RuntimeError, match="runner"):
            annotator.get_element_tree()

    def test_runner_url_takes_priority_over_wda(self):
        """When both runner_url and wda params are set, runner is used."""
        annotator = SoMAnnotator(
            runner_url="http://localhost:8222",
            wda_url="http://localhost:8100",
            session_id="abc",
        )
        with patch.object(annotator, "_get_element_tree_from_runner", return_value="<xml/>") as mock_runner:
            result = annotator.get_element_tree()
        mock_runner.assert_called_once()
        assert result == "<xml/>"


class TestNoWDAImportsInHotPath:
    def test_som_runner_no_wda_import_at_module_level(self):
        """som_runner.py must not import wda_driver at module level.

        The WDA import is lazy (inside _start_legacy) so it does not increase
        cold-start cost or break environments without WDA installed.
        """
        import specterqa.ios.som_runner as mod
        source = inspect.getsource(mod)
        # Module-level code is everything before the first class definition.
        # The lazy import inside _start_legacy is acceptable and expected.
        assert "from specterqa.ios.wda_driver" not in source.split("class")[0]


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
