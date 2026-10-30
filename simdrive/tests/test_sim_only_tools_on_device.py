"""perf/perf_baseline/perf_compare/memory/crashes refuse device sessions.

They read host-side data (ps/vmmap on the Mac, ~/Library/Logs/DiagnosticReports),
so on a device they returned app_not_running or Mac crash reports instead of
saying the tool does not apply.
"""
from __future__ import annotations

import pytest

from simdrive import diagnostics, errors, perf, server, session
from simdrive.sim import Device


@pytest.fixture
def make_session(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_entitlement_gate", lambda: None)
    created = []

    def _make(target):
        s = session.Session(session_id=f"so-{target}", device=Device(udid="U", name="P", os_version="", state="active"),
                            workdir=tmp_path, target=target, app_bundle_id="com.x")
        session._SESSIONS[s.session_id] = s
        created.append(s.session_id)
        return s
    yield _make
    for sid in created:
        session._SESSIONS.pop(sid, None)


@pytest.mark.parametrize("tool, name", [
    (server.tool_perf, "perf"),
    (server.tool_perf_baseline, "perf_baseline"),
    (server.tool_perf_compare, "perf_compare"),
    (server.tool_memory, "memory"),
    (server.tool_crashes, "crashes"),
])
def test_tool_refuses_device_session_without_touching_host(tool, name, make_session, monkeypatch):
    s = make_session("device")
    host_calls = []
    monkeypatch.setattr(perf, "snapshot", lambda *a, **k: host_calls.append("snapshot"))
    monkeypatch.setattr(perf, "memory_detail", lambda *a, **k: host_calls.append("memory"))
    monkeypatch.setattr(diagnostics, "list_crashes", lambda **k: host_calls.append("crashes"))
    with pytest.raises(errors.SimdriveError) as exc:
        tool({"session_id": s.session_id})
    assert exc.value.code == "not_supported_on_device"
    assert exc.value.details == {"tool": name, "target": "device"}
    assert host_calls == []


def test_crashes_still_runs_on_simulator(make_session, monkeypatch):
    s = make_session("simulator")
    monkeypatch.setattr(diagnostics, "list_crashes", lambda **k: [{"path": "/x.ips"}])
    assert server.tool_crashes({"session_id": s.session_id}) == {"crashes": [{"path": "/x.ips"}]}
