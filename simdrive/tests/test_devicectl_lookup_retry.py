"""Bounded retry for devicectl's intermittent device-lookup failure (CoreDeviceError 1011).

`devicectl device install app` sometimes fails with "unable to locate a device
matching ecid_..." while other calls with the same id succeed; a retry works.
Only that failure is retried, at most twice in total.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from simdrive import device

FLAKE = ("ERROR: Unable to locate a device matching ecid_8458062252752942. "
         "(com.apple.dt.CoreDeviceError error 1011.)\n"
         "unable to locate a device matching ecid_8458062252752942")


def _cp(rc, stderr="", stdout=""):
    return subprocess.CompletedProcess([], rc, stdout, stderr)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(device.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def test_retries_lookup_flake_once_then_succeeds(_no_sleep):
    with patch.object(device.subprocess, "run", side_effect=[_cp(1, FLAKE), _cp(0)]) as run:
        res = device.run_devicectl_with_retry(["xcrun", "devicectl"], timeout=5)
    assert res.returncode == 0
    assert run.call_count == 2
    assert _no_sleep == [device.DEVICECTL_LOOKUP_RETRY_BACKOFF_S]


def test_gives_up_after_two_attempts(_no_sleep):
    with patch.object(device.subprocess, "run", side_effect=[_cp(1, FLAKE)] * 5) as run:
        res = device.run_devicectl_with_retry(["xcrun", "devicectl"], timeout=5)
    assert res.returncode == 1
    assert run.call_count == 2


def test_other_failures_are_not_retried(_no_sleep):
    with patch.object(device.subprocess, "run", side_effect=[_cp(1, "code signature invalid")]) as run:
        res = device.run_devicectl_with_retry(["xcrun", "devicectl"], timeout=5)
    assert res.returncode == 1
    assert run.call_count == 1
    assert _no_sleep == []


def test_launch_app_retries_lookup_flake():
    out = '{"result": {"process": {"processIdentifier": 321}}}'
    with patch.object(device.subprocess, "run", side_effect=[_cp(1, FLAKE), _cp(0, stdout=out)]):
        assert device.launch_app("UDID", "com.x") == 321


def test_install_app_retries_lookup_flake(tmp_path):
    app = tmp_path / "X.app"
    app.mkdir()
    with patch.object(device.subprocess, "run", side_effect=[_cp(1, FLAKE), _cp(0)]) as run:
        device.install_app("UDID", app)
    assert run.call_count == 2


def test_wda_install_retries_lookup_flake(tmp_path, monkeypatch):
    from simdrive.wda import bootstrap
    app = tmp_path / "WebDriverAgentRunner-Runner.app"
    app.mkdir()
    monkeypatch.setattr(bootstrap, "_find_wda_app_bundle", lambda dd: app)
    calls = []

    def fake_run(cmd, *a, **kw):
        calls.append(cmd)
        if cmd[3] == "uninstall":
            return _cp(0)
        installs = sum(1 for c in calls if c[3] == "install")
        return _cp(1, FLAKE) if installs == 1 else _cp(0)

    with patch.object(subprocess, "run", side_effect=fake_run):
        assert bootstrap.install_wda("CD-UUID", tmp_path, bundle_id="co.x.wda") == "co.x.wda"
    assert sum(1 for c in calls if c[3] == "install") == 2
