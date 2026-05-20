"""Targeted tests for ``simdrive.server`` dispatcher + clear_field branches.

server.py is large (1000+ stmts) and many of its handlers require a live
simulator. This file focuses on the recently-touched dispatcher paths
(``call_tool`` / ``call_tool_async`` / ``_check_quota_for_call``) and the
``tool_clear_field`` simulator path's HID error swallowing branch.
"""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simdrive import errors, server, session
from simdrive.cloud.errors import QuotaExceededError
from simdrive.cloud.middleware.quotas import LocalQuotaSnapshot
from simdrive.sim import Device


# ── _check_version_drift / call_tool warning side-channel ───────────────────


def test_disk_version_cached():
    """A second call within the TTL should reuse the cache, not re-query."""
    # Prime the cache with a known sentinel and a fresh timestamp.
    server._DISK_VERSION_CACHE["version"] = "9.9.9"
    server._DISK_VERSION_CACHE["checked_at"] = server.time.time()
    try:
        v = server._disk_version()
        assert v == "9.9.9"
    finally:
        server._DISK_VERSION_CACHE["version"] = None
        server._DISK_VERSION_CACHE["checked_at"] = 0.0


def test_disk_version_metadata_failure_returns_none():
    """If importlib.metadata.version raises, _disk_version returns None."""
    server._DISK_VERSION_CACHE["version"] = None
    server._DISK_VERSION_CACHE["checked_at"] = 0.0
    # importlib.metadata is imported lazily inside _disk_version; we can patch
    # sys.modules so the inner import resolves to a dummy that raises.
    import importlib.metadata as _md_real
    try:
        with patch.object(_md_real, "version", side_effect=Exception("no metadata")):
            v = server._disk_version()
        assert v is None
    finally:
        server._DISK_VERSION_CACHE["version"] = None
        server._DISK_VERSION_CACHE["checked_at"] = 0.0


def test_check_version_drift_no_drift_when_disk_none(monkeypatch):
    monkeypatch.setattr(server, "_disk_version", lambda: None)
    assert server._check_version_drift() is None


def test_check_version_drift_no_drift_when_equal(monkeypatch):
    monkeypatch.setattr(server, "_disk_version", lambda: server._LOADED_VERSION)
    assert server._check_version_drift() is None


def test_check_version_drift_warns_on_mismatch(monkeypatch):
    monkeypatch.setattr(server, "_disk_version", lambda: "999.0.0")
    out = server._check_version_drift()
    assert out is not None
    assert "999.0.0" in out


def test_call_tool_attaches_simdrive_warning_on_drift(monkeypatch):
    """When loaded != disk, call_tool should annotate dict results with _simdrive_warning."""
    monkeypatch.setattr(server, "_check_version_drift", lambda: "restart needed")
    result = server.call_tool("version", {})
    assert result.get("_simdrive_warning") == "restart needed"


def test_call_tool_does_not_overwrite_existing_simdrive_warning(monkeypatch):
    """If the handler already returned a _simdrive_warning, call_tool shouldn't clobber it."""
    sentinel_result = {"ok": True, "_simdrive_warning": "handler-set"}

    original_handler = None
    for t in server._TOOLS:
        if t["name"] == "version":
            original_handler = t["handler"]
            t["handler"] = lambda args: sentinel_result
            break
    try:
        monkeypatch.setattr(server, "_check_version_drift", lambda: "drift detected")
        result = server.call_tool("version", {})
        assert result["_simdrive_warning"] == "handler-set"
    finally:
        for t in server._TOOLS:
            if t["name"] == "version":
                t["handler"] = original_handler


# ── call_tool: unknown tool / async-tool guard ──────────────────────────────


def test_call_tool_async_handler_raises_runtimeerror():
    """call_tool (sync) should reject async-handler tools with RuntimeError."""
    async_tool_name = None
    for t in server._TOOLS:
        if inspect.iscoroutinefunction(t["handler"]):
            async_tool_name = t["name"]
            break
    if not async_tool_name:
        pytest.skip("no async handlers registered to test against")
    with pytest.raises(RuntimeError) as exc:
        server.call_tool(async_tool_name, {})
    assert "call_tool_async" in str(exc.value)


def test_call_tool_unknown_tool_raises_valueerror():
    with pytest.raises(ValueError) as exc:
        server.call_tool("no-such-tool", {})
    assert "unknown tool" in str(exc.value)


# ── call_tool_async ─────────────────────────────────────────────────────────


def test_call_tool_async_dispatches_sync_handler():
    """call_tool_async should also work with non-async handlers."""
    async def _go():
        return await server.call_tool_async("version", {})

    result = asyncio.run(_go())
    assert isinstance(result, dict)
    assert "version" in result


def test_call_tool_async_unknown_tool_raises():
    async def _go():
        return await server.call_tool_async("no-such-thing", {})

    with pytest.raises(ValueError):
        asyncio.run(_go())


