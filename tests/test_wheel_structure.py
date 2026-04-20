"""Wheel structure assertions — v14.0.0b1 Phase 2 cleanup.

Asserts:
  1. runner/__init__.py exists and is a valid Python module.
  2. src/specterqa/ios/runner_source/ is GONE (deleted in Phase 2).
  3. setup.py has NO build_py override (class build_py removed).
  4. pyproject.toml uses packages.find (not manual package list).

Run:
    /opt/homebrew/bin/python3.11 -m pytest tests/test_wheel_structure.py -xvs
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# 1. runner/__init__.py exists and is importable as a module
# ---------------------------------------------------------------------------


def test_runner_init_exists():
    """runner/__init__.py must exist."""
    init_path = REPO_ROOT / "runner" / "__init__.py"
    assert init_path.exists(), (
        f"runner/__init__.py not found at {init_path}. "
        "Phase 2 requires creating runner/__init__.py."
    )


def test_runner_init_is_valid_python():
    """runner/__init__.py must be valid Python (no syntax errors)."""
    init_path = REPO_ROOT / "runner" / "__init__.py"
    if not init_path.exists():
        pytest.skip("runner/__init__.py not yet created")
    content = init_path.read_text(encoding="utf-8")
    # compile() raises SyntaxError on bad Python
    try:
        compile(content, str(init_path), "exec")
    except SyntaxError as exc:
        pytest.fail(f"runner/__init__.py has a syntax error: {exc}")


# ---------------------------------------------------------------------------
# 2. runner_source/ is GONE
# ---------------------------------------------------------------------------


def test_runner_source_deleted():
    """src/specterqa/ios/runner_source/ must be deleted in Phase 2."""
    runner_source = REPO_ROOT / "src" / "specterqa" / "ios" / "runner_source"
    assert not runner_source.exists(), (
        f"runner_source/ still exists at {runner_source}. "
        "Phase 2 requires: git rm -r src/specterqa/ios/runner_source."
    )


# ---------------------------------------------------------------------------
# 3. setup.py has NO build_py override
# ---------------------------------------------------------------------------


def test_setup_py_no_build_py_override():
    """setup.py must NOT define a class build_py override."""
    setup_py = REPO_ROOT / "setup.py"
    assert setup_py.exists(), "setup.py must exist (even if minimal)"
    content = setup_py.read_text(encoding="utf-8")
    assert "class build_py" not in content, (
        "setup.py still has 'class build_py' override. "
        "Phase 2 requires removing the build_py subclass entirely."
    )


def test_setup_py_no_runner_source_sync():
    """setup.py must not contain runner_source sync logic (_sync_runner_tree)."""
    setup_py = REPO_ROOT / "setup.py"
    if not setup_py.exists():
        pytest.skip("setup.py not found")
    content = setup_py.read_text(encoding="utf-8")
    assert "_sync_runner_tree" not in content, (
        "setup.py still contains _sync_runner_tree. "
        "Phase 2 requires removing the runner sync override."
    )


# ---------------------------------------------------------------------------
# 4. pyproject.toml uses packages.find (auto-discovery)
# ---------------------------------------------------------------------------


def test_pyproject_uses_packages_find():
    """pyproject.toml must use [tool.setuptools.packages.find] for auto-discovery."""
    pyproject = REPO_ROOT / "pyproject.toml"
    assert pyproject.exists(), "pyproject.toml must exist"
    content = pyproject.read_text(encoding="utf-8")
    assert "[tool.setuptools.packages.find]" in content, (
        "pyproject.toml does not have [tool.setuptools.packages.find] section. "
        "Phase 2 requires switching to auto-discovery."
    )


def test_pyproject_no_runner_source_package_data():
    """pyproject.toml must not have runner_source package-data globs."""
    pyproject = REPO_ROOT / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    assert "runner_source" not in content, (
        "pyproject.toml still references runner_source in package-data. "
        "Phase 2 requires removing these globs."
    )


def test_pyproject_version_is_final():
    """pyproject.toml version must be 14.0.2 (v14.0.2 morning-triage release)."""
    pyproject = REPO_ROOT / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    assert 'version = "14.0.2"' in content, (
        "pyproject.toml version is not 14.0.2. "
        "Bump version from 14.0.1 to 14.0.2."
    )
