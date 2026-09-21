"""Hermetic tests for INIT-2026-641 Wave 2 — server.py's AX routing guard.

`_ax_routing_allowed` is the actual routing decision `observe.observe()`'s
`allow_ax` argument encodes: computed from live `session.all_sessions()`
state, not a flag a caller sets. `tool_observe` must thread `device_name`
and this decision through to `observe.observe()` on every simulator call.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from PIL import Image

from simdrive import server, session
from simdrive.observe import Observation
from simdrive.sim import Device


def _sim_session(tmp_path, sid, device_name="iPhone 17 Pro Max"):
    s = session.Session(
        session_id=sid,
        device=Device(udid=f"UDID-{sid}", name=device_name, os_version="26.3", state="Booted"),
        workdir=tmp_path / f"wd-{sid}",
        target="simulator",
    )
    s.workdir.mkdir(parents=True, exist_ok=True)
    session._SESSIONS[sid] = s
    return s


def _png(path: Path, w=1320, h=2868) -> Path:
    Image.new("RGB", (w, h), (5, 5, 5)).save(path)
    return path


def _fake_observation(png_path: Path) -> Observation:
    return Observation(
        screenshot_path=png_path,
        annotated_path=None,
        screenshot_w=1320,
        screenshot_h=2868,
        window_bounds=None,
        captured_at=0.0,
        marks=[],
    )


def setup_function(_fn):
    # `_ax_routing_allowed` reads the live global session count — the whole
    # point of the guard being state-derived, not a flag. That means these
    # tests need a CLEAN session table, not just their own ids removed:
    # anything another test file left behind (the codebase-wide convention —
    # see test_replay_outcome_verification.py's `_make_sim_session` — is
    # `_SESSIONS.clear()`, not selective pop) would otherwise silently trip
    # the multi-sim guard here.
    session._SESSIONS.clear()


def teardown_function(_fn):
    session._SESSIONS.clear()


def test_ax_routing_allowed_with_single_simulator_session(tmp_path):
    s = _sim_session(tmp_path, "ax-routing-solo")
    assert server._ax_routing_allowed(s) is True


def test_ax_routing_disallowed_with_multiple_concurrent_simulator_sessions(tmp_path):
    s1 = _sim_session(tmp_path, "ax-routing-multi-1")
    _s2 = _sim_session(tmp_path, "ax-routing-multi-2")
    assert server._ax_routing_allowed(s1) is False


def test_ax_routing_ignores_device_target_sessions_in_the_count(tmp_path):
    """A concurrent target=device session doesn't contend for a Simulator
    window — it must not trip the multi-sim guard."""
    s = _sim_session(tmp_path, "ax-routing-sim-1")
    dev = session.Session(
        session_id="ax-routing-dev-1",
        device=Device(udid="UDID-DEV", name="Real iPhone", os_version="26.3", state="active"),
        workdir=tmp_path / "wd-dev",
        target="device",
    )
    session._SESSIONS["ax-routing-dev-1"] = dev
    assert server._ax_routing_allowed(s) is True


def test_tool_observe_threads_device_name_and_routing_decision(tmp_path):
    pngfile = _png(tmp_path / "src.png")
    s = _sim_session(tmp_path, "ax-routing-thread")
    captured = {}

    def fake_observe(udid, out_dir, **kwargs):
        captured.update(kwargs)
        return _fake_observation(pngfile)

    with patch("simdrive.observe.observe", side_effect=fake_observe):
        server.tool_observe({"session_id": s.session_id})

    assert captured["device_name"] == "iPhone 17 Pro Max"
    assert captured["allow_ax"] is True


def test_tool_observe_disables_ax_when_multiple_sim_sessions_active(tmp_path):
    pngfile = _png(tmp_path / "src2.png")
    s1 = _sim_session(tmp_path, "ax-routing-thread-multi-1")
    _s2 = _sim_session(tmp_path, "ax-routing-thread-multi-2")
    captured = {}

    def fake_observe(udid, out_dir, **kwargs):
        captured.update(kwargs)
        return _fake_observation(pngfile)

    with patch("simdrive.observe.observe", side_effect=fake_observe):
        server.tool_observe({"session_id": s1.session_id})

    assert captured["allow_ax"] is False


def test_tool_observe_caller_can_force_ocr_with_allow_ax_false(tmp_path):
    """A caller must be able to opt out of AX perception.

    AX-primary observe collapses some content-dense screens to a handful of
    container marks (a Palace catalog grid returns 5 marks against OCR's 41,
    and the tab items are not individually addressable), so an agent needs a
    way to ask for the OCR view. `observe.observe()` has always taken
    `allow_ax`; the MCP tool did not expose it, leaving no escape hatch on
    the surface agents actually drive.
    """
    pngfile = _png(tmp_path / "src-force-ocr.png")
    s = _sim_session(tmp_path, "ax-routing-force-ocr")
    captured = {}

    def fake_observe(udid, out_dir, **kwargs):
        captured.update(kwargs)
        return _fake_observation(pngfile)

    with patch("simdrive.observe.observe", side_effect=fake_observe):
        server.tool_observe({"session_id": s.session_id, "allow_ax": False})

    assert captured["allow_ax"] is False


def test_tool_observe_allow_ax_true_cannot_override_the_routing_guard(tmp_path):
    """`allow_ax` may only NARROW to OCR, never widen.

    The multi-sim guard is a correctness constraint (AX reads whichever
    Simulator window is frontmost), not a preference. A caller passing
    allow_ax=True while two sim sessions are live must still get OCR.
    """
    pngfile = _png(tmp_path / "src-no-widen.png")
    s1 = _sim_session(tmp_path, "ax-routing-no-widen-1")
    _s2 = _sim_session(tmp_path, "ax-routing-no-widen-2")
    captured = {}

    def fake_observe(udid, out_dir, **kwargs):
        captured.update(kwargs)
        return _fake_observation(pngfile)

    with patch("simdrive.observe.observe", side_effect=fake_observe):
        server.tool_observe({"session_id": s1.session_id, "allow_ax": True})

    assert captured["allow_ax"] is False


def test_tool_observe_defaults_to_ax_when_allow_ax_omitted(tmp_path):
    """Omitting the new parameter must not change existing behaviour."""
    pngfile = _png(tmp_path / "src-default.png")
    s = _sim_session(tmp_path, "ax-routing-default")
    captured = {}

    def fake_observe(udid, out_dir, **kwargs):
        captured.update(kwargs)
        return _fake_observation(pngfile)

    with patch("simdrive.observe.observe", side_effect=fake_observe):
        server.tool_observe({"session_id": s.session_id})

    assert captured["allow_ax"] is True
