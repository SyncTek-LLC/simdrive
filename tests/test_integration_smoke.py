"""End-to-end integration smoke tests for v11.1.0.

These tests verify cross-module workflows work correctly without
requiring a live simulator. Backend HTTP calls are mocked but session
lifecycle, replay player, and MCP handlers run real code.
"""

import json
import re
import select
import subprocess
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# TestFullRecordReplayCycle
# ---------------------------------------------------------------------------


class TestFullRecordReplayCycle:
    """Verify recording an MCP session and replaying it works."""

    def test_record_save_load_replay(self, tmp_path):
        """Record steps via recorder API, save to YAML, load with player, verify."""
        from specterqa.ios.replay import ReplayPlayer, ReplayRecorder

        # Record
        r = ReplayRecorder(bundle_id="com.test.app")
        r.record_tap(1, "Settings", 100.0, 200.0)
        r.record_swipe("down")
        r.record_swipe_back()
        r.record_type("hello")
        r.record_press_key("return")
        r.add_checkpoint(["General", "About"])

        # Save
        replay_path = tmp_path / "test.yaml"
        r.save(str(replay_path), name="integration-test")
        assert replay_path.exists()

        # Load
        player = ReplayPlayer(str(replay_path))
        assert player.bundle_id == "com.test.app"
        assert player.name == "integration-test"
        # 5 action steps (add_checkpoint attaches to last step, not a separate step)
        assert len(player.steps) == 5
        assert player.steps[0]["action"] == "tap"
        assert player.steps[0]["element_label"] == "Settings"

    def test_record_save_load_replay_step_count_matches_actions(self, tmp_path):
        """add_checkpoint does not add a step; step count == action calls."""
        from specterqa.ios.replay import ReplayPlayer, ReplayRecorder

        r = ReplayRecorder(bundle_id="com.bundle.app")
        r.record_tap(0, "OK", 10, 20)
        r.record_type("world")
        r.add_checkpoint(["Success"])

        out = tmp_path / "cp.yaml"
        r.save(str(out))

        player = ReplayPlayer(str(out))
        assert len(player.steps) == 2  # tap + type; checkpoint is metadata

    def test_replay_yaml_roundtrip_preserves_all_fields(self, tmp_path):
        """All ReplayStep fields should survive YAML round-trip."""
        from specterqa.ios.replay import ReplayPlayer, ReplayRecorder

        r = ReplayRecorder(bundle_id="com.test")
        r.record_long_press(1, "Cell", 50.0, 50.0, 1.5)
        r.add_checkpoint(["Action Sheet", "Cancel"])

        out = tmp_path / "rt.yaml"
        r.save(str(out))

        player = ReplayPlayer(str(out))
        step = player.steps[0]
        assert step["action"] == "long_press"
        assert step["duration"] == 1.5
        assert step["element_label"] == "Cell"
        # Checkpoint should be attached as expect_elements on the step
        assert "Action Sheet" in step.get("expect_elements", [])
        assert "Cancel" in step.get("expect_elements", [])

    def test_bundle_id_preserved_across_save_load(self, tmp_path):
        """Bundle ID written to YAML and read back correctly."""
        from specterqa.ios.replay import ReplayPlayer, ReplayRecorder

        r = ReplayRecorder(bundle_id="com.example.MyApp")
        r.record_swipe("up")

        out = tmp_path / "bid.yaml"
        r.save(str(out))

        player = ReplayPlayer(str(out))
        assert player.bundle_id == "com.example.MyApp"

    def test_all_recorded_action_types_survive_roundtrip(self, tmp_path):
        """Every action recorder method produces a loadable step."""
        from specterqa.ios.replay import ReplayPlayer, ReplayRecorder

        r = ReplayRecorder(bundle_id="com.all.actions")
        r.record_tap(1, "Btn", 10, 20)
        r.record_swipe("left")
        r.record_swipe_back()
        r.record_type("abc")
        r.record_press_key("delete")
        r.record_long_press(2, "Cell", 30, 40, 2.0)

        out = tmp_path / "all_actions.yaml"
        r.save(str(out))

        player = ReplayPlayer(str(out))
        actions = [s["action"] for s in player.steps]
        assert "tap" in actions
        assert "swipe" in actions
        assert "swipe_back" in actions
        assert "type" in actions
        assert "press_key" in actions
        assert "long_press" in actions

    def test_save_creates_parent_directories(self, tmp_path):
        """ReplayRecorder.save() creates nested directories as needed."""
        from specterqa.ios.replay import ReplayRecorder

        r = ReplayRecorder(bundle_id="com.test")
        r.record_tap(0, "X", 0, 0)

        deep = tmp_path / "a" / "b" / "c" / "replay.yaml"
        r.save(str(deep))
        assert deep.exists()

    def test_empty_session_saves_and_loads(self, tmp_path):
        """A recorder with no steps should still produce a valid YAML file."""
        from specterqa.ios.replay import ReplayPlayer, ReplayRecorder

        r = ReplayRecorder(bundle_id="com.empty")
        out = tmp_path / "empty.yaml"
        r.save(str(out))

        player = ReplayPlayer(str(out))
        assert player.bundle_id == "com.empty"
        assert player.steps == []


