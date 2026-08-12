"""State contracts must pin text the screen renders *stably*.

Vision OCR reads text drawn inside images — book cover art, logos, photographed
words — differently between consecutive reads of an unchanged screen, and does
so at full confidence, so confidence banding cannot filter it. A contract built
from one sample pinned those readings and then rejected genuinely correct state
at replay-start.

Observed on a real capture of the Palace audiobook player, same screen, reads
seconds apart:

    "JAMES PATTERSON"  /  "JAMES PATERSON"
    "MICHAEL WHITE"    /  "MICHALL WHITE"
    "PRIVATE"          /  "PRVATE" / "PRUALE" / "PRIATE"

while the UI-drawn chrome ("Private Down Under", "End of Chapter", "Off") read
identically every time. The contract is therefore built from the intersection of
consecutive observations.

Note the fixtures below use only band-"high" strings. Short or non-word chrome
like "Track 1" and "1.0x" fails the English-likeness gate and is never eligible
for a contract in the first place, so it cannot demonstrate anything here.
"""
from __future__ import annotations

from simdrive.recorder import _build_requires_block, _stable_texts
from simdrive.som import Mark


def _mark(mid: int, text: str, *, x=100, y=200, w=300, h=60, conf=1.0) -> Mark:
    return Mark(id=mid, x=x, y=y, w=w, h=h, text=text, confidence=conf)


# Chrome renders identically on both reads; the cover art does not.
FIRST_READ = [
    _mark(1, "Private Down Under", y=280),
    _mark(2, "End of Chapter", y=660),
    _mark(3, "JAMES PATTERSON", y=1100, w=560, h=160),   # cover art, huge
    _mark(4, "MICHAEL WHITE", y=1780, w=450, h=145),     # cover art
    _mark(5, "Off", y=2450, w=114),
]
SECOND_READ = [
    _mark(1, "Private Down Under", y=280),
    _mark(2, "End of Chapter", y=660),
    _mark(3, "JAMES PATERSON", y=1100, w=560, h=160),    # ← drifted
    _mark(4, "MICHALL WHITE", y=1780, w=450, h=145),     # ← drifted
    _mark(5, "Off", y=2450, w=114),
]

STABLE_CHROME = {"Private Down Under", "End of Chapter", "Off"}


def test_the_confidence_gate_cannot_catch_these_misreads():
    """Why stability sampling is needed at all.

    The existing dictionary gate demotes gibberish to band "low", so one might
    expect it to filter drifting cover-art readings. It does not: a misread that
    still looks like English scores "high", which is precisely how these landed
    in contracts. Nothing but cross-sample comparison distinguishes them.
    """
    for text in ("JAMES PATERSON", "MICHALL WHITE", "DOWI UNDER"):
        assert _mark(9, text).confidence_band == "high", text


def test_unstable_ocr_is_excluded_from_the_required_text():
    stable = _stable_texts([FIRST_READ, SECOND_READ])
    block = _build_requires_block(
        FIRST_READ, screen_h=2622, app_bundle_id="org.example.app",
        app_version="493", sim_device="iPhone 17 Pro", sim_ios_version="26.1",
        stable_texts=stable,
    )
    required = block.initial_state.text_subset_required
    assert "Private Down Under" in required
    assert "End of Chapter" in required
    assert "Off" in required
    # The readings that differ between samples must not become requirements —
    # pinning either spelling rejects the screen half the time.
    assert "JAMES PATTERSON" not in required
    assert "MICHAEL WHITE" not in required


def test_single_sample_keeps_the_old_unfiltered_behaviour():
    """migrate_recording has one stored screenshot and nothing to cross-check;
    it must not silently produce an empty contract."""
    assert _stable_texts([FIRST_READ]) is None
    block = _build_requires_block(
        FIRST_READ, screen_h=2622, app_bundle_id="org.example.app",
        app_version="493", sim_device="iPhone 17 Pro", sim_ios_version="26.1",
        stable_texts=None,
    )
    assert "JAMES PATTERSON" in block.initial_state.text_subset_required


