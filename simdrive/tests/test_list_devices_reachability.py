"""list_devices separates 'devicectl knows a transport' from 'reachable now'.

A phone with transportType=localNetwork and a disconnected tunnel was listed
as available although devicectl/xctrace could not reach it.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from simdrive import device, server
from simdrive.device import RealDevice


def _dev(udid, transport="localNetwork", tunnel="disconnected"):
    return RealDevice(udid=udid, name=udid, model="iPhone", transport=transport,
                      state="available" if transport else "unavailable",
                      tunnel_state=tunnel, coredevice_id=f"CD-{udid}")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("WDA_REGISTRY_DIR", str(tmp_path))
    monkeypatch.setattr(server, "_entitlement_gate", lambda: None)
    monkeypatch.setattr(device, "libimobiledevice_available", lambda: (True, []))


DEVICES = [_dev("CONNECTED", tunnel="connected"), _dev("ASLEEP"), _dev("GONE", transport=None, tunnel=None)]


def _by_udid(out):
    return {d["udid"]: d for d in out["devices"]}


def test_without_probe_reachable_comes_from_tunnel_state():
    with patch.object(device, "list_devices", return_value=DEVICES), \
         patch.object(device, "probe_reachable") as probe:
        out = _by_udid(server.tool_list_devices({}))
    probe.assert_not_called()
    assert out["CONNECTED"]["reachable"] is True
    assert out["ASLEEP"]["reachable"] is None
    assert out["ASLEEP"]["state"] == "available"
    assert out["ASLEEP"]["tunnel_state"] == "disconnected"
    assert out["GONE"]["reachable"] is False


def test_probe_sets_reachable_for_devices_with_a_transport():
    answers = {"CONNECTED": True, "ASLEEP": False}
    with patch.object(device, "list_devices", return_value=DEVICES), \
         patch.object(device, "probe_reachable", side_effect=lambda u: answers[u]) as probe:
        out = _by_udid(server.tool_list_devices({"probe": True, "compact": True}))
    assert sorted(c.args[0] for c in probe.call_args_list) == ["ASLEEP", "CONNECTED"]
    assert out["CONNECTED"]["reachable"] is True
    assert out["ASLEEP"]["reachable"] is False
    assert out["GONE"]["reachable"] is False


def test_probe_reachable_uses_lockstate_with_timeout():
    import subprocess
    with patch.object(device.subprocess, "run",
                      return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
        assert device.probe_reachable("U1", timeout_s=4.0) is True
    argv = run.call_args.args[0]
    assert argv[:5] == ["xcrun", "devicectl", "device", "info", "lockState"]
    assert argv[argv.index("--timeout") + 1] == "4"
    assert run.call_args.kwargs["timeout"] == 6.0


def test_probe_reachable_false_on_error_or_timeout():
    import subprocess
    with patch.object(device.subprocess, "run",
                      return_value=subprocess.CompletedProcess([], 1, "", "timed out")):
        assert device.probe_reachable("U1") is False
    with patch.object(device.subprocess, "run",
                      side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1)):
        assert device.probe_reachable("U1") is False
