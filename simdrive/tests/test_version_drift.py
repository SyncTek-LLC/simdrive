"""Regression tests for Bug 2 — _disk_version() reads wrong package name (INIT-2026-543).

server.py:_disk_version() calls importlib.metadata.version("specterqa-ios")
but the package is now named "simdrive".  In the Example Reader dogfood environment
the old specterqa-ios 16.0.0a3 wheel was still installed, so _disk_version()
returned "16.0.0a3" — a perpetual mismatch with _LOADED_VERSION "1.0.0a2",
causing a false-positive _simdrive_warning on every single tool call.

Fix required: change the metadata lookup from "specterqa-ios" to "simdrive".

TDD: written BEFORE the fix. All tests must FAIL on current code.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


class TestDiskVersionReadsSimdrivePackage:

    def test_disk_version_reads_simdrive_package(self) -> None:
        """_disk_version() must query the 'simdrive' package, not 'specterqa-ios'.

        This test patches importlib.metadata.version to intercept which package
        name is looked up. If the lookup uses 'specterqa-ios' (current buggy code),
        the patched function raises PackageNotFoundError for 'specterqa-ios' but
        returns '1.0.0a2' for 'simdrive'.

        After the fix (_disk_version queries 'simdrive'), the function returns
        '1.0.0a2' which matches _LOADED_VERSION.

        Currently FAILS because _disk_version() queries 'specterqa-ios', not 'simdrive'.
        """
        import importlib.metadata
        from simdrive import server
        import simdrive

        # Force a fresh lookup by invalidating the TTL cache
        server._DISK_VERSION_CACHE["checked_at"] = 0.0
        server._DISK_VERSION_CACHE["version"] = None

        original_version = importlib.metadata.version

        def patched_metadata_version(package_name: str) -> str:
            """Accept 'simdrive', reject 'specterqa-ios' (old name)."""
            if package_name == "simdrive":
                return simdrive.__version__
            elif package_name == "specterqa-ios":
                raise importlib.metadata.PackageNotFoundError("specterqa-ios")
            return original_version(package_name)

        with patch("importlib.metadata.version", side_effect=patched_metadata_version):
            # Also invalidate cache inside the patch so it re-queries
            server._DISK_VERSION_CACHE["checked_at"] = 0.0
            server._DISK_VERSION_CACHE["version"] = None
            disk = server._disk_version()

        # After the fix: _disk_version() calls version('simdrive') → '1.0.0a2'
        # Before the fix: _disk_version() calls version('specterqa-ios') → raises
        #   PackageNotFoundError → returns None → test fails the assertion below
        assert disk == simdrive.__version__, (
            f"_disk_version() returned {disk!r} but should return {simdrive.__version__!r}. "
            "This means _disk_version() is still querying 'specterqa-ios' instead of 'simdrive'. "
            "Fix: change `_md.version('specterqa-ios')` to `_md.version('simdrive')` "
            "in server.py:_disk_version()."
        )

    def test_disk_version_old_wheel_causes_false_positive_warning(self) -> None:
        """Simulate the Example Reader dogfood environment where specterqa-ios 16.0.0a3
        was installed alongside simdrive 1.0.0a2.

        When _disk_version() queries 'specterqa-ios' and gets '16.0.0a3', it
        returns a version string that doesn't match _LOADED_VERSION '1.0.0a2',
        so _check_version_drift() fires a warning on every tool call.

        This test asserts that _disk_version() does NOT return the old
        specterqa-ios version when the simdrive package is present.

        Currently FAILS: _disk_version() queries 'specterqa-ios' and would
        return '16.0.0a3' if that wheel is present — but even without the old
        wheel installed, the test demonstrates the exact wrong query is made by
        patching it to return the stale version.
        """
        import importlib.metadata
        from simdrive import server
        import simdrive

        # Simulate the Example Reader environment: specterqa-ios 16.0.0a3 is installed
        def old_env_metadata_version(package_name: str) -> str:
            if package_name == "specterqa-ios":
                return "16.0.0a3"  # old wheel still present
            elif package_name == "simdrive":
                return simdrive.__version__
            raise importlib.metadata.PackageNotFoundError(package_name)

        with patch("importlib.metadata.version", side_effect=old_env_metadata_version):
            server._DISK_VERSION_CACHE["checked_at"] = 0.0
            server._DISK_VERSION_CACHE["version"] = None
            disk = server._disk_version()

        # On current (buggy) code: disk == "16.0.0a3" (reads specterqa-ios)
        # After fix: disk == "1.0.0a2" (reads simdrive)
        assert disk != "16.0.0a3", (
            f"_disk_version() returned '16.0.0a3' (the old specterqa-ios wheel version). "
            "This confirms _disk_version() is reading the wrong package name. "
            "Fix: change the lookup from 'specterqa-ios' to 'simdrive'."
        )
        assert disk == simdrive.__version__, (
            f"_disk_version() must return the simdrive version ({simdrive.__version__!r}), "
            f"not the specterqa-ios version. Got: {disk!r}"
        )

    def test_check_version_drift_no_false_positive_in_old_wheel_env(self) -> None:
        """In the Example Reader dogfood environment (specterqa-ios 16.0.0a3 installed),
        _check_version_drift() must return None (no warning) when simdrive
        is correctly installed as 'simdrive'.

        Currently FAILS: _disk_version() returns '16.0.0a3' via the specterqa-ios
        lookup, causing a spurious version drift warning on every tool call.
        """
        import importlib.metadata
        from simdrive import server
        import simdrive

        def old_env_metadata_version(package_name: str) -> str:
            if package_name == "specterqa-ios":
                return "16.0.0a3"
            elif package_name == "simdrive":
                return simdrive.__version__
            raise importlib.metadata.PackageNotFoundError(package_name)

        with patch("importlib.metadata.version", side_effect=old_env_metadata_version):
            server._DISK_VERSION_CACHE["checked_at"] = 0.0
            server._DISK_VERSION_CACHE["version"] = None
            warning = server._check_version_drift()

        assert warning is None, (
            f"_check_version_drift() returned a false-positive warning in an "
            f"environment where simdrive {simdrive.__version__!r} is correctly installed:\n"
            f"  {warning!r}\n"
            "Root cause: _disk_version() reads 'specterqa-ios' (returned '16.0.0a3') "
            f"instead of 'simdrive' (which would return {simdrive.__version__!r}). "
            "Fix: change the package name in _disk_version()."
        )

    def test_call_tool_injects_warning_in_example_dogfood_environment(self) -> None:
        """In the Example Reader dogfood environment (specterqa-ios 16.0.0a3 still installed),
        every call_tool() response INCORRECTLY gets _simdrive_warning injected
        because _disk_version() returns "16.0.0a3" via the stale package name.

        This test asserts that after the fix (querying 'simdrive' not 'specterqa-ios'),
        call_tool() does NOT inject a warning when the simdrive version matches.

        Currently FAILS: the buggy _disk_version() call gets "16.0.0a3" which
        != "1.0.0a2" (loaded version), so the warning IS injected — but it should
        not be. After the fix, _disk_version() correctly returns "1.0.0a2" from
        the 'simdrive' package, no drift is detected, no warning is injected.
        """
        import importlib.metadata
        from simdrive import server
        import simdrive

        # Simulate Example Reader dogfood: specterqa-ios 16.0.0a3 installed, simdrive 1.0.0a2 installed
        def example_env_metadata_version(package_name: str) -> str:
            if package_name == "specterqa-ios":
                return "16.0.0a3"  # old wheel still present → buggy code returns this
            elif package_name == "simdrive":
                return simdrive.__version__  # correct wheel present → fix returns this
            raise importlib.metadata.PackageNotFoundError(package_name)

        with patch("importlib.metadata.version", side_effect=example_env_metadata_version):
            server._DISK_VERSION_CACHE["checked_at"] = 0.0
            server._DISK_VERSION_CACHE["version"] = None
            result = server.call_tool("version", {})

        assert isinstance(result, dict), "call_tool('version', {}) must return a dict"
        # After the fix: _disk_version() returns '1.0.0a2' (from 'simdrive') == _LOADED_VERSION
        # → no warning injected.
        # Currently (before fix): _disk_version() returns '16.0.0a3' (from 'specterqa-ios')
        # != '1.0.0a2' → warning IS injected → test FAILS.
        assert "_simdrive_warning" not in result, (
            f"call_tool() injected a false-positive _simdrive_warning in the Example Reader "
            f"dogfood environment (specterqa-ios 16.0.0a3 + simdrive 1.0.0a2 installed). "
            f"Warning injected: {result.get('_simdrive_warning')!r}\n"
            "Root cause: _disk_version() queries 'specterqa-ios' (returned '16.0.0a3') "
            f"instead of 'simdrive' (which returns {simdrive.__version__!r}). "
            "Fix: change the package name lookup in server.py:_disk_version()."
        )
