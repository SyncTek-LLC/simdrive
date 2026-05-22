"""Tests for SimDrive F#1: MCP server self-restart on version drift.

When the on-disk wheel version differs from the loaded version, the MCP
server should:

  1. Still return the current tool result so the caller is not lost.
  2. Annotate the result with both ``_simdrive_warning`` (existing) and a
     new ``_simdrive_action`` field set to ``"restarting"`` (action is
     scheduled, exec happens after the response flushes).
  3. Schedule a background re-exec (``os.execv``) so the next tool call
     lands on the new on-disk code.
  4. Honor the ``SIMDRIVE_NO_AUTO_RESTART`` env-var opt-out: when set to
     a truthy value, the server warns but does NOT schedule a re-exec
     and does NOT add the ``_simdrive_action`` field.

Review-feedback hardening (PR #143 round 2):
  - Auto-restart only on UPGRADES, never downgrades (oscillation guard).
  - Thread-safe latch — concurrent async dispatches schedule exactly one
    timer, not N.
  - Loop guard via ``SIMDRIVE_RESTART_COUNT`` env var carried across exec.
  - Re-exec target is the console_script wrapper, not ``python -m``.
  - Action field is ``"restarting"`` (future tense), not ``"restarted"``.
  - When auto-restart is disabled, warning text drops the auto-restart
    language.

The tests monkeypatch the re-exec trigger so no real process replacement
happens during pytest.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
from pathlib import Path

import pytest

from simdrive import server


# ---------------------------------------------------------------------------
# Helpers — capture re-exec scheduling without actually calling os.execv.
# ---------------------------------------------------------------------------


class _ExecRecorder:
    """Stand-in for the scheduler; tracks invocations instead of forking."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args, **kwargs) -> None:  # noqa: D401 — simple recorder
        self.calls += 1


@pytest.fixture
def patched_drift(monkeypatch):
    """Force upgrade-drift to be detected and capture re-exec scheduling."""
    monkeypatch.setattr(
        server, "_check_version_drift", lambda: "Auto-restarting to pick up disk version 9.9.9."
    )
    # Force the upgrade check to return True so the gating logic in
    # _maybe_handle_drift treats this as an UPGRADE (not downgrade).
    monkeypatch.setattr(server, "_is_upgrade", lambda disk, loaded: True)
    monkeypatch.setattr(server, "_disk_version", lambda: "9.9.9")
    rec = _ExecRecorder()
    monkeypatch.setattr(server, "_schedule_self_restart", rec)
    # Reset the "already-scheduled" latch so each test starts fresh.
    monkeypatch.setattr(server, "_RESTART_SCHEDULED", False, raising=False)
    # Clear restart loop counter so the loop guard doesn't trip.
    monkeypatch.delenv(server._RESTART_COUNT_ENV, raising=False)
    return rec


# ---------------------------------------------------------------------------
# call_tool (sync) path
# ---------------------------------------------------------------------------


def test_call_tool_on_drift_emits_action_restarting_and_schedules(patched_drift, monkeypatch):
    """Drift detected => result has _simdrive_action='restarting' + scheduler fired."""
    monkeypatch.delenv("SIMDRIVE_NO_AUTO_RESTART", raising=False)
    result = server.call_tool("version", {})
    # NOTE: action is "restarting" (future tense) per review feedback — the
    # exec happens after the response flushes, not before.
    assert result.get("_simdrive_action") == "restarting"
    assert result.get("_simdrive_warning") is not None
    assert patched_drift.calls == 1


def test_call_tool_opt_out_env_disables_restart(monkeypatch):
    """SIMDRIVE_NO_AUTO_RESTART=1 => warning only, no action, no scheduler call."""
    monkeypatch.setattr(server, "_disk_version", lambda: "9.9.9")
    monkeypatch.setattr(server, "_is_upgrade", lambda disk, loaded: True)
    rec = _ExecRecorder()
    monkeypatch.setattr(server, "_schedule_self_restart", rec)
    monkeypatch.setattr(server, "_RESTART_SCHEDULED", False, raising=False)
    monkeypatch.delenv(server._RESTART_COUNT_ENV, raising=False)
    monkeypatch.setenv("SIMDRIVE_NO_AUTO_RESTART", "1")

    result = server.call_tool("version", {})
    assert result.get("_simdrive_warning") is not None
    assert "_simdrive_action" not in result
    assert rec.calls == 0


