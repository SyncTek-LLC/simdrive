"""Tests for tool_observe on target=device — routes through WDA /screenshot.

Verifies that:
  1. tool_observe with a target=device session calls WdaClient.screenshot_any
     instead of idevicescreenshot / observe.observe.
  2. The returned dict has the expected shape (screenshot_path, screenshot_size_pixels,
     marks=[], target="device").
  3. The screenshot PNG is written to the session workdir/observations directory.
  4. Session state (last_screenshot_w/h/path) is updated correctly.
  5. idevicescreenshot is NOT called for target=device sessions.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# 1x1 transparent PNG — smallest valid PNG that PIL can open.
_ONE_PX_PNG = bytes.fromhex(
    "89504e470d0a1a0a"          # PNG magic
    "0000000d49484452"          # IHDR chunk length + type
    "00000001"                  # width = 1
    "00000001"                  # height = 1
    "08060000001f15c489"        # bit-depth=8, colour=RGBA, crc
    "0000000a49444154"          # IDAT chunk
    "789c6260000000020001"      # zlib-compressed 1x1 RGBA pixel
    "e221bc33"                  # IDAT crc
    "0000000049454e44ae426082"  # IEND chunk
)


# ── session factory ───────────────────────────────────────────────────────────


def _make_device_session(tmp_path: Path, udid: str = "TEST-CORE-DEVICE-OBSERVE") -> object:
    """Create an in-memory Session with target='device', inserted into the global registry."""
    from simdrive import session as session_mod
    from simdrive.sim import Device

    d = Device(udid=udid, name="Moes Max", os_version="26.3.1", state="active")
    workdir = tmp_path / "sessions" / "obstest"
    workdir.mkdir(parents=True, exist_ok=True)
    s = session_mod.Session(
        session_id="obstest",
        device=d,
        workdir=workdir,
        target="device",
    )
    session_mod._SESSIONS["obstest"] = s
    return s


@pytest.fixture(autouse=True)
def _cleanup_sessions():
    from simdrive import session as session_mod
    yield
    session_mod._SESSIONS.pop("obstest", None)


# ── helpers ───────────────────────────────────────────────────────────────────


def _registry_entry(host: str = "localhost", port: int = 8100) -> dict:
    return {"host": host, "port": port}


# ── tests ─────────────────────────────────────────────────────────────────────


def test_observe_target_device_calls_screenshot_any(tmp_path):
    """tool_observe with target=device must call WdaClient.screenshot_any, not observe.observe."""
    _make_device_session(tmp_path)

    mock_client = MagicMock()
    mock_client.screenshot_any.return_value = _ONE_PX_PNG

    observe_called = {"called": False}

    def _fake_observe(*args, **kwargs):
        observe_called["called"] = True
        raise AssertionError("observe.observe called for target=device session")

    with patch("simdrive.wda.registry.load", return_value=_registry_entry()), \
         patch("simdrive.wda.client.WdaClient") as mock_cls, \
         patch("simdrive.observe.observe", side_effect=_fake_observe):
        mock_cls.return_value = mock_client

        from simdrive.server import tool_observe
        result = tool_observe({"session_id": "obstest", "annotate": False})

    assert mock_client.screenshot_any.called, "WdaClient.screenshot_any must be called"
    assert not observe_called["called"], "observe.observe must NOT be called for target=device"


def test_observe_target_device_return_shape(tmp_path):
    """The result dict must include screenshot_path, screenshot_size_pixels, marks, target."""
    _make_device_session(tmp_path)

    mock_client = MagicMock()
    mock_client.screenshot_any.return_value = _ONE_PX_PNG

    with patch("simdrive.wda.registry.load", return_value=_registry_entry()), \
         patch("simdrive.wda.client.WdaClient") as mock_cls:
        mock_cls.return_value = mock_client

        from simdrive.server import tool_observe
        result = tool_observe({"session_id": "obstest"})

    assert "screenshot_path" in result
    assert "screenshot_size_pixels" in result
    assert result["screenshot_size_pixels"] == [1, 1]
    assert result["marks"] == []
    assert result["target"] == "device"
    assert result["annotated_path"] is None


def test_observe_target_device_screenshot_b64_is_valid_png(tmp_path):
    """screenshot_b64 in the result must decode to a valid PNG."""
    _make_device_session(tmp_path)

    mock_client = MagicMock()
    mock_client.screenshot_any.return_value = _ONE_PX_PNG

    with patch("simdrive.wda.registry.load", return_value=_registry_entry()), \
         patch("simdrive.wda.client.WdaClient") as mock_cls:
        mock_cls.return_value = mock_client

        from simdrive.server import tool_observe
        result = tool_observe({"session_id": "obstest"})

    assert "screenshot_b64" in result
    decoded = base64.b64decode(result["screenshot_b64"])
    # PNG magic bytes
    assert decoded[:8] == b"\x89PNG\r\n\x1a\n", "screenshot_b64 must decode to a valid PNG"


def test_observe_target_device_writes_file_to_workdir(tmp_path):
    """The screenshot must be written to workdir/observations/observe-<ts>.png."""
    s = _make_device_session(tmp_path)

    mock_client = MagicMock()
    mock_client.screenshot_any.return_value = _ONE_PX_PNG

    with patch("simdrive.wda.registry.load", return_value=_registry_entry()), \
         patch("simdrive.wda.client.WdaClient") as mock_cls:
        mock_cls.return_value = mock_client

        from simdrive.server import tool_observe
        result = tool_observe({"session_id": "obstest"})

    screenshot_path = Path(result["screenshot_path"])
    assert screenshot_path.exists(), "screenshot file must be written to disk"
    assert screenshot_path.parent == s.workdir / "observations"
    assert screenshot_path.name.startswith("observe-")
    assert screenshot_path.suffix == ".png"
    assert screenshot_path.read_bytes() == _ONE_PX_PNG


def test_observe_target_device_updates_session_state(tmp_path):
    """Session last_screenshot_w/h/path must be updated after a device observe."""
    s = _make_device_session(tmp_path)
    assert s.last_screenshot_w == 0
    assert s.last_screenshot_h == 0

    mock_client = MagicMock()
    mock_client.screenshot_any.return_value = _ONE_PX_PNG

    with patch("simdrive.wda.registry.load", return_value=_registry_entry()), \
         patch("simdrive.wda.client.WdaClient") as mock_cls:
        mock_cls.return_value = mock_client

        from simdrive.server import tool_observe
        result = tool_observe({"session_id": "obstest"})

    assert s.last_screenshot_w == 1
    assert s.last_screenshot_h == 1
    assert s.last_screenshot_path is not None
    assert s.last_screenshot_path == Path(result["screenshot_path"])


def test_observe_target_device_does_not_call_idevicescreenshot(tmp_path):
    """idevicescreenshot must NOT be invoked for target=device sessions."""
    _make_device_session(tmp_path)

    mock_client = MagicMock()
    mock_client.screenshot_any.return_value = _ONE_PX_PNG
    idevice_called = {"called": False}

    def _fake_idevicescreenshot(*args, **kwargs):
        idevice_called["called"] = True
        raise AssertionError("idevicescreenshot called for a target=device session")

    with patch("simdrive.wda.registry.load", return_value=_registry_entry()), \
         patch("simdrive.wda.client.WdaClient") as mock_cls, \
         patch("simdrive.device.screenshot", side_effect=_fake_idevicescreenshot):
        mock_cls.return_value = mock_client

        from simdrive.server import tool_observe
        result = tool_observe({"session_id": "obstest"})

    assert not idevice_called["called"], "idevicescreenshot must not be called for target=device"
