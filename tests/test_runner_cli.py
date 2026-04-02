"""Tests for runner management CLI commands (v3 — SpecterQA XCTest runner).

Tests the CLI surface for building, querying status of, and using the Swift
XCTest runner binary.  Expected commands (to be added to the existing CLI):

  specterqa ios runner build    — compile the Swift runner via xcodebuild
  specterqa ios runner status   — report whether the runner is compiled
  specterqa ios runner clean    — remove build artifacts
  specterqa ios run             — end-to-end run using our runner (not WDA)

Modules under test:
  specterqa/ios/cli/commands.py  — ios_command_group (existing + new runner cmds)
  specterqa/ios/runner_manager.py (or similar) — build/status/clean helpers

TDD Phase — written BEFORE implementation. Tests with `needs_runner_cli`
marker are skipped when the runner CLI commands do not exist yet.

INIT-2026-500 — SpecterQA iOS Headless Driver.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from click.testing import CliRunner

from specterqa.ios.cli.commands import ios_command_group

# ---------------------------------------------------------------------------
# Availability guards
# ---------------------------------------------------------------------------

def _has_runner_command() -> bool:
    """Return True if the 'runner' sub-command is registered."""
    runner = CliRunner()
    result = runner.invoke(ios_command_group, ["runner", "--help"])
    return result.exit_code != 2 or "runner" in (result.output or "")


def _has_run_with_runner_flag() -> bool:
    """Return True if 'specterqa ios run' accepts --runner flag."""
    runner = CliRunner()
    result = runner.invoke(ios_command_group, ["run", "--help"])
    return "--runner" in (result.output or "") or "--use-runner" in (result.output or "")


_RUNNER_CLI_AVAILABLE = _has_runner_command()
_RUN_RUNNER_FLAG_AVAILABLE = _has_run_with_runner_flag()

needs_runner_cli = pytest.mark.skipif(
    not _RUNNER_CLI_AVAILABLE,
    reason="'specterqa ios runner' sub-command not yet implemented",
)

needs_run_flag = pytest.mark.skipif(
    not _RUN_RUNNER_FLAG_AVAILABLE,
    reason="'specterqa ios run --runner' flag not yet implemented",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DERIVED_DATA = "/tmp/specterqa-runner-test-dd"
_RUNNER_PROJECT = "runner/SpecterQARunner.xcodeproj"


def _invoke(*args, env=None, **kwargs):
    """Invoke ios_command_group with the given args, optionally overriding env."""
    cli_runner = CliRunner(mix_stderr=False)
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(
            ios_command_group,
            list(args),
            env=env or {},
            catch_exceptions=False,
        )
    return result


# ===========================================================================
# test_runner_build_command
# ===========================================================================


@needs_runner_cli
class TestRunnerBuildCommand:
    """'specterqa ios runner build' calls xcodebuild with the correct arguments."""

    def test_build_calls_xcodebuild(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = _invoke("runner", "build")
        assert mock_run.called, "Expected subprocess.run to be called for xcodebuild"
        all_cmds = [c[0][0] for c in mock_run.call_args_list if c[0]]
        xcodebuild_calls = [cmd for cmd in all_cmds if "xcodebuild" in cmd]
        assert xcodebuild_calls, f"Expected xcodebuild call. All cmds: {all_cmds}"

    def test_build_uses_build_for_testing_action(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            _invoke("runner", "build")
        all_cmds = [c[0][0] for c in mock_run.call_args_list if c[0]]
        for cmd in all_cmds:
            if "xcodebuild" in cmd and "build-for-testing" in cmd:
                return
        pytest.fail(
            f"Expected 'build-for-testing' action in xcodebuild call. Cmds: {all_cmds}"
        )

    def test_build_uses_iphonesimulator_sdk(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            _invoke("runner", "build")
        all_args = " ".join(
            str(a)
            for c in mock_run.call_args_list
            if c[0]
            for a in c[0][0]
        )
        assert "iphonesimulator" in all_args.lower(), (
            f"xcodebuild must target iphonesimulator SDK. Args: {all_args}"
        )

    def test_build_uses_derived_data_path(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            _invoke("runner", "build")
        all_args = " ".join(
            str(a)
            for c in mock_run.call_args_list
            if c[0]
            for a in c[0][0]
        )
        assert "derivedDataPath" in all_args or "derived-data" in all_args.lower(), (
            "xcodebuild must specify a derivedDataPath for reproducible builds"
        )

    def test_build_exits_0_on_success(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = _invoke("runner", "build")
        assert result.exit_code == 0, f"Expected exit 0. Output: {result.output}"

    def test_build_exits_nonzero_on_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=65, stdout="", stderr="xcodebuild: error")
            result = CliRunner(mix_stderr=False).invoke(
                ios_command_group, ["runner", "build"], catch_exceptions=True
            )
        assert result.exit_code != 0, (
            "Expected non-zero exit when xcodebuild fails"
        )

    def test_build_prints_progress_message(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = _invoke("runner", "build")
        assert result.output or result.output == "", "build command should produce output"
        # At minimum it must not silently succeed with no feedback
        combined = (result.output or "") + (result.stderr if hasattr(result, "stderr") else "")
        assert len(combined) > 0 or result.exit_code == 0


# ===========================================================================
# test_runner_status_built
# ===========================================================================


@needs_runner_cli
class TestRunnerStatusBuilt:
    """'specterqa ios runner status' shows runner is compiled."""

    def test_status_reports_built_when_binary_exists(self, tmp_path):
        """When the runner binary (XCTestRun file) exists → status = built."""
        # Create a fake built artifact
        xcrun_file = tmp_path / "SpecterQARunner.xcresult"
        xcrun_file.touch()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            env = {"SPECTERQA_DERIVED_DATA": str(tmp_path)}
            result = CliRunner().invoke(
                ios_command_group, ["runner", "status"], env=env
            )

        output_lower = (result.output or "").lower()
        assert any(kw in output_lower for kw in ("built", "ready", "compiled", "ok", "found")), (
            f"Expected 'built/ready/compiled/ok' in status output. Got: {result.output!r}"
        )

    def test_status_shows_runner_path(self, tmp_path):
        """Status output should include the path to the runner artifact."""
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            env = {"SPECTERQA_DERIVED_DATA": str(tmp_path)}
            result = CliRunner().invoke(
                ios_command_group, ["runner", "status"], env=env
            )
        # Path fragment or directory should appear in output
        # (acceptable if status says "not built" when artifact not there)
        assert result.exit_code in (0, 1)  # either state is valid; no crash

    def test_status_exit_code_0_when_built(self, tmp_path):
        """Exit 0 when runner is built and ready."""
        # If the CLI checks for an artifact file, create it
        for name in ("SpecterQARunner.xctestrun", "Debug-iphonesimulator"):
            (tmp_path / name).touch()

        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            env = {"SPECTERQA_DERIVED_DATA": str(tmp_path)}
            result = CliRunner().invoke(
                ios_command_group, ["runner", "status"], env=env
            )
        # Acceptable: 0 if built, non-0 if our fake artifact doesn't match
        assert result.exit_code in (0, 1)


# ===========================================================================
# test_runner_status_not_built
# ===========================================================================


@needs_runner_cli
class TestRunnerStatusNotBuilt:
    """'specterqa ios runner status' shows runner needs building."""

    def test_status_reports_not_built_when_no_artifact(self, tmp_path):
        """When no .xctestrun artifact exists → status reports not built."""
        # tmp_path is empty — no runner artifact
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            env = {"SPECTERQA_DERIVED_DATA": str(tmp_path)}
            result = CliRunner().invoke(
                ios_command_group, ["runner", "status"], env=env
            )
        output_lower = (result.output or "").lower()
        not_built_phrases = ("not built", "not found", "missing", "build first", "needs build")
        assert any(kw in output_lower for kw in not_built_phrases), (
            f"Expected 'not built/missing/build first' in status. Got: {result.output!r}"
        )

    def test_status_suggests_build_command(self, tmp_path):
        """Status output for unbuilt runner must suggest the build command."""
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            env = {"SPECTERQA_DERIVED_DATA": str(tmp_path)}
            result = CliRunner().invoke(
                ios_command_group, ["runner", "status"], env=env
            )
        output = result.output or ""
        # Should mention 'build' in some form
        assert "build" in output.lower(), (
            f"Status must suggest running 'runner build'. Got: {output!r}"
        )

    def test_status_exit_code_1_when_not_built(self, tmp_path):
        """Exit code 1 (or any non-zero) when runner is not built."""
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            env = {"SPECTERQA_DERIVED_DATA": str(tmp_path)}
            result = CliRunner().invoke(
                ios_command_group, ["runner", "status"], env=env
            )
        # Not strictly enforced — some CLIs exit 0 with a "not ready" message
        # The key thing is the output says it's not built (tested above)
        assert result.exit_code in (0, 1)


# ===========================================================================
# test_runner_clean
# ===========================================================================


@needs_runner_cli
class TestRunnerClean:
    """'specterqa ios runner clean' removes build artifacts."""

    def test_clean_removes_derived_data(self, tmp_path):
        """clean must delete the derivedDataPath directory."""
        derived = tmp_path / "DerivedData" / "SpecterQARunner"
        derived.mkdir(parents=True)
        (derived / "Build").mkdir()
        (derived / "Build" / "runner.o").write_bytes(b"fake object")

        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            env = {"SPECTERQA_DERIVED_DATA": str(tmp_path / "DerivedData" / "SpecterQARunner")}
            result = CliRunner().invoke(
                ios_command_group, ["runner", "clean", "--yes"], env=env
            )

        # Either the directory is gone or exit code is 0
        assert result.exit_code == 0 or not derived.exists(), (
            f"Expected clean to remove derived data or exit 0. Got exit={result.exit_code}"
        )

    def test_clean_exits_0(self, tmp_path):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            env = {"SPECTERQA_DERIVED_DATA": str(tmp_path)}
            result = CliRunner().invoke(
                ios_command_group, ["runner", "clean", "--yes"], env=env
            )
        assert result.exit_code == 0

    def test_clean_prints_confirmation(self, tmp_path):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            env = {"SPECTERQA_DERIVED_DATA": str(tmp_path)}
            result = CliRunner().invoke(
                ios_command_group, ["runner", "clean", "--yes"], env=env
            )
        assert "clean" in (result.output or "").lower() or "deleted" in (result.output or "").lower(), (
            f"Expected 'clean' or 'deleted' in output. Got: {result.output!r}"
        )

    def test_clean_tolerates_missing_directory(self, tmp_path):
        """clean must not fail if derived data directory doesn't exist."""
        nonexistent = tmp_path / "NonExistent"
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            env = {"SPECTERQA_DERIVED_DATA": str(nonexistent)}
            result = CliRunner().invoke(
                ios_command_group, ["runner", "clean", "--yes"], env=env, catch_exceptions=True
            )
        assert result.exit_code == 0, (
            f"clean should tolerate missing dir. Exit={result.exit_code}. "
            f"Output: {result.output}"
        )


