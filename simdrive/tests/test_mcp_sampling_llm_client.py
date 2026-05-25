"""TDD tests for MCPSamplingLLMClient — [internal-tracker].

These tests pin the contract for the NEW MCPSamplingLLMClient that calls
MCP sampling (session.create_message) instead of the Anthropic SDK directly.

ALL tests in this file must FAIL until engineering creates
  simdrive/journey/mcp_sampling_client.py
and wires up the async Protocol.

Design notes
------------
- We use asyncio.run() in test bodies to avoid requiring pytest-asyncio.
  pytest-asyncio IS declared in [dev] extras, but asyncio.run() works on
  plain pytest too and keeps the fixture surface minimal.
- The MCP 1.27.0 ServerSession.create_message signature:
    async create_message(
        messages: list[SamplingMessage],
        *,
        max_tokens: int,
        system_prompt: str | None = None,
        model_preferences: ModelPreferences | None = None,
        ...
    ) -> CreateMessageResult
- CreateMessageResult fields: role, content (TextContent|ImageContent), model, stopReason
- SamplingMessage: role, content
- TextContent: type="text", text: str
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Import the module under test.
# This WILL fail with ImportError until engineering creates the file.
# That is the correct TDD signal.
# ---------------------------------------------------------------------------
from simdrive.journey.mcp_sampling_client import MCPSamplingLLMClient  # type: ignore[import]
from simdrive.journey.runner import StepDecision


# ---------------------------------------------------------------------------
# Helpers to build fake MCP sampling objects
# ---------------------------------------------------------------------------

def _make_text_content(text: str) -> SimpleNamespace:
    """Fake mcp.types.TextContent."""
    return SimpleNamespace(type="text", text=text)


def _make_create_message_result(text: str, model: str = "claude-opus-4-7") -> SimpleNamespace:
    """Fake mcp.types.CreateMessageResult with TextContent."""
    return SimpleNamespace(
        role="assistant",
        content=_make_text_content(text),
        model=model,
        stopReason="end_turn",
    )


def _make_session(response_text: str = '{"tool":"tap","args":{"label":"Skip"},"rationale":"skip intro","confidence":0.85}') -> AsyncMock:
    """Return an AsyncMock session whose create_message returns a canned result."""
    session = AsyncMock()
    session.create_message = AsyncMock(
        return_value=_make_create_message_result(response_text)
    )
    return session


# ---------------------------------------------------------------------------
# Test 1: happy-path — call returns StepDecision
# ---------------------------------------------------------------------------


class TestMCPSamplingClientHappyPath:
    """MCPSamplingLLMClient.call returns a correct StepDecision on success."""

    def test_sampling_client_call_returns_step_decision(self):
        """call() returns a StepDecision parsed from the session's TextContent.

        The session is mocked with AsyncMock; create_message returns
        a valid JSON StepDecision payload.  Asserts tool/args/confidence.
        """
        session = _make_session(
            '{"tool":"tap","args":{"label":"Skip"},"rationale":"skip intro","confidence":0.85}'
        )
        client = MCPSamplingLLMClient(session)

        result = asyncio.run(
            client.call(
                system_prompt="You are a test agent.",
                user_prompt="What should I tap?",
                screenshot_path=None,
            )
        )

        assert isinstance(result, StepDecision), (
            f"Expected StepDecision, got {type(result)}"
        )
        assert result.tool == "tap", f"Expected tool='tap', got {result.tool!r}"
        assert result.args == {"label": "Skip"}, f"Unexpected args: {result.args}"
        assert abs(result.confidence - 0.85) < 1e-6, f"Unexpected confidence: {result.confidence}"

    def test_sampling_client_call_invokes_create_message(self):
        """create_message must be called exactly once per call() invocation."""
        session = _make_session()
        client = MCPSamplingLLMClient(session)

        asyncio.run(
            client.call(
                system_prompt="sys",
                user_prompt="user",
                screenshot_path=None,
            )
        )

        session.create_message.assert_called_once()

    def test_sampling_client_uses_model_preferences_for_high_intelligence(self):
        """create_message must be called with model_preferences that request
        intelligence-priority reasoning.

        Journey decisions require multi-step reasoning; we must signal to the
        MCP client that a capable model should be used.  The exact
        intelligencePriority value is spec-flex — this test just asserts the
        kwarg is present and non-None, and that intelligencePriority > 0.5.
        """
        session = _make_session()
        client = MCPSamplingLLMClient(session)

        asyncio.run(
            client.call(
                system_prompt="sys",
                user_prompt="user",
                screenshot_path=None,
            )
        )

        call_kwargs = session.create_message.call_args.kwargs
        model_prefs = call_kwargs.get("model_preferences")
        assert model_prefs is not None, (
            "create_message must be called with model_preferences=... "
            "so the MCP client picks a capable model for journey decisions. "
            "Got model_preferences=None."
        )
        # ModelPreferences from mcp 1.27.0 has intelligencePriority field.
        intelligence = getattr(model_prefs, "intelligencePriority", None)
        assert intelligence is not None, (
            f"model_preferences must have intelligencePriority set; got {model_prefs!r}"
        )
        assert intelligence > 0.5, (
            f"intelligencePriority should be > 0.5 for journey decisions; got {intelligence}"
        )


# ---------------------------------------------------------------------------
# Test 2: screenshot as ImageContent
# ---------------------------------------------------------------------------


class TestMCPSamplingClientScreenshot:
    """When screenshot_path is provided, the message must include ImageContent."""

    def test_sampling_client_passes_screenshot_as_image_content(self, tmp_path: Path):
        """When screenshot_path is an existing PNG, create_message receives a
        messages list that contains an image block alongside the text block.

        The image block must be base64-encoded.  This mirrors what ClaudeLLMClient
        does with the Anthropic SDK, but adapted to MCP SamplingMessage format.
        """
        # Write a tiny fake PNG to disk so the file-open succeeds.
        fake_png = tmp_path / "screen.png"
        fake_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

        session = _make_session()
        client = MCPSamplingLLMClient(session)

        asyncio.run(
            client.call(
                system_prompt="sys",
                user_prompt="Describe what you see.",
                screenshot_path=str(fake_png),
            )
        )

        session.create_message.assert_called_once()
        call_kwargs = session.create_message.call_args.kwargs
        # 'messages' may be a positional arg — check both.
        call_args = session.create_message.call_args
        messages = call_args.args[0] if call_args.args else call_kwargs.get("messages", [])

        assert len(messages) >= 1, "At least one SamplingMessage must be sent"

        # Find the user message; it should have content that includes an image block.
        user_msg = messages[-1]  # convention: last message is user turn
        content = user_msg.content if hasattr(user_msg, "content") else None

        # Content may be a list (multi-part) or a single object.
        if isinstance(content, list):
            content_items = content
        else:
            content_items = [content] if content is not None else []

        image_blocks = [
            item for item in content_items
            if getattr(item, "type", None) == "image"
        ]
        assert image_blocks, (
            "When screenshot_path is provided, the SamplingMessage content must "
            "include at least one image block (type='image'). "
            f"Got content items: {content_items!r}"
        )

    def test_sampling_client_no_screenshot_sends_text_only(self, tmp_path: Path):
        """When screenshot_path=None, create_message must NOT crash and must send
        a text-only message.  No image blocks expected.
        """
        session = _make_session()
        client = MCPSamplingLLMClient(session)

        result = asyncio.run(
            client.call(
                system_prompt="sys",
                user_prompt="What next?",
                screenshot_path=None,
            )
        )

        # Must succeed and return a StepDecision.
        assert isinstance(result, StepDecision)
        session.create_message.assert_called_once()

    def test_sampling_client_missing_screenshot_file_does_not_crash(self, tmp_path: Path):
        """When screenshot_path points to a file that does NOT exist, the client
        must degrade gracefully (skip the image block) rather than raising.
        """
        session = _make_session()
        client = MCPSamplingLLMClient(session)

        result = asyncio.run(
            client.call(
                system_prompt="sys",
                user_prompt="What next?",
                screenshot_path="/tmp/this_file_does_not_exist_12345.png",
            )
        )
        # Must not raise — degrade to text-only
        assert isinstance(result, StepDecision)


# ---------------------------------------------------------------------------
# Test 3: unparseable response → fail StepDecision
# ---------------------------------------------------------------------------


class TestMCPSamplingClientParseErrors:
    """Malformed LLM responses must degrade to a 'fail' StepDecision."""

    def test_sampling_client_handles_unparseable_response(self):
        """When the session returns non-JSON text, call() returns a 'fail'
        StepDecision matching the existing _parse_decision fallback contract
        (as used in claude_client.py).

        Specifically:
          - StepDecision.tool == "fail"
          - "parse error" in StepDecision.rationale (case-insensitive)
          - confidence == 0.0
        """
        session = _make_session("this is absolutely not JSON { broken }")
        client = MCPSamplingLLMClient(session)

        result = asyncio.run(
            client.call(
                system_prompt="sys",
                user_prompt="user",
                screenshot_path=None,
            )
        )

        assert result.tool == "fail", (
            f"Unparseable response must yield tool='fail', got {result.tool!r}"
        )
        assert "parse error" in result.rationale.lower(), (
            f"Rationale must mention parse error; got {result.rationale!r}"
        )
        assert result.confidence == 0.0, (
            f"Confidence on parse failure must be 0.0; got {result.confidence}"
        )

    def test_sampling_client_handles_empty_response(self):
        """Empty string response must also degrade gracefully to 'fail'."""
        session = _make_session("")
        client = MCPSamplingLLMClient(session)

        result = asyncio.run(
            client.call(
                system_prompt="sys",
                user_prompt="user",
                screenshot_path=None,
            )
        )

        assert result.tool == "fail"


# ---------------------------------------------------------------------------
# Test 4: session errors propagate cleanly
# ---------------------------------------------------------------------------


class TestMCPSamplingClientSessionErrors:
    """Errors from session.create_message must propagate as clear domain errors.

    Contract choice (documented here per spec):
    The MCPSamplingLLMClient re-raises the original exception as-is.
    Rationale: the runner's generic `except Exception` at the call site already
    converts any exception to outcome="error" with the message captured in
    failure_reason.  Wrapping in a custom exception type would lose the original
    cause without adding debuggability.  engineering MAY choose to wrap in a
    specific simdrive.errors.SimdriveError subclass — if so, update this test
    to assert the wrapper type while still checking .cause.
    """

    def test_sampling_client_propagates_session_errors(self):
        """session.create_message raises RuntimeError → the exception propagates.

        The important invariant: the exception is NOT silently swallowed and does
        NOT become a StepDecision(tool='fail') — that would hide infra failures.
        """
        session = AsyncMock()
        session.create_message = AsyncMock(
            side_effect=RuntimeError("MCP client does not support sampling")
        )
        client = MCPSamplingLLMClient(session)

        with __import__("pytest").raises(Exception) as exc_info:
            asyncio.run(
                client.call(
                    system_prompt="sys",
                    user_prompt="user",
                    screenshot_path=None,
                )
            )

        # The exception must NOT be silently eaten.
        # Accept either a plain RuntimeError propagation OR a wrapped domain error.
        assert exc_info.value is not None, "Session error must NOT be silently swallowed"
        # Confirm it's not a StepDecision (i.e. it IS an exception, which is true here
        # by virtue of pytest.raises succeeding — documented for clarity).


# ---------------------------------------------------------------------------
# Test 5: no anthropic import in module source
# ---------------------------------------------------------------------------


class TestMCPSamplingClientNoAnthropicImport:
    """The MCPSamplingLLMClient module must NEVER import anthropic.

    This is the critical property that makes the MCP path work without the
    user's Anthropic API key.  The MCP client (Claude Code, Cline, Cursor, etc.)
    already has reasoning capability and handles model selection internally.

    We read the source file and grep for anthropic imports rather than patching
    sys.modules because the latter is unreliable with lazy imports and cached
    module state.
    """

    def test_sampling_client_no_anthropic_import(self):
        """The source of mcp_sampling_client.py must not contain 'import anthropic'
        or 'from anthropic'.

        If this test fails after engineering creates the file, it means the
        implementation accidentally pulled in the Anthropic SDK — which would
        force every MCP user to `pip install anthropic` and set ANTHROPIC_API_KEY.
        """
        src_root = Path(__file__).parent.parent / "src" / "simdrive" / "journey"
        module_path = src_root / "mcp_sampling_client.py"

        assert module_path.exists(), (
            f"mcp_sampling_client.py not found at {module_path}. "
            "engineering must create this file."
        )

        source_text = module_path.read_text(encoding="utf-8")

        # Check for any direct anthropic import at top level or inside functions.
        bad_patterns = ["import anthropic", "from anthropic"]
        for pattern in bad_patterns:
            assert pattern not in source_text, (
                f"Found forbidden pattern {pattern!r} in mcp_sampling_client.py. "
                "The MCP sampling client must NOT import the anthropic package. "
                "Reasoning is delegated to the MCP client (Claude Code/Cline/etc.) "
                "via session.create_message — no Anthropic SDK required."
            )
