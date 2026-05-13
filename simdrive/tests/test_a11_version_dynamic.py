"""F-001: Dynamic __version__ via importlib.metadata with fallback.

Tests:
  1. version_matches_installed_package_metadata — simdrive.__version__ must equal
     importlib.metadata.version("simdrive") (not a hardcoded literal).
  2. version_fallback_when_metadata_missing — when PackageNotFoundError is raised,
     __version__ must be "0.0.0+local".

These tests FAIL on feat/v17-claude-native HEAD (3a22bd4) because __init__.py
hardcodes __version__ = "1.0.0a9" instead of using importlib.metadata.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import sys

import pytest


def test_version_matches_installed_package_metadata():
    """simdrive.__version__ must be resolved dynamically from package metadata.

    Importing the module and checking the value against a fresh
    importlib.metadata.version() call ensures the version is not hardcoded.

    Fails on 3a22bd4: __version__ = "1.0.0a9" is a literal; it will differ
    from the installed package metadata version (which may be "1.0.0a11" or
    similar once CodeAtlas bumps pyproject.toml).
    """
    # Get what the installed package reports independently.
    try:
        expected = importlib.metadata.version("simdrive")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("simdrive not installed as a package — install with `pip install -e .`")

    import simdrive
    importlib.reload(simdrive)  # reload to pick up any monkeypatch state
    assert simdrive.__version__ == expected, (
        f"simdrive.__version__ ({simdrive.__version__!r}) != "
        f"importlib.metadata.version('simdrive') ({expected!r}). "
        "F-001 requires __version__ to be resolved from package metadata, not hardcoded."
    )


def test_version_fallback_when_metadata_missing(monkeypatch):
    """When importlib.metadata.version raises PackageNotFoundError,
    __version__ must fall back to '0.0.0+local'.

    Implementation strategy: monkeypatch importlib.metadata.version inside the
    simdrive package namespace, then reload the module so the top-level
    assignment runs again with the patched call.

    Fails on 3a22bd4: __init__.py has a hardcoded literal — it never calls
    importlib.metadata.version() at all, so:
      (a) there is no fallback path to test, and
      (b) after reload the value is "1.0.0a9", not "0.0.0+local".
    """
    import importlib.metadata as _meta

    original_version = _meta.version

    def _raise_not_found(name: str) -> str:
        if name == "simdrive":
            raise _meta.PackageNotFoundError(name)
        return original_version(name)

    monkeypatch.setattr(_meta, "version", _raise_not_found)

    # Remove cached module so the top-level code runs again.
    simdrive_mod = sys.modules.pop("simdrive", None)
    try:
        import simdrive as sd_reloaded  # noqa: F401 — side-effect import
        result = sd_reloaded.__version__
    finally:
        # Restore original module reference regardless of test outcome.
        if simdrive_mod is not None:
            sys.modules["simdrive"] = simdrive_mod
        else:
            sys.modules.pop("simdrive", None)

    assert result == "0.0.0+local", (
        f"Expected fallback __version__ == '0.0.0+local' when PackageNotFoundError "
        f"is raised, but got {result!r}. "
        "F-001 requires this fallback to be implemented in simdrive/__init__.py."
    )
