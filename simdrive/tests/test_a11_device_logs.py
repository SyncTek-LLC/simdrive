"""F-003: device branch of tool_logs invokes idevicesyslog correctly.

Tests (against simdrive.device.get_log_tail and tool_logs via server):
  3. idevicesyslog_invoked_with_udid_and_bundle — Popen args include idevicesyslog,
     -u, the UDID, and a process/bundle filter.
  4. logs_truncate_to_requested_lines — fake 100-line stdout, request 10; result has 10.
  5. logs_predicate_passed_through — predicate="ERROR" filters lines or appears in cmd.
  6. logs_idevicesyslog_missing_returns_clear_error — shutil.which returns None;
     result is {ok: false, error: {code: "device_logs_unavailable", ...}}.
  7. logs_timeout_returns_what_was_captured — process hangs after 3 lines; result
     returns those 3 lines, no exception.

Tests 3, 4, 5, 7 FAIL on 3a22bd4 because:
  - get_log_tail does not accept a bundle_id filter argument.
  - The Popen call does NOT pass a bundle/process filter flag.
  - There is no timeout guard returning partial lines — a TimeoutExpired kills the
    process and returns "" (empty string) rather than captured lines.

Test 6 FAILS on 3a22bd4 because:
  - When idevicesyslog is missing, get_log_tail raises DeviceError (an exception),
    rather than returning {ok: false, error: {code: "device_logs_unavailable", ...}}.
  - tool_logs in server.py propagates the DeviceError uncaught.
"""
from __future__ import annotations

import io
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_device_session(tmp_path: Path, udid: str = "FAKE-UDID-A11", bundle_id: str = "com.example.app"):
    """Return a minimal Session-like object with target='device'."""
    from simdrive.sim import Device
    device = Device(udid=udid, name="Test iPhone", os_version="26.0", state="available")
    session = SimpleNamespace(
        session_id="test-session-id",
        device=device,
        target="device",
        app_bundle_id=bundle_id,
        workdir=tmp_path,
    )
    return session


# ── test 3 ───────────────────────────────────────────────────────────────────


def test_idevicesyslog_invoked_with_udid_and_bundle(tmp_path, monkeypatch):
    """Popen args must include idevicesyslog, -u, the UDID, and the bundle id filter.

    F-003: tool_logs on a device session must pass the session's app_bundle_id
    to idevicesyslog so only relevant process logs are returned.

    Fails on 3a22bd4: Popen is called with [idevicesyslog, -u, udid] only —
    no bundle/process filter.
    """
    udid = "FAKE-UDID-A11"
    bundle_id = "com.example.testapp"

    from simdrive import device as device_mod

    # Fake idevicesyslog binary present
    monkeypatch.setattr(device_mod, "_which", lambda name: f"/usr/bin/{name}" if name == "idevicesyslog" else None)

    fake_proc = MagicMock()
    fake_proc.communicate.return_value = ("line1\nline2\n", "")
    fake_proc.returncode = 0

    captured_args = []

    def _fake_popen(args, **kwargs):
        captured_args.extend(args)
        return fake_proc

    monkeypatch.setattr(device_mod.subprocess, "Popen", _fake_popen)

    # Call get_log_tail with a bundle_id argument (F-003 adds this parameter).
    result = device_mod.get_log_tail(udid, lines=20, predicate=None, bundle_id=bundle_id)

    # Verify the args contain the key elements.
    assert "idevicesyslog" in " ".join(str(a) for a in captured_args), (
        f"idevicesyslog not in Popen args: {captured_args}"
    )
    assert udid in captured_args, f"UDID {udid!r} not in Popen args: {captured_args}"
    assert bundle_id in " ".join(str(a) for a in captured_args), (
        f"bundle_id {bundle_id!r} not found anywhere in Popen args: {captured_args}. "
        "F-003 requires passing the bundle/process filter to idevicesyslog."
    )


# ── test 4 ───────────────────────────────────────────────────────────────────


def test_logs_truncate_to_requested_lines(tmp_path, monkeypatch):
    """When idevicesyslog produces 100 lines, get_log_tail(lines=10, bundle_id=...) returns exactly 10.

    Tests that the 'lines' parameter is honoured on the F-003 device path
    (which requires the bundle_id parameter to exist).

    Fails on 3a22bd4: get_log_tail does not accept bundle_id, so the call
    raises TypeError before any line-count logic runs.
    """
    udid = "FAKE-UDID-A11"
    bundle_id = "com.example.testapp"
    fake_output = "\n".join(f"log line {i}" for i in range(100)) + "\n"

    from simdrive import device as device_mod

    monkeypatch.setattr(device_mod, "_which", lambda name: f"/usr/bin/{name}" if name == "idevicesyslog" else None)

    fake_proc = MagicMock()
    fake_proc.communicate.return_value = (fake_output, "")
    fake_proc.returncode = 0
    monkeypatch.setattr(device_mod.subprocess, "Popen", lambda *a, **kw: fake_proc)

    # F-003: call with bundle_id — this parameter does not exist on 3a22bd4.
    result_text = device_mod.get_log_tail(udid, lines=10, bundle_id=bundle_id)
    result_lines = [ln for ln in result_text.splitlines() if ln]
    assert len(result_lines) == 10, (
        f"Expected exactly 10 lines but got {len(result_lines)}: {result_lines}"
    )


# ── test 5 ───────────────────────────────────────────────────────────────────


