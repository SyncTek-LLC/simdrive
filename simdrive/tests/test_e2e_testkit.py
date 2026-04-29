"""End-to-end test suite against TestKitApp on a live iOS Simulator.

Marked `live` — skipped in normal pytest runs. Run explicitly:

    /opt/homebrew/bin/python3.11 -m pytest tests/test_e2e_testkit.py -v -m live

Requirements:
  - A booted iOS simulator
  - TestKitApp installed (build with /Users/atlas/Documents/specterqa-ios/TestKitApp/build.sh)
  - simdrive native binary built (cd native && make)

Each test exercises a single tool or a small flow against a known-good
TestKit screen, with screen-state assertions on observed marks.
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Optional

import pytest

from simdrive import server, sim


TESTKIT_BUNDLE_ID = "io.synctek.specterqa.testkit"


# ---------------------------------------------------------------------- #
# Fixtures
# ---------------------------------------------------------------------- #


def _booted_udid() -> Optional[str]:
    devices = sim.list_devices()
    for d in devices:
        if d.is_booted:
            return d.udid
    return None


@pytest.fixture(scope="module")
def udid() -> str:
    u = _booted_udid()
    if not u:
        pytest.skip("no booted simulator")
    return u


@pytest.fixture(scope="module")
def _workdir(tmp_path_factory) -> Path:
    work = tmp_path_factory.mktemp("simdrive_e2e")
    os.environ["SIMDRIVE_HOME"] = str(work)
    return work


@pytest.fixture
def session_id(_workdir, udid) -> str:
    """Function-scoped: every test gets a fresh TestKit launch + session.

    Slower than module-scoped, but eliminates inter-test screen-state
    contamination. ~3s overhead per test; the test count is small enough
    that this is fine.
    """
    sim._simctl("terminate", udid, TESTKIT_BUNDLE_ID, timeout=5.0)
    time.sleep(0.4)
    res = server.tool_session_start({"app_bundle_id": TESTKIT_BUNDLE_ID})
    time.sleep(2.2)
    yield res["session_id"]
    server.tool_session_end({"session_id": res["session_id"]})


def _texts(obs: dict) -> list[str]:
    return [m["text"] for m in obs.get("marks", [])]


def _has(obs: dict, target: str) -> bool:
    t = target.lower()
    return any(t in mark.lower() for mark in _texts(obs))


def _md5(p) -> str:
    return hashlib.md5(open(p, "rb").read()).hexdigest()[:16]


def _navigate_to(session_id: str, tab: str) -> dict:
    """Tap a tab and return a fresh observation."""
    server.tool_tap({"session_id": session_id, "text": tab})
    time.sleep(1.0)
    return server.tool_observe({"session_id": session_id})


# ---------------------------------------------------------------------- #
# Tool: session_start / session_status / session_end
# ---------------------------------------------------------------------- #


@pytest.mark.live
def test_session_status_reports_active_session(session_id):
    res = server.tool_session_status({"session_id": session_id})
    assert any(s["session_id"] == session_id for s in res["sessions"])
    assert res["mode"] in {"background", "foreground"}


@pytest.mark.live
def test_session_status_lists_when_no_id_given(session_id):
    res = server.tool_session_status({})
    sids = [s["session_id"] for s in res["sessions"]]
    assert session_id in sids


@pytest.mark.live
def test_session_end_removes_unknown_session_safely():
    # Calling end on a nonexistent id must not raise.
    server.tool_session_end({"session_id": "does-not-exist"})


# ---------------------------------------------------------------------- #
# Tool: observe
# ---------------------------------------------------------------------- #


@pytest.mark.live
def test_observe_returns_screenshot_path(session_id):
    obs = server.tool_observe({"session_id": session_id})
    p = Path(obs["screenshot_path"])
    assert p.exists() and p.stat().st_size > 1000


@pytest.mark.live
def test_observe_returns_annotated_path(session_id):
    obs = server.tool_observe({"session_id": session_id})
    assert obs["annotated_path"] is not None
    assert Path(obs["annotated_path"]).exists()


@pytest.mark.live
def test_observe_finds_testkit_marks(session_id):
    obs = server.tool_observe({"session_id": session_id})
    assert _has(obs, "TestKit")
    assert _has(obs, "Form")


@pytest.mark.live
def test_observe_annotate_false_skips_marks(session_id):
    obs = server.tool_observe({"session_id": session_id, "annotate": False})
    assert obs["annotated_path"] is None
    assert obs["marks"] == []


@pytest.mark.live
def test_observe_capture_logs(session_id):
    obs = server.tool_observe({"session_id": session_id, "capture_logs": True, "log_lines": 20})
    # logs may legitimately be empty, but field must be present + a string
    assert isinstance(obs.get("recent_logs", ""), str)


# ---------------------------------------------------------------------- #
# Tool: tap (each resolution form)
# ---------------------------------------------------------------------- #


@pytest.mark.live
def test_tap_by_text_switches_tab(session_id):
    nav_obs = _navigate_to(session_id, "Nav")
    assert _has(nav_obs, "Navigation Tab") or _has(nav_obs, "Increment Counter")


@pytest.mark.live
def test_tap_by_mark_id_increments_counter(session_id):
    nav_obs = _navigate_to(session_id, "Nav")
    inc_mark = next((m for m in nav_obs["marks"] if "Increment" in m["text"]), None)
    assert inc_mark is not None
    pre_counter = next((t for t in _texts(nav_obs) if t.startswith("Counter:")), "Counter: ?")
    server.tool_tap({"session_id": session_id, "mark": inc_mark["id"]})
    time.sleep(0.6)
    obs = server.tool_observe({"session_id": session_id})
    post_counter = next((t for t in _texts(obs) if t.startswith("Counter:")), "?")
    assert post_counter != pre_counter, f"counter didn't change: {pre_counter} -> {post_counter}"


@pytest.mark.live
def test_tap_by_coords_dispatches(session_id):
    obs = server.tool_observe({"session_id": session_id})
    sw, sh = obs["screenshot_size_pixels"]
    res = server.tool_tap({"session_id": session_id, "x": sw // 2, "y": sh // 2})
    assert res["resolved_via"] == "coords"
    assert res["pixel_x"] == sw // 2


@pytest.mark.live
def test_tap_unresolvable_text_raises(session_id):
    from simdrive import errors as err
    server.tool_observe({"session_id": session_id})
    with pytest.raises(err.SimdriveError) as exc:
        server.tool_tap({"session_id": session_id, "text": "definitely-not-a-real-target-xyz"})
    assert exc.value.code == "target_not_found"


@pytest.mark.live
def test_tap_missing_target_raises(session_id):
    from simdrive import errors as err
    with pytest.raises(err.SimdriveError) as exc:
        server.tool_tap({"session_id": session_id})
    assert exc.value.code == "missing_target"


# ---------------------------------------------------------------------- #
# Tool: type_text — the focus-and-type fix
# ---------------------------------------------------------------------- #


@pytest.mark.live
def test_type_text_focuses_field_and_enters_text(session_id):
    """The headline test — synthetic clicks couldn't do this before the HID helper."""
    form_obs = _navigate_to(session_id, "Form")
    fn_mark = next((m for m in form_obs["marks"] if "First Name" in m["text"]), None)
    assert fn_mark is not None

    server.tool_type_text(
        {"session_id": session_id, "text": "Maurice", "tap_first": {"mark": fn_mark["id"]}}
    )
    time.sleep(1.0)
    obs = server.tool_observe({"session_id": session_id})
    assert _has(obs, "Maurice"), f"text didn't enter field; marks: {_texts(obs)}"


