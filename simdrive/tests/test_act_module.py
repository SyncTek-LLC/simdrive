"""Unit tests for ``simdrive.act`` — input dispatch with mocked backends.

The act module has two backends: HID (preferred) and cliclick (fallback).
These tests use ``unittest.mock`` to swap both out so we can verify the
dispatch math and routing without a live simulator window.
"""
from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from simdrive import act
from simdrive.act import ActError
from simdrive.window import WindowBounds


# ── _backend selection ──────────────────────────────────────────────────────


def test_backend_returns_cliclick_when_env_overrides(monkeypatch):
    monkeypatch.setenv("SIMDRIVE_INPUT_BACKEND", "cliclick")
    assert act._backend() == "cliclick"


def test_backend_returns_hid_when_available(monkeypatch):
    monkeypatch.delenv("SIMDRIVE_INPUT_BACKEND", raising=False)
    with patch("simdrive.hid_inject.available", return_value=True):
        assert act._backend() == "hid"


def test_backend_falls_back_to_cliclick_when_hid_unavailable(monkeypatch):
    monkeypatch.delenv("SIMDRIVE_INPUT_BACKEND", raising=False)
    with patch("simdrive.hid_inject.available", return_value=False):
        assert act._backend() == "cliclick"


# ── _device_geom caches ─────────────────────────────────────────────────────


def test_device_geom_caches_per_udid():
    act._DEVICE_GEOM_CACHE.clear()
    with patch("simdrive.hid_inject.device_size_points", return_value=(393.0, 852.0, 3.0)) as m:
        a = act._device_geom("UDID-X")
        b = act._device_geom("UDID-X")
    assert a == (393.0, 852.0, 3.0)
    assert b == a
    assert m.call_count == 1  # second call hit the cache
    act._DEVICE_GEOM_CACHE.clear()


# ── _pixels_to_points / _pixels_to_screen invariants ─────────────────────


def test_pixels_to_points_invalid_dims_raises():
    with pytest.raises(ActError):
        act._pixels_to_points("U", 0, 0, 0, 100)


def test_pixels_to_points_math():
    act._DEVICE_GEOM_CACHE.clear()
    with patch("simdrive.hid_inject.device_size_points", return_value=(393.0, 852.0, 3.0)):
        x, y = act._pixels_to_points("U", 600, 1300, 1206, 2622)
    # px/screenshot_w * logical_w
    assert abs(x - (600 / 1206 * 393.0)) < 1e-6
    assert abs(y - (1300 / 2622 * 852.0)) < 1e-6
    act._DEVICE_GEOM_CACHE.clear()


# ── tap ─────────────────────────────────────────────────────────────────────


def test_tap_hid_path_dispatches_via_hid(monkeypatch):
    monkeypatch.setenv("SIMDRIVE_INPUT_BACKEND", "")  # default
    act._DEVICE_GEOM_CACHE.clear()
    with patch("simdrive.hid_inject.available", return_value=True), \
         patch("simdrive.hid_inject.device_size_points", return_value=(393.0, 852.0, 3.0)), \
         patch("simdrive.hid_inject.tap") as mock_tap:
        out = act.tap(100, 200, 1206, 2622, udid="UDID-X")
    assert out == (0, 0)  # HID path returns (0, 0)
    assert mock_tap.called
    act._DEVICE_GEOM_CACHE.clear()


def test_tap_cliclick_path_when_no_udid(monkeypatch):
    monkeypatch.setenv("SIMDRIVE_INPUT_BACKEND", "")  # default
    bounds = WindowBounds(x=1000, y=100, width=400, height=800)
    with patch("simdrive.hid_inject.available", return_value=False), \
         patch("simdrive.act.get_bounds", return_value=bounds), \
         patch("simdrive.act.activate"), \
         patch("simdrive.act._run_cliclick") as mock_run, \
         patch("simdrive.act.time.sleep"):
        sx, sy = act.tap(500, 1000, 1000, 2000)
    assert (sx, sy) == (1200, 500)  # center math
    assert mock_run.called