def test_call_tool_no_drift_no_action_no_scheduler(monkeypatch):
    """When versions match, neither warning nor action nor scheduler fires."""
    monkeypatch.setattr(server, "_check_version_drift", lambda: None)
    rec = _ExecRecorder()
    monkeypatch.setattr(server, "_schedule_self_restart", rec)
    monkeypatch.setattr(server, "_RESTART_SCHEDULED", False, raising=False)
    monkeypatch.delenv("SIMDRIVE_NO_AUTO_RESTART", raising=False)

    result = server.call_tool("version", {})
    assert "_simdrive_warning" not in result
    assert "_simdrive_action" not in result
    assert rec.calls == 0


def test_call_tool_restart_only_scheduled_once_across_calls(patched_drift, monkeypatch):
    """A second drifted call should NOT double-schedule the restart."""
    monkeypatch.delenv("SIMDRIVE_NO_AUTO_RESTART", raising=False)

    server.call_tool("version", {})
    server.call_tool("version", {})
    assert patched_drift.calls == 1


# ---------------------------------------------------------------------------
# call_tool_async (MCP server hot path)
# ---------------------------------------------------------------------------


def test_call_tool_async_on_drift_emits_action_restarting(patched_drift, monkeypatch):
    monkeypatch.delenv("SIMDRIVE_NO_AUTO_RESTART", raising=False)

    async def _go():
        return await server.call_tool_async("version", {})

    result = asyncio.run(_go())
    assert result.get("_simdrive_action") == "restarting"
    assert result.get("_simdrive_warning") is not None
    assert patched_drift.calls == 1


def test_call_tool_async_opt_out_env_disables_restart(monkeypatch):
    monkeypatch.setattr(server, "_disk_version", lambda: "9.9.9")
    monkeypatch.setattr(server, "_is_upgrade", lambda disk, loaded: True)
    rec = _ExecRecorder()
    monkeypatch.setattr(server, "_schedule_self_restart", rec)
    monkeypatch.setattr(server, "_RESTART_SCHEDULED", False, raising=False)
    monkeypatch.delenv(server._RESTART_COUNT_ENV, raising=False)
    monkeypatch.setenv("SIMDRIVE_NO_AUTO_RESTART", "1")

    async def _go():
        return await server.call_tool_async("version", {})

    result = asyncio.run(_go())
    assert result.get("_simdrive_warning") is not None
    assert "_simdrive_action" not in result
    assert rec.calls == 0


# ---------------------------------------------------------------------------
# Warning text — context-aware on opt-out and upgrade/downgrade.
# ---------------------------------------------------------------------------


def test_check_version_drift_warning_mentions_auto_restart(monkeypatch):
    """When auto-restart is enabled on an upgrade, warning mentions auto-restart."""
    monkeypatch.setattr(server, "_disk_version", lambda: "9.9.9")
    monkeypatch.setattr(server, "_LOADED_VERSION", "1.0.0", raising=False)
    monkeypatch.delenv("SIMDRIVE_NO_AUTO_RESTART", raising=False)
    msg = server._check_version_drift()
    assert msg is not None
    assert "9.9.9" in msg
    assert "auto-restart" in msg.lower() or "restarting" in msg.lower()


def test_check_version_drift_warning_omits_autorestart_when_disabled(monkeypatch):
    """SUGGESTION: when SIMDRIVE_NO_AUTO_RESTART=1, drop auto-restart language.

    The warning should tell the user to restart manually, not promise a
    pending auto-restart that will never come.
    """
    monkeypatch.setattr(server, "_disk_version", lambda: "9.9.9")
    monkeypatch.setattr(server, "_LOADED_VERSION", "1.0.0", raising=False)
    monkeypatch.setenv("SIMDRIVE_NO_AUTO_RESTART", "1")
    msg = server._check_version_drift()
    assert msg is not None
    assert "9.9.9" in msg
    assert "manually" in msg.lower()
    # Should NOT promise an auto-restart since the env-var opts out.
    assert "auto-restarting" not in msg.lower()


def test_check_version_drift_downgrade_warns_but_no_autorestart_language(monkeypatch):
    """MUST-FIX #3: downgrade scenario warns but never mentions auto-restart.

    The reviewer's concern: returning a warning on any disk != loaded
    mismatch (including downgrades) could oscillate if a wheel reports
    inconsistent versions. The new behavior: still warn, but with text
    that explicitly says "not auto-restarting".
    """
    monkeypatch.setattr(server, "_disk_version", lambda: "0.9.0")
    monkeypatch.setattr(server, "_LOADED_VERSION", "1.0.0", raising=False)
    msg = server._check_version_drift()
    assert msg is not None
    assert "0.9.0" in msg
    assert "not auto-restarting" in msg.lower() or "older" in msg.lower()


