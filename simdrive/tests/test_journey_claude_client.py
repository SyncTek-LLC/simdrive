"""Unit tests for journey/claude_client.py.

All tests use a mocked anthropic.Anthropic client — no real API calls are made.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from specterqa_ios.journey.claude_client import (
    ClaudeLLMClient,
    _compute_cost,
    _parse_decision,
)
from specterqa_ios.journey.runner import StepDecision


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_response(text: str, input_tokens: int = 100, output_tokens: int = 50) -> MagicMock:
    """Build a minimal fake anthropic Message response."""
    block = SimpleNamespace(text=text)
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    resp = MagicMock()
    resp.content = [block]
    resp.usage = usage
    return resp


# ── _compute_cost ─────────────────────────────────────────────────────────────


def test_compute_cost_zero():
    assert _compute_cost(0, 0) == 0.0


def test_compute_cost_positive():
    # 1M input tokens at $15/M + 1M output at $75/M = $90
    cost = _compute_cost(1_000_000, 1_000_000)
    assert abs(cost - 90.0) < 1e-6


# ── _parse_decision ───────────────────────────────────────────────────────────


def test_parse_decision_valid_tap():
    text = '{"tool": "tap", "args": {"x": 100, "y": 200}, "rationale": "tap button", "confidence": 0.9}'
    decision = _parse_decision(text)
    assert decision.tool == "tap"
    assert decision.args == {"x": 100, "y": 200}
    assert decision.confidence == 0.9


def test_parse_decision_done():
    text = '{"tool": "done", "args": {}, "rationale": "goal reached", "confidence": 1.0}'
    decision = _parse_decision(text)
    assert decision.tool == "done"


def test_parse_decision_invalid_json():
    """Falls back to a 'fail' decision on unparseable output."""
    decision = _parse_decision("not valid json at all")
    assert decision.tool == "fail"
    assert "parse error" in decision.rationale.lower()
    assert decision.confidence == 0.0


# ── ClaudeLLMClient ───────────────────────────────────────────────────────────


@patch("specterqa_ios.journey.claude_client.anthropic.Anthropic")
def test_client_call_returns_decision(mock_anthropic_cls):
    """Happy-path: client calls SDK and returns a parsed StepDecision."""
    fake_response = _make_response(
        '{"tool": "swipe", "args": {"direction": "up"}, "rationale": "scroll", "confidence": 0.8}',
        input_tokens=200,
        output_tokens=80,
    )
    mock_client = MagicMock()
    mock_client.messages.create.return_value = fake_response
    mock_anthropic_cls.return_value = mock_client

    client = ClaudeLLMClient(api_key="test-key")
    decision = client.call(
        system_prompt="You are a test agent.",
        user_prompt="What should I do?",
        screenshot_path=None,
    )

    assert isinstance(decision, StepDecision)
    assert decision.tool == "swipe"
    assert client.cost_usd > 0.0


@patch("specterqa_ios.journey.claude_client.anthropic.Anthropic")
def test_client_cost_accumulates(mock_anthropic_cls):
    """Cost accumulates across multiple calls."""
    fake_response = _make_response(
        '{"tool": "done", "args": {}, "rationale": "done", "confidence": 1.0}',
        input_tokens=100,
        output_tokens=50,
    )
    mock_client = MagicMock()
    mock_client.messages.create.return_value = fake_response
    mock_anthropic_cls.return_value = mock_client

    client = ClaudeLLMClient(api_key="test-key")
    assert client.cost_usd == 0.0

    client.call("system", "user1", None)
    cost_after_1 = client.cost_usd
    assert cost_after_1 > 0.0

    client.call("system", "user2", None)
    cost_after_2 = client.cost_usd
    assert cost_after_2 > cost_after_1


@patch("specterqa_ios.journey.claude_client.anthropic.Anthropic")
def test_client_sdk_exception_propagates(mock_anthropic_cls):
    """SDK exceptions bubble up so runner can wrap them as claude_call_failed."""
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("network error")
    mock_anthropic_cls.return_value = mock_client

    client = ClaudeLLMClient(api_key="test-key")
    with pytest.raises(RuntimeError, match="network error"):
        client.call("system", "user", None)
