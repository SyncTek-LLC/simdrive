"""Device trace tools: detached xctrace record, stop via SIGINT, export to a
file (xctrace export prints nothing without --output), exit-139 tolerance,
and .aar import. Subprocess and signals are faked; nothing reaches a device."""
from __future__ import annotations

import json
import signal
import subprocess
from pathlib import Path

import pytest

from simdrive import trace


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
    return tmp_path


class _Proc:
    def __init__(self, pid=4321):
        self.pid = pid


@pytest.fixture
def fake_popen(monkeypatch):
    calls = []

    def popen(cmd, **kw):
        calls.append((cmd, kw))
        return _Proc()
    monkeypatch.setattr(trace.subprocess, "Popen", popen)
    return calls


@pytest.fixture
def alive(monkeypatch):
    state = {"alive": True}
    monkeypatch.setattr(trace, "_pid_alive", lambda pid: state["alive"])
    return state


def test_start_spawns_detached_xctrace_record(fake_popen, _home):
    st = trace.start("00008150-00142D540A87801C", "Palace", time_limit="45s")
    cmd, kw = fake_popen[0]
    assert cmd[:3] == ["xcrun", "xctrace", "record"]
    assert cmd[cmd.index("--device") + 1] == "00008150-00142D540A87801C"
    assert cmd[cmd.index("--template") + 1] == "Power Profiler"
    assert cmd[cmd.index("--attach") + 1] == "Palace"
    assert cmd[cmd.index("--time-limit") + 1] == "45s"
    assert cmd[cmd.index("--output") + 1] == st["output"]
    assert kw["start_new_session"] is True
    saved = json.loads((_home / "traces" / st["trace_id"] / "state.json").read_text())
    assert saved["status"] == "recording" and saved["pid"] == 4321


@pytest.mark.parametrize("bad", ["60", "1 m", "abc", ""])
def test_start_rejects_bad_time_limit(bad, fake_popen):
    with pytest.raises(trace.TraceError):
        trace.start("U", "Palace", time_limit=bad)
    assert fake_popen == []


def test_status_marks_finished_when_recorder_exits_with_output(fake_popen, alive):
    st = trace.start("U", "Palace")
    assert trace.status(st["trace_id"])["status"] == "recording"
    Path(st["output"]).mkdir()
    alive["alive"] = False
    assert trace.status(st["trace_id"])["status"] == "finished"


def test_status_marks_failed_when_recorder_exits_without_output(fake_popen, alive):
    st = trace.start("U", "Palace")
    alive["alive"] = False
    assert trace.status(st["trace_id"])["status"] == "failed"


def test_stop_sends_sigint_not_sigkill(fake_popen, alive, monkeypatch):
    st = trace.start("U", "Palace")
    sent = []

    def kill(pid, sig):
        sent.append((pid, sig))
        alive["alive"] = False
        Path(st["output"]).mkdir()
    monkeypatch.setattr(trace.os, "kill", kill)
    out = trace.stop(st["trace_id"], wait_s=1)
    assert sent == [(4321, signal.SIGINT)]
    assert out["status"] == "stopped"


def _finished_trace(fake_popen, alive):
    st = trace.start("U", "Palace")
    Path(st["output"]).mkdir()
    alive["alive"] = False
    return st


def _run_writing(rc, content="<xml/>"):
    def run(cmd, **kw):
        out = Path(cmd[cmd.index("--output") + 1])
        if content:
            out.write_text(content)
        return subprocess.CompletedProcess(cmd, rc, "", "Segmentation fault" if rc else "")
    return run


def test_export_toc_writes_to_output_file(fake_popen, alive, monkeypatch):
    st = _finished_trace(fake_popen, alive)
    seen = []
    run = _run_writing(0)
    monkeypatch.setattr(trace.subprocess, "run", lambda cmd, **kw: (seen.append(cmd), run(cmd, **kw))[1])
    out = trace.export(st["trace_id"], toc=True)
    assert seen[0][:3] == ["xcrun", "xctrace", "export"]
    assert "--output" in seen[0] and "--toc" in seen[0]
    assert out["bytes"] == len("<xml/>") and out["warning"] is None


def test_export_tolerates_exit_139_with_nonempty_file(fake_popen, alive, monkeypatch):
    st = _finished_trace(fake_popen, alive)
    monkeypatch.setattr(trace.subprocess, "run", _run_writing(139))
    out = trace.export(st["trace_id"], xpath='/trace-toc/run[@number="1"]/data/table')
    assert out["returncode"] == 139
    assert "segfault" in out["warning"]


def test_export_exit_139_without_file_is_an_error(fake_popen, alive, monkeypatch):
    st = _finished_trace(fake_popen, alive)
    monkeypatch.setattr(trace.subprocess, "run", _run_writing(139, content=""))
    with pytest.raises(trace.TraceError, match="exited 139"):
        trace.export(st["trace_id"], toc=True)


def test_export_refuses_while_recording(fake_popen, alive):
    st = trace.start("U", "Palace")
    with pytest.raises(trace.TraceError, match="still recording"):
        trace.export(st["trace_id"], toc=True)


def test_export_requires_exactly_one_of_xpath_or_toc(fake_popen, alive):
    st = _finished_trace(fake_popen, alive)
    with pytest.raises(trace.TraceError):
        trace.export(st["trace_id"])
    with pytest.raises(trace.TraceError):
        trace.export(st["trace_id"], xpath="/x", toc=True)


def test_import_aar_extracts_then_imports(tmp_path, monkeypatch):
    aar = tmp_path / "PerfTrace.aar"
    aar.write_bytes(b"x")
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "aa":
            d = Path(cmd[cmd.index("-d") + 1])
            (d / "trace.atrc").write_text("t")
        else:
            Path(cmd[cmd.index("--output") + 1]).mkdir()
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(trace.subprocess, "run", run)
    st = trace.import_file(str(aar))
    assert calls[0][:2] == ["aa", "extract"]
    imp = calls[1]
    assert imp[:3] == ["xcrun", "xctrace", "import"]
    assert imp[imp.index("--input") + 1].endswith("trace.atrc")
    assert imp[imp.index("--template") + 1] == "Power Profiler"
    assert st["status"] == "imported"


def test_unknown_trace_id_is_a_trace_error():
    with pytest.raises(trace.TraceError):
        trace.status("nope-1234")


def test_server_trace_start_refuses_simulator_session(tmp_path, monkeypatch):
    from simdrive import errors, server, session
    from simdrive.sim import Device
    monkeypatch.setattr(server, "_entitlement_gate", lambda: None)
    s = session.Session(session_id="tr-sim", device=Device(udid="S", name="iPhone", os_version="", state="Booted"),
                        workdir=tmp_path, target="simulator")
    session._SESSIONS[s.session_id] = s
    try:
        with pytest.raises(errors.SimdriveError) as exc:
            server.tool_trace_start({"session_id": "tr-sim", "attach": "Palace"})
    finally:
        session._SESSIONS.pop("tr-sim", None)
    assert exc.value.code == "not_supported_on_simulator"


def test_server_trace_tools_wrap_errors(monkeypatch):
    from simdrive import server
    monkeypatch.setattr(server, "_entitlement_gate", lambda: None)
    out = server.tool_trace_status({"trace_id": "missing-1234"})
    assert out["ok"] is False and out["error"]["code"] == "trace_failed"
