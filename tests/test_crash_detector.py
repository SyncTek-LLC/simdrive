"""Tests for M8: CrashDetector — specterqa/ios/drivers/simulator/crash.py

TDD Phase — INIT-2026-492.
These tests are written BEFORE the implementation exists and must be importable
even when the implementation module is absent.

Module under test (to be created by CodeAtlas):
  specterqa/ios/drivers/simulator/crash.py  — CrashDetector, CrashReport
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard — tests are importable even if impl is missing.
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.drivers.simulator.crash import CrashDetector, CrashReport  # type: ignore[import]

    _CRASH_AVAILABLE = True
except ImportError:
    _CRASH_AVAILABLE = False
    CrashDetector = None  # type: ignore[assignment,misc]
    CrashReport = None  # type: ignore[assignment,misc]

needs_crash = pytest.mark.skipif(
    not _CRASH_AVAILABLE,
    reason="specterqa.ios.drivers.simulator.crash not yet implemented",
)


# ---------------------------------------------------------------------------
# .ips fixture factories
# ---------------------------------------------------------------------------


def _make_ips_json(
    bundle_id: str = "com.example.testapp",
    exception_type: str = "EXC_BAD_ACCESS",
    exception_code: str = "0x0000000000000001",
    crashing_thread: int = 0,
    backtrace_frames: list[str] | None = None,
    last_exception: str | None = "NSInvalidArgumentException: nil argument",
    app_version: str = "1.2.3",
    os_version: str = "iOS 17.4",
    device: str = "iPhone 15 Pro",
    timestamp: str = "2026-03-28T10:00:00Z",
) -> dict:
    """Build a minimal .ips JSON dict that CrashDetector._parse_ips should handle."""
    if backtrace_frames is None:
        backtrace_frames = [
            "0  libsystem_c.dylib           0x1a2b3c4d5e6f methodA",
            "1  libsystem_c.dylib           0x1a2b3c4d5e70 methodB",
            "2  com.example.testapp         0x0000000100001234 -[AppDelegate crashMe:] + 32",
        ]
    return {
        "timestamp": timestamp,
        "bundleID": bundle_id,
        "os_version": os_version,
        "device": device,
        "app_version": app_version,
        "exception": {
            "type": exception_type,
            "codes": exception_code,
        },
        "crashing_thread": crashing_thread,
        "threads": [
            {
                "id": crashing_thread,
                "backtrace": backtrace_frames,
            }
        ],
        "NSException": last_exception,
    }


def _write_ips_file(directory: Path, filename: str, content: dict | None = None) -> Path:
    """Write a .ips JSON fixture file to a directory and return its path."""
    path = directory / filename
    payload = content or _make_ips_json()
    path.write_text(json.dumps(payload))
    return path


def _make_subprocess_result(stdout: str = "", returncode: int = 0) -> MagicMock:
    result = MagicMock()
    result.stdout = stdout
    result.returncode = returncode
    result.stderr = ""
    return result


# ===========================================================================
#  M8: CrashDetector — 15 tests
# ===========================================================================


@needs_crash
class TestCrashReportDataclass:
    """CrashReport dataclass — required fields."""

    def test_crash_report_has_all_required_fields(self):
        """CrashReport must define all specified fields as dataclass fields."""
        required_fields = {
            "timestamp",
            "exception_type",
            "exception_code",
            "crashing_thread",
            "backtrace",
            "last_exception",
            "app_version",
            "os_version",
            "device",
            "raw_path",
        }
        actual_fields = {f.name for f in dataclasses.fields(CrashReport)}
        missing = required_fields - actual_fields
        assert not missing, f"CrashReport is missing fields: {missing}"


@needs_crash
class TestStart:
    """start() — record baseline .ips file list."""

    def test_start_records_baseline_file_list(self, tmp_path: Path):
        """start() scans the DiagnosticReports directory and stores the baseline list."""
        # Create two pre-existing .ips files representing crashes before start()
        _write_ips_file(tmp_path, "old_crash_1.ips")
        _write_ips_file(tmp_path, "old_crash_2.ips")

        detector = CrashDetector(device_id="booted", bundle_id="com.example.testapp")

        with patch.object(detector, "_reports_dir", str(tmp_path)):
            detector.start()
            # Baseline should capture both existing files
            baseline = detector._baseline  # type: ignore[attr-defined]

        assert isinstance(baseline, (set, list, frozenset))
        assert len(baseline) == 2


@needs_crash
class TestCheck:
    """check() — diff current .ips files against baseline."""

    def test_returns_empty_list_when_no_new_crashes(self, tmp_path: Path):
        """check() returns an empty list when no new .ips files appeared after start()."""
        _write_ips_file(tmp_path, "old_crash.ips")

        detector = CrashDetector(device_id="booted", bundle_id="com.example.testapp")

        with patch.object(detector, "_reports_dir", str(tmp_path)):
            detector.start()
            # No new files added — check should return empty
            crashes = detector.check()

        assert crashes == []

    def test_detects_new_ips_file_after_baseline(self, tmp_path: Path):
        """check() returns a CrashReport for a new .ips file that appeared after start()."""
        _write_ips_file(tmp_path, "old_crash.ips")

        detector = CrashDetector(device_id="booted", bundle_id="com.example.testapp")

        with patch.object(detector, "_reports_dir", str(tmp_path)):
            detector.start()

            # Simulate a new crash appearing after start()
            _write_ips_file(
                tmp_path,
                "new_crash.ips",
                content=_make_ips_json(bundle_id="com.example.testapp"),
            )
            crashes = detector.check()

        assert len(crashes) == 1
        assert isinstance(crashes[0], CrashReport)

    def test_filters_by_bundle_id_ignores_other_apps(self, tmp_path: Path):
        """check() only returns crashes matching the detector's bundle_id."""
        detector = CrashDetector(device_id="booted", bundle_id="com.example.testapp")

        with patch.object(detector, "_reports_dir", str(tmp_path)):
            detector.start()

            # Two new crashes — one for our app, one for a different app
            _write_ips_file(
                tmp_path,
                "target_app_crash.ips",
                content=_make_ips_json(bundle_id="com.example.testapp"),
            )
            _write_ips_file(
                tmp_path,
                "other_app_crash.ips",
                content=_make_ips_json(bundle_id="com.other.app"),
            )

            crashes = detector.check()

        assert len(crashes) == 1
        assert crashes[0].exception_type is not None  # It's the target app crash


