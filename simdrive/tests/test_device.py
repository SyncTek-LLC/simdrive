"""Tests for simdrive.device — real-device lifecycle helpers.

Focused on the launch_app argv: modern devicectl rejects --start-stopped=false,
so we must not emit it.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock


def test_launch_app_does_not_pass_start_stopped_flag():
    """devicectl launch must NOT include --start-stopped=false (or any
    --start-stopped variant) — modern devicectl rejects it and the default
    behaviour already starts the app normally."""
    from simdrive import device

    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = '{"result": {"process": {"processIdentifier": 1234}}}'
    fake_result.stderr = ""

    with patch("simdrive.device.subprocess.run", return_value=fake_result) as mock_run:
        pid = device.launch_app("FAKE-UDID-1234", "com.example.app")

    assert pid == 1234
    assert mock_run.call_count == 1
    argv = mock_run.call_args[0][0]
    # The whole point: no --start-stopped anywhere in argv.
    for arg in argv:
        assert not str(arg).startswith("--start-stopped"), (
            f"argv must not contain --start-stopped (modern devicectl rejects it). "
            f"Got argv: {argv}"
        )
    # Sanity: the bundle id is still in argv.
    assert "com.example.app" in argv
    # Sanity: the udid is still in argv.
    assert "FAKE-UDID-1234" in argv
