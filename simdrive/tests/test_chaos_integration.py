"""Chaos integration tests for the SimDrive resilience stack (INIT-2026-549).

These tests prove the resilience hardening from Wave 1 + Wave 2 holds up
under failures that happen *during* a replay, not just at isolated call
sites (which is what ``tests/test_wda_resilience.py`` already covers).

Scenarios
---------
1. **WDA dies mid-replay then recovers** — between step 2 and step 3, WDA
   returns a transient ``httpx.ReadError`` once, then succeeds. The
   exponential-backoff loop in ``WdaClient._request_with_backoff`` retries,
   step 3 succeeds, and the replay completes all 5 steps.

2. **WDA dies and never comes back** — same setup but WDA fails forever
   from step 2 onwards. Backoff fires ``max_attempts`` times, then
   ``wda_recovery_exhausted`` is raised. The replay result surfaces this as
   an ``execute_error`` halt with the recovery history visible in the
   step's error string.

3. **Code 41 mid-replay** — during step 2 WDA returns
   ``XCTDaemonErrorDomain Code=41`` (entitlement revoked). The
   ``_request`` recovery path rebuilds + reopens (we stub the heavy work)
   and the retry succeeds, replay continues. This is the "system" view of
   the per-call-site logic exercised by
   ``test_wda_resilience.TestCode41Regex``.

4. **Recorder drift hysteresis under noise** — a recording with three
   steps where step 2 has ONE noisy sub-threshold SSIM sample followed by
   a clean recapture (no halt — replay proceeds), and step 3 has TWO
   consecutive sub-threshold samples (halt with both scores quoted in the
   error message).

5. **Quota check trips mid-replay** — a session whose
   ``LocalQuotaSnapshot`` flips from under-limit to over-limit between
   two ``server.call_tool`` dispatches surfaces ``QuotaExceededError`` on
   the second call. This validates the wave-2 dispatcher gate as the
   user would experience it in practice (a long-running journey where
   the snapshot refresh push them over).

All HTTP I/O is mocked via ``httpx.MockTransport``. ``time.sleep`` is
patched so the backoff doesn't burn wall-clock time. No real WDA, no
real network, no real simulator.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import patch

import httpx
import pytest
import yaml
from PIL import Image


# ── Shared chaos helpers ─────────────────────────────────────────────────


def _png(path: Path, *, size: tuple[int, int] = (1170, 2532),
         fill: tuple[int, int, int] = (210, 210, 210)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, fill).save(path)
    return path


def _png_bytes(*, size: tuple[int, int] = (1170, 2532),
               fill: tuple[int, int, int] = (210, 210, 210)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, fill).save(buf, format="PNG")
    return buf.getvalue()


def _make_response(status: int, body: Any) -> httpx.Response:
    """Lift a dict / list / str into an httpx.Response."""
    if isinstance(body, (dict, list)):
        content = json.dumps(body).encode()
        headers = {"content-type": "application/json"}
    else:
        content = str(body).encode()
        headers = {"content-type": "text/plain"}
    return httpx.Response(status, content=content, headers=headers)


def _patched_sleep(sleeps: list[float]) -> Callable[[float], None]:
    """Return a fake ``time.sleep`` that records its argument instead of
    actually sleeping. Keeps the chaos tests deterministic in CI."""

    def _fake(seconds: float) -> None:
        sleeps.append(float(seconds))

    return _fake


def _make_device_session(tmp_path: Path, *, app_bundle_id: str = "com.chaos.app",
                         udid: str = "CHAOS-UDID-0001"):
    """Build a real (in-memory) device Session with a mocked WdaClient.

    Mirrors the helper in ``test_a13_device_replay.py`` but doesn't use
    MagicMock for the WDA client — chaos tests inject failures via
    ``httpx.MockTransport`` against a real ``WdaClient`` so the actual
    backoff / Code-41 / recovery paths in ``simdrive.wda.client`` run.
    """
    from simdrive import session as ses_mod
    from simdrive.sim import Device
    from simdrive.wda.client import WdaClient

    ses_mod._SESSIONS.clear()
    device = Device(
        udid=udid,
        name="iPhone 16 Pro",
        os_version="18.4.1",
        state="connected",
    )
    s = ses_mod.Session(
        session_id="chaos-replay",
        device=device,
        workdir=tmp_path / "wd",
        target="device",
        app_bundle_id=app_bundle_id,
    )
    s.workdir.mkdir(parents=True, exist_ok=True)

    wda = WdaClient(host="127.0.0.1", port=8100, max_transport_attempts=3)
    wda._udid = udid
    wda._session_id = "wda-sid-chaos"
    wda._last_bundle_id = app_bundle_id
    s.wda_client = wda
    s.pixel_per_point_scale = 3.0
    return s


def _write_fixture_recording(rec_dir: Path, *, steps_count: int,
                             recorded_marks_count: int = 0,
                             app_bundle_id: str = "com.chaos.app",
                             udid: str = "CHAOS-UDID-0001") -> Path:
    """Lay out a minimal N-step recording on disk.

    Every step is a tap; the pre/post screenshots are 1170x2532 grey
    PNGs (matches the "live" screenshot we'll feed back through
    ``_observe_for_replay``). State-contract verification is bypassed by
    ``foreground=False`` + empty text subsets so the replay engine
    proceeds straight to step 1.
    """
    snaps = rec_dir / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    steps = []
    for i in range(1, steps_count + 1):
        pre = snaps / f"{i:03d}_pre.png"
        post = snaps / f"{i:03d}_post.png"
        _png(pre)
        _png(post)
        step = {
            "id": i,
            "action": "tap",
            "args": {
                "x": 100 + 10 * i,
                "y": 200 + 10 * i,
                "screenshot_w": 1170,
                "screenshot_h": 2532,
            },
            "pre_screenshot": f"snapshots/{i:03d}_pre.png",
            "post_screenshot": f"snapshots/{i:03d}_post.png",
            "captured_at": float(i),
        }
        if recorded_marks_count:
            step["marks_count"] = recorded_marks_count
        steps.append(step)

    payload = {
        "name": rec_dir.name,
        "created_at": 0.0,
        "target": "device",
        "device": "iPhone 16 Pro",
        "os_version": "18.4.1",
        "app_bundle_id": app_bundle_id,
        "simdrive_version": "test",
        "requires": {
            "target": "device",
            "app": {
                "bundle_id": app_bundle_id,
                "version": None,
                "version_match": "minor",
            },
            "sim": {"device": None, "ios_version": None},
            "device": {
                "udid": udid,
                "device_name": "iPhone 16 Pro",
                "os_version": "18.4.1",
                "os_major": 18,
            },
            "initial_state": {
                "foreground": False,
                "text_subset_required": [],
                "text_subset_forbidden": [],
                "primary_button_label": None,
            },
        },
        "steps": steps,
    }
    (rec_dir / "recording.yaml").write_text(yaml.safe_dump(payload, sort_keys=False))
    return rec_dir / "recording.yaml"


def _install_passthrough_observe(monkeypatch, *, live_marks_count: int = 0):
    """Patch the replay-time observe helpers to return a clean image.

    Lets chaos tests focus on the action-dispatch path (``wda.tap()`` etc.)
    without also having to mock the screenshot-fetch side of replay. Both
    ``_observe_for_replay`` (used inside the step loop) and
    ``_observe_live_marks`` (used by ``_verify_state_contract`` before
    step 1) are stubbed so the only WDA HTTP traffic that reaches the
    MockTransport handler is the action calls themselves.
    """
    from simdrive import recorder as rec_mod

    def _fake(session, *, _counter=[0]):
        _counter[0] += 1
        out_dir = session.workdir / "replay"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"live_{_counter[0]:03d}.png"
        _png(path)  # same grey fill → SSIM ~1.0 with fixture
        return {
            "screenshot_path": path,
            "marks_count": live_marks_count,
            "marks": [],
            "screenshot_w": 1170,
            "screenshot_h": 2532,
        }

    monkeypatch.setattr(rec_mod, "_observe_for_replay", _fake, raising=False)
    monkeypatch.setattr(
        rec_mod, "_observe_live_marks",
        lambda session, workdir: [],
        raising=False,
    )


def _install_alternating_observe(monkeypatch, frames: list[tuple[int, int, int]]):
    """Patch ``_observe_for_replay`` to walk through a list of fill colours.

    Drives recorder-drift hysteresis chaos: the i-th call returns a PNG
    with ``frames[i]`` fill colour so the test controls the SSIM trajectory
    sample-by-sample.
    """
    from simdrive import recorder as rec_mod

    state = {"i": 0}

    def _fake(session):
        idx = min(state["i"], len(frames) - 1)
        fill = frames[idx]
        state["i"] += 1
        out_dir = session.workdir / "replay"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"frame_{state['i']:03d}.png"
        _png(path, fill=fill)
        return {
            "screenshot_path": path,
            "marks_count": 0,
            "marks": [],
            "screenshot_w": 1170,
            "screenshot_h": 2532,
        }

    monkeypatch.setattr(rec_mod, "_observe_for_replay", _fake, raising=False)
    monkeypatch.setattr(
        rec_mod, "_observe_live_marks",
        lambda session, workdir: [],
        raising=False,
    )
    return state


# ── Scenario 1: WDA dies mid-replay then recovers ────────────────────────


class TestWdaDiesMidReplayThenRecovers:
    """A transient ``httpx.ReadError`` on step 3's tap triggers the exponential
    backoff loop, the retry succeeds, and the replay completes all 5 steps.
    """

    def test_transient_failure_on_step_3_recovers(self, tmp_path, monkeypatch):
        from simdrive import recorder

        monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
        rec_dir = recorder.recordings_root() / "chaos-transient"
        _write_fixture_recording(rec_dir, steps_count=5)
        _install_passthrough_observe(monkeypatch)

        s = _make_device_session(tmp_path)
        tap_count = [0]
        # The third tap fails once with httpx.ReadError, then succeeds.
        # All other taps succeed first try.
        target_failing_tap = 3

        def _handler(request: httpx.Request) -> httpx.Response:
            tap_count[0] += 1
            # Fail on the FIRST send of the 3rd tap only; the retry (4th
            # call) succeeds with a 200.
            if tap_count[0] == target_failing_tap:
                raise httpx.ReadError("WDA flake mid-swipe")
            return _make_response(200, {"value": {}})

        s.wda_client._replace_transport(httpx.MockTransport(_handler))

        sleeps: list[float] = []
        with patch("simdrive.wda.client.time.sleep", _patched_sleep(sleeps)):
            result = recorder.replay(
                "chaos-transient",
                s,
                on_drift="force",
                halt_on_state_mismatch=False,
            )

        assert result["ok"] is True, f"Replay should recover: {result}"
        executed = [st for st in result["steps"] if st["executed"]]
        assert len(executed) == 5, "All 5 steps must execute despite the flake"
        # Exactly one backoff sleep — the initial 0.2s — confirms we recovered
        # on the first retry, not after a longer dance.
        assert sleeps == [pytest.approx(0.2)], (
            f"Expected exactly one backoff at 0.2s, got {sleeps}"
        )
        # 5 successful taps + 1 retried tap = 6 transport-level requests.
        assert tap_count[0] == 6, f"Expected 6 tap requests, got {tap_count[0]}"


# ── Scenario 2: WDA dies and never comes back ────────────────────────────


class TestWdaDiesPermanentlyMidReplay:
    """Once step 2 starts failing, every retry also fails. The backoff loop
    exhausts ``max_attempts``, ``wda_recovery_exhausted`` is raised, and the
    replay engine surfaces it as ``execute_error`` with the recovery
    history visible in the per-step error string.
    """

    def test_permanent_failure_exhausts_and_halts_replay(self, tmp_path, monkeypatch):
        from simdrive import recorder

        monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
        rec_dir = recorder.recordings_root() / "chaos-dead-wda"
        _write_fixture_recording(rec_dir, steps_count=5)
        _install_passthrough_observe(monkeypatch)

        s = _make_device_session(tmp_path)
        tap_count = [0]

        def _handler(request: httpx.Request) -> httpx.Response:
            tap_count[0] += 1
            # Step 1 succeeds (1 call); thereafter WDA is dead forever.
            if tap_count[0] == 1:
                return _make_response(200, {"value": {}})
            raise httpx.ReadError("WDA gone")

        s.wda_client._replace_transport(httpx.MockTransport(_handler))

        sleeps: list[float] = []
        with patch("simdrive.wda.client.time.sleep", _patched_sleep(sleeps)):
            result = recorder.replay(
                "chaos-dead-wda",
                s,
                on_drift="force",
                halt_on_state_mismatch=False,
            )

        assert result["ok"] is False
        assert result["halt_reason"] == "execute_error"
        assert result["halted_at"] == 2, (
            f"Expected halt at step 2 (first failing step), got {result['halted_at']}"
        )
        # Step 1 should be marked executed; step 2 should carry the
        # exhaustion error.
        steps = result["steps"]
        assert steps[0]["executed"] is True
        assert steps[1]["executed"] is False
        err = steps[1]["error"] or ""
        assert "wda_recovery_exhausted" in err or "gave up" in err, (
            f"Expected wda_recovery_exhausted in step error: {err!r}"
        )
        # Backoff fired 2 sleeps (between 3 attempts) for step 2's failures.
        assert len(sleeps) == 2, (
            f"Expected 2 backoff sleeps for the 3-attempt exhaustion, got {sleeps}"
        )


# ── Scenario 3: Code 41 mid-replay (entitlement revoked) ─────────────────


class TestCode41MidReplay:
    """During step 2, WDA returns ``XCTDaemonErrorDomain Code=41``. The
    in-line recovery logic in ``_send_once`` runs the rebuild + reopen path
    (stubbed) and retries the same request. The retry returns 200 so the
    replay continues to completion.

    This is the per-call-site Code-41 recovery from
    ``test_wda_resilience.TestCode41Regex`` exercised through the actual
    replay engine, with multiple steps in flight. Surfaces any subtle
    issue where mid-replay rebuild leaves session state inconsistent.
    """

    CODE41_BODY = (
        '{"value": {"error": "XCTDaemonErrorDomain Code=41 '
        'Not authorized for performing UI testing actions."}}'
    )

    def test_code41_on_step_2_recovers_and_continues(self, tmp_path, monkeypatch):
        from simdrive import recorder

        monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
        rec_dir = recorder.recordings_root() / "chaos-code41"
        _write_fixture_recording(rec_dir, steps_count=4)
        _install_passthrough_observe(monkeypatch)

        s = _make_device_session(tmp_path)
        tap_count = [0]

        def _handler(request: httpx.Request) -> httpx.Response:
            tap_count[0] += 1
            # 1st tap: step 1 succeeds.
            # 2nd tap: step 2 first send → Code-41.
            # 3rd tap: step 2 retry after rebuild → 200.
            # 4th, 5th taps: steps 3 + 4 succeed.
            if tap_count[0] == 2:
                return _make_response(403, self.CODE41_BODY)
            return _make_response(200, {"value": {}})

        s.wda_client._replace_transport(httpx.MockTransport(_handler))

        rebuild_calls = [0]

        def _stub_rebuild(self):
            rebuild_calls[0] += 1
            # Pretend rebuild + reopen succeeded; preserve session_id so the
            # retry hits the same MockTransport handler.
            return None

        with patch("simdrive.wda.client.WdaClient._rebuild_and_reopen",
                   _stub_rebuild):
            result = recorder.replay(
                "chaos-code41",
                s,
                on_drift="force",
                halt_on_state_mismatch=False,
            )

        assert result["ok"] is True, f"Code-41 recovery should let replay finish: {result}"
        executed = [st for st in result["steps"] if st["executed"]]
        assert len(executed) == 4
        assert rebuild_calls[0] == 1, (
            "_rebuild_and_reopen must fire exactly once for the single Code-41 event"
        )
        # 4 successful taps + 1 Code-41 retry on step 2 = 5 transport calls.
        assert tap_count[0] == 5, f"Expected 5 tap requests, got {tap_count[0]}"

    def test_code41_with_no_auto_rebuild_env_halts_replay(self, tmp_path, monkeypatch):
        """SIMDRIVE_NO_AUTO_REBUILD=1 turns the Code-41 path into a hard
        ``wda_ui_automation_disabled`` raise. Replay surfaces it as
        ``execute_error``."""
        from simdrive import recorder

        monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
        monkeypatch.setenv("SIMDRIVE_NO_AUTO_REBUILD", "1")

        rec_dir = recorder.recordings_root() / "chaos-code41-opt-out"
        _write_fixture_recording(rec_dir, steps_count=3)
        _install_passthrough_observe(monkeypatch)

        s = _make_device_session(tmp_path)
        tap_count = [0]

        def _handler(request: httpx.Request) -> httpx.Response:
            tap_count[0] += 1
            if tap_count[0] == 2:
                return _make_response(403, self.CODE41_BODY)
            return _make_response(200, {"value": {}})

        s.wda_client._replace_transport(httpx.MockTransport(_handler))

        result = recorder.replay(
            "chaos-code41-opt-out",
            s,
            on_drift="force",
            halt_on_state_mismatch=False,
        )

        assert result["ok"] is False
        assert result["halt_reason"] == "execute_error"
        assert result["halted_at"] == 2
        err = result["steps"][1]["error"] or ""
        assert "wda_ui_automation_disabled" in err or "UI testing" in err, (
            f"Expected the Code-41 hard-fail error in step 2: {err!r}"
        )


# ── Scenario 4: Recorder drift hysteresis under noise ────────────────────


class TestDriftHysteresisUnderNoise:
    """A 3-step replay where step 2 has a noisy-then-clean recovery and
    step 3 has two consecutive sub-threshold frames. The first survives the
    hysteresis recheck; the second halts and the error string quotes both
    bad scores. This is the recorder-side resilience proven through the
    replay engine end-to-end.
    """

    def test_three_step_replay_with_step3_drift_after_step2_recovery(
        self, tmp_path, monkeypatch,
    ):
        from simdrive import recorder

        monkeypatch.setenv("SIMDRIVE_HOME", str(tmp_path))
        rec_dir = recorder.recordings_root() / "chaos-hysteresis"
        _write_fixture_recording(rec_dir, steps_count=3)

        # Frame sequence (one entry per _observe_for_replay call):
        #   Step 1 (clean)       → frame 1: matches fixture grey (210,210,210)
        #   Step 2 (noisy then clean — recheck recovers)
        #       → frame 2: black sub-threshold
        #       → frame 3: grey clean (recheck recovers)
        #   Step 3 (two consecutive sub-threshold — halt)
        #       → frame 4: black sub-threshold
        #       → frame 5: dark grey sub-threshold (recheck STILL bad)
        FRAMES = [
            (210, 210, 210),
            (10, 10, 10),
            (210, 210, 210),
            (10, 10, 10),
            (15, 15, 15),
        ]
        state = _install_alternating_observe(monkeypatch, FRAMES)

        s = _make_device_session(tmp_path)

        def _handler(request: httpx.Request) -> httpx.Response:
            return _make_response(200, {"value": {}})

        s.wda_client._replace_transport(httpx.MockTransport(_handler))

        result = recorder.replay(
            "chaos-hysteresis",
            s,
            on_drift="halt",
            drift_threshold=0.85,
            halt_on_state_mismatch=False,
        )

        assert result["ok"] is False
        assert result["halt_reason"] == "drift"
        assert result["halted_at"] == 3, (
            f"Expected drift halt at step 3 (after step 2 hysteresis recovery), "
            f"got halted_at={result['halted_at']}"
        )

        steps = result["steps"]
        # Step 1 + Step 2: both executed; step 2 *did* recover from a noisy
        # frame so its similarity score reflects the lower of the two samples.
        assert steps[0]["executed"] is True
        assert steps[1]["executed"] is True, (
            "Step 2 must have executed (single noisy frame → recheck recovered, no halt)"
        )
        # Step 3 must have halted before executing.
        assert steps[2]["executed"] is False

        err3 = steps[2]["error"] or ""
        assert "2 consecutive sub-threshold samples" in err3, (
            f"step 3 error must explain the hysteresis halt: {err3!r}"
        )
        assert " then " in err3, (
            f"step 3 error must quote both consecutive SSIM scores (e.g. 'X then Y'): {err3!r}"
        )

        # Frame counter sanity: 1 (step1) + 2 (step2 with recheck) + 2 (step3 with recheck) = 5
        assert state["i"] == 5, (
            f"Expected exactly 5 observe calls (1+2+2), got {state['i']}"
        )


# ── Scenario 5: Quota check trips mid-replay ─────────────────────────────


class TestQuotaTripsMidReplay:
    """A session whose ``LocalQuotaSnapshot`` starts under-limit and is
    mutated to over-limit between two ``server.call_tool`` invocations.
    The next dispatch surfaces ``QuotaExceededError`` from the wave-2
    defense-in-depth gate inside ``server.call_tool``.

    We exercise this at the dispatcher boundary (``server.call_tool``)
    rather than reaching into ``check_local_quota`` directly because
    that's the seam where the user *experiences* the failure during a
    long-running journey: a tool call mid-journey suddenly raises
    QuotaExceededError because the refresh bump flipped the snapshot.
    """

    def test_first_call_passes_then_snapshot_flips_then_second_call_raises(self):
        from simdrive import server, session
        from simdrive.cloud.errors import QuotaExceededError
        from simdrive.cloud.middleware.quotas import LocalQuotaSnapshot

        sid = "chaos-quota-flip"
        snapshot = LocalQuotaSnapshot(tier="pro", runs_used=249, runs_limit=250)
        fake_session = SimpleNamespace(session_id=sid, quota_snapshot=snapshot)
        session._SESSIONS[sid] = fake_session  # type: ignore[assignment]
        try:
            # First call: snapshot under limit (249/250). The session_status
            # handler doesn't know how to operate on a SimpleNamespace so it
            # will raise SOMETHING, but it MUST NOT be QuotaExceededError.
            with pytest.raises(Exception) as exc_info_1:
                server.call_tool("session_status", {"session_id": sid})
            assert not isinstance(exc_info_1.value, QuotaExceededError), (
                "First call must pass the quota gate (under limit)"
            )

            # Simulate a snapshot refresh that just tipped us over the cap.
            fake_session.quota_snapshot = LocalQuotaSnapshot(
                tier="pro", runs_used=250, runs_limit=250,
            )

            # Second call: must hit the QuotaExceededError raised by the
            # dispatcher gate before the handler runs.
            with pytest.raises(QuotaExceededError) as exc_info_2:
                server.call_tool("session_status", {"session_id": sid})

            err = exc_info_2.value
            assert err.code == "cloud_quota_exceeded"
            assert err.details["tool_name"] == "session_status"
            assert err.details["runs_used"] == 250
            assert err.details["runs_limit"] == 250
            assert err.details["tier"] == "pro"
            assert "session_status" in err.message
        finally:
            session._SESSIONS.pop(sid, None)

    def test_quota_error_surfaces_with_recovery_hint(self):
        """The QuotaExceededError surfaced mid-replay must include a recovery
        hint pointing the user at upgrade or wait-for-reset. Operators
        triaging a stuck journey shouldn't have to guess what to do next."""
        from simdrive import server, session
        from simdrive.cloud.errors import QuotaExceededError
        from simdrive.cloud.middleware.quotas import LocalQuotaSnapshot

        sid = "chaos-quota-hint"
        over = LocalQuotaSnapshot(tier="solo", runs_used=50, runs_limit=50)
        fake_session = SimpleNamespace(session_id=sid, quota_snapshot=over)
        session._SESSIONS[sid] = fake_session  # type: ignore[assignment]
        try:
            with pytest.raises(QuotaExceededError) as exc:
                server.call_tool("session_status", {"session_id": sid})
            msg = exc.value.message
            assert "Recovery:" in msg, f"missing recovery hint: {msg!r}"
            assert "upgrade" in msg.lower() or "reset" in msg.lower(), (
                f"recovery hint should point at upgrade or quota reset: {msg!r}"
            )
        finally:
            session._SESSIONS.pop(sid, None)
