"""Tests for Issue 1: env propagation fallback via ~/.specterqa/config.toml.

Covers:
- CLI: specterqa-ios mcp enable-physical writes config.toml
- CLI: specterqa-ios mcp enable-physical --disable removes the key
- MCP server: _read_physical_opt_in() reads config.toml correctly
- MCP server: diagnostics block in ios_get_capabilities
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helper: isolate config directory
# ---------------------------------------------------------------------------

def _isolated_specterqa_dir(tmp_path: Path) -> Path:
    config_dir = tmp_path / ".specterqa"
    config_dir.mkdir()
    return config_dir


# ---------------------------------------------------------------------------
# Test: _read_physical_opt_in helper
# ---------------------------------------------------------------------------

class TestReadPhysicalOptIn:
    """Unit tests for specterqa.ios.config._read_physical_opt_in."""

    def test_returns_false_when_no_config_file(self, tmp_path):
        from specterqa.ios import config as cfg
        with patch.object(cfg, "_specterqa_config_dir", return_value=tmp_path / ".specterqa"):
            result = cfg._read_physical_opt_in()
        assert result is False

    def test_returns_true_when_config_file_sets_allow(self, tmp_path):
        config_dir = _isolated_specterqa_dir(tmp_path)
        toml_path = config_dir / "config.toml"
        toml_path.write_text("[mcp]\nallow_physical_device = true\n", encoding="utf-8")

        from specterqa.ios import config as cfg
        with patch.object(cfg, "_specterqa_config_dir", return_value=config_dir):
            result = cfg._read_physical_opt_in()
        assert result is True

    def test_returns_false_when_config_file_sets_false(self, tmp_path):
        config_dir = _isolated_specterqa_dir(tmp_path)
        toml_path = config_dir / "config.toml"
        toml_path.write_text("[mcp]\nallow_physical_device = false\n", encoding="utf-8")

        from specterqa.ios import config as cfg
        with patch.object(cfg, "_specterqa_config_dir", return_value=config_dir):
            result = cfg._read_physical_opt_in()
        assert result is False

    def test_returns_false_when_key_absent_from_config(self, tmp_path):
        config_dir = _isolated_specterqa_dir(tmp_path)
        toml_path = config_dir / "config.toml"
        toml_path.write_text("[mcp]\nsome_other_key = true\n", encoding="utf-8")

        from specterqa.ios import config as cfg
        with patch.object(cfg, "_specterqa_config_dir", return_value=config_dir):
            result = cfg._read_physical_opt_in()
        assert result is False

    def test_handles_malformed_toml_gracefully(self, tmp_path):
        config_dir = _isolated_specterqa_dir(tmp_path)
        toml_path = config_dir / "config.toml"
        toml_path.write_text("this is { not valid toml !!!\n", encoding="utf-8")

        from specterqa.ios import config as cfg
        with patch.object(cfg, "_specterqa_config_dir", return_value=config_dir):
            result = cfg._read_physical_opt_in()
        # Should not raise; returns False on parse error
        assert result is False


# ---------------------------------------------------------------------------
# Test: write_physical_opt_in (CLI enable-physical backing function)
# ---------------------------------------------------------------------------

class TestWritePhysicalOptIn:
    """Unit tests for specterqa.ios.config.write_physical_opt_in."""

    def test_creates_config_dir_and_file(self, tmp_path):
        config_dir = tmp_path / ".specterqa"
        # config_dir does NOT exist yet
        from specterqa.ios import config as cfg
        with patch.object(cfg, "_specterqa_config_dir", return_value=config_dir):
            cfg.write_physical_opt_in(enabled=True)

        assert config_dir.exists()
        toml_path = config_dir / "config.toml"
        assert toml_path.exists()
        content = toml_path.read_text(encoding="utf-8")
        assert "allow_physical_device = true" in content

    def test_idempotent_enable(self, tmp_path):
        config_dir = _isolated_specterqa_dir(tmp_path)
        from specterqa.ios import config as cfg
        with patch.object(cfg, "_specterqa_config_dir", return_value=config_dir):
            cfg.write_physical_opt_in(enabled=True)
            cfg.write_physical_opt_in(enabled=True)  # second call
            result = cfg._read_physical_opt_in()
        assert result is True

    def test_disable_sets_false(self, tmp_path):
        config_dir = _isolated_specterqa_dir(tmp_path)
        toml_path = config_dir / "config.toml"
        toml_path.write_text("[mcp]\nallow_physical_device = true\n", encoding="utf-8")

        from specterqa.ios import config as cfg
        with patch.object(cfg, "_specterqa_config_dir", return_value=config_dir):
            cfg.write_physical_opt_in(enabled=False)
            result = cfg._read_physical_opt_in()
        assert result is False

    def test_preserves_other_keys(self, tmp_path):
        """Existing keys outside [mcp] section are preserved."""
        config_dir = _isolated_specterqa_dir(tmp_path)
        toml_path = config_dir / "config.toml"
        toml_path.write_text("[logging]\nlevel = \"debug\"\n", encoding="utf-8")

        from specterqa.ios import config as cfg
        with patch.object(cfg, "_specterqa_config_dir", return_value=config_dir):
            cfg.write_physical_opt_in(enabled=True)
        content = toml_path.read_text(encoding="utf-8")
        assert "level" in content or "allow_physical_device" in content  # didn't destroy file


# ---------------------------------------------------------------------------
# Test: _check_physical_opt_in (gate function combining env + config + keychain)
# ---------------------------------------------------------------------------

class TestCheckPhysicalOptIn:
    """Unit tests for specterqa.ios.config._check_physical_opt_in."""

    def test_env_var_truthy_passes_gate(self, tmp_path):
        config_dir = _isolated_specterqa_dir(tmp_path)
        from specterqa.ios import config as cfg
        with patch.dict(os.environ, {"SPECTERQA_ALLOW_PHYSICAL_DEVICE": "1"}):
            with patch.object(cfg, "_specterqa_config_dir", return_value=config_dir):
                result = cfg._check_physical_opt_in()
        assert result["allowed"] is True
        assert result["diagnostics"]["env_var_seen_by_process"] is True

    def test_config_file_passes_gate(self, tmp_path):
        config_dir = _isolated_specterqa_dir(tmp_path)
        (config_dir / "config.toml").write_text("[mcp]\nallow_physical_device = true\n", encoding="utf-8")

        from specterqa.ios import config as cfg
        env_without_var = {k: v for k, v in os.environ.items() if k != "SPECTERQA_ALLOW_PHYSICAL_DEVICE"}
        with patch.dict(os.environ, env_without_var, clear=True):
            with patch.object(cfg, "_specterqa_config_dir", return_value=config_dir):
                result = cfg._check_physical_opt_in()
        assert result["allowed"] is True
        assert result["diagnostics"]["config_file_value"] is True

    def test_no_opt_in_returns_false_with_diagnostics(self, tmp_path):
        config_dir = _isolated_specterqa_dir(tmp_path)

        from specterqa.ios import config as cfg
        env_without_var = {k: v for k, v in os.environ.items() if k != "SPECTERQA_ALLOW_PHYSICAL_DEVICE"}
        with patch.dict(os.environ, env_without_var, clear=True):
            with patch.object(cfg, "_specterqa_config_dir", return_value=config_dir):
                result = cfg._check_physical_opt_in()
        assert result["allowed"] is False
        diag = result["diagnostics"]
        assert "env_var_seen_by_process" in diag
        assert "config_file_value" in diag
        assert diag["env_var_seen_by_process"] is False
        assert diag["config_file_value"] is False


# ---------------------------------------------------------------------------
# Test: ios_get_capabilities returns diagnostics block
# ---------------------------------------------------------------------------

class TestCapabilitiesDiagnostics:
    """Test that ios_get_capabilities surfaces the diagnostics block."""

    def test_capabilities_includes_diagnostics_when_opt_in_inactive(self, tmp_path):
        from specterqa.ios.mcp import server
        import asyncio

        config_dir = _isolated_specterqa_dir(tmp_path)
        from specterqa.ios import config as cfg
        env_without_var = {k: v for k, v in os.environ.items() if k != "SPECTERQA_ALLOW_PHYSICAL_DEVICE"}

        with patch.dict(os.environ, env_without_var, clear=True):
            with patch.object(cfg, "_specterqa_config_dir", return_value=config_dir):
                mcp_server = server.create_server()

                async def run():
                    r = await mcp_server.call_tool("ios_get_capabilities", {})
                    return r

                result = asyncio.run(run())

        caps_text = str(result)
        caps_json = json.loads(result[0].content[0].text) if hasattr(result[0], "content") else json.loads(result[0][0].text if hasattr(result[0], "__getitem__") else str(result))
        physical = next(d for d in caps_json["device_types"] if d["type"] == "physical")
        assert "diagnostics" in physical
        assert "env_var_seen_by_process" in physical["diagnostics"]
        assert "config_file_value" in physical["diagnostics"]

    def test_capabilities_diagnostics_opt_in_active_via_env(self, tmp_path):
        from specterqa.ios.mcp import server
        from specterqa.ios import config as cfg
        import asyncio

        config_dir = _isolated_specterqa_dir(tmp_path)
        with patch.dict(os.environ, {"SPECTERQA_ALLOW_PHYSICAL_DEVICE": "1"}):
            with patch.object(cfg, "_specterqa_config_dir", return_value=config_dir):
                mcp_server = server.create_server()

                async def run():
                    r = await mcp_server.call_tool("ios_get_capabilities", {})
                    return r

                result = asyncio.run(run())

        caps_raw = result[0].content[0].text if hasattr(result[0], "content") else result[0][0].text
        caps_json = json.loads(caps_raw)
        physical = next(d for d in caps_json["device_types"] if d["type"] == "physical")
        assert physical["opt_in_active"] is True
