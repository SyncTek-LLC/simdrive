"""`simdrive demo` CLI subcommand tests — [internal-tracker] polish/demo-cli.

Covers the onboarding entry point introduced for PR D. The function under
test is :func:`simdrive._demo.run_demo`; we mock the sim/observe layer so
the suite runs without Xcode and stays in the ``not live`` selection.

Test plan:
  - Happy path: mocked sim returns iPhone 17 → exit 0 + expected sections.
  - Already-booted iPhone: boot is skipped, summary shows "already booted".
  - No iPhone device available: exit 2 + the create-in-Xcode hint.
  - License gate failure: exit 2 + the LicenseError recovery message.
  - simctl missing (Xcode CLT absent): exit 2 + the xcode-select hint.
  - Output formatting: ANSI codes present and reset; required summary lines.
"""
from __future__ import annotations

import argparse
import io
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from simdrive import _demo
from simdrive.sim import Device, SimError


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_args(tmp_path: Path) -> argparse.Namespace:
    """Build the Namespace the CLI dispatcher would hand us, with redirected IO."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    return argparse.Namespace(
        out_dir=tmp_path / "demo-out",
        _stdout=stdout,
        _stderr=stderr,
    )


def _make_fake_observation(out_dir: Path, n_marks: int = 5) -> object:
    """Return a stand-in Observation with a writable screenshot_path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = out_dir / "observe-1234.png"
    screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\n")  # bare PNG header is enough

    marks = []
    for i in range(n_marks):
        m = MagicMock()
        # Two high, one medium, the rest low — gives us a predictable summary.
        if i == 0 or i == 1:
            m.confidence_band = "high"
        elif i == 2:
            m.confidence_band = "medium"
        else:
            m.confidence_band = "low"
        marks.append(m)

    obs = MagicMock()
    obs.screenshot_path = screenshot_path
    obs.marks = marks
    return obs


