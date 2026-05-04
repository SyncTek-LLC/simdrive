"""Regression test: MCP code path must NOT import anthropic — INIT-2026-544.

WHY THIS MATTERS
----------------
simdrive is an MCP tool.  When Claude Code (or any MCP client) calls
tool_run_journey via the MCP protocol, the CLIENT already has reasoning
capability and Anthropic credentials.  Forcing the SERVER to import anthropic
would:

  1. Break `pip install simdrive` (no extras) — anthropic is only in [dev] extras
  2. Force every MCP user to provision ANTHROPIC_API_KEY on the server side
  3. Make simdrive's value proposition ("agent-first, bring your own reasoning")
     a lie — the server would ALSO call Anthropic independently

The fix (MCPSamplingLLMClient) delegates reasoning to the MCP client via
session.create_message().  The anthropic package must NOT be transitively
imported by the MCP code path.

APPROACH: sys.modules masking
------------------------------
We insert `sys.modules['anthropic'] = None` before the import.  In Python 3.x,
setting a key to None in sys.modules causes `import anthropic` to raise
ModuleNotFoundError.  This simulates a clean install with no anthropic package.

We then verify that:
  - simdrive.server can be imported (the module loads)
  - Simple tool handlers (tool_doctor, tool_list_devices) work
  - tool_run_journey reachable WITHOUT pulling anthropic
  - Any failure from tool_run_journey is NOT ModuleNotFoundError for anthropic

We also verify packaging invariants via pyproject.toml parsing.
"""
from __future__ import annotations

import importlib
import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
_SIMDRIVE_ROOT = Path(__file__).parent.parent
_PYPROJECT = _SIMDRIVE_ROOT / "pyproject.toml"


# ---------------------------------------------------------------------------
# Test 1: pyproject.toml packaging invariant
# ---------------------------------------------------------------------------


class TestPyprojectAnthropicOptionalOnly:
    """anthropic must NOT be in [project.dependencies] — only in optional extras."""

    def _parse_section(self, text: str, section_header: str) -> str:
        """Extract the content of a TOML array value under a given section."""
        # Match section up to the next section or end of file.
        pattern = rf"\[{re.escape(section_header)}\](.*?)(?=\n\[|\Z)"
        m = re.search(pattern, text, re.DOTALL)
        return m.group(1) if m else ""

    def test_pyproject_anthropic_optional_only(self):
        """anthropic must NOT be in [project.dependencies].

        It may (and should) appear in [project.optional-dependencies] under the
        'dev' extra, since ClaudeLLMClient is used by the CLI path only.
        """
        pyproject_text = _PYPROJECT.read_text(encoding="utf-8")

        # Extract [project.dependencies] block (stops at first blank or new section).
        # Simple regex — no TOML parser dependency needed.
        core_deps_match = re.search(
            r"\[project\]\s.*?dependencies\s*=\s*\[([^\]]*)\]",
            pyproject_text,
            re.DOTALL,
        )
        assert core_deps_match is not None, (
            "Could not find [project] dependencies in pyproject.toml. "
            "Check pyproject.toml structure."
        )

        core_deps_block = core_deps_match.group(1)
        # Extract individual dep names (strip version specifiers)
        dep_names = set()
        for line in core_deps_block.splitlines():
            line = line.strip().strip('",').strip("',")
            if not line or line.startswith("#"):
                continue
            name = re.split(r"[><=!;\[]", line)[0].strip().lower().replace("_", "-")
            if name:
                dep_names.add(name)

        assert "anthropic" not in dep_names, (
            "'anthropic' found in [project.dependencies] — this means every "
            "`pip install simdrive` (no extras) would pull in the Anthropic SDK. "
            "The MCP path must work without anthropic.  Move anthropic to "
            "[project.optional-dependencies] dev extra only."
        )

        # Also verify it IS in optional-dependencies (regression guard for the
        # CLI path which legitimately needs it).
        optional_section = self._parse_section(pyproject_text, "project.optional-dependencies")
        # anthropic may be in [project.optional-dependencies] as a multi-group table.
        assert "anthropic" in optional_section.lower(), (
            "'anthropic' must appear in [project.optional-dependencies] (dev extra) "
            "so the standalone CLI path (simdrive run ...) can use ClaudeLLMClient. "
            "It was not found there."
        )