# ---------------------------------------------------------------------------
# MUST-FIX #1 — Re-exec target uses the console_script wrapper.
# ---------------------------------------------------------------------------


def test_resolve_exec_target_uses_console_script_when_argv0_absolute(monkeypatch):
    """When argv[0] is an absolute path to the simdrive wrapper, exec it directly."""
    fake_path = "/usr/local/bin/simdrive"
    monkeypatch.setattr(sys, "argv", [fake_path, "--flag", "value"])
    target, new_argv = server._resolve_exec_target()
    assert target == fake_path
    assert new_argv[0] == fake_path
    assert new_argv[1:] == ["--flag", "value"]


def test_resolve_exec_target_falls_back_to_path_lookup(monkeypatch):
    """When argv[0] is bare 'simdrive' (no path), use execvp via PATH."""
    monkeypatch.setattr(sys, "argv", ["simdrive", "--flag"])
    target, new_argv = server._resolve_exec_target()
    assert target == "simdrive"
    # No path separators => caller must use execvp.
    assert os.sep not in target
    assert new_argv == ["simdrive", "--flag"]


def test_resolve_exec_target_falls_back_to_python_dash_m_for_dev_invocation(monkeypatch):
    """When launched via `python -m simdrive.server`, fall back to that form."""
    # Simulate `python -m simdrive.server`: argv[0] is the Python interpreter
    # or a path that does NOT end in "simdrive".
    monkeypatch.setattr(sys, "argv", ["/usr/bin/python3", "--debug"])
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
    target, new_argv = server._resolve_exec_target()
    assert target == "/usr/bin/python3"
    assert "-m" in new_argv
    assert "simdrive.server" in new_argv


# ---------------------------------------------------------------------------
# MUST-FIX #2 — Latch is thread-safe under concurrent claims.
# ---------------------------------------------------------------------------


