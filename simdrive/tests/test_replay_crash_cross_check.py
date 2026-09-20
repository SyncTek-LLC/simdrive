"""INIT-2026-641 item 4.7 (D3) — replay-time crash cross-check.

Nothing inside replay() checks for a crash produced mid-replay before this
item; only session_start's one-time _verify_launch() does. Recorded steps
can all pass their SSIM checks while the app has actually crashed and
silently relaunched or left a `.ips` behind (dossier LANE C item 8).
"""
from __future__ import annotations

from pathlib import Path

import yaml
from PIL import Image

from simdrive import recorder as rec_mod

GREY = (210, 210, 210)


def _make_sim_session(tmp_path: Path, sid: str = "crash-check-sim"):
    from simdrive import session as ses_mod
    from simdrive.sim import Device

    ses_mod._SESSIONS.clear()
    device = Device(udid="SIM-CRASH-UDID", name="iPhone 17 Pro", os_version="26.1", state="active")
    workdir = tmp_path / "sessions" / sid
    workdir.mkdir(parents=True, exist_ok=True)
    s = ses_mod.Session(
        session_id=sid, device=device, workdir=workdir, target="simulator",
        last_screenshot_w=1206, last_screenshot_h=2622, last_marks=[],
        app_bundle_id="com.acme.reader",
    )
    ses_mod._SESSIONS[sid] = s
    return s


def _write_recording(rec_dir: Path, *, steps: int = 2) -> None:
    """Every pre/post frame is the same solid GREY — a passing recording by
    construction, so a failure here can only come from the crash check."""
    snaps = rec_dir / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    step_list = []
    for i in range(1, steps + 1):
        Image.new("RGB", (1206, 2622), GREY).save(snaps / f"{i:03d}_pre.png")
        Image.new("RGB", (1206, 2622), GREY).save(snaps / f"{i:03d}_post.png")
        step_list.append({
            "id": i,
            "action": "tap",
            "args": {"x": 300, "y": 1900, "screenshot_w": 1206, "screenshot_h": 2622},
            "pre_screenshot": f"snapshots/{i:03d}_pre.png",
            "post_screenshot": f"snapshots/{i:03d}_post.png",
            "captured_at": float(i),
        })
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump({
        "name": rec_dir.name, "created_at": 0.0, "target": "simulator",
        "device": "iPhone 17 Pro", "os_version": "26.1",
        "app_bundle_id": "com.acme.reader", "simdrive_version": "test",
        "steps": step_list,
    }, sort_keys=False))


def _patch_all_grey_live(monkeypatch):
    def _fake(session):
        out_dir = Path(session.workdir) / "replay"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "live.png"
        Image.new("RGB", (1206, 2622), GREY).save(path)
        return {"screenshot_path": path, "marks_count": 0, "marks": [],
                "screenshot_w": 1206, "screenshot_h": 2622}
    monkeypatch.setattr(rec_mod, "_observe_for_replay", _fake, raising=False)


def _patch_tap(monkeypatch):
    from simdrive import act
    monkeypatch.setattr(act, "tap", lambda *a, **kw: None)


class TestReplayCrashCrossCheck:
    def test_replay_fails_when_app_crashes_mid_sequence(self, tmp_path, monkeypatch):
        """A crash timestamped after the replay started must halt the
        replay with halt_reason='crash_detected', a distinct reason from
        'outcome_drift' — a crash and a silent no-op must not report
        through the same code path, or the operator cannot tell them apart.
        """
        s = _make_sim_session(tmp_path)
        rec_dir = tmp_path / "recordings" / "crash_mid_run"
        monkeypatch.setattr(rec_mod, "recordings_root", lambda: tmp_path / "recordings")
        _write_recording(rec_dir, steps=2)
        _patch_all_grey_live(monkeypatch)
        _patch_tap(monkeypatch)

        fake_crash = {
            "path": "/fake/crash.ips", "name": "crash.ips", "timestamp": "now",
            "exception": "EXC_CRASH", "bundle_id": "com.acme.reader", "mtime": 999999.0,
            "backtrace_first_lines": [],
        }

        def fake_list_crashes(since_ts=0.0, bundle_id=None, max_results=10, **kw):
            # Only "sees" the crash for a lookup scoped to after replay start.
            return [fake_crash] if bundle_id == "com.acme.reader" else []

        from simdrive import diagnostics
        monkeypatch.setattr(diagnostics, "list_crashes", fake_list_crashes)

        result = rec_mod.replay("crash_mid_run", s)

        assert result["ok"] is False
        assert result["halt_reason"] == "crash_detected"
        assert result["crashes"] == [fake_crash]
        # The SSIM checks all passed (GREY vs GREY) — crash detection must
        # override that, not be shadowed by it.
        assert result.get("final_state", {}).get("drifted") is not True

    def test_replay_passes_when_no_new_crash(self, tmp_path, monkeypatch):
        s = _make_sim_session(tmp_path)
        rec_dir = tmp_path / "recordings" / "no_crash"
        monkeypatch.setattr(rec_mod, "recordings_root", lambda: tmp_path / "recordings")
        _write_recording(rec_dir, steps=2)
        _patch_all_grey_live(monkeypatch)
        _patch_tap(monkeypatch)

        from simdrive import diagnostics
        monkeypatch.setattr(diagnostics, "list_crashes", lambda **kw: [])

        result = rec_mod.replay("no_crash", s)

        assert result["ok"] is True
        assert result.get("halt_reason") is None
        assert "crashes" not in result

    def test_replay_crash_check_scoped_to_bundle_id_via_session(self, tmp_path, monkeypatch):
        """The crash cross-check must call list_crashes with the session's
        own app_bundle_id, exercising the exact-match fix from the prior
        commit rather than a substring lookup that could also catch an
        unrelated extension's crash.
        """
        s = _make_sim_session(tmp_path)
        rec_dir = tmp_path / "recordings" / "bundle_scoped"
        monkeypatch.setattr(rec_mod, "recordings_root", lambda: tmp_path / "recordings")
        _write_recording(rec_dir, steps=1)
        _patch_all_grey_live(monkeypatch)
        _patch_tap(monkeypatch)

        captured_kwargs = {}

        def fake_list_crashes(**kwargs):
            captured_kwargs.update(kwargs)
            return []

        from simdrive import diagnostics
        monkeypatch.setattr(diagnostics, "list_crashes", fake_list_crashes)

        rec_mod.replay("bundle_scoped", s)

        assert captured_kwargs.get("bundle_id") == "com.acme.reader"
        assert "since_ts" in captured_kwargs
