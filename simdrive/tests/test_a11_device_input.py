"""Regression tests for simdrive a11 device input pipeline.

F-005: Session-client reuse — input tools prefer s.wda_client when set,
       falling back to _wda_client_for() only when wda_client is None.

F-006: Pixel-to-point scale conversion — pixel coords from screenshots are
       divided by the device's pixel-per-point ratio before reaching WDA's
       tap/swipe endpoints. Sim sessions skip the WDA lookup (fast-path).
       The scale is cached on first call to avoid redundant HTTP roundtrips.

All 12 tests FAIL on feat/v17-claude-native HEAD (3a22bd4) because:
  F-005: server.py always calls _wda_client_for(), never checks s.wda_client.
  F-006: No scale logic, no window_size_points(), no pixel_per_point_scale.
"""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ─── helpers ─────────────────────────────────────────────────────────────────

_FAKE_UDID = "31471BBD-0000-DEAD-BEEF-A11TESTDEVICE"


def _make_device_session(
    tmp_path: Path,
    sid: str = "a11test",
    udid: str = _FAKE_UDID,
    screenshot_w: int = 0,
    screenshot_h: int = 0,
    wda_client=None,
    target: str = "device",
) -> object:
    """Construct a Session and register it in the global _SESSIONS dict."""
    from simdrive import session as session_mod
    from simdrive.sim import Device

    d = Device(udid=udid, name="Test Device", os_version="26.0", state="active")
    workdir = tmp_path / "sessions" / sid
    workdir.mkdir(parents=True, exist_ok=True)

    kwargs: dict = dict(
        session_id=sid,
        device=d,
        workdir=workdir,
        target=target,
        last_screenshot_w=screenshot_w,
        last_screenshot_h=screenshot_h,
        wda_client=wda_client,
    )
    # pixel_per_point_scale is a new F-006 field; may not exist on HEAD.
    try:
        s = session_mod.Session(**kwargs)
    except TypeError:
        # Fall back for HEAD where the field doesn't exist yet.
        s = session_mod.Session(**{k: v for k, v in kwargs.items()})
    session_mod._SESSIONS[sid] = s
    return s


def _mock_wda(session_id: str = "open-sid") -> MagicMock:
    """Return a MagicMock WdaClient with an open session."""
    m = MagicMock()
    m._session_id = session_id
    return m


@pytest.fixture(autouse=True)
def _cleanup_sessions():
    """Remove test sessions after each test."""
    from simdrive import session as session_mod
    before = set(session_mod._SESSIONS.keys())
    yield
    for k in list(session_mod._SESSIONS.keys()):
        if k not in before:
            session_mod._SESSIONS.pop(k, None)


# ─── F-005 tests: session-client reuse ───────────────────────────────────────


def test_session_client_reused_for_device_tap(tmp_path):
    """tool_tap must use s.wda_client directly, not call _wda_client_for."""
    wda = _mock_wda()
    s = _make_device_session(tmp_path, screenshot_w=1320, screenshot_h=2868, wda_client=wda)

    with patch("simdrive.server._wda_client_for") as mock_fallback:
        from simdrive import server
        server.tool_tap({"session_id": s.session_id, "x": 100, "y": 100})

    mock_fallback.assert_not_called()
    wda.tap.assert_called_once()


def test_fallback_when_no_session_client(tmp_path):
    """tool_tap must call _wda_client_for when s.wda_client is None."""
    s = _make_device_session(tmp_path, screenshot_w=1320, screenshot_h=2868, wda_client=None)

    stub_wda = _mock_wda()
    with patch("simdrive.server._wda_client_for", return_value=stub_wda) as mock_fallback:
        from simdrive import server
        server.tool_tap({"session_id": s.session_id, "x": 100, "y": 100})

    mock_fallback.assert_called_once()
    stub_wda.tap.assert_called_once()


def test_swipe_reuses_session_client(tmp_path):
    """tool_swipe must use s.wda_client directly when set."""
    wda = _mock_wda()
    s = _make_device_session(tmp_path, screenshot_w=1320, screenshot_h=2868, wda_client=wda)

    with patch("simdrive.server._wda_client_for") as mock_fallback:
        from simdrive import server
        server.tool_swipe({
            "session_id": s.session_id,
            "x1": 100, "y1": 200, "x2": 100, "y2": 400,
        })

    mock_fallback.assert_not_called()
    wda.swipe.assert_called_once()