def test_try_claim_restart_atomic_under_concurrent_access(monkeypatch):
    """Exactly ONE caller wins the claim across many concurrent threads.

    Reviewer concern: ``_RESTART_SCHEDULED`` was read-then-set without a
    lock, so two concurrent async dispatches could both pass the check
    and schedule two timers. The fix wraps check-and-set in a
    module-level ``threading.Lock`` (``_RESTART_LATCH``) and exposes a
    single ``_try_claim_restart()`` API used by both sync and async paths.
    """
    monkeypatch.setattr(server, "_RESTART_SCHEDULED", False, raising=False)
    n_threads = 32
    barrier = threading.Barrier(n_threads)
    winners: list[bool] = []
    winners_lock = threading.Lock()

    def _race():
        barrier.wait()  # maximize contention by releasing all threads at once
        claimed = server._try_claim_restart()
        with winners_lock:
            winners.append(claimed)

    threads = [threading.Thread(target=_race) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one thread should have claimed the slot.
    assert sum(1 for w in winners if w) == 1
    assert sum(1 for w in winners if not w) == n_threads - 1
    # State should reflect that the claim is held.
    assert server._RESTART_SCHEDULED is True


def test_lock_based_latch_claimed_exactly_once_across_n_concurrent_dispatches(
    patched_drift, monkeypatch
):
    """SUGGESTION: lock-based latch is acquired exactly once across N dispatches.

    Simulates N concurrent ``call_tool_async`` invocations and asserts the
    scheduler is invoked at most once total.
    """
    monkeypatch.delenv("SIMDRIVE_NO_AUTO_RESTART", raising=False)

    async def _dispatch_n(n: int):
        # Use asyncio.gather to dispatch N simulated concurrent tool calls.
        return await asyncio.gather(*[server.call_tool_async("version", {}) for _ in range(n)])

    results = asyncio.run(_dispatch_n(16))
    # All N calls should have the action field set (every drifted result
    # advertises the pending restart).
    assert all(r.get("_simdrive_action") == "restarting" for r in results)
    # But the scheduler should fire EXACTLY ONCE — not N times.
    assert patched_drift.calls == 1


# ---------------------------------------------------------------------------
# MUST-FIX #3 — Downgrade scenario + loop guard counter.
# ---------------------------------------------------------------------------


def test_is_upgrade_only_true_for_strictly_newer(monkeypatch):
    """_is_upgrade returns True only when disk > loaded under PEP 440 parsing."""
    assert server._is_upgrade("1.0.1", "1.0.0") is True
    assert server._is_upgrade("1.0.0", "1.0.0") is False
    assert server._is_upgrade("0.9.0", "1.0.0") is False
    # Pre-release ordering (1.0.0b3 < 1.0.0).
    assert server._is_upgrade("1.0.0b3", "1.0.0") is False
    assert server._is_upgrade("1.0.0", "1.0.0b3") is True
    # None/empty inputs return False (never auto-restart on unknown).
    assert server._is_upgrade(None, "1.0.0") is False
    assert server._is_upgrade("1.0.0", None) is False


def test_downgrade_does_not_schedule_restart(monkeypatch):
    """A downgrade (disk < loaded) must warn but never schedule a restart."""
    monkeypatch.setattr(server, "_disk_version", lambda: "0.9.0")
    monkeypatch.setattr(server, "_LOADED_VERSION", "1.0.0", raising=False)
    rec = _ExecRecorder()
    monkeypatch.setattr(server, "_schedule_self_restart", rec)
    monkeypatch.setattr(server, "_RESTART_SCHEDULED", False, raising=False)
    monkeypatch.delenv("SIMDRIVE_NO_AUTO_RESTART", raising=False)
    monkeypatch.delenv(server._RESTART_COUNT_ENV, raising=False)

    result = server.call_tool("version", {})
    # Warning still surfaces so the operator notices the skew.
    assert result.get("_simdrive_warning") is not None
    # But no action and no scheduler call.
    assert "_simdrive_action" not in result
    assert rec.calls == 0


def test_loop_guard_trips_after_max_restarts(monkeypatch):
    """When SIMDRIVE_RESTART_COUNT >= max, refuse to schedule another restart."""
    monkeypatch.setattr(server, "_disk_version", lambda: "9.9.9")
    monkeypatch.setattr(server, "_is_upgrade", lambda disk, loaded: True)
    rec = _ExecRecorder()
    monkeypatch.setattr(server, "_schedule_self_restart", rec)
    monkeypatch.setattr(server, "_RESTART_SCHEDULED", False, raising=False)
    monkeypatch.delenv("SIMDRIVE_NO_AUTO_RESTART", raising=False)
    # Simulate that we've already restarted exactly _RESTART_LOOP_GUARD_MAX
    # times in this process tree — the next attempt must NOT schedule another.
    monkeypatch.setenv(server._RESTART_COUNT_ENV, str(server._RESTART_LOOP_GUARD_MAX))

    result = server.call_tool("version", {})
    assert result.get("_simdrive_warning") is not None
    assert "loop guard" in result["_simdrive_warning"].lower()
    assert "_simdrive_action" not in result
    assert rec.calls == 0


def test_loop_guard_does_not_trip_below_max(monkeypatch):
    """Below the guard threshold, auto-restart still fires normally."""
    monkeypatch.setattr(server, "_disk_version", lambda: "9.9.9")
    monkeypatch.setattr(server, "_is_upgrade", lambda disk, loaded: True)
    rec = _ExecRecorder()
    monkeypatch.setattr(server, "_schedule_self_restart", rec)
    monkeypatch.setattr(server, "_RESTART_SCHEDULED", False, raising=False)
    monkeypatch.delenv("SIMDRIVE_NO_AUTO_RESTART", raising=False)
    # 2 < default max of 3 — one more restart is allowed.
    monkeypatch.setenv(server._RESTART_COUNT_ENV, str(server._RESTART_LOOP_GUARD_MAX - 1))

    result = server.call_tool("version", {})
    assert result.get("_simdrive_action") == "restarting"
    assert rec.calls == 1


# ---------------------------------------------------------------------------
# _schedule_self_restart helper — exists and is callable.
# ---------------------------------------------------------------------------


def test_schedule_self_restart_helper_exists():
    """The module exposes a _schedule_self_restart hook tests can patch."""
    assert hasattr(server, "_schedule_self_restart")
    assert callable(server._schedule_self_restart)


def test_try_claim_restart_helper_exists():
    """Module exposes the new lock-based latch claim API."""
    assert hasattr(server, "_try_claim_restart")
    assert callable(server._try_claim_restart)
    assert hasattr(server, "_RESTART_LATCH")
    assert isinstance(server._RESTART_LATCH, type(threading.Lock()))