def test_call_tool_async_propagates_version_drift_warning(monkeypatch):
    monkeypatch.setattr(server, "_check_version_drift", lambda: "upgrade!")

    async def _go():
        return await server.call_tool_async("version", {})

    result = asyncio.run(_go())
    assert result.get("_simdrive_warning") == "upgrade!"


# ── _check_quota_for_call ──────────────────────────────────────────────────


def test_check_quota_no_session_id_returns_immediately():
    """No session_id in args => skip quota lookup entirely."""
    # If this would consult the cloud, it'd error — but it must just return.
    server._check_quota_for_call("anything", {})  # No raise.


def test_check_quota_unknown_session_returns():
    """An unknown session_id raises no_session via session.get, which is caught and swallowed."""
    server._check_quota_for_call("anything", {"session_id": "definitely-not-real"})


# ── tool_clear_field — simulator path with HID failure swallowed ────────────


def _fake_sim_session(sid, tmp_path):
    s = session.Session(
        session_id=sid,
        device=Device(udid="UDID-X", name="iPhone", os_version="26.3", state="Booted"),
        workdir=tmp_path,
        target="simulator",
    )
    return s


def test_clear_field_sim_hid_failure_returns_cleared_false(tmp_path):
    """When the HID chord fails on simulator, clear_field should report cleared=False
    instead of raising (the simulator path swallows the OSError-class exceptions)."""
    sid = "test-clear-field-fail"
    s = _fake_sim_session(sid, tmp_path)
    session._SESSIONS[sid] = s
    try:
        with patch("simdrive.hid_inject.chord", side_effect=RuntimeError("hid down")):
            result = server.tool_clear_field({"session_id": sid})
        assert result["cleared"] is False
        assert result["ok"] is False
    finally:
        session._SESSIONS.pop(sid, None)


def test_clear_field_sim_success_returns_cleared_true(tmp_path):
    sid = "test-clear-field-ok"
    s = _fake_sim_session(sid, tmp_path)
    session._SESSIONS[sid] = s
    try:
        with patch("simdrive.hid_inject.chord") as mock_chord, \
             patch("simdrive.act.press_key") as mock_pk:
            result = server.tool_clear_field({"session_id": sid})
        assert result["cleared"] is True
        assert mock_chord.called
        assert mock_pk.called
    finally:
        session._SESSIONS.pop(sid, None)


def test_clear_field_sim_with_target_taps_first(tmp_path):
    """Passing target={x,y} taps before clearing."""
    sid = "test-clear-field-tap"
    s = _fake_sim_session(sid, tmp_path)
    s.last_screenshot_w = 1000
    s.last_screenshot_h = 2000
    session._SESSIONS[sid] = s
    try:
        with patch("simdrive.act.tap") as mock_tap, \
             patch("simdrive.hid_inject.chord"), \
             patch("simdrive.act.press_key"), \
             patch("simdrive.server.time.sleep"):
            result = server.tool_clear_field({
                "session_id": sid,
                "target": {"x": 100, "y": 200},
            })
        assert result["cleared"] is True
        assert mock_tap.called
    finally:
        session._SESSIONS.pop(sid, None)


def test_clear_field_device_path_uses_wda(tmp_path):
    """Device session routes to wda.clear_field()."""
    sid = "test-clear-field-device"
    fake_wda = SimpleNamespace(
        clear_field=lambda: None,
        tap=lambda *a, **k: None,
    )
    s = session.Session(
        session_id=sid,
        device=Device(udid="UDID-Y", name="Real Device", os_version="26.4", state="active"),
        workdir=tmp_path,
        target="device",
        wda_client=fake_wda,
    )
    session._SESSIONS[sid] = s
    cleared_called = []

    class _W:
        def tap(self, x, y):
            pass

        def clear_field(self):
            cleared_called.append(True)
    s.wda_client = _W()
    try:
        result = server.tool_clear_field({"session_id": sid})
        assert result["ok"] is True
        assert result["cleared"] is True
        assert cleared_called == [True]
    finally:
        session._SESSIONS.pop(sid, None)


def test_clear_field_device_with_target_taps_first(tmp_path):
    """Device path also resolves a target and taps before clearing."""
    sid = "test-clear-field-device-target"
    tap_calls = []
    clear_calls = []

    class _W:
        def tap(self, x, y):
            tap_calls.append((x, y))

        def clear_field(self):
            clear_calls.append(True)

    s = session.Session(
        session_id=sid,
        device=Device(udid="UDID-Z", name="Real Device", os_version="26.4", state="active"),
        workdir=tmp_path,
        target="device",
        wda_client=_W(),
        pixel_per_point_scale=3.0,
    )
    s.last_screenshot_w = 1200
    s.last_screenshot_h = 2700
    session._SESSIONS[sid] = s
    try:
        with patch("simdrive.server.time.sleep"):
            result = server.tool_clear_field({
                "session_id": sid,
                "target": {"x": 600, "y": 1300},
            })
        assert tap_calls == [(200.0, 433.3333333333333)]  # px/scale conversion
        assert clear_calls == [True]
        assert result["cleared"] is True
    finally:
        session._SESSIONS.pop(sid, None)