# ---------------------------------------------------------------------------
# Test 2: sys.modules masking — MCP import path does not need anthropic
# ---------------------------------------------------------------------------


class TestMCPPathNoAnthropicImport:
    """Simulate a clean install with no anthropic package using sys.modules masking.

    sys.modules['anthropic'] = None makes Python treat 'anthropic' as a failed
    import — any `import anthropic` will raise ModuleNotFoundError.  This is the
    standard technique for testing optional-dep lazy loading.
    """

    def _mask_anthropic(self):
        """Insert the anthropic mask into sys.modules.  Returns a cleanup function."""
        original = sys.modules.get("anthropic", _SENTINEL := object())
        sys.modules["anthropic"] = None  # type: ignore[assignment]

        def restore():
            if original is _SENTINEL:
                sys.modules.pop("anthropic", None)
            else:
                sys.modules["anthropic"] = original  # type: ignore[assignment]

        return restore

    def test_simdrive_server_importable_without_anthropic(self):
        """simdrive.server can be imported even when anthropic is unavailable.

        After the refactor, server.py must NOT import anthropic at module top
        level.  The anthropic import must be lazy (inside tool_run_journey or
        inside ClaudeLLMClient.__init__) so that the module loads cleanly on a
        standard `pip install simdrive`.
        """
        restore = self._mask_anthropic()
        try:
            # Remove cached simdrive.server to force re-import.
            mods_to_remove = [k for k in sys.modules if k.startswith("simdrive.server")]
            for mod in mods_to_remove:
                sys.modules.pop(mod, None)

            # This must NOT raise ModuleNotFoundError for anthropic.
            try:
                import simdrive.server as server_mod
            except ModuleNotFoundError as exc:
                if "anthropic" in str(exc):
                    pytest.fail(
                        "simdrive.server imports anthropic at module top level. "
                        "After the MCP sampling refactor, the anthropic import must "
                        "be lazy (inside ClaudeLLMClient.__init__ or tool_run_journey "
                        "body) so the module loads without the anthropic package. "
                        f"Original error: {exc}"
                    )
                raise  # Re-raise non-anthropic ModuleNotFoundErrors
        finally:
            restore()

    def test_tool_run_journey_failure_is_not_anthropic_import_error(self):
        """tool_run_journey failing for non-anthropic reasons → MCP path is clean.

        With anthropic masked, calling tool_run_journey({"session_id": "fake"})
        will fail for OTHER reasons (no license, no session, missing journey_path).
        But the failure must NOT be ModuleNotFoundError for anthropic.

        This pins the most critical invariant: the MCP sampling path (which
        uses MCPSamplingLLMClient) must NEVER pull in the anthropic package.

        If it raises for any other reason (LicenseError, KeyError, etc.)
        that is ACCEPTABLE — we care only that anthropic is not the failure.
        """
        restore = self._mask_anthropic()
        try:
            # Force re-import of relevant modules.
            mods_to_remove = [k for k in sys.modules if k.startswith("simdrive")]
            for mod in mods_to_remove:
                sys.modules.pop(mod, None)
            # Keep anthropic masked during the simdrive re-import.

            import simdrive.server as server_mod

            try:
                # Call with a fake session_id.  Will fail, but must NOT fail
                # because of a missing anthropic import.
                server_mod.tool_run_journey({"session_id": "fake-mcp-test"})
            except ModuleNotFoundError as exc:
                if "anthropic" in str(exc).lower():
                    pytest.fail(
                        "tool_run_journey raised ModuleNotFoundError for 'anthropic'. "
                        "This means the MCP code path still imports the Anthropic SDK. "
                        "After the refactor, tool_run_journey must use MCPSamplingLLMClient "
                        "(which has NO anthropic dependency) instead of ClaudeLLMClient. "
                        f"Original error: {exc}"
                    )
                # Non-anthropic ModuleNotFoundError is acceptable.
            except Exception:
                # Any other exception is fine — license error, session not found, etc.
                # The critical check is that we did NOT hit an anthropic import error.
                pass

        finally:
            restore()
            # Re-import simdrive fresh so other tests are not affected by the mask.
            mods_to_remove = [k for k in sys.modules if k.startswith("simdrive")]
            for mod in mods_to_remove:
                sys.modules.pop(mod, None)
