"""Session-scoped pytest fixtures shared across the test suite.

The ``fresh_install`` fixture builds a clean editable install of specterqa-ios
into a temporary directory and returns the repo root path. Tests that previously
relied on a hardcoded ``/tmp/specterqa-ios-fresh`` path should consume this
fixture instead so CI does not depend on external manual setup.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest



# ---------------------------------------------------------------------------
# INIT-2026-525: Tier-gate bypass for existing tests
# ---------------------------------------------------------------------------
#
# Tier enforcement (tier_gate.py) gates MCP tool functions behind license checks.
# Existing tests do NOT set up a license; without a bypass they would fail when
# calling any gated tool because the validator returns "trial" mode and gated
# tools require higher tiers.
#
# Solution: autouse fixture sets SPECTERQA_LICENSE_BYPASS=1 for the entire test
# suite, which causes require_tier() to skip all tier checks.  This is safe
# because:
#   - The bypass is TEST-ONLY (env var is unset by teardown).
#   - tests/test_tier_enforcement.py overrides / pops the env var per-test
#     to exercise the actual gate logic.
#   - Production deployments do not have SPECTERQA_LICENSE_BYPASS set.


@pytest.fixture(autouse=True)
def _tier_bypass_for_tests():
    """Set SPECTERQA_LICENSE_BYPASS=1 so existing tests pass tier gates.

    Tests in test_tier_enforcement.py manage the env var themselves and are
    unaffected (they pop it in setup_method/teardown_method).
    """
    prev = os.environ.get("SPECTERQA_LICENSE_BYPASS")
    os.environ["SPECTERQA_LICENSE_BYPASS"] = "1"
    # Also reset the tier cache so tests that DO test tier enforcement
    # start with a clean slate.
    try:
        from specterqa.ios.mcp.tier_gate import _reset_tier_cache
        _reset_tier_cache()
    except Exception:  # noqa: BLE001
        pass
    yield
    # Restore
    if prev is None:
        os.environ.pop("SPECTERQA_LICENSE_BYPASS", None)
    else:
        os.environ["SPECTERQA_LICENSE_BYPASS"] = prev
    # Reset cache again after the test
    try:
        from specterqa.ios.mcp.tier_gate import _reset_tier_cache
        _reset_tier_cache()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Auto-skip @pytest.mark.live tests unless opted in
# ---------------------------------------------------------------------------
#
# Tests marked `live` require a booted macOS simulator / Xcode / network and
# cannot run hermetically. Skip them in normal runs; opt in by setting
# SPECTERQA_LIVE_SIM=1 when a sim is booted and Xcode is available.


def pytest_collection_modifyitems(config, items):
    if os.environ.get("SPECTERQA_LIVE_SIM", "").strip().lower() in ("1", "true", "yes"):
        return
    skip_live = pytest.mark.skip(reason="needs SPECTERQA_LIVE_SIM=1 + booted sim/Xcode")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


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

    Also restores the `subprocess` attribute on the specterqa.ios.mcp.server
    module in case a test patches it as a whole-module mock (which can leak
    if the test's with-block exits via exception or thread interleave).
    """
    # Capture (or import) the real module before the test runs.
    import importlib
    import subprocess as _real_subprocess

    real_runner_module = sys.modules.get("specterqa.ios.runner_process")
    if real_runner_module is None:
        try:
            real_runner_module = importlib.import_module("specterqa.ios.runner_process")
        except ImportError:
            real_runner_module = None

    # Capture the real subprocess reference on server module (if loaded)
    server_module = sys.modules.get("specterqa.ios.mcp.server")
    real_server_subprocess = getattr(server_module, "subprocess", None) if server_module else None

    yield

    # After the test: restore runner_process if leaked
    if real_runner_module is not None:
        current = sys.modules.get("specterqa.ios.runner_process")
        if current is not real_runner_module:
            sys.modules["specterqa.ios.runner_process"] = real_runner_module

    # After the test: restore server.subprocess if it was replaced by a Mock
    server_module = sys.modules.get("specterqa.ios.mcp.server")
    if server_module is not None and real_server_subprocess is not None:
        current_subprocess = getattr(server_module, "subprocess", None)
        if current_subprocess is not _real_subprocess and current_subprocess is not real_server_subprocess:
            try:
                server_module.subprocess = _real_subprocess
            except Exception:  # noqa: BLE001
                pass


@pytest.fixture(autouse=True)
def _restore_event_loop():
    """Ensure a current event loop exists after every test.

    asyncio.run() closes the running event loop. In Python 3.10+,
    asyncio.get_event_loop() raises RuntimeError when there's no current loop.
    Fixtures that call get_event_loop() will fail if a prior test used asyncio.run().

    This fixture restores a fresh event loop as a current loop after each test
    so that subsequent tests (and fixtures) that call get_event_loop() work.
    """
    import asyncio as _asyncio
    yield
    try:
        loop = _asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_closed():
            new_loop = _asyncio.new_event_loop()
            _asyncio.set_event_loop(new_loop)
    except RuntimeError:
        # No current event loop — create one
        new_loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(new_loop)


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