# ── _check_quota_for_call quota-snapshot path ──────────────────────────────


def test_check_quota_over_limit_raises(tmp_path):
    sid = "test-quota-snap"
    over = LocalQuotaSnapshot(tier="free", runs_used=50, runs_limit=50)
    s = SimpleNamespace(session_id=sid, quota_snapshot=over)
    session._SESSIONS[sid] = s  # type: ignore[assignment]
    try:
        with pytest.raises(QuotaExceededError):
            server._check_quota_for_call("tap", {"session_id": sid})
    finally:
        session._SESSIONS.pop(sid, None)


def test_check_quota_under_limit_passes(tmp_path):
    sid = "test-quota-ok"
    snap = LocalQuotaSnapshot(tier="pro", runs_used=1, runs_limit=1000)
    s = SimpleNamespace(session_id=sid, quota_snapshot=snap)
    session._SESSIONS[sid] = s  # type: ignore[assignment]
    try:
        server._check_quota_for_call("tap", {"session_id": sid})  # No raise.
    finally:
        session._SESSIONS.pop(sid, None)


# ── _wda_client_for ────────────────────────────────────────────────────────


def test_wda_client_for_no_registry_raises():
    """Missing registry => wda_not_bootstrapped SimdriveError."""
    with patch("simdrive.wda.registry.load", return_value=None):
        with pytest.raises(errors.SimdriveError) as exc:
            server._wda_client_for("UDID-NOPE")
    assert "wda_not_bootstrapped" in (exc.value.code or "")


def test_wda_client_for_with_registry_returns_client():
    fake_entry = {"host": "1.2.3.4", "port": 8100}
    with patch("simdrive.wda.registry.load", return_value=fake_entry), \
         patch("simdrive.wda.client.WdaClient") as mock_cls:
        out = server._wda_client_for("UDID-OK")
    assert mock_cls.called
    assert out is mock_cls.return_value


# ── _session_scale ─────────────────────────────────────────────────────────


def test_session_scale_simulator_returns_1(tmp_path):
    s = session.Session(
        session_id="t",
        device=Device(udid="U", name="i", os_version="26.3", state="Booted"),
        workdir=tmp_path,
        target="simulator",
    )
    assert server._session_scale(s) == 1.0


def test_session_scale_device_uses_cache(tmp_path):
    s = session.Session(
        session_id="t",
        device=Device(udid="U", name="i", os_version="26.3", state="active"),
        workdir=tmp_path,
        target="device",
        pixel_per_point_scale=3.0,
    )
    assert server._session_scale(s) == 3.0


def test_session_scale_device_wda_failure_returns_1(tmp_path):
    s = session.Session(
        session_id="t",
        device=Device(udid="U", name="i", os_version="26.3", state="active"),
        workdir=tmp_path,
        target="device",
    )
    s.last_screenshot_w = 1320
    s.last_screenshot_h = 2868

    class _W:
        def window_size_points(self):
            raise RuntimeError("WDA broken")
    assert server._session_scale(s, wda=_W()) == 1.0


def test_session_scale_device_invalid_window_returns_1(tmp_path):
    s = session.Session(
        session_id="t",
        device=Device(udid="U", name="i", os_version="26.3", state="active"),
        workdir=tmp_path,
        target="device",
    )
    s.last_screenshot_w = 1320
    s.last_screenshot_h = 2868

    class _W:
        def window_size_points(self):
            return (0, 0)
    assert server._session_scale(s, wda=_W()) == 1.0


def test_session_scale_device_normal(tmp_path):
    s = session.Session(
        session_id="t",
        device=Device(udid="U", name="i", os_version="26.3", state="active"),
        workdir=tmp_path,
        target="device",
    )
    s.last_screenshot_w = 1320
    s.last_screenshot_h = 2868

    class _W:
        def window_size_points(self):
            return (440, 956)
    out = server._session_scale(s, wda=_W())
    assert 2.99 < out < 3.01  # ≈ 3.0


# ── _get_current_mcp_session ────────────────────────────────────────────────


def test_get_current_mcp_session_no_server_returns_none():
    orig = server._MCP_SERVER
    try:
        server._MCP_SERVER = None
        assert server._get_current_mcp_session() is None
    finally:
        server._MCP_SERVER = orig


def test_get_current_mcp_session_no_request_context_returns_none():
    orig = server._MCP_SERVER
    try:
        # Object with no request_context attribute.
        server._MCP_SERVER = SimpleNamespace()
        assert server._get_current_mcp_session() is None
    finally:
        server._MCP_SERVER = orig