def test_type_text_reuses_session_client(tmp_path):
    """tool_type_text must use s.wda_client directly when set."""
    wda = _mock_wda()
    s = _make_device_session(tmp_path, screenshot_w=1320, screenshot_h=2868, wda_client=wda)

    # observe is called post-type; stub it out to avoid real simulator calls.
    mock_obs = MagicMock()
    mock_obs.screenshot_w = 1320
    mock_obs.screenshot_h = 2868
    mock_obs.screenshot_path = tmp_path / "obs.png"
    mock_obs.marks = []

    with patch("simdrive.server._wda_client_for") as mock_fallback, \
         patch("simdrive.observe.observe", return_value=mock_obs), \
         patch("simdrive.server.tool_observe", return_value={}):
        from simdrive import server
        server.tool_type_text({"session_id": s.session_id, "text": "hello"})

    mock_fallback.assert_not_called()
    wda.type_text.assert_called_once_with("hello")


def test_press_key_reuses_session_client(tmp_path):
    """tool_press_key must use s.wda_client directly when set."""
    wda = _mock_wda()
    s = _make_device_session(tmp_path, screenshot_w=1320, screenshot_h=2868, wda_client=wda)

    with patch("simdrive.server._wda_client_for") as mock_fallback:
        from simdrive import server
        server.tool_press_key({"session_id": s.session_id, "key": "home"})

    mock_fallback.assert_not_called()
    wda.press_key.assert_called_once()


def test_observe_device_reuses_session_client(tmp_path):
    """tool_observe on target=device must use s.wda_client directly when set."""
    import io
    from unittest.mock import MagicMock

    # Build a 1-pixel PNG so PIL can open it.
    _ONE_PX_PNG = bytes.fromhex(
        "89504e470d0a1a0a"
        "0000000d49484452"
        "00000001"
        "00000001"
        "08060000001f15c489"
        "0000000a49444154"
        "789c6260000000020001"
        "e221bc33"
        "0000000049454e44ae426082"
    )

    wda = _mock_wda()
    wda.screenshot_any.return_value = _ONE_PX_PNG
    s = _make_device_session(tmp_path, wda_client=wda)

    with patch("simdrive.server._wda_client_for") as mock_fallback:
        from simdrive import server
        server.tool_observe({"session_id": s.session_id})

    mock_fallback.assert_not_called()
    wda.screenshot_any.assert_called_once()


# ─── F-006 tests: pixel-to-point scale conversion ────────────────────────────


def test_scale_3x_pro_max(tmp_path):
    """3x device: pixel coords divided by 3.0 before reaching wda.tap."""
    wda = _mock_wda()
    wda.window_size_points.return_value = (440, 956)
    s = _make_device_session(
        tmp_path, screenshot_w=1320, screenshot_h=2868, wda_client=wda
    )

    from simdrive import server
    server.tool_tap({"session_id": s.session_id, "x": 495, "y": 2680})

    wda.tap.assert_called_once()
    args = wda.tap.call_args[0]
    # scale = 1320 / 440 = 3.0
    assert args[0] == pytest.approx(495 / 3.0, rel=1e-3)
    assert args[1] == pytest.approx(2680 / 3.0, rel=1e-3)


def test_scale_2x_se(tmp_path):
    """2x device: pixel coords divided by 2.0 before reaching wda.tap."""
    wda = _mock_wda()
    wda.window_size_points.return_value = (375, 667)
    s = _make_device_session(
        tmp_path, screenshot_w=750, screenshot_h=1334, wda_client=wda
    )

    from simdrive import server
    server.tool_tap({"session_id": s.session_id, "x": 200, "y": 400})

    wda.tap.assert_called_once()
    args = wda.tap.call_args[0]
    assert args[0] == pytest.approx(100.0, rel=1e-3)
    assert args[1] == pytest.approx(200.0, rel=1e-3)


