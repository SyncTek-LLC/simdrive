"""Dogfood gap 1 — `logs` could not see `.info` / `.debug`, and its window was pinned.

`log show` drops `.info` and `.debug` records unless `--info` / `--debug` are
passed, so an app that narrates at `.info` (most do) was structurally invisible
to the very tool built to read it. Separately, the `--last 30s` window was
hardcoded while `lines` slices *after* capture, so `lines=200` could never reach
further back than 30 seconds — useless for a defect noticed a minute later.

Tests here pin:
  1. the default `log show` argv now carries `--info`
  2. `level="debug"` adds `--debug` (and keeps `--info`)
  3. `level="default"` restores the pre-fix noise floor (neither flag)
  4. `last` reaches the argv as `--last <window>`, defaulting to 30s
  5. malformed `level` / `last` are rejected rather than shell-injected
  6. `tool_logs` forwards both through to the simulator path and echoes them back

All of these fail before the fix: get_log_tail takes no level/last parameter.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simdrive import sim
from simdrive.sim import SimError


# ── helpers ─────────────────────────────────────────────────────────────────


def _ok(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=stderr)


def _capture_argv(stdout: str = "line one\n"):
    """Return (recorder_fn, captured) where captured['args'] is the _simctl argv."""
    captured: dict = {}

    def _fake(*args, **kwargs):
        captured["args"] = list(args)
        captured["kwargs"] = dict(kwargs)
        return _ok(stdout)

    return _fake, captured


def _make_sim_session(tmp_path: Path, udid: str = "SIM-LOGLEVEL"):
    from simdrive.sim import Device
    d = Device(udid=udid, name="iPhone 17 Pro", os_version="26.0", state="Booted")
    return SimpleNamespace(
        session_id="sid-loglevel",
        device=d,
        target="simulator",
        app_bundle_id="com.example.app",
        workdir=tmp_path,
        last_action_at=0.0,
    )


# ── sim.get_log_tail — level ────────────────────────────────────────────────


def test_default_level_includes_info_flag():
    """The whole point of the fix: `.info` records are captured by default."""
    fake, captured = _capture_argv()
    with patch("simdrive.sim._simctl", side_effect=fake):
        sim.get_log_tail("UDID")
    assert "--info" in captured["args"], (
        f"default log capture must pass --info or app .info logging stays invisible; "
        f"argv={captured['args']}"
    )
    assert "--debug" not in captured["args"], "default must not opt into the noisy --debug firehose"


def test_level_debug_adds_debug_and_keeps_info():
    fake, captured = _capture_argv()
    with patch("simdrive.sim._simctl", side_effect=fake):
        sim.get_log_tail("UDID", level="debug")
    assert "--debug" in captured["args"]
    assert "--info" in captured["args"], "--debug alone does not reliably imply --info"


def test_level_default_restores_pre_fix_noise_floor():
    """Noise-sensitive callers can still get the old default-level-only stream."""
    fake, captured = _capture_argv()
    with patch("simdrive.sim._simctl", side_effect=fake):
        sim.get_log_tail("UDID", level="default")
    assert "--info" not in captured["args"]
    assert "--debug" not in captured["args"]


def test_invalid_level_raises_rather_than_silently_downgrading():
    with patch("simdrive.sim._simctl", side_effect=_capture_argv()[0]):
        with pytest.raises(SimError) as exc:
            sim.get_log_tail("UDID", level="verbose")
    assert "level" in str(exc.value)


# ── sim.get_log_tail — window ───────────────────────────────────────────────


def test_window_defaults_to_30s_for_backwards_compatibility():
    fake, captured = _capture_argv()
    with patch("simdrive.sim._simctl", side_effect=fake):
        sim.get_log_tail("UDID")
    args = captured["args"]
    assert "--last" in args
    assert args[args.index("--last") + 1] == "30s"


def test_window_is_configurable():
    fake, captured = _capture_argv()
    with patch("simdrive.sim._simctl", side_effect=fake):
        sim.get_log_tail("UDID", last="5m")
    args = captured["args"]
    assert args[args.index("--last") + 1] == "5m"


def test_bare_integer_window_is_accepted_as_seconds():
    fake, captured = _capture_argv()
    with patch("simdrive.sim._simctl", side_effect=fake):
        sim.get_log_tail("UDID", last="90")
    args = captured["args"]
    assert args[args.index("--last") + 1] == "90"


@pytest.mark.parametrize("bad", ["5 minutes", "; rm -rf /", "5m; echo", "", "m"])
def test_malformed_window_is_rejected(bad):
    """`last` lands in an argv we build — reject anything that is not a duration."""
    with patch("simdrive.sim._simctl", side_effect=_capture_argv()[0]):
        with pytest.raises(SimError):
            sim.get_log_tail("UDID", last=bad)


def test_longer_window_gets_a_longer_subprocess_timeout():
    """A 5-minute --info window returns far more output than a 30s one; the old
    fixed 10s timeout would truncate it into a spurious empty result."""
    fake, captured = _capture_argv()
    with patch("simdrive.sim._simctl", side_effect=fake):
        sim.get_log_tail("UDID", last="30s")
    short = captured["kwargs"]["timeout"]
    with patch("simdrive.sim._simctl", side_effect=fake):
        sim.get_log_tail("UDID", last="5m")
    long = captured["kwargs"]["timeout"]
    assert long > short, f"5m window must get more time than 30s (got {long} vs {short})"


# ── tool_logs wiring ────────────────────────────────────────────────────────


def test_tool_logs_forwards_level_and_window(tmp_path, monkeypatch):
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    import simdrive.sim as sim_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)

    captured: dict = {}

    def _fake_get_log_tail(udid, lines=50, predicate=None, level="info", last="30s"):
        captured.update({"level": level, "last": last})
        return "a log line\n"

    monkeypatch.setattr(sim_mod, "get_log_tail", _fake_get_log_tail)

    result = server_mod.tool_logs({
        "session_id": s.session_id,
        "level": "debug",
        "last": "2m",
    })

    assert result["ok"] is True
    assert captured == {"level": "debug", "last": "2m"}
    # Echoed back so the agent can tell what noise floor produced this payload.
    assert result["level"] == "debug"
    assert result["last"] == "2m"


def test_tool_logs_defaults_to_info_level(tmp_path, monkeypatch):
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    import simdrive.sim as sim_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)

    captured: dict = {}

    def _fake_get_log_tail(udid, lines=50, predicate=None, level="default", last="30s"):
        captured.update({"level": level, "last": last})
        return "a log line\n"

    monkeypatch.setattr(sim_mod, "get_log_tail", _fake_get_log_tail)
    server_mod.tool_logs({"session_id": s.session_id})

    assert captured["level"] == "info", (
        "the MCP tool must default to info — that is the level agents actually need"
    )
    assert captured["last"] == "30s", "the default window must not regress"


def test_tool_logs_rejects_unknown_level(tmp_path, monkeypatch):
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)

    with pytest.raises(Exception) as exc:
        server_mod.tool_logs({"session_id": s.session_id, "level": "trace"})
    assert "level" in str(exc.value)


def test_level_and_window_are_declared_in_the_tool_schema():
    import simdrive.server as server_mod
    spec = next(t for t in server_mod._TOOLS if t["name"] == "logs")
    props = spec["inputSchema"]["properties"]
    assert props["level"]["enum"] == ["default", "info", "debug"]
    assert props["level"]["default"] == "info"
    assert props["last"]["default"] == "30s"
    # The volume tradeoff must be stated where the agent reads it.
    assert "noisy" in props["level"]["description"].lower()


def test_device_target_ignores_level_without_error(tmp_path, monkeypatch):
    """idevicesyslog has no level concept — passing level must not break device logs."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    from simdrive import device as device_mod
    from simdrive.sim import Device

    d = Device(udid="DEV-UDID", name="Test iPhone", os_version="26.0", state="available")
    s = SimpleNamespace(
        session_id="sid-loglevel-dev", device=d, target="device",
        app_bundle_id="com.example.app", workdir=tmp_path, last_action_at=0.0,
    )
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    monkeypatch.setattr(
        device_mod, "get_log_tail",
        lambda udid, lines=200, predicate=None, predicate_kind="substring": "device line\n",
    )

    result = server_mod.tool_logs({"session_id": s.session_id, "level": "debug", "last": "5m"})
    assert result["ok"] is True
    assert result["logs"].strip() == "device line"
