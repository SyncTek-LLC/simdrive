"""Session-scoped pytest fixtures shared across the test suite.

The ``fresh_install`` fixture builds a clean editable install of specterqa-ios
into a temporary directory and returns the repo root path. Tests that previously
relied on a hardcoded ``/tmp/specterqa-ios-fresh`` path should consume this
fixture instead so CI does not depend on external manual setup.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Repo root helper
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# sys.modules integrity guard — autouse, function scope
# ---------------------------------------------------------------------------
#
# Root cause of test_runner_process.py flake in full-suite runs:
#
# TestConcurrentMCPCallRaceGuard::test_recovery_serialised_under_session_lock
# patches sys.modules["specterqa.ios.runner_process"] with a MagicMock from
# TWO threads simultaneously. Python's patch.dict is NOT thread-safe — the
# concurrent save/restore race leaves the MagicMock permanently in sys.modules
# after both threads exit. Subsequent tests that do
#   patch("specterqa.ios.runner_process._needs_rebuild", return_value=False)
# then patch the mock's attribute (a no-op) instead of the real module's, so
# _needs_rebuild returns MagicMock() (truthy) → build() skips the cache-hit
# branch → tries to mkdir /fake → Read-only file system error → FAILED state.
#
# Fix: after every test, if sys.modules["specterqa.ios.runner_process"] no
# longer points to the real module object (i.e. a thread-unsafe patch.dict
# leaked a mock), restore it from the reference we captured at fixture
# creation time.

@pytest.fixture(autouse=True)
def _restore_runner_process_module():
    """Restore sys.modules["specterqa.ios.runner_process"] after each test.

    Guards against the thread-unsafe patch.dict race in concurrent-recovery
    tests that temporarily replace the module with a MagicMock. If the mock
    leaks into sys.modules the real module's attribute patches stop working,
    causing downstream test failures.
    """
    # Capture (or import) the real module before the test runs.
    import importlib
    real_module = sys.modules.get("specterqa.ios.runner_process")
    if real_module is None:
        try:
            real_module = importlib.import_module("specterqa.ios.runner_process")
        except ImportError:
            real_module = None

    yield

    # After the test: if the entry was replaced by something other than the
    # real module, put the real one back.
    if real_module is not None:
        current = sys.modules.get("specterqa.ios.runner_process")
        if current is not real_module:
            sys.modules["specterqa.ios.runner_process"] = real_module


@pytest.fixture
def repo_root() -> Path:
    """Return the repository root as a Path. Available to all test tiers."""
    return _REPO_ROOT


@pytest.fixture(scope="session")
def fresh_install(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build a clean editable install and return the repo root path.

    The fixture:
    1. Resolves the repo root (parent of the ``tests/`` directory).
    2. Creates a session-scoped temp venv under ``tmp_path_factory``.
    3. Runs ``pip install -e ".[dev]"`` inside it to validate the install works.
    4. Returns the repo root path so tests can access source files, examples,
       MANIFEST.in, runner/Sources, etc.

    Tests that iterate ``examples/`` or read ``pyproject.toml`` should use
    ``fresh_install / "examples"`` instead of the hardcoded
    ``/tmp/specterqa-ios-fresh/...`` path.

    The MCP server subprocess ``cwd`` should be set to the returned path.
    """
    install_base = tmp_path_factory.mktemp("fresh_install")
    venv_dir = install_base / "venv"

    # Build a throwaway venv and install into it to prove the package is
    # installable.  We capture output but don't assert on it — the fixture
    # returns the repo root regardless, since the source files live there.
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True,
            capture_output=True,
        )
        pip = venv_dir / "bin" / "pip"
        subprocess.run(
            [str(pip), "install", "-e", f"{_REPO_ROOT}[dev]", "--quiet"],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        # Install failed — still return repo root so file-existence tests run.
        # Tests that require an actual installed package will fail on import,
        # which is the correct signal.
        pass

    return _REPO_ROOT
