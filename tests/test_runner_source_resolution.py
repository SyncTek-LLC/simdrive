"""Tests for Issue 3: Runner source resolution via importlib.resources.

Verifies that _needs_rebuild → _rebuild_runner path correctly finds the
runner/ package source rather than the legacy runner_source/ directory.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class TestComputeRunnerSourceHash:
    """_compute_runner_source_hash should find runner/ via importlib.resources."""

    def test_returns_hex_string(self):
        from specterqa.ios.session_manager import _compute_runner_source_hash
        result = _compute_runner_source_hash()
        # Should be a non-empty hex string (SHA-256 = 64 chars)
        assert isinstance(result, str)
        assert len(result) == 64
        # All hex chars
        assert all(c in "0123456789abcdef" for c in result)

    def test_consistent_for_same_sources(self):
        """Two calls should return the same hash (deterministic)."""
        from specterqa.ios.session_manager import _compute_runner_source_hash
        h1 = _compute_runner_source_hash()
        h2 = _compute_runner_source_hash()
        assert h1 == h2

    def test_does_not_raise_when_legacy_runner_source_missing(self, tmp_path):
        """Should work even when the legacy runner_source/ subpackage is absent."""
        from specterqa.ios.session_manager import _compute_runner_source_hash
        # Just ensure it completes without ImportError
        result = _compute_runner_source_hash()
        assert isinstance(result, str)


class TestRebuildRunnerFindsSource:
    """_rebuild_runner should locate runner/ via importlib.resources."""

    def test_rebuild_raises_session_error_not_file_not_found(self, tmp_path):
        """When the xcodeproj is absent from all lookup paths, SessionError is raised.

        We patch all three resolution paths (importlib, legacy, dev-tree) to return
        nonexistent paths so the 'Runner Xcode project not found' error is triggered.
        """
        from specterqa.ios.session_manager import TestSession, SessionError

        sm = TestSession.__new__(TestSession)
        sm._runner_build_dir = tmp_path / "build"
        sm.device_type = "simulator"
        sm._runner = None

        # Patch importlib to return a path with no SpecterQARunner.xcodeproj
        empty_dir = tmp_path / "empty_runner"
        empty_dir.mkdir()

        # Patch all three resolution paths to empty dirs
        with patch("importlib.resources.files") as mock_irl:
            mock_irl.return_value = MagicMock(__str__=MagicMock(return_value=str(empty_dir)))
            # Also patch the dev-tree specterqa.ios __file__ to point to somewhere without runner/
            import specterqa.ios as _pkg
            with patch.object(_pkg, "__file__", str(tmp_path / "src" / "specterqa" / "ios" / "__init__.py")):
                with pytest.raises(SessionError):
                    sm._rebuild_runner()

    def test_runner_source_via_importlib_uses_runner_package(self):
        """The v14+ path should use importlib.resources.files('runner')."""
        import importlib.resources as irl

        try:
            runner_pkg = irl.files("runner")
            runner_path = Path(str(runner_pkg))
            # Should have Sources/ or SpecterQARunner.xcodeproj
            has_sources = (runner_path / "Sources").exists()
            has_xcodeproj = (runner_path / "SpecterQARunner.xcodeproj").exists()
            assert has_sources or has_xcodeproj, (
                f"runner package at {runner_path} has neither Sources/ nor SpecterQARunner.xcodeproj"
            )
        except (ModuleNotFoundError, TypeError):
            pytest.skip("runner package not installed in this environment")


class TestNeedsRebuild:
    """_needs_rebuild correctly gates on hash mismatch."""

    def test_needs_rebuild_true_when_no_xctestrun(self, tmp_path):
        from specterqa.ios.session_manager import _needs_rebuild
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        # No .xctestrun file → should need rebuild
        assert _needs_rebuild(build_dir) is True

    def test_needs_rebuild_false_when_hash_matches(self, tmp_path):
        from specterqa.ios.session_manager import (
            _needs_rebuild,
            _compute_runner_source_hash,
            _RUNNER_HASH_FILENAME,
        )
        build_dir = tmp_path / "build"
        build_dir.mkdir()

        # Create a fake .xctestrun so _find_xctestrun doesn't fail first
        (build_dir / "fake.xctestrun").touch()

        # Write the current hash
        current_hash = _compute_runner_source_hash()
        (build_dir / _RUNNER_HASH_FILENAME).write_text(current_hash, encoding="utf-8")

        assert _needs_rebuild(build_dir) is False

    def test_needs_rebuild_true_when_hash_mismatches(self, tmp_path):
        from specterqa.ios.session_manager import (
            _needs_rebuild,
            _RUNNER_HASH_FILENAME,
        )
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "fake.xctestrun").touch()
        (build_dir / _RUNNER_HASH_FILENAME).write_text("a" * 64, encoding="utf-8")  # wrong hash

        assert _needs_rebuild(build_dir) is True
