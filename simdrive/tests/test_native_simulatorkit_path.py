"""The native HID helper finds SimulatorKit in both Xcode layouts.

Xcode 27 moved SimulatorKit.framework from
``<Xcode>/Contents/Developer/Library/PrivateFrameworks`` to
``<Xcode>/Contents/SharedFrameworks``. The helper must not link SimulatorKit
(dyld aborts before ``main`` when the linked path is gone) and must pick the
framework up from whichever layout the selected developer dir uses.

The fake-layout tests symlink the host's real SimulatorKit binary into a
temporary tree, so they run on any Mac with Xcode installed and need no
simulator. The tap test is ``live`` and runs only when SIMDRIVE_TEST_UDID
names a booted simulator.
"""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

import pytest

BINARY = Path(__file__).resolve().parent.parent / "src" / "simdrive" / "_bin" / "simdrive-input"

OLD_REL = Path("Contents/Developer/Library/PrivateFrameworks/SimulatorKit.framework/SimulatorKit")
NEW_REL = Path("Contents/SharedFrameworks/SimulatorKit.framework/SimulatorKit")

pytestmark = pytest.mark.skipif(
    platform.system() != "Darwin" or not BINARY.exists(),
    reason="needs macOS and the built simdrive-input helper",
)


def _host_simulatorkit() -> Path | None:
    """Return the real SimulatorKit binary of the selected Xcode, either layout."""
    try:
        dev = subprocess.run(
            ["xcode-select", "-p"], capture_output=True, text=True, timeout=10, check=False
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not dev:
        return None
    app = Path(dev).parent.parent
    for rel in (OLD_REL, NEW_REL):
        if (app / rel).exists():
            return (app / rel).resolve()
    return None


def _clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DYLD_")}
    env.update(extra)
    return env


def _run_simkit_path(developer_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(BINARY), "--simkit-path"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=_clean_env(DEVELOPER_DIR=str(developer_dir)),
    )


def _fake_xcode(root: Path, real_simkit: Path, *layouts: Path) -> Path:
    """Build a fake Xcode.app holding SimulatorKit at each given layout."""
    app = root / "Xcode.app"
    (app / "Contents" / "Developer").mkdir(parents=True)
    for rel in layouts:
        target = app / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(real_simkit)
    return app / "Contents" / "Developer"


@pytest.fixture()
def real_simkit() -> Path:
    p = _host_simulatorkit()
    if p is None:
        pytest.skip("no SimulatorKit found for the selected Xcode")
    return p


def test_helper_does_not_link_simulatorkit():
    """A linked SimulatorKit makes dyld abort before main when Xcode moves it."""
    out = subprocess.run(["otool", "-L", str(BINARY)], capture_output=True, text=True, check=False).stdout
    assert "SimulatorKit" not in out


def test_helper_starts_without_dyld_framework_path(real_simkit):
    res = subprocess.run(
        [str(BINARY), "--simkit-path"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=_clean_env(),
    )
    assert res.returncode == 0, res.stderr
    assert Path(res.stdout.strip()).resolve() == real_simkit


def test_xcode26_layout_loads_private_frameworks_path(tmp_path, real_simkit):
    dev = _fake_xcode(tmp_path, real_simkit, OLD_REL)
    res = _run_simkit_path(dev)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == str(tmp_path / "Xcode.app" / OLD_REL)


def test_xcode27_layout_loads_shared_frameworks_path(tmp_path, real_simkit):
    dev = _fake_xcode(tmp_path, real_simkit, NEW_REL)
    res = _run_simkit_path(dev)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == str(tmp_path / "Xcode.app" / NEW_REL)


def test_xcode_app_root_as_developer_dir_resolves_contents_developer(tmp_path, real_simkit):
    """xcode-select accepts the .app root; DEVELOPER_DIR set that way must work too."""
    _fake_xcode(tmp_path, real_simkit, NEW_REL)
    res = _run_simkit_path(tmp_path / "Xcode.app")
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == str(tmp_path / "Xcode.app" / NEW_REL)


def test_missing_simulatorkit_names_both_paths_tried(tmp_path, real_simkit):
    dev = _fake_xcode(tmp_path, real_simkit)
    res = _run_simkit_path(dev)
    assert res.returncode != 0
    assert str(tmp_path / "Xcode.app" / OLD_REL) in res.stderr
    assert str(tmp_path / "Xcode.app" / NEW_REL) in res.stderr


@pytest.mark.live
def test_tap_on_booted_simulator_succeeds_without_dyld_framework_path():
    """A tap goes through SimServiceContext and the HID client on the host's Xcode."""
    # Opt-in by UDID: the test taps the screen, so it must not pick a simulator
    # that something else is driving.
    udid = os.environ.get("SIMDRIVE_TEST_UDID")
    if not udid:
        pytest.skip("set SIMDRIVE_TEST_UDID to a booted simulator to run")
    size = subprocess.run(
        [str(BINARY), udid, "size"], capture_output=True, text=True, timeout=30, env=_clean_env(), check=False
    )
    assert size.returncode == 0, size.stderr
    width, height, scale = (float(v) for v in size.stdout.split())
    tap = subprocess.run(
        [str(BINARY), udid, "tap", str(width / scale / 2), str(height / scale / 2)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=_clean_env(),
    )
    assert tap.returncode == 0, tap.stderr
    assert "send error" not in tap.stderr
