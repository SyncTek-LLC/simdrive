"""a12: predicate_kind parameter for mcp__simdrive__logs.

Tests:
  1. test_logs_predicate_kind_nspredicate_on_sim_uses_log_show
     - sim target + predicate_kind="nspredicate" → existing log show subprocess
       call shape is preserved (not broken by the new parameter).
  2. test_logs_predicate_kind_regex_post_filters_on_device
     - device target + predicate_kind="regex", predicate="^ERROR".
       Mock idevicesyslog stdout with 5 lines (3 starting "ERROR").
       Assert result has the 3 ERROR lines.
  3. test_logs_predicate_kind_substring_post_filters_on_device
     - device + predicate_kind="substring", predicate="Example Reader".
       Mock 5 lines, 2 contain "Example Reader". Assert result has those 2.
  4. test_logs_predicate_kind_nspredicate_on_device_warns_and_downgrades
     - device + predicate_kind="nspredicate".
       Assert a WARNING log mentioning downgrade was emitted; result is non-empty.

All 4 tests FAIL on feat/v17-claude-native HEAD because:
  - tool_logs does not accept a predicate_kind argument.
  - device.get_log_tail applies only substring filtering regardless of kind.
  - No WARNING is emitted when nspredicate is requested on a device target.
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_device_session(tmp_path: Path, udid: str = "UDID-A12-LOGKIND"):
    from simdrive.sim import Device
    d = Device(udid=udid, name="Test iPhone", os_version="26.0", state="available")
    return SimpleNamespace(
        session_id="sid-a12-logkind",
        device=d,
        target="device",
        app_bundle_id="com.example.app",
        workdir=tmp_path,
        last_action_at=0.0,
    )


def _make_sim_session(tmp_path: Path, udid: str = "SIM-UDID-A12"):
    from simdrive.sim import Device
    d = Device(udid=udid, name="iPhone 17 Pro", os_version="26.0", state="booted")
    return SimpleNamespace(
        session_id="sid-a12-sim",
        device=d,
        target="simulator",
        app_bundle_id="com.example.app",
        workdir=tmp_path,
        last_action_at=0.0,
    )


# ── test 1 ────────────────────────────────────────────────────────────────────


def test_logs_predicate_kind_nspredicate_on_sim_uses_log_show(tmp_path, monkeypatch):
    """sim + predicate_kind='nspredicate' → sim.get_log_tail is called (log show preserved).

    Fails on HEAD: tool_logs raises TypeError for unexpected 'predicate_kind' kwarg
    (or the parameter doesn't exist in _TOOLS schema).
    """
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    import simdrive.sim as sim_mod

    udid = "SIM-UDID-A12"
    sid = "sid-a12-sim"
    s = _make_sim_session(tmp_path, udid=udid)
    monkeypatch.setitem(session_mod._SESSIONS, sid, s)

    captured = {}

    def _fake_get_log_tail(udid_, lines=200, predicate=None):
        captured["udid"] = udid_
        captured["predicate"] = predicate
        return "sim log line\n"

    monkeypatch.setattr(sim_mod, "get_log_tail", _fake_get_log_tail)

    result = server_mod.tool_logs({
        "session_id": sid,
        "lines": 50,
        "predicate": "eventMessage CONTAINS 'Example Reader'",
        "predicate_kind": "nspredicate",
    })

    assert result.get("ok") is True, f"Expected ok=True, got: {result}"
    assert captured.get("udid") == udid, (
        f"sim.get_log_tail was not called with the right udid. captured={captured}"
    )


# ── test 2 ────────────────────────────────────────────────────────────────────


def test_logs_predicate_kind_regex_post_filters_on_device(tmp_path, monkeypatch):
    """device + predicate_kind='regex', predicate='^ERROR' → 3 ERROR lines returned.

    5 fake idevicesyslog lines; 3 start with 'ERROR'. Result must contain exactly 3.

    Fails on HEAD: predicate_kind param not accepted; device path only does substring.
    """
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    from simdrive import device as device_mod

    udid = "UDID-A12-REGEX"
    sid = "sid-a12-regex"
    s = _make_device_session(tmp_path, udid=udid)
    monkeypatch.setitem(session_mod._SESSIONS, sid, s)

    fake_lines = [
        "ERROR: crash in module A",
        "INFO: all good",
        "ERROR: null pointer at line 42",
        "DEBUG: verbose output",
        "ERROR: socket timeout",
    ]
    fake_output = "\n".join(fake_lines) + "\n"

    monkeypatch.setattr(device_mod, "_which",
                        lambda name: f"/usr/bin/{name}" if name == "idevicesyslog" else None)

    fake_proc = MagicMock()
    fake_proc.communicate.return_value = (fake_output, "")
    fake_proc.returncode = 0
    monkeypatch.setattr(device_mod.subprocess, "Popen", lambda *a, **kw: fake_proc)

    result = server_mod.tool_logs({
        "session_id": sid,
        "lines": 200,
        "predicate": "^ERROR",
        "predicate_kind": "regex",
    })

    assert result.get("ok") is True, f"Expected ok=True, got: {result}"
    returned_lines = [ln for ln in result.get("logs", "").splitlines() if ln]
    error_lines = [ln for ln in returned_lines if re.match(r"^ERROR", ln)]
    assert len(error_lines) == 3, (
        f"Expected 3 ERROR lines matching '^ERROR', got {len(error_lines)}: {returned_lines}"
    )
    non_error = [ln for ln in returned_lines if not re.match(r"^ERROR", ln)]
    assert not non_error, (
        f"regex filter should exclude non-ERROR lines, but got: {non_error}"
    )


# ── test 3 ────────────────────────────────────────────────────────────────────


def test_logs_predicate_kind_substring_post_filters_on_device(tmp_path, monkeypatch):
    """device + predicate_kind='substring', predicate='Example Reader' → 2 Example Reader lines returned.

    5 fake lines, 2 contain 'Example Reader'. Result must have exactly those 2.

    Fails on HEAD: predicate_kind param not accepted; also baseline substring
    behaviour may differ if the predicate_kind routing is missing.
    """
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    from simdrive import device as device_mod

    udid = "UDID-A12-SUBSTR"
    sid = "sid-a12-substr"
    s = _make_device_session(tmp_path, udid=udid)
    monkeypatch.setitem(session_mod._SESSIONS, sid, s)

    fake_lines = [
        "Example Reader app started",
        "INFO: unrelated",
        "Example Reader user tapped checkout",
        "DEBUG: background task",
        "ERROR: network timeout",
    ]
    fake_output = "\n".join(fake_lines) + "\n"

    monkeypatch.setattr(device_mod, "_which",
                        lambda name: f"/usr/bin/{name}" if name == "idevicesyslog" else None)

    fake_proc = MagicMock()
    fake_proc.communicate.return_value = (fake_output, "")
    fake_proc.returncode = 0
    monkeypatch.setattr(device_mod.subprocess, "Popen", lambda *a, **kw: fake_proc)

    result = server_mod.tool_logs({
        "session_id": sid,
        "lines": 200,
        "predicate": "Example Reader",
        "predicate_kind": "substring",
    })

    assert result.get("ok") is True, f"Expected ok=True, got: {result}"
    returned_lines = [ln for ln in result.get("logs", "").splitlines() if ln]
    example_lines = [ln for ln in returned_lines if "Example Reader" in ln]
    assert len(example_lines) == 2, (
        f"Expected 2 lines containing 'Example Reader', got {len(example_lines)}: {returned_lines}"
    )
    others = [ln for ln in returned_lines if "Example Reader" not in ln]
    assert not others, (
        f"substring filter should exclude non-Example Reader lines, but got: {others}"
    )


# ── test 4 ────────────────────────────────────────────────────────────────────


def test_logs_predicate_kind_nspredicate_on_device_warns_and_downgrades(
    tmp_path, monkeypatch, caplog
):
    """device + predicate_kind='nspredicate' → WARNING emitted, result is non-empty.

    idevicesyslog does not support NSPredicate; a12 must emit a WARNING about
    downgrading to substring and still return whatever lines match.

    Fails on HEAD: predicate_kind param not accepted at all.
    """
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    from simdrive import device as device_mod

    udid = "UDID-A12-NSPRED"
    sid = "sid-a12-nspred"
    s = _make_device_session(tmp_path, udid=udid)
    monkeypatch.setitem(session_mod._SESSIONS, sid, s)

    # Lines where at least 1 contains the predicate text as a substring so
    # the downgraded substring filter returns something.
    fake_lines = [
        "eventMessage CONTAINS 'Example Reader'",
        "unrelated debug line",
        "another unrelated line",
    ]
    fake_output = "\n".join(fake_lines) + "\n"

    monkeypatch.setattr(device_mod, "_which",
                        lambda name: f"/usr/bin/{name}" if name == "idevicesyslog" else None)

    fake_proc = MagicMock()
    fake_proc.communicate.return_value = (fake_output, "")
    fake_proc.returncode = 0
    monkeypatch.setattr(device_mod.subprocess, "Popen", lambda *a, **kw: fake_proc)

    with caplog.at_level(logging.WARNING):
        result = server_mod.tool_logs({
            "session_id": sid,
            "lines": 200,
            "predicate": "eventMessage CONTAINS 'Example Reader'",
            "predicate_kind": "nspredicate",
        })

    assert result.get("ok") is True, f"Expected ok=True, got: {result}"

    # A WARNING mentioning downgrade must have been emitted.
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    downgrade_warnings = [
        r for r in warning_records
        if "downgrade" in r.getMessage().lower()
        or "nspredicate" in r.getMessage().lower()
        or "not supported" in r.getMessage().lower()
    ]
    assert downgrade_warnings, (
        f"Expected a WARNING about nspredicate downgrade on device target, "
        f"but no such log record found. All warnings: {[r.getMessage() for r in warning_records]}"
    )

    # Result must be non-empty (the substring fallback matched at least 1 line).
    returned_lines = [ln for ln in result.get("logs", "").splitlines() if ln]
    assert returned_lines, (
        "Expected non-empty result after nspredicate downgrade to substring, got empty."
    )