def test_logs_predicate_passed_through(tmp_path, monkeypatch):
    """predicate='ERROR' must filter lines or appear in idevicesyslog command args.

    Calls get_log_tail with BOTH bundle_id and predicate (F-003 signature).
    Fails on 3a22bd4 because bundle_id is not a valid parameter — TypeError.

    Accepts both implementation strategies for predicate:
      (a) passed as --match/regex flag to idevicesyslog, OR
      (b) applied as Python-side substring filter after capture.
    """
    udid = "FAKE-UDID-A11"
    bundle_id = "com.example.testapp"
    # Mix of matching and non-matching lines.
    fake_output = "line no match\nERROR: something failed\nanother line\nERROR: second error\n"

    from simdrive import device as device_mod

    monkeypatch.setattr(device_mod, "_which", lambda name: f"/usr/bin/{name}" if name == "idevicesyslog" else None)

    captured_args: list = []

    fake_proc = MagicMock()
    fake_proc.communicate.return_value = (fake_output, "")
    fake_proc.returncode = 0

    def _fake_popen(args, **kwargs):
        captured_args.extend(args)
        return fake_proc

    monkeypatch.setattr(device_mod.subprocess, "Popen", _fake_popen)

    # F-003: call with bundle_id (and predicate) — bundle_id is the new param.
    result_text = device_mod.get_log_tail(udid, lines=50, predicate="ERROR", bundle_id=bundle_id)

    # Either the predicate appears in the cmd args (passthrough) OR it was
    # applied as a Python filter (only ERROR lines in the result).
    args_str = " ".join(str(a) for a in captured_args)
    filtered_lines = [ln for ln in result_text.splitlines() if ln]

    predicate_in_cmd = "ERROR" in args_str
    predicate_filtered = all("ERROR" in ln for ln in filtered_lines) and len(filtered_lines) > 0

    assert predicate_in_cmd or predicate_filtered, (
        f"predicate 'ERROR' was neither passed to idevicesyslog (args: {args_str!r}) "
        f"nor applied as a line filter (result lines: {filtered_lines!r}). "
        "F-003 requires the predicate to be honoured."
    )


# ── test 6 ───────────────────────────────────────────────────────────────────


def test_logs_idevicesyslog_missing_returns_clear_error(tmp_path, monkeypatch):
    """When idevicesyslog is not on PATH, tool_logs must return a structured error dict.

    Expected: {"ok": False, "error": {"code": "device_logs_unavailable", ...}}
    NOT: a raised exception (DeviceError or otherwise).

    Fails on 3a22bd4: get_log_tail raises DeviceError("idevicesyslog not found..."),
    which propagates out of tool_logs as an unhandled exception.
    """
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    from simdrive.sim import Device

    udid = "FAKE-UDID-A11"
    sid = "test-missing-idevicesyslog"

    # Inject a fake device session.
    device = Device(udid=udid, name="Test iPhone", os_version="26.0", state="available")
    fake_session = SimpleNamespace(
        session_id=sid,
        device=device,
        target="device",
        app_bundle_id="com.example.app",
        workdir=tmp_path,
    )
    monkeypatch.setitem(session_mod._SESSIONS, sid, fake_session)

    # Make idevicesyslog invisible.
    from simdrive import device as device_mod
    monkeypatch.setattr(device_mod, "_which", lambda name: None)

    # tool_logs must NOT raise — it must return the error dict.
    result = server_mod.tool_logs({"session_id": sid, "lines": 20})

    assert result.get("ok") is False, (
        f"Expected ok=False when idevicesyslog is missing, got: {result}"
    )
    error = result.get("error", {})
    assert error.get("code") == "device_logs_unavailable", (
        f"Expected error.code='device_logs_unavailable', got: {error!r}. "
        "F-003 requires tool_logs to return a structured error, not raise."
    )
    assert error.get("message"), "error.message must be non-empty"


# ── test 7 ───────────────────────────────────────────────────────────────────


def test_logs_timeout_returns_what_was_captured(tmp_path, monkeypatch):
    """When idevicesyslog hangs after 3 lines, tool_logs returns those 3 lines.

    F-003: partial capture on timeout — the implementation must collect lines
    as they arrive and return whatever was captured when the timeout fires,
    rather than returning "" (which is the 3a22bd4 behaviour when
    TimeoutExpired is caught and `out` is set to "").

    Fails on 3a22bd4: the current TimeoutExpired handler sets out = "" and
    returns the empty string, discarding the 3 lines already written.
    """
    udid = "FAKE-UDID-A11"
    partial_lines = ["partial line 1", "partial line 2", "partial line 3"]

    from simdrive import device as device_mod

    monkeypatch.setattr(device_mod, "_which", lambda name: f"/usr/bin/{name}" if name == "idevicesyslog" else None)

    fake_proc = MagicMock()
    # Simulate TimeoutExpired — communicate() raises, but the proc has already
    # written 3 lines to its stdout buffer.
    partial_output = "\n".join(partial_lines) + "\n"
    fake_proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="idevicesyslog", timeout=2.0)
    # After kill(), reading stdout returns the partial output.
    fake_proc.stdout = io.StringIO(partial_output)
    fake_proc.returncode = None

    monkeypatch.setattr(device_mod.subprocess, "Popen", lambda *a, **kw: fake_proc)

    # Should not raise; should return the partial lines.
    result_text = device_mod.get_log_tail(udid, lines=10)
    result_lines = [ln for ln in result_text.splitlines() if ln]

    assert len(result_lines) >= 1, (
        f"Expected at least 1 captured line on timeout, got {len(result_lines)!r}. "
        "F-003 requires returning partial capture, not empty string, on timeout."
    )
    assert len(result_lines) <= 10, f"Line count {len(result_lines)} exceeds requested 10"
