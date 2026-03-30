"""Tests for M1: SimulatorDriver — the main facade that composes all sub-modules.

TDD Phase — INIT-2026-492.
These tests are written BEFORE the implementation exists and must be importable
even when the implementation modules are absent.

Module under test (to be created by CodeAtlas):
  specterqa/ios/drivers/simulator/driver.py — SimulatorDriver
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard — tests are importable even if impl is missing.
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.drivers.simulator.driver import SimulatorDriver  # type: ignore[import]
    _DRIVER_AVAILABLE = True
except ImportError:
    _DRIVER_AVAILABLE = False
    SimulatorDriver = None  # type: ignore[assignment,misc]

needs_driver = pytest.mark.skipif(
    not _DRIVER_AVAILABLE,
    reason="specterqa.ios.drivers.simulator.driver not yet implemented",
)

# ---------------------------------------------------------------------------
# Minimal config helpers
# ---------------------------------------------------------------------------

def _minimal_config() -> dict[str, Any]:
    """Minimal valid config: only required keys."""
    return {
        "device_id": "booted",
        "bundle_id": "com.example.testapp",
    }


def _full_config() -> dict[str, Any]:
    """Full config with all optional keys set explicitly."""
    return {
        "device_id": "ABCD-1234-UUID",
        "bundle_id": "com.example.myapp",
        "device_name": "iPhone 15 Pro",
        "screenshot_resize_width": 800,
        "title_bar_offset": 32,
        "log_subsystem": "com.example.myapp",
        "enable_network_capture": True,
        "enable_perf_monitoring": True,
        "enable_crash_detection": True,
    }


# ---------------------------------------------------------------------------
# Shared patch context: all sub-module constructors replaced with MagicMocks.
# ---------------------------------------------------------------------------

_SUB_MODULE_PATCHES = [
    "specterqa.ios.drivers.simulator.driver.InteractionLayer",
    "specterqa.ios.drivers.simulator.driver.ScreenCapture",
    "specterqa.ios.drivers.simulator.driver.ConsoleMonitor",
    "specterqa.ios.drivers.simulator.driver.NetworkInspector",
    "specterqa.ios.drivers.simulator.driver.PerfProfiler",
    "specterqa.ios.drivers.simulator.driver.StateInspector",
    "specterqa.ios.drivers.simulator.driver.CrashDetector",
    "specterqa.ios.drivers.simulator.driver.SimulatorAIContext",
    "specterqa.ios.drivers.simulator.driver.DataRedactor",
]


def _build_driver_with_mocks(config: dict[str, Any] | None = None):
    """Construct a SimulatorDriver with all sub-modules mocked.

    Returns (driver, mock_dict) where mock_dict maps sub-module class name
    to the MagicMock that replaced the constructor.
    """
    if config is None:
        config = _full_config()

    mocks: dict[str, MagicMock] = {}
    patchers = []
    for target in _SUB_MODULE_PATCHES:
        class_name = target.split(".")[-1]
        m = MagicMock(name=class_name)
        patcher = patch(target, m)
        patcher.start()
        patchers.append(patcher)
        mocks[class_name] = m

    driver = SimulatorDriver(config)

    for patcher in patchers:
        patcher.stop()

    return driver, mocks


# ===========================================================================
#  Group 1: Constructor and sub-module composition
# ===========================================================================


@needs_driver
class TestSimulatorDriverConstructor:
    """SimulatorDriver constructor creates and wires all sub-modules."""

    def test_constructor_creates_all_sub_modules(self):
        """All eight sub-modules are instantiated during __init__."""
        config = _full_config()

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer") as MockInteraction, \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture") as MockCapture, \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor") as MockConsole, \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector") as MockNetwork, \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler") as MockPerf, \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector") as MockState, \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector") as MockCrash, \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext") as MockAICtx, \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor") as MockRedactor:

            driver = SimulatorDriver(config)

        MockInteraction.assert_called_once()
        MockCapture.assert_called_once()
        MockConsole.assert_called_once()
        MockNetwork.assert_called_once()
        MockPerf.assert_called_once()
        MockState.assert_called_once()
        MockCrash.assert_called_once()
        MockAICtx.assert_called_once()

    def test_constructor_uses_default_resize_width(self):
        """When screenshot_resize_width is absent, ScreenCapture receives 1024."""
        config = _minimal_config()

        with patch("specterqa.ios.drivers.simulator.driver.ScreenCapture") as MockCapture, \
             patch("specterqa.ios.drivers.simulator.driver.InteractionLayer"), \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor"), \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector"), \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext"), \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"):

            driver = SimulatorDriver(config)

        # ScreenCapture must be called with resize_width=1024 (default)
        call_kwargs = MockCapture.call_args
        all_args = list(call_kwargs.args) + list(call_kwargs.kwargs.values())
        assert 1024 in all_args or call_kwargs.kwargs.get("resize_width") == 1024, (
            f"Expected default resize_width=1024; ScreenCapture called with {call_kwargs}"
        )

    def test_constructor_uses_default_title_bar_offset(self):
        """When title_bar_offset is absent, InteractionLayer receives 28."""
        config = _minimal_config()

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer") as MockInteraction, \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture"), \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor"), \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector"), \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext"), \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"):

            driver = SimulatorDriver(config)

        call_kwargs = MockInteraction.call_args
        all_values = list(call_kwargs.args) + list(call_kwargs.kwargs.values())
        assert 28 in all_values or call_kwargs.kwargs.get("title_bar_offset") == 28, (
            f"Expected default title_bar_offset=28; InteractionLayer called with {call_kwargs}"
        )

    def test_constructor_minimal_config_does_not_raise(self):
        """A config with only device_id and bundle_id constructs without error."""
        config = _minimal_config()

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer"), \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture"), \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor"), \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector"), \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext"), \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"):

            driver = SimulatorDriver(config)  # must not raise

        assert driver is not None

    def test_data_redactor_shared_across_network_and_ai_context(self):
        """A single DataRedactor instance is passed to both NetworkInspector and SimulatorAIContext."""
        config = _full_config()
        sentinel_redactor = MagicMock(name="SharedRedactor")

        with patch("specterqa.ios.drivers.simulator.driver.DataRedactor", return_value=sentinel_redactor) as MockRedactor, \
             patch("specterqa.ios.drivers.simulator.driver.InteractionLayer"), \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture"), \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor"), \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector") as MockNetwork, \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector"), \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext") as MockAICtx:

            driver = SimulatorDriver(config)

        # Both NetworkInspector and SimulatorAIContext must receive the same redactor object
        network_call = MockNetwork.call_args
        ai_ctx_call = MockAICtx.call_args

        network_args = list(network_call.args) + list(network_call.kwargs.values())
        ai_ctx_args = list(ai_ctx_call.args) + list(ai_ctx_call.kwargs.values())

        assert sentinel_redactor in network_args, (
            "DataRedactor not passed to NetworkInspector"
        )
        assert sentinel_redactor in ai_ctx_args, (
            "DataRedactor not passed to SimulatorAIContext"
        )


# ===========================================================================
#  Group 2: ActionExecutor protocol methods
# ===========================================================================


@needs_driver
class TestSimulatorDriverActionMethods:
    """screenshot(), click(), fill(), scroll(), keyboard(), wait() delegation."""

    def _make_driver(self):
        """Return a SimulatorDriver with all sub-modules as MagicMocks."""
        config = _full_config()
        mocks: dict[str, MagicMock] = {}
        patchers = []
        for target in _SUB_MODULE_PATCHES:
            class_name = target.split(".")[-1]
            m = MagicMock(name=class_name)
            patchers.append(patch(target, m))
            mocks[class_name] = m

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer") as MockInteraction, \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture") as MockCapture, \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor"), \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector"), \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext"), \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"):

            driver = SimulatorDriver(_full_config())

        return driver

    def test_screenshot_delegates_to_capture(self):
        """screenshot() calls self._capture.capture() and returns a dict."""
        config = _full_config()
        fake_capture_result = {
            "base64": "abc123",
            "width": 1024,
            "height": 2048,
            "timestamp": 12345.0,
            "raw_path": "/tmp/shot.png",
        }

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer"), \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture") as MockCapture, \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor"), \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector"), \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext"), \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"):

            mock_capture_instance = MockCapture.return_value
            mock_capture_instance.capture.return_value = fake_capture_result
            driver = SimulatorDriver(config)

        result = driver.screenshot()

        mock_capture_instance.capture.assert_called_once()
        assert isinstance(result, dict)

    def test_click_delegates_to_interaction_tap(self):
        """click(x, y) calls self._interaction.tap(x, y, img_w, img_h)."""
        config = _full_config()
        fake_capture_result = {
            "base64": "abc123",
            "width": 1170,
            "height": 2532,
            "timestamp": 12345.0,
            "raw_path": "/tmp/shot.png",
        }

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer") as MockInteraction, \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture") as MockCapture, \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor"), \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector"), \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext"), \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"):

            mock_capture_instance = MockCapture.return_value
            mock_capture_instance.capture.return_value = fake_capture_result
            mock_interaction_instance = MockInteraction.return_value
            driver = SimulatorDriver(config)

        # Take a screenshot first so dimensions are cached
        driver.screenshot()
        result = driver.click(200, 450)

        mock_interaction_instance.tap.assert_called_once()
        tap_args = mock_interaction_instance.tap.call_args
        all_args = list(tap_args.args) + list(tap_args.kwargs.values())
        assert 200 in all_args, "x coordinate not passed to tap()"
        assert 450 in all_args, "y coordinate not passed to tap()"
        assert isinstance(result, dict)

    def test_fill_delegates_to_interaction_type_text(self):
        """fill(text) calls self._interaction.type_text(text)."""
        config = _full_config()

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer") as MockInteraction, \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture"), \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor"), \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector"), \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext"), \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"):

            mock_interaction_instance = MockInteraction.return_value
            driver = SimulatorDriver(config)

        result = driver.fill("hello world")

        mock_interaction_instance.type_text.assert_called_once_with("hello world")
        assert isinstance(result, dict)

    def test_scroll_delegates_to_interaction_swipe(self):
        """scroll(direction, amount) calls self._interaction.swipe() with mapped coordinates."""
        config = _full_config()

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer") as MockInteraction, \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture"), \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor"), \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector"), \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext"), \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"):

            mock_interaction_instance = MockInteraction.return_value
            driver = SimulatorDriver(config)

        result = driver.scroll("down", 3)

        mock_interaction_instance.swipe.assert_called_once()
        assert isinstance(result, dict)

    def test_keyboard_delegates_to_interaction_press_key(self):
        """keyboard(key) calls self._interaction.press_key(key)."""
        config = _full_config()

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer") as MockInteraction, \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture"), \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor"), \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector"), \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext"), \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"):

            mock_interaction_instance = MockInteraction.return_value
            driver = SimulatorDriver(config)

        result = driver.keyboard("enter")

        mock_interaction_instance.press_key.assert_called_once_with("enter")
        assert isinstance(result, dict)

    def test_wait_sleeps_for_specified_duration(self):
        """wait(seconds) sleeps for the given number of seconds."""
        config = _full_config()

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer"), \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture"), \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor"), \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector"), \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext"), \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"):

            driver = SimulatorDriver(config)

        with patch("time.sleep") as mock_sleep:
            result = driver.wait(2.5)

        mock_sleep.assert_called_once_with(2.5)
        assert isinstance(result, dict)

    def test_action_methods_return_dict_with_success_and_action(self):
        """All action methods return a dict containing 'success' and 'action' keys."""
        config = _full_config()
        fake_capture_result = {
            "base64": "abc",
            "width": 1024,
            "height": 2048,
            "timestamp": 1.0,
            "raw_path": "/tmp/s.png",
        }

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer"), \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture") as MockCapture, \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor"), \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector"), \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext"), \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"):

            MockCapture.return_value.capture.return_value = fake_capture_result
            driver = SimulatorDriver(config)

        with patch("time.sleep"):
            screenshot_result = driver.screenshot()
            fill_result = driver.fill("test")
            keyboard_result = driver.keyboard("enter")
            scroll_result = driver.scroll("up")
            wait_result = driver.wait(0.1)

        for name, result in [
            ("screenshot", screenshot_result),
            ("fill", fill_result),
            ("keyboard", keyboard_result),
            ("scroll", scroll_result),
            ("wait", wait_result),
        ]:
            assert isinstance(result, dict), f"{name}() must return a dict"
            assert "success" in result, f"{name}() result missing 'success' key"
            assert "action" in result, f"{name}() result missing 'action' key"


# ===========================================================================
#  Group 3: Driver lifecycle
# ===========================================================================


@needs_driver
class TestSimulatorDriverLifecycle:
    """start(), stop(), launch_app(), terminate_app() lifecycle methods."""

    def test_start_boots_simulator_via_simctl(self):
        """start() runs xcrun simctl boot <device_id>."""
        config = _full_config()

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer"), \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture"), \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor") as MockConsole, \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector") as MockNetwork, \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector") as MockCrash, \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext"), \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"), \
             patch("subprocess.run") as mock_run:

            mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
            driver = SimulatorDriver(config)
            driver.start()

        # Verify xcrun simctl boot was called with the device_id
        boot_calls = [
            c for c in mock_run.call_args_list
            if "boot" in (c.args[0] if c.args else [])
        ]
        # At least one call should contain 'simctl' and 'boot'
        all_simctl_cmds = [
            c.args[0] if c.args else []
            for c in mock_run.call_args_list
        ]
        boot_found = any(
            "boot" in cmd and "simctl" in " ".join(str(x) for x in cmd)
            for cmd in all_simctl_cmds
        )
        assert boot_found, (
            f"xcrun simctl boot not called during start(). "
            f"All calls: {all_simctl_cmds}"
        )

    def test_start_starts_console_monitor(self):
        """start() calls console.start() to begin log streaming."""
        config = _full_config()

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer"), \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture"), \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor") as MockConsole, \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector") as MockNetwork, \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector") as MockCrash, \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext"), \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"), \
             patch("subprocess.run") as mock_run:

            mock_run.return_value = MagicMock(returncode=0)
            mock_console = MockConsole.return_value
            driver = SimulatorDriver(config)
            driver.start()

        mock_console.start.assert_called_once()

    def test_start_starts_all_monitors(self):
        """start() starts console, network, crash monitors (and perf if enabled)."""
        config = _full_config()

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer"), \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture"), \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor") as MockConsole, \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector") as MockNetwork, \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler") as MockPerf, \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector") as MockCrash, \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext"), \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"), \
             patch("subprocess.run") as mock_run:

            mock_run.return_value = MagicMock(returncode=0)
            mock_console = MockConsole.return_value
            mock_network = MockNetwork.return_value
            mock_crash = MockCrash.return_value
            driver = SimulatorDriver(config)
            driver.start()

        mock_console.start.assert_called_once()
        mock_network.start.assert_called_once()
        mock_crash.start.assert_called_once()

    def test_stop_stops_all_monitors(self):
        """stop() calls stop() on console, network, and crash monitors."""
        config = _full_config()

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer"), \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture"), \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor") as MockConsole, \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector") as MockNetwork, \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector") as MockCrash, \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext"), \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"), \
             patch("subprocess.run") as mock_run:

            mock_run.return_value = MagicMock(returncode=0)
            mock_console = MockConsole.return_value
            mock_network = MockNetwork.return_value
            mock_crash = MockCrash.return_value
            driver = SimulatorDriver(config)
            driver.start()
            driver.stop()

        mock_console.stop.assert_called_once()
        mock_network.stop.assert_called_once()
        mock_crash.stop.assert_called_once()

    def test_launch_app_calls_simctl_launch(self):
        """launch_app() runs xcrun simctl launch <device_id> <bundle_id>."""
        config = _full_config()

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer"), \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture"), \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor"), \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector"), \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext"), \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"), \
             patch("subprocess.run") as mock_run:

            mock_run.return_value = MagicMock(returncode=0)
            driver = SimulatorDriver(config)
            driver.launch_app()

        all_cmds = [
            c.args[0] if c.args else []
            for c in mock_run.call_args_list
        ]
        launch_found = any(
            "launch" in cmd
            and config["bundle_id"] in cmd
            for cmd in all_cmds
        )
        assert launch_found, (
            f"xcrun simctl launch {config['bundle_id']} not found. "
            f"All calls: {all_cmds}"
        )

    def test_terminate_app_calls_simctl_terminate(self):
        """terminate_app() runs xcrun simctl terminate <device_id> <bundle_id>."""
        config = _full_config()

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer"), \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture"), \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor"), \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector"), \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext"), \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"), \
             patch("subprocess.run") as mock_run:

            mock_run.return_value = MagicMock(returncode=0)
            driver = SimulatorDriver(config)
            driver.terminate_app()

        all_cmds = [
            c.args[0] if c.args else []
            for c in mock_run.call_args_list
        ]
        terminate_found = any(
            "terminate" in cmd
            and config["bundle_id"] in cmd
            for cmd in all_cmds
        )
        assert terminate_found, (
            f"xcrun simctl terminate {config['bundle_id']} not found. "
            f"All calls: {all_cmds}"
        )


# ===========================================================================
#  Group 4: Context aggregation
# ===========================================================================


@needs_driver
class TestSimulatorDriverContextAggregation:
    """get_context() aggregates from all sub-modules via ai_context.build_context()."""

    def test_get_context_calls_ai_context_build_context(self):
        """get_context() delegates to self._ai_context.build_context()."""
        config = _full_config()

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer"), \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture") as MockCapture, \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor"), \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector"), \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext") as MockAICtx, \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"):

            fake_capture = {"base64": "x", "width": 1024, "height": 2048, "timestamp": 0.0, "raw_path": ""}
            MockCapture.return_value.capture.return_value = fake_capture

            mock_ai_ctx = MockAICtx.return_value
            mock_ai_ctx.build_context.return_value = MagicMock(
                screenshot_base64="x",
                recent_logs=[],
                active_requests=[],
                perf_snapshot=None,
                app_state={},
                crashes=[],
            )

            driver = SimulatorDriver(config)

        result = driver.get_context()

        mock_ai_ctx.build_context.assert_called_once()

    def test_get_context_returns_dict(self):
        """get_context() returns a dict (serialisable form of DriverContext)."""
        config = _full_config()

        from unittest.mock import MagicMock

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer"), \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture") as MockCapture, \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor"), \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector"), \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext") as MockAICtx, \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"):

            fake_capture = {"base64": "x", "width": 1024, "height": 2048, "timestamp": 0.0, "raw_path": ""}
            MockCapture.return_value.capture.return_value = fake_capture

            mock_ctx = MagicMock()
            mock_ctx.screenshot_base64 = "x"
            mock_ctx.recent_logs = []
            mock_ctx.active_requests = []
            mock_ctx.perf_snapshot = None
            mock_ctx.app_state = {}
            mock_ctx.crashes = []
            MockAICtx.return_value.build_context.return_value = mock_ctx

            driver = SimulatorDriver(config)

        result = driver.get_context()

        assert isinstance(result, dict), "get_context() must return a dict"

    def test_get_context_contains_expected_keys(self):
        """get_context() dict has keys: screenshot, logs, network, perf, state, crashes."""
        config = _full_config()

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer"), \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture") as MockCapture, \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor"), \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector"), \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext") as MockAICtx, \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"):

            fake_capture = {"base64": "x", "width": 1024, "height": 2048, "timestamp": 0.0, "raw_path": ""}
            MockCapture.return_value.capture.return_value = fake_capture

            mock_ctx = MagicMock()
            mock_ctx.screenshot_base64 = "screenshot_data"
            mock_ctx.recent_logs = ["log1"]
            mock_ctx.active_requests = []
            mock_ctx.perf_snapshot = None
            mock_ctx.app_state = {"has_auth_token": False}
            mock_ctx.crashes = []
            MockAICtx.return_value.build_context.return_value = mock_ctx

            driver = SimulatorDriver(config)

        result = driver.get_context()

        # The dict should contain keys corresponding to the DriverContext fields
        # (exact naming may vary; we check for presence of key concepts)
        expected_keys = {"screenshot", "logs", "network", "perf", "state", "crashes"}
        result_keys_lower = {k.lower() for k in result.keys()}
        missing = expected_keys - result_keys_lower
        assert not missing, (
            f"get_context() result is missing expected keys: {missing}. "
            f"Got: {set(result.keys())}"
        )


# ===========================================================================
#  Group 5: Resilience and property access
# ===========================================================================


@needs_driver
class TestSimulatorDriverResilience:
    """Error isolation and sub-module property access."""

    def test_sub_module_error_does_not_crash_driver(self):
        """An exception in one sub-module's method doesn't propagate from action methods."""
        config = _full_config()

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer") as MockInteraction, \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture") as MockCapture, \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor"), \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector"), \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext"), \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"):

            MockCapture.return_value.capture.side_effect = RuntimeError("simctl failed")
            driver = SimulatorDriver(config)

        # screenshot() must not propagate the RuntimeError — it should return
        # a dict with success=False instead
        result = driver.screenshot()
        assert isinstance(result, dict), "screenshot() must return dict even on error"
        assert result.get("success") is False, (
            "screenshot() must return success=False when capture fails"
        )

    def test_driver_exposes_sub_modules_as_properties(self):
        """Driver exposes sub-modules via properties for direct test access."""
        config = _full_config()

        with patch("specterqa.ios.drivers.simulator.driver.InteractionLayer"), \
             patch("specterqa.ios.drivers.simulator.driver.ScreenCapture"), \
             patch("specterqa.ios.drivers.simulator.driver.ConsoleMonitor"), \
             patch("specterqa.ios.drivers.simulator.driver.NetworkInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.PerfProfiler"), \
             patch("specterqa.ios.drivers.simulator.driver.StateInspector"), \
             patch("specterqa.ios.drivers.simulator.driver.CrashDetector"), \
             patch("specterqa.ios.drivers.simulator.driver.SimulatorAIContext"), \
             patch("specterqa.ios.drivers.simulator.driver.DataRedactor"):

            driver = SimulatorDriver(config)

        # Each sub-module must be accessible, either as a property or
        # as an attribute with an underscore-prefixed name
        sub_module_names = [
            "_interaction", "_capture", "_console",
            "_network", "_perf", "_state", "_crash", "_ai_context",
        ]
        for attr in sub_module_names:
            assert hasattr(driver, attr) or hasattr(driver, attr.lstrip("_")), (
                f"Driver must expose sub-module via {attr!r} (or public variant)"
            )
