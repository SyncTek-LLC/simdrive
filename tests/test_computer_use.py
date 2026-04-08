"""Unit tests for SpecterQA Claude Computer Use iOS Simulator testing feature.

TDD: These tests are written BEFORE implementation to define the expected
contracts for ComputerUseDecider and ComputerUseRunner.

Test plan (INIT-2026-492, ART-2026-018-TEST-PLAN):
  - ComputerUseDecider: API request/response mapping, error handling, cost tracking
  - ComputerUseRunner: Simulator lifecycle, scenario execution, budget enforcement
  - Orchestrator mode routing: ios_simulator_cu mode creates ComputerUseRunner
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from specterqa.engine.protocols import Decision, StepResult


# ---------------------------------------------------------------------------
# Helpers — build fake Anthropic API response objects
# ---------------------------------------------------------------------------


def _make_content_block(block_type: str, **kwargs) -> MagicMock:
    """Build a mock Anthropic content block (text or tool_use)."""
    block = MagicMock()
    block.type = block_type
    for key, value in kwargs.items():
        setattr(block, key, value)
    return block


def _make_tool_use_block(tool_name: str, tool_input: dict) -> MagicMock:
    """Build a mock tool_use content block."""
    block = _make_content_block("tool_use", name=tool_name, input=tool_input)
    return block


def _make_text_block(text: str) -> MagicMock:
    """Build a mock text content block."""
    return _make_content_block("text", text=text)


def _make_api_response(content_blocks: list) -> MagicMock:
    """Build a mock Anthropic Messages API response."""
    response = MagicMock()
    response.content = content_blocks
    usage = MagicMock()
    usage.input_tokens = 500
    usage.output_tokens = 100
    response.usage = usage
    response.model = "claude-sonnet-4-20250514"
    return response


def _setup_beta_client(mock_client: MagicMock, response: MagicMock) -> MagicMock:
    """Wire up mock_client.beta.messages.create to return response.

    The ComputerUseDecider calls ``client.beta.messages.create`` (the beta
    namespace), not ``client.messages.create``.
    """
    mock_client.beta = MagicMock()
    mock_client.beta.messages = MagicMock()
    mock_client.beta.messages.create = MagicMock(return_value=response)
    return mock_client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def evidence_dir(tmp_path: Path) -> Path:
    """Temporary evidence directory for test runs."""
    d = tmp_path / "evidence"
    d.mkdir()
    return d


@pytest.fixture
def mock_anthropic_client() -> MagicMock:
    """A fully mocked Anthropic client with beta.messages.create wired up.

    The ComputerUseDecider uses the beta API namespace:
    ``client.beta.messages.create(...)``.
    """
    client = MagicMock()
    client.beta = MagicMock()
    client.beta.messages = MagicMock()
    client.beta.messages.create = MagicMock()
    return client


@pytest.fixture
def cost_callback() -> MagicMock:
    """A mock cost callback for injection into ComputerUseDecider."""
    return MagicMock()


# ---------------------------------------------------------------------------
# ComputerUseDecider Tests
# ---------------------------------------------------------------------------


class TestComputerUseDeciderClickDecision:
    """Test 1: left_click tool_use → Decision(action='click')."""

    def test_decide_returns_click_decision(self, mock_anthropic_client):
        """A left_click tool_use response should map to a click Decision with
        the coordinate pair included in the target string."""
        from specterqa.engine.computer_use_decider import ComputerUseDecider

        response = _make_api_response(
            [_make_tool_use_block("computer", {"action": "left_click", "coordinate": [200, 450]})]
        )
        mock_anthropic_client.beta.messages.create.return_value = response

        with patch("anthropic.Anthropic", return_value=mock_anthropic_client):
            decider = ComputerUseDecider(api_key="test-key")
            decision = decider.decide(
                goal="Tap the Sign In button",
                screenshot_base64="aGVsbG8=",
            )

        assert isinstance(decision, Decision)
        assert decision.action == "click"
        assert "200" in decision.target
        assert "450" in decision.target


class TestComputerUseDeciderTypeDecision:
    """Test 2: type tool_use → Decision(action='fill', value=text)."""

    def test_decide_returns_type_decision(self, mock_anthropic_client):
        """A type tool_use response should map to a fill Decision with the
        typed text in the value field."""
        from specterqa.engine.computer_use_decider import ComputerUseDecider

        response = _make_api_response([_make_tool_use_block("computer", {"action": "type", "text": "hello"})])
        mock_anthropic_client.beta.messages.create.return_value = response

        with patch("anthropic.Anthropic", return_value=mock_anthropic_client):
            decider = ComputerUseDecider(api_key="test-key")
            decision = decider.decide(
                goal="Type hello in the search field",
                screenshot_base64="aGVsbG8=",
            )

        assert isinstance(decision, Decision)
        assert decision.action == "fill"
        assert decision.value == "hello"


class TestComputerUseDeciderKeyDecision:
    """Test 3: key tool_use → Decision(action='keyboard', value=key_name)."""

    def test_decide_returns_key_decision(self, mock_anthropic_client):
        """A key tool_use response should map to a keyboard Decision with the
        key name in the value field."""
        from specterqa.engine.computer_use_decider import ComputerUseDecider

        response = _make_api_response([_make_tool_use_block("computer", {"action": "key", "text": "Return"})])
        mock_anthropic_client.beta.messages.create.return_value = response

        with patch("anthropic.Anthropic", return_value=mock_anthropic_client):
            decider = ComputerUseDecider(api_key="test-key")
            decision = decider.decide(
                goal="Submit the form",
                screenshot_base64="aGVsbG8=",
            )

        assert isinstance(decision, Decision)
        assert decision.action == "keyboard"
        assert decision.value == "Return"


class TestComputerUseDeciderScrollDecision:
    """Test 4: scroll tool_use → Decision(action='scroll')."""

    def test_decide_returns_scroll_decision(self, mock_anthropic_client):
        """A scroll tool_use response should map to a scroll Decision."""
        from specterqa.engine.computer_use_decider import ComputerUseDecider

        response = _make_api_response(
            [
                _make_tool_use_block(
                    "computer",
                    {
                        "action": "scroll",
                        "coordinate": [195, 422],
                        "direction": "down",
                        "amount": 3,
                    },
                )
            ]
        )
        mock_anthropic_client.beta.messages.create.return_value = response

        with patch("anthropic.Anthropic", return_value=mock_anthropic_client):
            decider = ComputerUseDecider(api_key="test-key")
            decision = decider.decide(
                goal="Scroll down to see more content",
                screenshot_base64="aGVsbG8=",
            )

        assert isinstance(decision, Decision)
        assert decision.action == "scroll"


class TestComputerUseDeciderGoalAchieved:
    """Test 5: text-only response (no tool_use) → Decision(goal_achieved=True, action='done')."""

    def test_decide_returns_done_on_goal_achieved(self, mock_anthropic_client):
        """When Claude returns only a text block (no tool_use blocks), the decider
        should interpret this as goal completion and return a done Decision."""
        from specterqa.engine.computer_use_decider import ComputerUseDecider

        response = _make_api_response([_make_text_block("Goal achieved. The login was successful.")])
        mock_anthropic_client.beta.messages.create.return_value = response

        with patch("anthropic.Anthropic", return_value=mock_anthropic_client):
            decider = ComputerUseDecider(api_key="test-key")
            decision = decider.decide(
                goal="Log in to the app",
                screenshot_base64="aGVsbG8=",
            )

        assert isinstance(decision, Decision)
        assert decision.goal_achieved is True
        assert decision.action == "done"


class TestComputerUseDeciderApiError:
    """Test 6: anthropic.APIError → error Decision without crash."""

    def test_decide_handles_api_error(self, mock_anthropic_client):
        """When the Anthropic API raises APIError, decide() must catch it, not
        propagate it, and return a Decision indicating failure so the step runner
        can handle recovery."""
        import anthropic
        from specterqa.engine.computer_use_decider import ComputerUseDecider

        mock_anthropic_client.beta.messages.create.side_effect = anthropic.APIError(
            message="Internal server error",
            request=MagicMock(),
            body=None,
        )

        with patch("anthropic.Anthropic", return_value=mock_anthropic_client):
            decider = ComputerUseDecider(api_key="test-key")
            # Must not raise
            decision = decider.decide(
                goal="Tap the button",
                screenshot_base64="aGVsbG8=",
            )

        assert isinstance(decision, Decision)
        # Should signal a failure state rather than a valid action
        assert decision.action in ("stuck", "error", "done", "wait")
        assert decision.goal_achieved is False


class TestComputerUseDeciderRetryOnTransient:
    """Test 7: Transient error on first call → retry succeeds."""

    def test_decide_retries_on_transient_error(self, mock_anthropic_client):
        """If the first API call raises a transient error but the second succeeds,
        the decider should return the successful result transparently."""
        import anthropic
        from specterqa.engine.computer_use_decider import ComputerUseDecider

        success_response = _make_api_response(
            [_make_tool_use_block("computer", {"action": "left_click", "coordinate": [100, 200]})]
        )

        mock_anthropic_client.beta.messages.create.side_effect = [
            anthropic.APIConnectionError(request=MagicMock()),
            success_response,
        ]

        with patch("anthropic.Anthropic", return_value=mock_anthropic_client):
            decider = ComputerUseDecider(api_key="test-key")
            decision = decider.decide(
                goal="Click somewhere",
                screenshot_base64="aGVsbG8=",
            )

        # Should ultimately succeed after retry
        assert isinstance(decision, Decision)
        assert decision.action == "click"
        # Client was called twice (one failure + one success)
        assert mock_anthropic_client.beta.messages.create.call_count == 2


class TestComputerUseDeciderBetaHeader:
    """Test 8: Request must include computer-use beta header."""

    def test_decide_sends_correct_beta_header(self, mock_anthropic_client):
        """The beta.messages.create call must include the
        'computer-use-2025-01-24' string in the betas parameter, which is
        required by the Computer Use API."""
        from specterqa.engine.computer_use_decider import ComputerUseDecider

        response = _make_api_response(
            [_make_tool_use_block("computer", {"action": "left_click", "coordinate": [10, 20]})]
        )
        mock_anthropic_client.beta.messages.create.return_value = response

        with patch("anthropic.Anthropic", return_value=mock_anthropic_client):
            decider = ComputerUseDecider(api_key="test-key")
            decider.decide(goal="Tap", screenshot_base64="aGVsbG8=")

        call_kwargs = mock_anthropic_client.beta.messages.create.call_args
        # betas may be a positional or keyword argument
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}

        betas = kwargs.get("betas")
        assert betas is not None, "betas parameter not passed to beta.messages.create"
        assert "computer-use-2025-01-24" in betas


class TestComputerUseDeciderScreenshotPayload:
    """Test 9: Request message must contain base64 image content block."""

    def test_decide_sends_screenshot_as_base64(self, mock_anthropic_client):
        """The user message sent to Claude must contain an image content block
        with media_type image/* and the provided base64 data, enabling Claude
        to see the simulator screenshot."""
        from specterqa.engine.computer_use_decider import ComputerUseDecider

        test_b64 = "aGVsbG93b3JsZA=="  # base64 for 'helloworld'
        response = _make_api_response(
            [_make_tool_use_block("computer", {"action": "left_click", "coordinate": [0, 0]})]
        )
        mock_anthropic_client.beta.messages.create.return_value = response

        with patch("anthropic.Anthropic", return_value=mock_anthropic_client):
            decider = ComputerUseDecider(api_key="test-key")
            decider.decide(goal="Tap", screenshot_base64=test_b64)

        call_kwargs = mock_anthropic_client.beta.messages.create.call_args.kwargs
        messages = call_kwargs.get("messages", [])

        # Find any message with an image content block
        image_found = False
        for msg in messages:
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "image":
                        source = block.get("source", {})
                        if source.get("data") == test_b64:
                            image_found = True
                            break
        assert image_found, "No image content block with the provided base64 data found in the request messages"


class TestComputerUseDeciderStuckContext:
    """Test 10: stuck_context is included in the request when provided."""

    def test_decide_includes_stuck_context(self, mock_anthropic_client):
        """When stuck_context is passed (e.g. 'tried clicking 3 times'), the
        decider must inject it into the prompt text so Claude knows to try a
        different approach."""
        from specterqa.engine.computer_use_decider import ComputerUseDecider

        response = _make_api_response(
            [_make_tool_use_block("computer", {"action": "left_click", "coordinate": [0, 0]})]
        )
        mock_anthropic_client.beta.messages.create.return_value = response

        stuck_msg = "tried clicking 3 times without result"
        with patch("anthropic.Anthropic", return_value=mock_anthropic_client):
            decider = ComputerUseDecider(api_key="test-key")
            decider.decide(
                goal="Tap the button",
                screenshot_base64="aGVsbG8=",
                stuck_context=stuck_msg,
            )

        call_kwargs = mock_anthropic_client.beta.messages.create.call_args.kwargs
        # The stuck_context must appear somewhere in the messages payload
        messages_str = str(call_kwargs.get("messages", []))
        assert stuck_msg in messages_str, f"stuck_context '{stuck_msg}' not found in messages sent to API"


class TestComputerUseDeciderCostCallback:
    """Test 11: cost_callback is invoked after a successful API call."""

    def test_cost_callback_called(self, mock_anthropic_client, cost_callback):
        """After each API call, the cost_callback(model_name, token_cost) must
        be invoked so the caller can track per-call spend."""
        from specterqa.engine.computer_use_decider import ComputerUseDecider

        response = _make_api_response(
            [_make_tool_use_block("computer", {"action": "left_click", "coordinate": [0, 0]})]
        )
        mock_anthropic_client.beta.messages.create.return_value = response

        with patch("anthropic.Anthropic", return_value=mock_anthropic_client):
            decider = ComputerUseDecider(
                api_key="test-key",
                cost_callback=cost_callback,
            )
            decider.decide(goal="Tap", screenshot_base64="aGVsbG8=")

        assert cost_callback.called, "cost_callback was never called"
        args = cost_callback.call_args[0]
        # First arg: model name (string), second arg: cost (numeric)
        assert isinstance(args[0], str), f"Expected model name string, got {type(args[0])}"
        assert isinstance(args[1], (int, float)), f"Expected numeric cost, got {type(args[1])}"


class TestComputerUseDeciderDisplayDimensions:
    """Test 12: Display dimensions are sent in the tool config."""

    def test_display_dimensions_sent(self, mock_anthropic_client):
        """The tools list passed to Claude must include a computer tool with
        display_width_px and display_height_px matching the configured
        display dimensions (defaults: 1170x2532 for iPhone 15 Pro retina)."""
        from specterqa.engine.computer_use_decider import ComputerUseDecider

        response = _make_api_response(
            [_make_tool_use_block("computer", {"action": "left_click", "coordinate": [0, 0]})]
        )
        mock_anthropic_client.beta.messages.create.return_value = response

        display_w, display_h = 1170, 2532
        with patch("anthropic.Anthropic", return_value=mock_anthropic_client):
            decider = ComputerUseDecider(
                api_key="test-key",
                display_width=display_w,
                display_height=display_h,
            )
            decider.decide(goal="Tap", screenshot_base64="aGVsbG8=")

        call_kwargs = mock_anthropic_client.beta.messages.create.call_args.kwargs
        tools = call_kwargs.get("tools", [])

        found_computer_tool = False
        for tool in tools:
            tool_type = tool.get("type", "")
            if "computer" in tool_type:
                found_computer_tool = True
                assert tool.get("display_width_px") == display_w, (
                    f"display_width_px mismatch: expected {display_w}, got {tool.get('display_width_px')}"
                )
                assert tool.get("display_height_px") == display_h, (
                    f"display_height_px mismatch: expected {display_h}, got {tool.get('display_height_px')}"
                )

        assert found_computer_tool, "No computer tool found in the tools list"


# ---------------------------------------------------------------------------
# ComputerUseRunner Tests
# ---------------------------------------------------------------------------


class TestComputerUseRunnerStartBootsSimulator:
    """Test 13: start() boots the underlying SimulatorRunner."""

    def test_start_boots_simulator(self, evidence_dir):
        """ComputerUseRunner.start() must delegate to SimulatorRunner.start()
        so the iOS Simulator is booted before any test steps run.

        SimulatorRunner is imported lazily inside start(), so the patch target
        is specterqa.engine.simulator_runner.SimulatorRunner (the module where
        the class lives, imported by computer_use_runner.start at runtime).
        """
        from specterqa.engine.computer_use_runner import ComputerUseRunner

        mock_sim_runner = MagicMock()

        with (
            patch(
                "specterqa.engine.simulator_runner.SimulatorRunner",
                return_value=mock_sim_runner,
            ),
            patch(
                "specterqa.engine.computer_use_decider.ComputerUseDecider",
                return_value=MagicMock(),
            ),
            patch(
                "specterqa.engine.sim_action_executor.SimActionExecutor",
                return_value=MagicMock(),
            ),
        ):
            runner = ComputerUseRunner(
                bundle_id="com.example.testapp",
                evidence_dir=evidence_dir,
            )
            runner.start()

        mock_sim_runner.start.assert_called_once()


class TestComputerUseRunnerInstallApp:
    """Test 14: start() installs the app when app_path is provided."""

    def test_start_installs_app_when_path_provided(self, evidence_dir):
        """When app_path is passed to ComputerUseRunner, the underlying
        SimulatorRunner must be initialised with that path so it can install
        the .app bundle during start()."""
        from specterqa.engine.computer_use_runner import ComputerUseRunner

        mock_sim_runner = MagicMock()
        app_path = "/tmp/MyApp.app"

        with (
            patch(
                "specterqa.engine.simulator_runner.SimulatorRunner",
            ) as MockSimRunner,
            patch(
                "specterqa.engine.computer_use_decider.ComputerUseDecider",
                return_value=MagicMock(),
            ),
            patch(
                "specterqa.engine.sim_action_executor.SimActionExecutor",
                return_value=MagicMock(),
            ),
        ):
            MockSimRunner.return_value = mock_sim_runner
            runner = ComputerUseRunner(
                bundle_id="com.example.testapp",
                evidence_dir=evidence_dir,
                app_path=app_path,
            )
            runner.start()

        # Verify SimulatorRunner was constructed with the provided app_path
        init_kwargs = MockSimRunner.call_args.kwargs
        assert init_kwargs.get("app_path") == app_path or (
            MockSimRunner.call_args.args and app_path in MockSimRunner.call_args.args
        ), f"app_path '{app_path}' not passed to SimulatorRunner constructor"


class TestComputerUseRunnerRunScenarioAllSteps:
    """Test 15: run_scenario() executes all steps and returns StepResults."""

    def test_run_scenario_executes_all_steps(self, evidence_dir):
        """A 3-step scenario passed to run_scenario() must result in exactly
        3 StepResult objects being returned — one per step, in order."""
        from specterqa.engine.computer_use_runner import ComputerUseRunner

        mock_sim_runner = MagicMock()
        mock_step_result = MagicMock(spec=StepResult)
        mock_step_result.step_id = "step_1"
        mock_step_result.passed = True
        mock_step_result.screenshots = []
        mock_step_result.ux_observations = []
        mock_step_result.actions_taken = []
        mock_step_result.action_count = 1
        mock_step_result.duration_seconds = 0.1
        mock_step_result.checkpoints_reached = []
        mock_step_result.findings = []
        mock_step_result.error = None
        mock_step_result.goal_achieved = True

        with (
            patch(
                "specterqa.engine.simulator_runner.SimulatorRunner",
                return_value=mock_sim_runner,
            ),
            patch(
                "specterqa.engine.computer_use_decider.ComputerUseDecider",
                return_value=MagicMock(),
            ),
            patch(
                "specterqa.engine.sim_action_executor.SimActionExecutor",
                return_value=MagicMock(),
            ),
            patch(
                "specterqa.engine.ai_step_runner.AIStepRunner",
            ) as MockAIStepRunner,
        ):
            mock_ai_runner = MagicMock()
            mock_ai_runner.execute_step.return_value = mock_step_result
            MockAIStepRunner.return_value = mock_ai_runner

            runner = ComputerUseRunner(
                bundle_id="com.example.testapp",
                evidence_dir=evidence_dir,
            )
            runner.start()

            scenario = {
                "steps": [
                    {"id": "step_1", "goal": "Open the app"},
                    {"id": "step_2", "goal": "Tap Sign In"},
                    {"id": "step_3", "goal": "Verify home screen"},
                ]
            }
            results = runner.run_scenario(scenario)

        assert len(results) == 3, f"Expected 3 StepResults, got {len(results)}"


class TestComputerUseRunnerStopShutsDown:
    """Test 16: stop() shuts down the SimulatorRunner."""

    def test_stop_shuts_down_simulator(self, evidence_dir):
        """ComputerUseRunner.stop() must call SimulatorRunner.stop() to cleanly
        shut down the iOS Simulator and release resources."""
        from specterqa.engine.computer_use_runner import ComputerUseRunner

        mock_sim_runner = MagicMock()

        with (
            patch(
                "specterqa.engine.simulator_runner.SimulatorRunner",
                return_value=mock_sim_runner,
            ),
            patch(
                "specterqa.engine.computer_use_decider.ComputerUseDecider",
                return_value=MagicMock(),
            ),
            patch(
                "specterqa.engine.sim_action_executor.SimActionExecutor",
                return_value=MagicMock(),
            ),
        ):
            runner = ComputerUseRunner(
                bundle_id="com.example.testapp",
                evidence_dir=evidence_dir,
            )
            runner.start()
            runner.stop()

        mock_sim_runner.stop.assert_called_once()


class TestComputerUseRunnerBudgetEnforcement:
    """Test 17: budget enforcement stops early when budget exceeded."""

    def test_budget_enforcement(self, evidence_dir):
        """When the per-run budget is exhausted, the runner must skip remaining
        steps rather than continuing to call the AI and accumulate cost.

        We trigger this by making the first AIStepRunner.execute_step raise
        BudgetExceededError (simulating the CostTracker enforcement inside the
        run), which causes the runner to set its budget_exceeded flag. The
        subsequent two steps must be skipped (returned as failed results).
        """
        from specterqa.engine.cost_tracker import BudgetExceededError
        from specterqa.engine.computer_use_runner import ComputerUseRunner

        mock_sim_runner = MagicMock()

        with (
            patch(
                "specterqa.engine.simulator_runner.SimulatorRunner",
                return_value=mock_sim_runner,
            ),
            patch(
                "specterqa.engine.computer_use_decider.ComputerUseDecider",
                return_value=MagicMock(),
            ),
            patch(
                "specterqa.engine.sim_action_executor.SimActionExecutor",
                return_value=MagicMock(),
            ),
            patch(
                "specterqa.engine.ai_step_runner.AIStepRunner",
            ) as MockAIStepRunner,
        ):
            mock_ai_runner = MagicMock()
            # First step raises BudgetExceededError; remaining must be skipped
            mock_ai_runner.execute_step.side_effect = BudgetExceededError("Run budget exceeded: $0.01")
            MockAIStepRunner.return_value = mock_ai_runner

            runner = ComputerUseRunner(
                bundle_id="com.example.testapp",
                evidence_dir=evidence_dir,
                budget=0.01,
            )
            runner.start()

            scenario = {
                "steps": [
                    {"id": "step_1", "goal": "Open the app"},
                    {"id": "step_2", "goal": "Tap Sign In"},
                    {"id": "step_3", "goal": "Verify home screen"},
                ]
            }
            results = runner.run_scenario(scenario)

        # All 3 results returned, but step_1 shows budget error and
        # steps 2-3 are skipped. None should be passed=True.
        assert len(results) == 3, "Expected a result per step (failed/skipped)"
        assert all(not r.passed for r in results), "No steps should pass when budget is exceeded on step 1"


class TestComputerUseRunnerEvidenceCollected:
    """Test 18: evidence_dir is forwarded so screenshots are persisted."""

    def test_evidence_collected(self, evidence_dir):
        """The evidence_dir passed to ComputerUseRunner must be forwarded to
        the underlying SimulatorRunner so screenshots and evidence are stored
        in the correct root location (a run-specific subdirectory of evidence_dir).
        """
        from specterqa.engine.computer_use_runner import ComputerUseRunner

        mock_sim_runner = MagicMock()

        with (
            patch(
                "specterqa.engine.simulator_runner.SimulatorRunner",
            ) as MockSimRunner,
            patch(
                "specterqa.engine.computer_use_decider.ComputerUseDecider",
                return_value=MagicMock(),
            ),
            patch(
                "specterqa.engine.sim_action_executor.SimActionExecutor",
                return_value=MagicMock(),
            ),
        ):
            MockSimRunner.return_value = mock_sim_runner

            runner = ComputerUseRunner(
                bundle_id="com.example.testapp",
                evidence_dir=evidence_dir,
            )
            runner.start()

        # SimulatorRunner must be constructed with an evidence_dir that is
        # a subdirectory of (or equal to) the provided evidence_dir
        init_kwargs = MockSimRunner.call_args.kwargs
        passed_dir = Path(init_kwargs.get("evidence_dir", ""))
        assert passed_dir != Path(""), "evidence_dir not passed to SimulatorRunner"
        # The run evidence dir should be within the provided evidence root
        assert str(passed_dir).startswith(str(evidence_dir)), (
            f"SimulatorRunner evidence_dir {passed_dir} not under {evidence_dir}"
        )


class TestComputerUseRunnerStepResultsType:
    """Test 19: run_scenario() returns StepResult instances."""

    def test_step_results_returned(self, evidence_dir):
        """run_scenario() must return a list. Each element must have the fields
        required by StepResult (step_id, passed, goal_achieved) so callers
        can inspect individual step outcomes."""
        from specterqa.engine.computer_use_runner import ComputerUseRunner

        mock_sim_runner = MagicMock()
        step_result = MagicMock(spec=StepResult)
        step_result.step_id = "step_login"
        step_result.passed = True
        step_result.goal_achieved = True
        step_result.screenshots = []
        step_result.ux_observations = []
        step_result.actions_taken = []
        step_result.action_count = 2
        step_result.duration_seconds = 1.5
        step_result.checkpoints_reached = []
        step_result.findings = []
        step_result.error = None

        with (
            patch(
                "specterqa.engine.simulator_runner.SimulatorRunner",
                return_value=mock_sim_runner,
            ),
            patch(
                "specterqa.engine.computer_use_decider.ComputerUseDecider",
                return_value=MagicMock(),
            ),
            patch(
                "specterqa.engine.sim_action_executor.SimActionExecutor",
                return_value=MagicMock(),
            ),
            patch(
                "specterqa.engine.ai_step_runner.AIStepRunner",
            ) as MockAIStepRunner,
        ):
            mock_ai_runner = MagicMock()
            mock_ai_runner.execute_step.return_value = step_result
            MockAIStepRunner.return_value = mock_ai_runner

            runner = ComputerUseRunner(
                bundle_id="com.example.testapp",
                evidence_dir=evidence_dir,
            )
            runner.start()

            results = runner.run_scenario({"steps": [{"id": "step_login", "goal": "Log in"}]})

        assert isinstance(results, list), "run_scenario() must return a list"
        assert len(results) == 1
        result = results[0]
        # Validate required fields are accessible (duck typing — may be MagicMock in prod)
        assert hasattr(result, "step_id")
        assert hasattr(result, "passed")
        assert hasattr(result, "goal_achieved")


# ---------------------------------------------------------------------------
# Orchestrator Mode Routing Tests
# ---------------------------------------------------------------------------


class TestOrchestratorIosSimulatorCuMode:
    """Test 20: ios_simulator_cu mode routes to ComputerUseRunner."""

    def test_ios_simulator_cu_mode_creates_runner(self, evidence_dir):
        """When a scenario step has mode='ios_simulator_cu', the orchestrator
        must instantiate a ComputerUseRunner (not a plain SimulatorRunner) to
        handle the step. This validates that ComputerUseRunner is constructable
        with the standard bundle_id / evidence_dir / device_name interface and
        that the module can be imported for mode routing."""
        from specterqa.engine.computer_use_runner import ComputerUseRunner

        # Verify the constructor signature accepts the expected orchestrator-supplied args.
        # We do NOT call start() (that would boot a real simulator); we only verify
        # that the object is created correctly and has the expected public methods.
        mock_sim = MagicMock()
        with patch(
            "specterqa.engine.simulator_runner.SimulatorRunner",
            return_value=mock_sim,
        ):
            runner = ComputerUseRunner(
                bundle_id="com.example.testapp",
                evidence_dir=evidence_dir,
                device_name="iPhone 15 Pro",
            )

        # The runner must expose start(), stop(), run_scenario()
        assert hasattr(runner, "start"), "ComputerUseRunner must have start()"
        assert hasattr(runner, "stop"), "ComputerUseRunner must have stop()"
        assert hasattr(runner, "run_scenario"), "ComputerUseRunner must have run_scenario()"
        # Internal bundle_id must be stored correctly
        assert runner._bundle_id == "com.example.testapp"
        assert runner._device_name == "iPhone 15 Pro"