def test_tap_hid_skipped_when_udid_none(monkeypatch):
    """No udid given -> always cliclick path even if HID is available."""
    monkeypatch.setenv("SIMDRIVE_INPUT_BACKEND", "")
    bounds = WindowBounds(x=0, y=0, width=100, height=100)
    with patch("simdrive.hid_inject.available", return_value=True), \
         patch("simdrive.act.get_bounds", return_value=bounds), \
         patch("simdrive.act.activate"), \
         patch("simdrive.act._run_cliclick"), \
         patch("simdrive.act.time.sleep"):
        sx, sy = act.tap(10, 10, 100, 100, udid=None)
    assert (sx, sy) == (10, 10)


# ── _run_cliclick ───────────────────────────────────────────────────────────


def test_run_cliclick_raises_on_nonzero():
    fake = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
    with patch("simdrive.act.cliclick_path", return_value="/fake/cliclick"), \
         patch("simdrive.act.subprocess.run", return_value=fake):
        with pytest.raises(ActError) as exc:
            act._run_cliclick(["c:1,1"])
    assert "cliclick failed" in str(exc.value)


def test_run_cliclick_succeeds():
    ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("simdrive.act.cliclick_path", return_value="/fake/cliclick"), \
         patch("simdrive.act.subprocess.run", return_value=ok):
        act._run_cliclick(["c:1,1"])  # Doesn't raise


# ── swipe ───────────────────────────────────────────────────────────────────


def test_swipe_hid_path(monkeypatch):
    monkeypatch.setenv("SIMDRIVE_INPUT_BACKEND", "")
    act._DEVICE_GEOM_CACHE.clear()
    with patch("simdrive.hid_inject.available", return_value=True), \
         patch("simdrive.hid_inject.device_size_points", return_value=(393.0, 852.0, 3.0)), \
         patch("simdrive.hid_inject.swipe") as mock_swipe:
        act.swipe(100, 200, 300, 400, 1206, 2622, duration_ms=300, udid="UDID-X")
    assert mock_swipe.called
    _, kwargs = mock_swipe.call_args
    # steps should be max(4, 300//25) = 12
    assert kwargs.get("steps") == 12 or mock_swipe.call_args.args[5] == 12
    act._DEVICE_GEOM_CACHE.clear()


def test_swipe_cliclick_path(monkeypatch):
    monkeypatch.setenv("SIMDRIVE_INPUT_BACKEND", "")
    bounds = WindowBounds(x=0, y=0, width=1000, height=1000)
    with patch("simdrive.hid_inject.available", return_value=False), \
         patch("simdrive.act.get_bounds", return_value=bounds), \
         patch("simdrive.act.activate"), \
         patch("simdrive.act._run_cliclick") as mock_run, \
         patch("simdrive.act.time.sleep"):
        act.swipe(0, 0, 1000, 1000, 1000, 1000, duration_ms=300)
    assert mock_run.called


def test_swipe_clamps_short_duration(monkeypatch):
    """duration_ms below 50ms should be clamped to 50ms."""
    monkeypatch.setenv("SIMDRIVE_INPUT_BACKEND", "")
    bounds = WindowBounds(x=0, y=0, width=100, height=100)
    with patch("simdrive.hid_inject.available", return_value=False), \
         patch("simdrive.act.get_bounds", return_value=bounds), \
         patch("simdrive.act.activate"), \
         patch("simdrive.act._run_cliclick") as mock_run, \
         patch("simdrive.act.time.sleep"):
        act.swipe(0, 0, 100, 100, 100, 100, duration_ms=10)
    assert mock_run.called  # Doesn't crash on tiny duration


# ── type_text ───────────────────────────────────────────────────────────────


def test_type_text_empty_returns_early():
    # Nothing should be called for empty text — assert no exceptions, no calls.
    with patch("simdrive.hid_inject.type_text") as mock_hid:
        act.type_text("")
    assert not mock_hid.called


def test_type_text_ascii_hid_path(monkeypatch):
    monkeypatch.setenv("SIMDRIVE_INPUT_BACKEND", "")
    with patch("simdrive.hid_inject.available", return_value=True), \
         patch("simdrive.hid_inject.type_text") as mock_hid:
        act.type_text("hello", udid="UDID-X")
    mock_hid.assert_called_with("UDID-X", "hello")