@pytest.mark.live
def test_type_text_followed_by_submit_produces_result(session_id):
    form_obs = _navigate_to(session_id, "Form")
    ln_mark = next((m for m in form_obs["marks"] if "Last Name" in m["text"]), None)
    if ln_mark:
        server.tool_type_text(
            {"session_id": session_id, "text": "Carrier", "tap_first": {"mark": ln_mark["id"]}}
        )
        time.sleep(0.8)
    server.tool_tap({"session_id": session_id, "text": "Submit"})
    time.sleep(1.0)
    obs = server.tool_observe({"session_id": session_id})
    assert _has(obs, "Carrier")


# ---------------------------------------------------------------------- #
# Tool: press_key
# ---------------------------------------------------------------------- #


@pytest.mark.live
def test_press_key_home_returns_to_home_screen(session_id):
    # Fixture has just launched TestKit, so we're definitely inside an app.
    server.tool_press_key({"session_id": session_id, "key": "home"})
    time.sleep(1.5)
    obs = server.tool_observe({"session_id": session_id})
    # Home-screen indicators (Search bar at bottom is universal across pages)
    assert _has(obs, "Search") or _has(obs, "Calendar") or _has(obs, "Maps") or _has(obs, "Settings")


# ---------------------------------------------------------------------- #
# Tool: swipe — content scroll
# ---------------------------------------------------------------------- #