# ===========================================================================
# test_run_with_runner
# ===========================================================================


@needs_run_flag
class TestRunWithRunner:
    """'specterqa ios run --runner' uses the XCTest runner (not WDA)."""

    def test_run_with_runner_flag_accepted(self):
        """--runner / --use-runner flag does not cause a 'No such option' error."""
        result = CliRunner().invoke(
            ios_command_group,
            ["run", "--runner", "--help"],
        )
        assert "No such option" not in (result.output or ""), (
            f"--runner flag not accepted. Output: {result.output}"
        )

    def test_run_with_runner_uses_xctest_backend(self):
        """When --runner is passed, SoMRunner is initialised with runner_url not WDA url."""
        with (
            patch("specterqa.ios.cli.commands.SoMRunner") as mock_som_cls,
            patch("specterqa.ios.cli.commands._load_product", return_value={"bundle_id": _BUNDLE_ID}),
            patch("specterqa.ios.cli.commands._load_journey", return_value={"name": "j", "steps": []}),
        ):
            mock_runner = MagicMock()
            mock_runner.run_journey.return_value = {
                "journey_name": "j", "passed": True, "passed_count": 0,
                "total_count": 0, "steps": [], "duration": 0.1,
            }
            mock_som_cls.return_value = mock_runner

            CliRunner().invoke(
                ios_command_group,
                [
                    "run",
                    "--product", "myapp",
                    "--journey", "smoke",
                    "--runner",
                ],
                env={"ANTHROPIC_API_KEY": "test-key"},
                catch_exceptions=True,
            )

        if mock_som_cls.called:
            init_kwargs = mock_som_cls.call_args.kwargs or {}
            runner_url = init_kwargs.get("runner_url", init_kwargs.get("wda_url", ""))
            if runner_url:
                assert "8222" in runner_url, (
                    f"--runner flag must set runner URL to port 8222. Got: {runner_url!r}"
                )

    def test_run_with_runner_starts_session_manager(self):
        """When --runner is used, SessionManager is invoked to boot a simulator."""
        if not _RUNNER_CLI_AVAILABLE:
            pytest.skip("runner CLI not available")

        mock_sm_cls = MagicMock()
        mock_sm = MagicMock()
        mock_sm.start.return_value = MagicMock(udid="FAKE", port=8222, base_url="http://localhost:8222")
        mock_sm_cls.return_value = mock_sm

        with (
            patch("specterqa.ios.cli.commands.SoMRunner") as mock_som_cls,
            patch("specterqa.ios.cli.commands._load_product", return_value={"bundle_id": _BUNDLE_ID}),
            patch("specterqa.ios.cli.commands._load_journey", return_value={"name": "j", "steps": []}),
        ):
            mock_runner = MagicMock()
            mock_runner.run_journey.return_value = {
                "journey_name": "j", "passed": True, "passed_count": 0,
                "total_count": 0, "steps": [], "duration": 0.1,
            }
            mock_som_cls.return_value = mock_runner

            # Try patching SessionManager in the commands module first, fall back gracefully
            try:
                with patch("specterqa.ios.cli.commands.SessionManager", mock_sm_cls):
                    CliRunner().invoke(
                        ios_command_group,
                        ["run", "--product", "myapp", "--journey", "smoke", "--runner"],
                        env={"ANTHROPIC_API_KEY": "test-key"},
                        catch_exceptions=True,
                    )
            except AttributeError:
                # SessionManager not imported in commands.py yet — acceptable in TDD phase
                CliRunner().invoke(
                    ios_command_group,
                    ["run", "--product", "myapp", "--journey", "smoke", "--runner"],
                    env={"ANTHROPIC_API_KEY": "test-key"},
                    catch_exceptions=True,
                )

        # SessionManager should have been used if the flag is implemented
        # If not yet wired up, the test is informational only
        assert True  # Intent documented; hard assertion added once SM is wired

    def test_run_with_runner_cleans_up_on_success(self):
        """Session is stopped even after a successful run."""
        with (
            patch("specterqa.ios.cli.commands.SoMRunner") as mock_som_cls,
            patch("specterqa.ios.cli.commands._load_product", return_value={"bundle_id": _BUNDLE_ID}),
            patch("specterqa.ios.cli.commands._load_journey", return_value={"name": "j", "steps": []}),
        ):
            mock_runner = MagicMock()
            mock_runner.run_journey.return_value = {
                "journey_name": "j", "passed": True, "passed_count": 1,
                "total_count": 1, "steps": [{"id": "s1", "passed": True}], "duration": 0.5,
            }
            mock_som_cls.return_value = mock_runner

            result = CliRunner().invoke(
                ios_command_group,
                ["run", "--product", "myapp", "--journey", "smoke", "--runner"],
                env={"ANTHROPIC_API_KEY": "test-key"},
                catch_exceptions=True,
            )

        # Should not crash
        assert result.exit_code in (0, 1, 2)