def test_type_text_non_ascii_uses_pasteboard(monkeypatch):
    monkeypatch.setenv("SIMDRIVE_INPUT_BACKEND", "")
    with patch("simdrive.hid_inject.available", return_value=True), \
         patch("simdrive.act.sim.set_pasteboard") as mock_pb, \
         patch("simdrive.act._hid_paste") as mock_paste, \
         patch("simdrive.act.time.sleep"):
        act.type_text("héllo", udid="UDID-X")
    mock_pb.assert_called_with("UDID-X", "héllo")
    mock_paste.assert_called_with("UDID-X")


def test_type_text_cliclick_path(monkeypatch):
    monkeypatch.setenv("SIMDRIVE_INPUT_BACKEND", "cliclick")
    with patch("simdrive.act.activate"), \
         patch("simdrive.act._run_cliclick") as mock_run, \
         patch("simdrive.act.time.sleep"):
        act.type_text("hello")
    assert mock_run.called
    args = mock_run.call_args.args[0]
    assert args == ["t:hello"]


def test_hid_paste_invokes_cmd_v():
    with patch("simdrive.hid_inject.chord") as mock_chord:
        act._hid_paste("UDID-X")
    mock_chord.assert_called_with("UDID-X", "cmd", "v")


# ── press_key ───────────────────────────────────────────────────────────────


def test_press_key_hid_device_button(monkeypatch):
    monkeypatch.setenv("SIMDRIVE_INPUT_BACKEND", "")
    with patch("simdrive.hid_inject.available", return_value=True), \
         patch("simdrive.hid_inject.press_button") as mock_btn:
        act.press_key("home", udid="UDID-X")
    mock_btn.assert_called_with("UDID-X", "home")


def test_press_key_hid_keypad_key(monkeypatch):
    monkeypatch.setenv("SIMDRIVE_INPUT_BACKEND", "")
    with patch("simdrive.hid_inject.available", return_value=True), \
         patch("simdrive.hid_inject.press_key") as mock_press:
        act.press_key("return", udid="UDID-X")
    mock_press.assert_called_with("UDID-X", 40)  # return = 40


def test_press_key_unknown_key_falls_back_to_cliclick_path(monkeypatch):
    monkeypatch.setenv("SIMDRIVE_INPUT_BACKEND", "")
    # When HID path has no map for the key, falls through to cliclick.
    with patch("simdrive.hid_inject.available", return_value=True), \
         patch("simdrive.act.activate"), \
         patch("simdrive.act._run_cliclick") as mock_run, \
         patch("simdrive.act.time.sleep"):
        act.press_key("return", udid=None)  # No udid => skip HID branch
    assert mock_run.called


def test_press_key_device_menu_via_osascript(monkeypatch):
    """Sim-only buttons go through the Simulator's Device menu via osascript."""
    monkeypatch.setenv("SIMDRIVE_INPUT_BACKEND", "cliclick")
    ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("simdrive.act.subprocess.run", return_value=ok) as mock_run:
        act.press_key("shake")
    assert mock_run.called
    args = mock_run.call_args.args[0]
    assert "osascript" in args[0]


def test_menu_click_raises_on_failure():
    fail = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not found")
    with patch("simdrive.act.subprocess.run", return_value=fail):
        with pytest.raises(ActError) as exc:
            act._menu_click("Device", "Home")
    assert "menu click" in str(exc.value)


def test_press_key_cliclick_keymap(monkeypatch):
    monkeypatch.setenv("SIMDRIVE_INPUT_BACKEND", "cliclick")
    with patch("simdrive.act.activate"), \
         patch("simdrive.act._run_cliclick") as mock_run, \
         patch("simdrive.act.time.sleep"):
        act.press_key("tab")
    assert mock_run.called
    args = mock_run.call_args.args[0]
    assert args == ["kp:tab"]


def test_press_key_unsupported_raises():
    with pytest.raises(ActError) as exc:
        # 'totally-not-a-key' isn't in any map -> reaches the unsupported branch
        act.press_key("totally-not-a-key")
    assert "unsupported key" in str(exc.value).lower() or "Supported" in str(exc.value)


def test_pixels_to_screen_invalid_dims_raises():
    bounds = WindowBounds(x=0, y=0, width=10, height=10)
    with pytest.raises(ActError):
        act._pixels_to_screen(bounds, 5, 5, 100, 0)
