"""Session-wide test bootstrap — [internal-tracker].5 paywall test fixture.

After PR #115 every MCP tool handler calls ``check_entitlement()`` which raises
``LicenseError [license_not_found]`` when ``~/.simdrive/license.json`` is absent.
CI runners have no license on disk, so 131 pre-existing tests that exercise
those handlers (test_unit.py, test_wda_act_integration.py, etc.) regressed.

Fix: at conftest module-load (before any test module imports ``simdrive`` and
therefore before ``_DEFAULT_LICENSE_PATH = Path.home()/...`` resolves), we
point ``HOME`` at a per-session temp dir and self-issue an offline dev trial
license into it via the existing ``simdrive.license.cli.cmd_trial_start``
function. Module-level (not fixture) timing matters: the license-path
constants in ``simdrive.license.{entitlement,trial,cli,trial_history}`` are
bound to ``Path.home()`` once at import time, so we must mutate ``HOME``
before any of those imports happen.

Tests that explicitly verify license-missing or license-expired behaviour can
opt out with ``@pytest.mark.no_license`` — the autouse fixture removes the
session license before such a test runs and restores it afterwards.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Module-load bootstrap — mutate HOME and write a license BEFORE any
# simdrive.license module gets imported (which would otherwise freeze the
# default path against the real ~/.simdrive).
# ---------------------------------------------------------------------------

# Persistent for the test session; cleaned up via finalizer registered below.
_FIXTURE_HOME = Path(tempfile.mkdtemp(prefix="simdrive-test-home-"))
_ORIGINAL_HOME = os.environ.get("HOME")

os.environ["HOME"] = str(_FIXTURE_HOME)

# Issue the license via the same Python API the CLI uses (no subprocess).
# NOTE: we pass ``offline_dev=True`` explicitly rather than setting the
# SIMDRIVE_OFFLINE_DEV env var globally — the env var would override
# ``offline_dev=False`` callers in tests (e.g. TestCloudUnreachable in
# test_trial_cli.py asserts the cloud path raises LicenseError on DNS
# failure, which requires the env var to be unset).
# The import has to happen AFTER HOME mutation so that the module-level
# _DEFAULT_LICENSE_PATH constants see the temp HOME.
from simdrive.license.cli import cmd_trial_start  # noqa: E402

_SESSION_LICENSE_PATH = _FIXTURE_HOME / ".simdrive" / "license.json"
_SESSION_LICENSE_PATH.parent.mkdir(parents=True, exist_ok=True)
cmd_trial_start(
    "ci@simdrive.test",
    license_path=_SESSION_LICENSE_PATH,
    offline_dev=True,
)

# Snapshot the license bytes so the no_license fixture can restore it.
_SESSION_LICENSE_BYTES = _SESSION_LICENSE_PATH.read_bytes()


def pytest_configure(config: pytest.Config) -> None:
    """Register the ``no_license`` marker so ``--strict-markers`` is happy."""
    config.addinivalue_line(
        "markers",
        "no_license: remove the session dev-trial license for this test "
        "(use for tests that explicitly verify license-missing behaviour).",
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Clean up the temp HOME at the end of the session."""
    # Restore HOME so any post-pytest teardown sees the real one.
    if _ORIGINAL_HOME is not None:
        os.environ["HOME"] = _ORIGINAL_HOME
    else:
        os.environ.pop("HOME", None)
    shutil.rmtree(_FIXTURE_HOME, ignore_errors=True)


# ---------------------------------------------------------------------------
# Per-test license toggle
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _session_license(request: pytest.FixtureRequest):
    """Remove the session license for tests marked ``no_license``.

    The license is restored after the test so the next test sees the gate
    pass again. Tests that don't have the marker simply inherit the
    session-wide license written at conftest module-load.
    """
    if request.node.get_closest_marker("no_license") is None:
        yield
        return

    if _SESSION_LICENSE_PATH.exists():
        _SESSION_LICENSE_PATH.unlink()
    try:
        yield
    finally:
        _SESSION_LICENSE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SESSION_LICENSE_PATH.write_bytes(_SESSION_LICENSE_BYTES)


@pytest.fixture(autouse=True)
def _clear_module_caches():
    """Reset module-level caches between tests so a value computed in one
    test (often against a mock UDID like 'TESTUDID') can't leak into the
    next. Cheap; only the dict is cleared, not the modules.

    Currently scoped to ``server._SCALE_CACHE_BY_UDID``. Add other module
    caches here as they're introduced.
    """
    from simdrive import server as _server
    _server._SCALE_CACHE_BY_UDID.clear()
    yield
    _server._SCALE_CACHE_BY_UDID.clear()


@pytest.fixture(autouse=True)
def _neutralize_self_restart(monkeypatch):
    """Globally neutralize the MCP server's drift-driven self-restart.

    The drift handler (F#1) calls ``_schedule_self_restart`` which spawns a
    Timer thread that runs ``os.execv`` — which would replace the pytest
    process. Any pre-existing test that patches ``_check_version_drift`` to
    return a string and then calls ``call_tool`` would trigger this. We
    autouse-patch the helpers to no-ops and reset the latch around every
    test so unit tests see deterministic state.
    """
    from simdrive import server as _server
    monkeypatch.setattr(_server, "_schedule_self_restart", lambda: None)
    monkeypatch.setattr(_server, "_do_self_restart", lambda: None)
    monkeypatch.setattr(_server, "_RESTART_SCHEDULED", False, raising=False)
    yield
    # Reset the latch so the next test starts fresh even if the test that
    # just ran flipped it via a deeper monkeypatch.
    _server._RESTART_SCHEDULED = False