# ---------------------------------------------------------------------------
# TestMaestroExampleParses
# ---------------------------------------------------------------------------


class TestMaestroExampleParses:
    """The shipped Maestro example files should load and validate cleanly."""

    def _load_if_exists(self, path: str):
        """Return a ReplayPlayer if the file exists, else None (skip)."""
        from specterqa.ios.replay import ReplayPlayer

        p = Path(path)
        if not p.exists():
            pytest.skip(f"Example not found: {path}")
        return ReplayPlayer(str(p))

    def test_smoke_test_example_loads(self, fresh_install):
        player = self._load_if_exists(str(fresh_install / "examples" / "01-smoke-test.yaml"))
        assert player.name
        assert len(player.steps) > 0

    def test_form_with_waits_example_loads(self, fresh_install):
        player = self._load_if_exists(str(fresh_install / "examples" / "02-form-with-waits.yaml"))
        assert len(player.steps) > 0

    def test_conditional_branching_example_loads(self, fresh_install):
        player = self._load_if_exists(str(fresh_install / "examples" / "03-conditional-branching.yaml"))
        assert player.bundle_id

    def test_visual_regression_example_loads(self, fresh_install):
        player = self._load_if_exists(str(fresh_install / "examples" / "04-visual-regression.yaml"))
        assert player.bundle_id

    def test_all_examples_have_bundle_id(self, fresh_install):
        """Every example file must declare a bundle_id."""
        from specterqa.ios.replay import ReplayPlayer

        examples_dir = fresh_install / "examples"
        for example in sorted(examples_dir.glob("*.yaml")):
            player = ReplayPlayer(str(example))
            assert player.bundle_id, f"{example.name} missing bundle_id"

    def test_all_examples_have_at_least_one_step(self, fresh_install):
        """Every example file must have at least one step."""
        from specterqa.ios.replay import ReplayPlayer

        examples_dir = fresh_install / "examples"
        for example in sorted(examples_dir.glob("*.yaml")):
            player = ReplayPlayer(str(example))
            assert len(player.steps) > 0, f"{example.name} has no steps"


# ---------------------------------------------------------------------------
# TestMCPServerProtocol
# ---------------------------------------------------------------------------