def test_primary_button_label_is_never_an_unstable_reading():
    """The label is chosen by area, which lands on hero artwork — the exact text
    that drifts. A drifting label failed the contract on a correct screen."""
    stable = _stable_texts([FIRST_READ, SECOND_READ])
    block = _build_requires_block(
        FIRST_READ, screen_h=2622, app_bundle_id="org.example.app",
        app_version="493", sim_device="iPhone 17 Pro", sim_ios_version="26.1",
        stable_texts=stable,
    )
    label = block.initial_state.primary_button_label
    assert label != "JAMES PATTERSON"
    assert label in (None, *STABLE_CHROME)


def test_stable_texts_intersects_every_sample():
    assert _stable_texts([FIRST_READ, SECOND_READ]) == STABLE_CHROME
    # An empty sample is ignored rather than collapsing the intersection.
    assert _stable_texts([FIRST_READ, []]) is None


def test_capture_state_contract_resamples_the_screen(tmp_path, monkeypatch):
    """Wiring check: the capture path must actually take the second sample.

    The pure filter above is useless if _capture_state_contract only ever
    observes once — which is what it did.
    """
    from pathlib import Path

    from PIL import Image

    from simdrive import observe as obs_mod
    from simdrive import recorder, session as ses_mod
    from simdrive.observe import Observation
    from simdrive.sim import Device

    reads = [FIRST_READ, SECOND_READ]
    calls: list = []

    def _fake_observe(udid, out_dir, **kwargs):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"obs_{len(calls)}.png"
        Image.new("RGB", (1206, 2622), (210, 210, 210)).save(path)
        marks = reads[min(len(calls), len(reads) - 1)]
        calls.append(path)
        return Observation(screenshot_path=path, annotated_path=None,
                           screenshot_w=1206, screenshot_h=2622,
                           window_bounds=None, captured_at=0.0, marks=marks)

    monkeypatch.setattr(obs_mod, "observe", _fake_observe)
    monkeypatch.setattr(recorder.observe, "observe", _fake_observe, raising=False)

    ses_mod._SESSIONS.clear()
    s = ses_mod.Session(
        session_id="contract-sample",
        device=Device(udid="SIM-CONTRACT", name="iPhone 17 Pro",
                      os_version="26.1", state="active"),
        workdir=tmp_path / "wd",
        target="simulator",
        app_bundle_id="org.example.app",
    )
    s.workdir.mkdir(parents=True, exist_ok=True)

    block, warning = recorder._capture_state_contract(s, s.workdir)

    assert warning is None
    assert len(calls) >= 2, f"contract must be cross-checked, saw {len(calls)} observe(s)"
    required = block.initial_state.text_subset_required
    assert "Private Down Under" in required
    assert "JAMES PATTERSON" not in required


def test_a_failed_resample_falls_back_instead_of_losing_the_contract(tmp_path, monkeypatch):
    """A flaky second screenshot must not cost us the contract entirely."""
    from pathlib import Path

    from PIL import Image

    from simdrive import observe as obs_mod
    from simdrive import recorder, session as ses_mod
    from simdrive.observe import Observation
    from simdrive.sim import Device

    calls: list = []

    def _fake_observe(udid, out_dir, **kwargs):
        if calls:
            raise RuntimeError("screenshot failed")
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "obs_0.png"
        Image.new("RGB", (1206, 2622), (210, 210, 210)).save(path)
        calls.append(path)
        return Observation(screenshot_path=path, annotated_path=None,
                           screenshot_w=1206, screenshot_h=2622,
                           window_bounds=None, captured_at=0.0, marks=FIRST_READ)

    monkeypatch.setattr(obs_mod, "observe", _fake_observe)
    monkeypatch.setattr(recorder.observe, "observe", _fake_observe, raising=False)

    ses_mod._SESSIONS.clear()
    s = ses_mod.Session(
        session_id="contract-flaky",
        device=Device(udid="SIM-CONTRACT-2", name="iPhone 17 Pro",
                      os_version="26.1", state="active"),
        workdir=tmp_path / "wd",
        target="simulator",
        app_bundle_id="org.example.app",
    )
    s.workdir.mkdir(parents=True, exist_ok=True)

    block, warning = recorder._capture_state_contract(s, s.workdir)

    assert warning is None
    assert block is not None
    assert "Private Down Under" in block.initial_state.text_subset_required