@needs_crash
class TestParseIps:
    """_parse_ips() — parse .ips JSON file into CrashReport."""

    def test_extracts_exception_type_from_json(self, tmp_path: Path):
        """_parse_ips extracts exception_type from the JSON 'exception.type' field."""
        ips_path = _write_ips_file(
            tmp_path,
            "test_crash.ips",
            content=_make_ips_json(exception_type="EXC_BAD_ACCESS"),
        )
        detector = CrashDetector(device_id="booted", bundle_id="com.example.testapp")

        report = detector._parse_ips(str(ips_path))

        assert report.exception_type == "EXC_BAD_ACCESS"

    def test_extracts_backtrace_frames(self, tmp_path: Path):
        """_parse_ips populates backtrace with a list of symbolicated frame strings."""
        frames = [
            "0  libsystem_c.dylib  0x1000 __methodA",
            "1  MyApp              0x2000 -[Foo bar] + 8",
        ]
        ips_path = _write_ips_file(
            tmp_path,
            "test_crash.ips",
            content=_make_ips_json(backtrace_frames=frames),
        )
        detector = CrashDetector(device_id="booted", bundle_id="com.example.testapp")

        report = detector._parse_ips(str(ips_path))

        assert isinstance(report.backtrace, list)
        assert len(report.backtrace) == 2
        assert any("__methodA" in f for f in report.backtrace)

    def test_extracts_last_exception_nsexception_reason(self, tmp_path: Path):
        """_parse_ips extracts the NSException reason into last_exception."""
        reason = "NSInvalidArgumentException: nil object passed"
        ips_path = _write_ips_file(
            tmp_path,
            "test_crash.ips",
            content=_make_ips_json(last_exception=reason),
        )
        detector = CrashDetector(device_id="booted", bundle_id="com.example.testapp")

        report = detector._parse_ips(str(ips_path))

        assert report.last_exception == reason

    def test_handles_malformed_ips_file_gracefully(self, tmp_path: Path):
        """_parse_ips does not raise on a malformed / non-JSON .ips file."""
        bad_ips = tmp_path / "malformed.ips"
        bad_ips.write_text("this is not valid JSON {{{{")

        detector = CrashDetector(device_id="booted", bundle_id="com.example.testapp")

        # Must not raise — should return None or a CrashReport with safe defaults
        result = detector._parse_ips(str(bad_ips))
        assert result is None or isinstance(result, CrashReport)