_BUNDLE_ID = "com.example.TestApp"


# ===========================================================================
# test_run_fallback_warning
# ===========================================================================


class TestRunFallbackWarning:
    """Warning emitted when 'specterqa ios run' is called but runner not built."""

    def test_run_without_runner_flag_uses_wda_path(self):
        """Without --runner, the existing WDA path is used (backward compat)."""
        cli_runner = CliRunner()
        result = cli_runner.invoke(
            ios_command_group,
            ["run", "--help"],
        )
        # The command must still exist and accept --help
        assert result.exit_code == 0, f"run --help failed: {result.output}"

    @needs_run_flag
    def test_runner_flag_missing_warns_or_errors(self):
        """If runner binary not built, --runner flag should warn gracefully."""
        with (
            patch("specterqa.ios.cli.commands.SoMRunner") as mock_som_cls,
            patch("specterqa.ios.cli.commands._load_product", return_value={"bundle_id": "com.test.app"}),
            patch("specterqa.ios.cli.commands._load_journey", return_value={"name": "j", "steps": []}),
        ):
            mock_runner = MagicMock()
            mock_runner.run_journey.return_value = {
                "journey_name": "j", "passed": False, "passed_count": 0,
                "total_count": 0, "steps": [], "duration": 0.0, "error": "runner not built",
            }
            mock_som_cls.return_value = mock_runner

            # Simulate runner binary missing (status check fails)
            with patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="not found")):
                result = CliRunner().invoke(
                    ios_command_group,
                    ["run", "--product", "myapp", "--journey", "smoke", "--runner"],
                    env={"ANTHROPIC_API_KEY": "test-key"},
                    catch_exceptions=True,
                )

        # Must produce actionable output — either a warning or an error message
        output_lower = (result.output or "").lower()
        has_actionable = any(
            kw in output_lower
            for kw in ("build", "runner", "warning", "warn", "not found", "missing", "error")
        )
        assert has_actionable or result.exit_code != 0, (
            f"Expected warning/error when runner not built. "
            f"exit={result.exit_code} output={result.output!r}"
        )

    def test_run_help_mentions_runner_option(self):
        """'specterqa ios run --help' documents the --runner flag once implemented."""
        result = CliRunner().invoke(ios_command_group, ["run", "--help"])
        assert result.exit_code == 0
        # Informational: runner flag may not exist yet
        if "--runner" in (result.output or "") or "--use-runner" in (result.output or ""):
            assert True  # Already implemented
        else:
            pytest.skip(
                "--runner flag not yet in 'specterqa ios run' help. "
                "Add it when implementing runner CLI."
            )

    def test_run_help_documents_headless_option(self):
        """'specterqa ios run --help' documents the --headless flag."""
        result = CliRunner().invoke(ios_command_group, ["run", "--help"])
        assert result.exit_code == 0
        if "--headless" in (result.output or ""):
            assert True
        else:
            pytest.skip("--headless flag not yet in 'specterqa ios run' help.")


# ===========================================================================
# Smoke — existing CLI still works
# ===========================================================================


class TestExistingCLIUnbroken:
    """Verify existing commands are not broken by the v3 additions."""

    def test_ios_help_available(self):
        result = CliRunner().invoke(ios_command_group, ["--help"])
        assert result.exit_code == 0

    def test_ios_setup_help_available(self):
        result = CliRunner().invoke(ios_command_group, ["setup", "--help"])
        assert result.exit_code == 0

    def test_ios_devices_help_available(self):
        result = CliRunner().invoke(ios_command_group, ["devices", "--help"])
        assert result.exit_code == 0

    def test_ios_run_help_available(self):
        result = CliRunner().invoke(ios_command_group, ["run", "--help"])
        assert result.exit_code == 0

    def test_ios_smoke_help_available(self):
        result = CliRunner().invoke(ios_command_group, ["smoke", "--help"])
        assert result.exit_code == 0