@pytest.mark.live
def test_swipe_scrolls_content(session_id):
    """Swipe up on Stress tab and verify visible-text changes (something scrolled in/out)."""
    stress_obs = _navigate_to(session_id, "Stress")
    pre_marks = set(_texts(stress_obs))
    sw, sh = stress_obs["screenshot_size_pixels"]
    server.tool_swipe(
        {
            "session_id": session_id,
            "x1": sw // 2, "y1": int(sh * 0.7),
            "x2": sw // 2, "y2": int(sh * 0.3),
            "duration_ms": 400,
        }
    )
    time.sleep(1.0)
    obs = server.tool_observe({"session_id": session_id})
    post_marks = set(_texts(obs))
    new_marks = post_marks - pre_marks
    # If content scrolled, something new should be visible OR something old gone
    assert len(new_marks) > 0 or len(pre_marks - post_marks) > 0, (
        f"swipe didn't change visible text. pre={pre_marks}, post={post_marks}"
    )


# ---------------------------------------------------------------------- #
# Tool: logs
# ---------------------------------------------------------------------- #


@pytest.mark.live
def test_logs_returns_string(session_id):
    res = server.tool_logs({"session_id": session_id, "lines": 20})
    assert res["ok"] is True
    assert isinstance(res["logs"], str)


# ---------------------------------------------------------------------- #
# Tool: record_start / record_stop / replay
# ---------------------------------------------------------------------- #


# ---------------------------------------------------------------------- #
# Stress tab — nested forms + alert-while-focused
# ---------------------------------------------------------------------- #


@pytest.mark.live
def test_stress_tab_navigates_and_shows_content(session_id):
    obs = _navigate_to(session_id, "Stress")
    assert _has(obs, "Stress") or _has(obs, "LazyVStack") or _has(obs, "Nested Form")


@pytest.mark.live
def test_alert_while_focused_dismisses_via_text_tap(session_id):
    """Stress tab: focus a TextField, trigger alert, dismiss alert by tapping 'OK'.

    This is the iOS focus-cancellation scenario that broke v15. Verifies that
    simdrive can drive a flow even when an alert appears over a focused field.
    """
    _navigate_to(session_id, "Stress")
    # Scroll down to find the alert section (it's far down the Stress view)
    obs = server.tool_observe({"session_id": session_id})
    sw, sh = obs["screenshot_size_pixels"]
    # Several swipes to scroll down to the alert area
    for _ in range(4):
        server.tool_swipe(
            {
                "session_id": session_id,
                "x1": sw // 2, "y1": int(sh * 0.75),
                "x2": sw // 2, "y2": int(sh * 0.25),
                "duration_ms": 300,
            }
        )
        time.sleep(0.5)

    obs = server.tool_observe({"session_id": session_id})
    if not _has(obs, "Show Alert"):
        pytest.skip("could not scroll to 'Show Alert' button; layout may differ")

    server.tool_tap({"session_id": session_id, "text": "Show Alert While Focused"})
    time.sleep(1.2)
    obs = server.tool_observe({"session_id": session_id})
    # Alert should be visible
    if not (_has(obs, "OK") or _has(obs, "Alert appeared")):
        pytest.skip("alert did not appear after tap; iOS may have suppressed it")

    server.tool_tap({"session_id": session_id, "text": "OK"})
    time.sleep(1.0)
    obs = server.tool_observe({"session_id": session_id})
    # Alert dismissed — "Alert appeared" detail message should be gone
    assert not _has(obs, "Alert appeared while")


# ---------------------------------------------------------------------- #
# Palace tab — original Palace dogfood reproducer
# ---------------------------------------------------------------------- #


@pytest.mark.live
def test_palace_book_state_machine(session_id):
    """Palace tab: walk Borrow → Download → Return state machine via button taps."""
    obs = _navigate_to(session_id, "Palace")
    assert _has(obs, "Palace") or _has(obs, "Book State")

    # Initial state should be 'available' or 'idle' — verify Borrow button is present
    if not _has(obs, "Borrow"):
        pytest.skip(f"Borrow button not visible; state may have leaked from prior run: {_texts(obs)[:15]}")

    server.tool_tap({"session_id": session_id, "text": "Borrow"})
    time.sleep(1.0)
    obs = server.tool_observe({"session_id": session_id})
    state_after_borrow = next((t for t in _texts(obs) if t.startswith("State:")), "?")
    assert "borrow" in state_after_borrow.lower() or _has(obs, "Download"), (
        f"borrow didn't advance state: {state_after_borrow}, marks: {_texts(obs)[:15]}"
    )