@needs_crash
class TestIsAppRunning:
    """is_app_running() — detect if app process is active."""

    def test_returns_true_when_process_found(self):
        """is_app_running returns True when launchctl output contains the bundle_id."""
        detector = CrashDetector(device_id="booted", bundle_id="com.example.testapp")

        launchctl_with_app = "5678\t0\tcom.example.testapp\n890\t0\tcom.apple.springboard\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_result(launchctl_with_app)
            result = detector.is_app_running()

        assert result is True

    def test_returns_false_when_process_not_found(self):
        """is_app_running returns False when launchctl output does not contain the bundle_id."""
        detector = CrashDetector(device_id="booted", bundle_id="com.example.testapp")

        launchctl_without_app = "890\t0\tcom.apple.springboard\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_result(launchctl_without_app)
            result = detector.is_app_running()

        assert result is False


@needs_crash
class TestLatestCrash:
    """latest_crash() — return most recent CrashReport or None."""

    def test_returns_none_when_no_crashes(self, tmp_path: Path):
        """latest_crash returns None when check() has produced no crashes."""
        detector = CrashDetector(device_id="booted", bundle_id="com.example.testapp")

        with patch.object(detector, "_reports_dir", str(tmp_path)):
            detector.start()
            result = detector.latest_crash()

        assert result is None

    def test_returns_most_recent_crash_by_timestamp(self, tmp_path: Path):
        """latest_crash returns the CrashReport with the most recent timestamp."""
        detector = CrashDetector(device_id="booted", bundle_id="com.example.testapp")

        with patch.object(detector, "_reports_dir", str(tmp_path)):
            detector.start()

            _write_ips_file(
                tmp_path,
                "crash_old.ips",
                content=_make_ips_json(
                    bundle_id="com.example.testapp",
                    timestamp="2026-03-28T08:00:00Z",
                ),
            )
            _write_ips_file(
                tmp_path,
                "crash_new.ips",
                content=_make_ips_json(
                    bundle_id="com.example.testapp",
                    timestamp="2026-03-28T12:00:00Z",
                ),
            )

            detector.check()  # populate internal crash list
            latest = detector.latest_crash()

        assert latest is not None
        assert isinstance(latest, CrashReport)
        assert "12:00:00" in latest.timestamp


@needs_crash
class TestMultipleCrashesDetected:
    """Multiple new crashes are all returned from check()."""

    def test_multiple_crashes_detected_correctly(self, tmp_path: Path):
        """check() returns one CrashReport per new .ips file for the target bundle_id."""
        detector = CrashDetector(device_id="booted", bundle_id="com.example.testapp")

        with patch.object(detector, "_reports_dir", str(tmp_path)):
            detector.start()

            for i in range(3):
                _write_ips_file(
                    tmp_path,
                    f"crash_{i}.ips",
                    content=_make_ips_json(
                        bundle_id="com.example.testapp",
                        timestamp=f"2026-03-28T{10 + i}:00:00Z",
                    ),
                )

            crashes = detector.check()

        assert len(crashes) == 3
        assert all(isinstance(c, CrashReport) for c in crashes)


@needs_crash
class TestBaselineIsolation:
    """Crashes that existed before start() are ignored by check()."""

    def test_pre_start_crashes_are_ignored(self, tmp_path: Path):
        """check() must not return crashes whose .ips files were present before start()."""
        # Write a crash BEFORE start() is called — this is in the baseline.
        _write_ips_file(
            tmp_path,
            "pre_existing_crash.ips",
            content=_make_ips_json(bundle_id="com.example.testapp"),
        )

        detector = CrashDetector(device_id="booted", bundle_id="com.example.testapp")

        with patch.object(detector, "_reports_dir", str(tmp_path)):
            detector.start()  # baseline captures pre_existing_crash.ips

            # Do NOT write any new file — check should see nothing new.
            crashes = detector.check()

        assert crashes == [], "Pre-existing crashes (before start()) must not appear in check() results"
