"""INIT-2026-641 item 4.8 (D3) — headless `simdrive replay <name> --json`.

Before this item: no `"replay"` key existed in `server._SUBCOMMANDS` at all,
and `pyproject.toml`'s `[project.scripts]` exposed only `simdrive` and
`simdrive-mcp`. There was no way to run a replay validation from a shell
with no agent session — the same agent claiming "fix validated" was the
only thing that could generate that evidence.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from simdrive import server


def _write_minimal_recording(rec_dir: Path) -> None:
    rec_dir.mkdir(parents=True, exist_ok=True)
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump({
        "name": rec_dir.name,
        "created_at": 0.0,
        "target": "simulator",
        "device": "iPhone 17 Pro",
        "os_version": "26.1",
        "app_bundle_id": "org.example.app",
        "simdrive_version": "test",
        "steps": [],
    }, sort_keys=False))


class TestReplaySubcommandRegistered:
    def test_replay_subcommand_registered(self):
        assert "replay" in server._SUBCOMMANDS
        assert server._SUBCOMMANDS["replay"] is server._cmd_replay

    def test_replay_subcommand_runs_headless_and_prints_json(self, tmp_path, capsys):
        """Hermetic: session.start and recorder.replay are mocked (no real
        simulator), proving the CLI plumbing — argument parsing, recording
        metadata defaults, JSON output, exit code from result['ok'] —
        without needing a booted device.
        """
        from simdrive import recorder as recorder_mod, session as session_mod

        rec_dir = tmp_path / "recordings" / "smoke_test"
        _write_minimal_recording(rec_dir)

        fake_session = session_mod.Session(
            session_id="cli-replay-1",
            device=session_mod.Device(udid="UDID-CLI", name="iPhone 17 Pro",
                                      os_version="26.1", state="Booted"),
            workdir=tmp_path / "wd",
            target="simulator",
        )
        fake_result = {"ok": True, "halt_reason": None, "steps": [], "steps_planned": 0}

        with patch.object(recorder_mod, "recordings_root", return_value=tmp_path / "recordings"), \
             patch.object(session_mod, "start", return_value=fake_session) as mock_start, \
             patch.object(session_mod, "end", return_value=None), \
             patch.object(recorder_mod, "replay", return_value=fake_result) as mock_replay, \
             pytest.raises(SystemExit) as exc_info:
            server._cmd_replay(["smoke_test", "--json"])

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        printed = json.loads(captured.out)
        assert printed == fake_result

        # Device metadata defaults came from the recording itself.
        _, kwargs = mock_start.call_args
        assert kwargs["device_name"] == "iPhone 17 Pro"
        assert kwargs["app_bundle_id"] == "org.example.app"
        assert kwargs["target"] == "simulator"
        mock_replay.assert_called_once()

    def test_replay_subcommand_exits_nonzero_on_failed_replay(self, tmp_path, capsys):
        from simdrive import recorder as recorder_mod, session as session_mod

        rec_dir = tmp_path / "recordings" / "failing_test"
        _write_minimal_recording(rec_dir)

        fake_session = session_mod.Session(
            session_id="cli-replay-2",
            device=session_mod.Device(udid="UDID-CLI2", name="iPhone 17 Pro",
                                      os_version="26.1", state="Booted"),
            workdir=tmp_path / "wd2",
            target="simulator",
        )
        fake_result = {"ok": False, "halt_reason": "crash_detected", "steps": [], "steps_planned": 0}

        with patch.object(recorder_mod, "recordings_root", return_value=tmp_path / "recordings"), \
             patch.object(session_mod, "start", return_value=fake_session), \
             patch.object(session_mod, "end", return_value=None), \
             patch.object(recorder_mod, "replay", return_value=fake_result), \
             pytest.raises(SystemExit) as exc_info:
            server._cmd_replay(["failing_test", "--json"])

        assert exc_info.value.code == 1

    def test_replay_subcommand_missing_recording_exits_2(self, tmp_path):
        from simdrive import recorder as recorder_mod

        with patch.object(recorder_mod, "recordings_root", return_value=tmp_path / "recordings"), \
             pytest.raises(SystemExit) as exc_info:
            server._cmd_replay(["does_not_exist", "--json"])

        assert exc_info.value.code == 2