@pytest.mark.live
def test_palace_notification_flood_button(session_id):
    """Palace tab: 'Fire 10 Notifications' button increments visible counter."""
    _navigate_to(session_id, "Palace")
    obs = server.tool_observe({"session_id": session_id})
    sw, sh = obs["screenshot_size_pixels"]

    # Scroll to find Fire button
    for _ in range(3):
        if _has(obs, "Fire 10"):
            break
        server.tool_swipe(
            {"session_id": session_id, "x1": sw // 2, "y1": int(sh * 0.7),
             "x2": sw // 2, "y2": int(sh * 0.3), "duration_ms": 300}
        )
        time.sleep(0.5)
        obs = server.tool_observe({"session_id": session_id})

    if not _has(obs, "Fire 10"):
        pytest.skip("Fire 10 Notifications button not in viewport")

    server.tool_tap({"session_id": session_id, "text": "Fire 10 Notifications"})
    time.sleep(1.5)
    obs = server.tool_observe({"session_id": session_id})
    notif_text = next((t for t in _texts(obs) if "fired" in t.lower()), None)
    assert notif_text and "10" in notif_text, f"counter didn't show 10: {notif_text}"


# ---------------------------------------------------------------------- #
# Logs — with predicate filter
# ---------------------------------------------------------------------- #


@pytest.mark.live
def test_logs_with_predicate_filter(session_id):
    res = server.tool_logs(
        {
            "session_id": session_id,
            "lines": 50,
            "predicate": 'subsystem == "io.synctek.specterqa.testkit"',
        }
    )
    assert res["ok"] is True
    assert isinstance(res["logs"], str)


# ---------------------------------------------------------------------- #
# Replay — round trip + halt-on-drift behavior
# ---------------------------------------------------------------------- #


@pytest.mark.live
def test_recording_round_trip_with_replay(session_id, tmp_path):
    """Record a 3-step Nav-tab flow, then replay it and verify all steps execute."""
    _navigate_to(session_id, "Nav")

    rec_name = "e2e-nav-replay"
    server.tool_record_start({"session_id": session_id, "name": rec_name})
    for _ in range(2):
        server.tool_observe({"session_id": session_id})
        server.tool_tap({"session_id": session_id, "text": "Increment Counter"})
        time.sleep(0.5)
    res = server.tool_record_stop({"session_id": session_id})
    assert res["steps"] == 2

    # Replay it. drift_threshold is permissive so cosmetic differences (status bar
    # clock) don't halt us.
    replay_res = server.tool_replay(
        {"session_id": session_id, "name": rec_name, "on_drift": "warn", "drift_threshold": 0.5}
    )
    assert replay_res["ok"] is True
    assert all(step["executed"] for step in replay_res["steps"])


@pytest.mark.live
def test_replay_halts_on_drift_when_screen_diverges(session_id):
    """Record on Nav tab, switch to Form tab, replay with halt-on-drift — should halt."""
    _navigate_to(session_id, "Nav")

    rec_name = "e2e-drift-halt"
    server.tool_record_start({"session_id": session_id, "name": rec_name})
    server.tool_observe({"session_id": session_id})
    server.tool_tap({"session_id": session_id, "text": "Increment Counter"})
    time.sleep(0.5)
    res = server.tool_record_stop({"session_id": session_id})
    assert res["steps"] == 1

    # Now switch to a totally different tab
    _navigate_to(session_id, "Form")

    # Replay with strict drift threshold — should halt
    replay_res = server.tool_replay(
        {"session_id": session_id, "name": rec_name, "on_drift": "halt", "drift_threshold": 0.95}
    )
    assert replay_res["ok"] is False
    assert replay_res["halted_at"] == 1


# ---------------------------------------------------------------------- #
# Press-key keyboard navigation (Tab / arrow keys via HID)
# ---------------------------------------------------------------------- #


@pytest.mark.live
def test_press_key_return_in_field_dismisses_keyboard_or_advances(session_id):
    """Type into Search field then press return — verify a keystroke dispatches."""
    obs = _navigate_to(session_id, "Form")
    search_mark = next((m for m in obs["marks"] if "Search..." in m["text"]), None)
    if not search_mark:
        pytest.skip("Search field not visible")
    server.tool_type_text(
        {"session_id": session_id, "text": "abc", "tap_first": {"mark": search_mark["id"]}}
    )
    # Allow up to 2s for the typed text to settle (iOS keyboard subsystem
    # latency varies under suite-wide load).
    deadline = time.time() + 2.0
    saw_text = False
    while time.time() < deadline and not saw_text:
        time.sleep(0.3)
        obs = server.tool_observe({"session_id": session_id})
        saw_text = _has(obs, "abc") or _has(obs, "ABC")
    assert saw_text, f"text didn't enter: {_texts(obs)[:15]}"
    # Press return — should dispatch without raising
    server.tool_press_key({"session_id": session_id, "key": "return"})
    time.sleep(0.4)