def test_scale_1x_sim_fastpath(tmp_path):
    """Simulator sessions skip window_size_points and pass pixel coords unchanged."""
    # We need a sim-target session, which means the tap goes through act.tap, not wda.
    # The fast-path is: target="simulator" → act.tap (HID/cliclick) → NO wda lookup.
    from simdrive import session as session_mod
    from simdrive.sim import Device

    d = Device(udid="SIM-UDID-FASTPATH", name="iPhone 16 Pro", os_version="26.0", state="active")
    workdir = tmp_path / "sessions" / "simtest"
    workdir.mkdir(parents=True, exist_ok=True)
    s = session_mod.Session(
        session_id="simtest",
        device=d,
        workdir=workdir,
        target="simulator",
        last_screenshot_w=1320,
        last_screenshot_h=2868,
    )
    session_mod._SESSIONS["simtest"] = s

    wda_sentinel = MagicMock()

    with patch("simdrive.server._wda_client_for", return_value=wda_sentinel) as mock_fallback, \
         patch("simdrive.act.tap", return_value=(100, 200)) as mock_act_tap:
        from simdrive import server
        server.tool_tap({"session_id": "simtest", "x": 100, "y": 200})

    # _wda_client_for should not be called for simulator sessions.
    mock_fallback.assert_not_called()
    # window_size_points should not be called either (wda_sentinel never returned).
    wda_sentinel.window_size_points.assert_not_called()
    # act.tap called with the raw pixel coords (no division).
    mock_act_tap.assert_called_once()
    act_args = mock_act_tap.call_args[0]
    assert act_args[0] == 100
    assert act_args[1] == 200


def test_scale_cached_across_calls(tmp_path):
    """window_size_points is called exactly once even when tool_tap is invoked 3 times."""
    wda = _mock_wda()
    wda.window_size_points.return_value = (440, 956)
    s = _make_device_session(
        tmp_path, screenshot_w=1320, screenshot_h=2868, wda_client=wda
    )

    from simdrive import server
    for _ in range(3):
        server.tool_tap({"session_id": s.session_id, "x": 300, "y": 600})

    # Caching means the HTTP call happens only once across 3 taps.
    assert wda.window_size_points.call_count == 1


def test_scale_swipe_converts_both_endpoints(tmp_path):
    """3x device swipe: both (x1,y1) and (x2,y2) divided by 3.0."""
    wda = _mock_wda()
    wda.window_size_points.return_value = (440, 956)
    s = _make_device_session(
        tmp_path, screenshot_w=1320, screenshot_h=2868, wda_client=wda
    )

    from simdrive import server
    server.tool_swipe({
        "session_id": s.session_id,
        "x1": 300, "y1": 600,
        "x2": 900, "y2": 1800,
    })

    wda.swipe.assert_called_once()
    args = wda.swipe.call_args[0]
    assert args[0] == pytest.approx(100.0, rel=1e-3)   # x1 / 3
    assert args[1] == pytest.approx(200.0, rel=1e-3)   # y1 / 3
    assert args[2] == pytest.approx(300.0, rel=1e-3)   # x2 / 3
    assert args[3] == pytest.approx(600.0, rel=1e-3)   # y2 / 3


def test_scale_window_size_http_error_defaults_to_1(tmp_path, caplog):
    """If window_size_points raises httpx.HTTPError, scale defaults to 1.0 and no exception escapes."""
    import httpx

    wda = _mock_wda()
    wda.window_size_points.side_effect = httpx.HTTPError("connection refused")
    s = _make_device_session(
        tmp_path, screenshot_w=1320, screenshot_h=2868, wda_client=wda
    )

    from simdrive import server
    with caplog.at_level(logging.WARNING):
        # Must NOT raise.
        server.tool_tap({"session_id": s.session_id, "x": 300, "y": 600})

    # With scale=1.0 fallback, pixel coords reach wda.tap unchanged.
    wda.tap.assert_called_once()
    args = wda.tap.call_args[0]
    assert args[0] == pytest.approx(300.0, rel=1e-3)
    assert args[1] == pytest.approx(600.0, rel=1e-3)
    # A warning must have been logged.
    assert any("warn" in r.levelname.lower() or r.levelno >= logging.WARNING
               for r in caplog.records), "Expected a WARNING log when window_size_points fails"