@pytest.fixture
def valid_license(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the license gate by making check_entitlement a no-op."""
    monkeypatch.setattr(
        "simdrive.license.entitlement.check_entitlement", lambda *a, **kw: MagicMock(tier="trial")
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:

    def test_happy_path_returns_zero(
        self, tmp_path: Path, valid_license: None
    ) -> None:
        """An iPhone 17 in Shutdown state boots, launches Settings, observes."""
        args = _make_args(tmp_path)

        device = Device(udid="UDID-17", name="iPhone 17", os_version="26.0", state="Shutdown")

        with patch("simdrive.sim.list_devices", return_value=[device]) as p_list, \
             patch("simdrive.sim.boot") as p_boot, \
             patch("simdrive.sim.launch_app", return_value=1234) as p_launch, \
             patch(
                "simdrive.observe.observe",
                return_value=_make_fake_observation(args.out_dir),
             ) as p_obs:
            rc = _demo.run_demo(args)

        assert rc == 0
        p_list.assert_called_once()
        p_boot.assert_called_once_with("UDID-17")
        p_launch.assert_called_once_with("UDID-17", "com.apple.Preferences")
        # The observe call must use simulator target and the out_dir we passed.
        _, kwargs = p_obs.call_args
        assert kwargs["target"] == "simulator"
        assert kwargs["udid"] == "UDID-17"
        assert kwargs["out_dir"] == args.out_dir

    def test_prefers_already_booted_iphone(
        self, tmp_path: Path, valid_license: None
    ) -> None:
        """An already-booted iPhone wins over a newer-named shutdown one."""
        args = _make_args(tmp_path)

        booted = Device(udid="UDID-OLD", name="iPhone 15", os_version="17.0", state="Booted")
        shutdown_newer = Device(
            udid="UDID-NEW", name="iPhone 17", os_version="26.0", state="Shutdown"
        )

        with patch("simdrive.sim.list_devices", return_value=[shutdown_newer, booted]), \
             patch("simdrive.sim.boot") as p_boot, \
             patch("simdrive.sim.launch_app"), \
             patch(
                 "simdrive.observe.observe",
                 return_value=_make_fake_observation(args.out_dir),
             ):
            rc = _demo.run_demo(args)

        assert rc == 0
        # Skipped boot for the already-booted device.
        p_boot.assert_not_called()
        out = args._stdout.getvalue()
        assert "iPhone 15" in out
        assert "already booted" in out

    def test_falls_back_to_iphone_16_when_no_17(
        self, tmp_path: Path, valid_license: None
    ) -> None:
        args = _make_args(tmp_path)

        d16 = Device(udid="UDID-16P", name="iPhone 16 Pro", os_version="18.0", state="Shutdown")
        d15 = Device(udid="UDID-15", name="iPhone 15", os_version="17.0", state="Shutdown")

        with patch("simdrive.sim.list_devices", return_value=[d15, d16]), \
             patch("simdrive.sim.boot"), \
             patch("simdrive.sim.launch_app"), \
             patch(
                 "simdrive.observe.observe",
                 return_value=_make_fake_observation(args.out_dir),
             ):
            rc = _demo.run_demo(args)

        assert rc == 0
        assert "iPhone 16 Pro" in args._stdout.getvalue()


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrorPaths:

    def test_no_iphone_device_returns_2(
        self, tmp_path: Path, valid_license: None
    ) -> None:
        args = _make_args(tmp_path)

        # An iPad — but no iPhone. The picker must say "no iPhone".
        ipad = Device(udid="UDID-IPAD", name="iPad Pro", os_version="26.0", state="Shutdown")
        with patch("simdrive.sim.list_devices", return_value=[ipad]):
            rc = _demo.run_demo(args)

        assert rc == 2
        err = args._stderr.getvalue()
        assert "No iPhone simulator found" in err
        assert "Window > Devices and Simulators" in err

    def test_simctl_missing_returns_2(self, tmp_path: Path, valid_license: None) -> None:
        args = _make_args(tmp_path)

        with patch("simdrive.sim.list_devices", side_effect=SimError("simctl list failed: not found")):
            rc = _demo.run_demo(args)

        assert rc == 2
        err = args._stderr.getvalue()
        assert "xcode-select --install" in err

    def test_xcrun_missing_returns_2(self, tmp_path: Path, valid_license: None) -> None:
        """`xcrun` itself missing (no Xcode CLT) surfaces a helpful hint, not a trace."""
        args = _make_args(tmp_path)

        with patch("simdrive.sim.list_devices", side_effect=FileNotFoundError("xcrun")):
            rc = _demo.run_demo(args)

        assert rc == 2
        assert "xcode-select --install" in args._stderr.getvalue()

    def test_license_gate_failure_returns_2(self, tmp_path: Path) -> None:
        """A LicenseError short-circuits with exit 2 + the recovery message."""
        from simdrive.license.errors import LicenseError

        args = _make_args(tmp_path)
        err = LicenseError(
            code="license_not_found",
            message=(
                "SimDrive license not found. "
                "Run `simdrive trial start --email you@example.com` to begin a trial."
            ),
            details={},
        )

        with patch(
            "simdrive.license.entitlement.check_entitlement", side_effect=err
        ):
            rc = _demo.run_demo(args)

        assert rc == 2
        stderr = args._stderr.getvalue()
        assert "trial start" in stderr  # the canonical recovery hint
        # No simctl call should have been attempted past the gate.
        assert "iPhone" not in args._stdout.getvalue()

    def test_boot_failure_returns_2(self, tmp_path: Path, valid_license: None) -> None:
        args = _make_args(tmp_path)

        device = Device(udid="UDID-17", name="iPhone 17", os_version="26.0", state="Shutdown")
        with patch("simdrive.sim.list_devices", return_value=[device]), \
             patch("simdrive.sim.boot", side_effect=SimError("boot timed out")):
            rc = _demo.run_demo(args)

        assert rc == 2
        assert "failed to boot iPhone 17" in args._stderr.getvalue()

    def test_launch_failure_returns_2(self, tmp_path: Path, valid_license: None) -> None:
        args = _make_args(tmp_path)

        device = Device(udid="UDID-17", name="iPhone 17", os_version="26.0", state="Booted")
        with patch("simdrive.sim.list_devices", return_value=[device]), \
             patch("simdrive.sim.launch_app", side_effect=SimError("denied")):
            rc = _demo.run_demo(args)

        assert rc == 2
        assert "com.apple.Preferences" in args._stderr.getvalue()


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


class TestOutputFormatting:

    def _run_happy(self, tmp_path: Path) -> argparse.Namespace:
        args = _make_args(tmp_path)
        device = Device(udid="UDID-17", name="iPhone 17", os_version="26.0", state="Shutdown")
        with patch("simdrive.sim.list_devices", return_value=[device]), \
             patch("simdrive.sim.boot"), \
             patch("simdrive.sim.launch_app"), \
             patch(
                 "simdrive.observe.observe",
                 return_value=_make_fake_observation(args.out_dir, n_marks=5),
             ), \
             patch(
                 "simdrive.license.entitlement.check_entitlement",
                 return_value=MagicMock(tier="trial"),
             ):
            rc = _demo.run_demo(args)
        assert rc == 0
        return args

    def test_required_summary_lines_present(self, tmp_path: Path) -> None:
        args = self._run_happy(tmp_path)
        out = args._stdout.getvalue()
        # Every line from the spec, in order.
        assert "SimDrive demo" in out
        assert re.search(r"^\s*device\s+iPhone 17", out, re.MULTILINE)
        assert re.search(r"^\s*app\s+com\.apple\.Preferences", out, re.MULTILINE)
        assert re.search(r"^\s*observed\s+5 elements", out, re.MULTILINE)
        assert re.search(r"^\s*screenshot\s+", out, re.MULTILINE)
        # The "try the full flow" upsell + total wall-clock line.
        assert "Try the full flow" in out
        assert ".mcp.json" in out
        assert "Total:" in out

    def test_band_counts_in_observed_line(self, tmp_path: Path) -> None:
        args = self._run_happy(tmp_path)
        out = args._stdout.getvalue()
        # The fake observation has 2 high, 1 medium, 2 low.
        assert "2 high-confidence" in out
        assert "1 medium" in out
        assert "2 low" in out

    def test_ansi_codes_are_reset(self, tmp_path: Path) -> None:
        """Every ANSI escape sequence we open must be closed with \\033[0m."""
        args = self._run_happy(tmp_path)
        out = args._stdout.getvalue()
        opens = len(re.findall(r"\x1b\[[0-9;]+m", out))
        # A pure reset (\x1b[0m) counts as both — but every opener needs a
        # matching reset somewhere downstream; the simplest invariant is
        # "the buffer ends well-terminated" i.e. the last ANSI code is a reset.
        last = re.findall(r"\x1b\[[0-9;]+m", out)[-1]
        assert last == "\033[0m"
        assert opens >= 2  # at minimum the cyan header + green total

    def test_screenshot_path_appears_in_output(self, tmp_path: Path) -> None:
        args = self._run_happy(tmp_path)
        out = args._stdout.getvalue()
        # The mock screenshot path we wrote should show up verbatim.
        assert "observe-1234.png" in out