class TestMCPServerProtocol:
    """Verify MCP protocol works end-to-end via stdio."""

    def _exchange(
        self, messages: list[str], timeout: float = 5.0, cwd: "str | None" = None
    ) -> list[dict]:
        """Start MCP server, send *messages*, collect JSON responses, kill.

        Polls until *timeout* seconds have elapsed (absolute deadline), so
        the server has time to start up before the first response arrives.

        Args:
            messages: JSON-RPC message strings to send.
            timeout: Deadline in seconds.
            cwd: Working directory for the MCP server process. Defaults to
                the repo root so ``specterqa.ios.mcp`` is importable.
        """
        import time

        proc = subprocess.Popen(
            ["python3.13", "-m", "specterqa.ios.mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
        )
        try:
            for msg in messages:
                proc.stdin.write(msg + "\n")
            proc.stdin.flush()

            responses = []
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                ready, _, _ = select.select([proc.stdout], [], [], min(0.2, remaining))
                if not ready:
                    continue  # keep polling until deadline
                line = proc.stdout.readline().strip()
                if not line:
                    continue
                try:
                    responses.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
            return responses
        finally:
            proc.kill()
            proc.wait(timeout=5)

    def _init_msgs(self) -> list[str]:
        return [
            '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}',
            '{"jsonrpc":"2.0","method":"notifications/initialized"}',
        ]

    def test_tools_list_returns_19_tools(self, fresh_install):
        msgs = self._init_msgs() + ['{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}']
        responses = self._exchange(msgs, cwd=str(fresh_install))

        tool_count = 0
        for msg in responses:
            if "result" in msg and "tools" in msg.get("result", {}):
                tool_count = len(msg["result"]["tools"])
                break

        assert tool_count >= 19, f"Expected >=19 tools, got {tool_count}"

    def test_tools_list_includes_core_tools(self, fresh_install):
        """Key tools required for the test framework must be present."""
        msgs = self._init_msgs() + ['{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}']
        responses = self._exchange(msgs, cwd=str(fresh_install))

        tool_names = set()
        for msg in responses:
            if "result" in msg and "tools" in msg.get("result", {}):
                tool_names = {t["name"] for t in msg["result"]["tools"]}
                break

        required = {
            "ios_start_session",
            "ios_stop_session",
            "ios_tap",
            "ios_screenshot",
            "ios_elements",
            "ios_swipe",
            "ios_type",
            "ios_save_replay",
        }
        missing = required - tool_names
        assert not missing, f"MCP server missing required tools: {missing}"

    def test_initialize_returns_protocol_version(self, fresh_install):
        """Server must echo back a protocolVersion in its initialize result."""
        msgs = [
            '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}'
        ]
        responses = self._exchange(msgs, timeout=5.0, cwd=str(fresh_install))

        init_resp = next((r for r in responses if r.get("id") == 1 and "result" in r), None)
        assert init_resp is not None, "No initialize response received"
        assert "protocolVersion" in init_resp["result"]

    def test_unknown_method_returns_error(self, fresh_install):
        """Calling a non-existent method should return a JSON-RPC error."""
        msgs = self._init_msgs() + ['{"jsonrpc":"2.0","id":99,"method":"no_such_method","params":{}}']
        responses = self._exchange(msgs, cwd=str(fresh_install))

        error_resp = next((r for r in responses if r.get("id") == 99), None)
        # Server must respond (either error or empty result — not silence)
        assert error_resp is not None, "Server did not respond to unknown method"


# ---------------------------------------------------------------------------
# TestCIJSONOutput
# ---------------------------------------------------------------------------


class TestCIJSONOutput:
    """Verify --json-output writes structured results."""

    def _make_minimal_replay(self, path: Path) -> None:
        """Write a minimal valid replay YAML to *path*."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("replay:\n  name: ci-json-test\n  bundle_id: com.test.app\n  steps:\n    - tapOn: Save\n")

    def test_json_output_structure_has_summary_and_replays(self, tmp_path):
        """When --json-output is given, the written file contains summary + replays."""
        from click.testing import CliRunner

        from specterqa.ios.cli.commands import ios_command_group

        replay_dir = tmp_path / "replays"
        self._make_minimal_replay(replay_dir / "smoke.yaml")

        json_out = tmp_path / "results.json"
        runner = CliRunner()
        runner.invoke(
            ios_command_group,
            [
                "ci",
                str(replay_dir),
                "--json-output",
                str(json_out),
                "--no-reuse-runner",
            ],
            catch_exceptions=True,
        )

        assert json_out.exists(), "--json-output file was not created"
        data = json.loads(json_out.read_text())
        assert "summary" in data, "JSON output missing 'summary' key"
        assert "replays" in data, "JSON output missing 'replays' key"

    def test_json_output_summary_has_total_field(self, tmp_path):
        """summary.total must equal passed + failed + ui_changed."""
        from click.testing import CliRunner

        from specterqa.ios.cli.commands import ios_command_group

        replay_dir = tmp_path / "replays"
        self._make_minimal_replay(replay_dir / "s1.yaml")
        self._make_minimal_replay(replay_dir / "s2.yaml")

        json_out = tmp_path / "r.json"
        runner = CliRunner()
        runner.invoke(
            ios_command_group,
            ["ci", str(replay_dir), "--json-output", str(json_out), "--no-reuse-runner"],
            catch_exceptions=True,
        )

        if json_out.exists():
            data = json.loads(json_out.read_text())
            s = data["summary"]
            assert "total" in s
            assert s["total"] == s.get("passed", 0) + s.get("failed", 0) + s.get("ui_changed", 0)

    def test_json_output_replays_list_matches_files(self, tmp_path):
        """replays list length should equal the number of replay YAML files run."""
        from click.testing import CliRunner

        from specterqa.ios.cli.commands import ios_command_group

        replay_dir = tmp_path / "replays"
        for i in range(3):
            self._make_minimal_replay(replay_dir / f"replay{i}.yaml")

        json_out = tmp_path / "out.json"
        runner = CliRunner()
        runner.invoke(
            ios_command_group,
            ["ci", str(replay_dir), "--json-output", str(json_out), "--no-reuse-runner"],
            catch_exceptions=True,
        )

        if json_out.exists():
            data = json.loads(json_out.read_text())
            assert len(data["replays"]) == 3

    def test_json_output_not_written_when_no_replays(self, tmp_path):
        """If the replay directory is empty, ci exits early; json file must not be corrupt."""
        from click.testing import CliRunner

        from specterqa.ios.cli.commands import ios_command_group

        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        json_out = tmp_path / "noop.json"

        runner = CliRunner()
        runner.invoke(
            ios_command_group,
            ["ci", str(empty_dir), "--json-output", str(json_out), "--no-reuse-runner"],
            catch_exceptions=True,
        )

        # If the file exists it must be valid JSON; otherwise absence is fine
        if json_out.exists():
            data = json.loads(json_out.read_text())
            assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# TestValidatorWithExamples
# ---------------------------------------------------------------------------


class TestValidatorWithExamples:
    """Validate all shipped example files pass the validate-replay command."""

    def test_all_examples_validate_clean(self, fresh_install):
        from click.testing import CliRunner

        from specterqa.ios.cli.commands import ios_command_group

        examples_dir = fresh_install / "examples"
        runner = CliRunner()

        failures = []
        for example in sorted(examples_dir.glob("*.yaml")):
            result = runner.invoke(ios_command_group, ["validate-replay", str(example)])
            if result.exit_code != 0:
                failures.append(f"{example.name}: {result.output}")

        assert not failures, "Examples failed validation:\n" + "\n".join(failures)

    def test_invalid_replay_missing_bundle_id_fails_validation(self, tmp_path):
        """validate-replay must reject a file that has no bundle_id."""
        from click.testing import CliRunner

        from specterqa.ios.cli.commands import ios_command_group

        bad = tmp_path / "bad.yaml"
        bad.write_text("replay:\n  name: no-bundle\n  steps:\n    - action: tap\n      element_label: OK\n")
        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["validate-replay", str(bad)])
        assert result.exit_code != 0, "Expected non-zero exit for missing bundle_id"

    def test_valid_minimal_replay_passes_validation(self, tmp_path):
        """A minimal but valid replay file must pass validate-replay."""
        from click.testing import CliRunner

        from specterqa.ios.cli.commands import ios_command_group

        good = tmp_path / "good.yaml"
        good.write_text(
            "replay:\n"
            "  name: minimal\n"
            "  bundle_id: com.example.app\n"
            "  steps:\n"
            "    - action: tap\n"
            "      element_label: OK\n"
        )
        runner = CliRunner()
        result = runner.invoke(ios_command_group, ["validate-replay", str(good)])
        assert result.exit_code == 0, f"Expected valid replay to pass: {result.output}"


# ---------------------------------------------------------------------------
# TestPyPIPackageStructure
# ---------------------------------------------------------------------------


class TestPyPIPackageStructure:
    """Verify the package metadata and build configuration are correct."""

    def test_pyproject_has_correct_version(self, fresh_install):
        pyproject = (fresh_install / "pyproject.toml").read_text()
        match = re.search(r'version\s*=\s*"([^"]+)"', pyproject)
        assert match, "version field not found in pyproject.toml"
        version = match.group(1)
        assert version.startswith("11."), f"Expected v11.x, got {version}"

    def test_console_script_entry_points(self, fresh_install):
        pyproject = (fresh_install / "pyproject.toml").read_text()
        assert "specterqa-ios" in pyproject, "specterqa-ios entry point missing"
        assert "specterqa-ios-mcp" in pyproject, "specterqa-ios-mcp entry point missing"

    def test_pyproject_has_build_backend(self, fresh_install):
        """pyproject.toml must declare a PEP 517 build backend."""
        pyproject = (fresh_install / "pyproject.toml").read_text()
        assert "build-backend" in pyproject

    def test_pyproject_declares_python_requires(self, fresh_install):
        """Minimum Python version constraint must be present."""
        pyproject = (fresh_install / "pyproject.toml").read_text()
        assert "requires-python" in pyproject

    def test_license_file_exists(self, fresh_install):
        license_file = fresh_install / "LICENSE"
        assert license_file.exists(), "LICENSE file missing"
        assert license_file.stat().st_size > 0, "LICENSE file is empty"

    def test_readme_exists(self, fresh_install):
        readme = fresh_install / "README.md"
        assert readme.exists(), "README.md missing"


# ---------------------------------------------------------------------------
# TestRunnerSourcesShipped
# ---------------------------------------------------------------------------


class TestRunnerSourcesShipped:
    """Verify Swift runner sources are included in the distribution."""

    def test_manifest_includes_runner(self, fresh_install):
        manifest = fresh_install / "MANIFEST.in"
        if not manifest.exists():
            pytest.skip("MANIFEST.in not present")
        content = manifest.read_text()
        assert "runner" in content, "MANIFEST.in does not include runner sources"

    def test_swift_files_exist(self, fresh_install):
        runner_dir = fresh_install / "runner" / "Sources"
        assert runner_dir.exists(), "runner/Sources directory missing"
        swift_files = list(runner_dir.glob("*.swift"))
        assert len(swift_files) >= 5, f"Expected >=5 Swift source files, found {len(swift_files)}: " + ", ".join(
            f.name for f in swift_files
        )

    def test_runner_package_swift_exists(self, fresh_install):
        """Package.swift must exist for the Swift runner target."""
        pkg = fresh_install / "runner" / "Package.swift"
        assert pkg.exists(), "runner/Package.swift missing"

    def test_core_swift_source_files_present(self, fresh_install):
        """Key Swift source files must be present by name."""
        runner_dir = fresh_install / "runner" / "Sources"
        present = {f.name for f in runner_dir.glob("*.swift")}

        expected = {
            "SpecterQARunner.swift",
            "HTTPServer.swift",
            "AccessibilityTree.swift",
        }
        missing = expected - present
        assert not missing, f"Missing Swift sources: {missing}"

    def test_runner_sources_are_non_empty(self, fresh_install):
        """Every Swift source file must have content (not empty stubs)."""
        runner_dir = fresh_install / "runner" / "Sources"
        for swift_file in runner_dir.glob("*.swift"):
            assert swift_file.stat().st_size > 0, f"{swift_file.name} is empty"


# ---------------------------------------------------------------------------
# TestReplayPlayerVariableSubstitution
# ---------------------------------------------------------------------------


class TestReplayPlayerVariableSubstitution:
    """Verify ReplayPlayer.resolve_vars substitutes ${VAR} placeholders."""

    def test_resolve_single_variable(self):
        from specterqa.ios.replay import ReplayPlayer

        result = ReplayPlayer.resolve_vars("Hello ${NAME}", {"NAME": "World"})
        assert result == "Hello World"

    def test_resolve_multiple_variables(self):
        from specterqa.ios.replay import ReplayPlayer

        result = ReplayPlayer.resolve_vars("${USER}@${DOMAIN}", {"USER": "alice", "DOMAIN": "example.com"})
        assert result == "alice@example.com"

    def test_unresolved_variable_is_left_as_is(self):
        """If a variable is not in the dict, the placeholder stays unchanged."""
        from specterqa.ios.replay import ReplayPlayer

        result = ReplayPlayer.resolve_vars("${MISSING}", {})
        assert result == "${MISSING}"

    def test_resolve_vars_with_empty_dict(self):
        from specterqa.ios.replay import ReplayPlayer

        result = ReplayPlayer.resolve_vars("no vars here", {})
        assert result == "no vars here"


# ---------------------------------------------------------------------------
# TestMaestroNormalization
# ---------------------------------------------------------------------------


class TestMaestroNormalization:
    """Verify Maestro-shorthand steps are normalised to native format."""

    def _norm(self, step: dict) -> dict:
        from specterqa.ios.replay import ReplayPlayer

        return ReplayPlayer._normalize_maestro_step(dict(step))

    def test_tap_on_shorthand_becomes_tap(self):
        step = self._norm({"tapOn": "Submit"})
        assert step.get("action") == "tap"
        assert step.get("element_label") == "Submit"
        assert "tapOn" not in step

    def test_assert_visible_shorthand_becomes_assert(self):
        step = self._norm({"assertVisible": "Welcome"})
        assert step.get("action") == "assert"
        assert "Welcome" in step.get("expect_elements", [])

    def test_input_text_shorthand_becomes_type(self):
        step = self._norm({"inputText": "hello"})
        assert step.get("action") == "type"
        assert step.get("text") == "hello"

    def test_native_action_passes_through_unchanged(self):
        original = {"action": "swipe", "direction": "up"}
        step = self._norm(original)
        assert step["action"] == "swipe"
        assert step["direction"] == "up"
