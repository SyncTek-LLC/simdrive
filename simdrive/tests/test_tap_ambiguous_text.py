"""F#6 — tap({text: X}) must not silently pick first-match on duplicate labels.

When SimDrive Demo had two marks both reading "Sign In" (screen title + submit
button), `tap({text: "Sign In"})` returned ok=true and silently picked the title
(first in mark order). The agent thought the submit button was tapped — wasted
a screen transition.

These tests enforce:

  - >1 marks tied at the highest precedence tier (exact/prefix/substring)
    → error code `ambiguous_text_target` with `details.candidates` listing
       up to 5 marks (each with stable_id, mark, bbox, confidence, text,
       position_hint).
  - Exactly 1 exact match (even if many prefix/substring matches also exist)
    → resolves unambiguously to the exact match (precedence tier matters).
  - 1 prefix-only match → resolves unambiguously.
  - 0 matches → existing `target_not_found` (don't regress).

Tier precedence: exact > prefix > substring. Only the top non-empty tier is
considered when counting ambiguity. The position_hint is a coarse 9-cell grid
("top-center", "bottom-right", "middle-left", …) derived from bbox center vs.
screen size.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ── Mark fixture helper ─────────────────────────────────────────────────────


def _mark(
    mark_id: int,
    text: str,
    bbox: list[int],
    *,
    stable_id: str | None = None,
    confidence: float = 1.0,
) -> dict:
    return {
        "id": mark_id,
        "stable_id": stable_id or f"sid_{mark_id:04d}",
        "stable_id_loose": f"loose_{mark_id:04d}",
        "bbox": bbox,
        "center": [bbox[0] + bbox[2] // 2, bbox[1] + bbox[3] // 2],
        "text": text,
        "confidence": confidence,
        "raw_confidence": confidence,
        "confidence_band": "high",
    }


# ── Session factory ─────────────────────────────────────────────────────────


def _make_session(
    tmp_path: Path,
    last_marks: list,
    session_id: str = "f6test",
    screenshot_w: int = 1320,
    screenshot_h: int = 2868,
) -> object:
    from simdrive import session as session_mod
    from simdrive.sim import Device

    d = Device(udid="TEST-F6-AMBIG", name="Test", os_version="18.0", state="active")
    workdir = tmp_path / "sessions" / session_id
    workdir.mkdir(parents=True, exist_ok=True)
    s = session_mod.Session(
        session_id=session_id,
        device=d,
        workdir=workdir,
        target="simulator",
        last_screenshot_w=screenshot_w,
        last_screenshot_h=screenshot_h,
    )
    s.last_marks = last_marks
    session_mod._SESSIONS[session_id] = s
    return s


@pytest.fixture(autouse=True)
def _cleanup():
    from simdrive import session as session_mod
    yield
    for sid in ("f6test",):
        session_mod._SESSIONS.pop(sid, None)


# ═══════════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════════


def test_two_exact_matches_raise_ambiguous_text_target(tmp_path):
    """Two marks with identical exact-match text → ambiguous_text_target error.

    Reproduces the SimDrive Demo "Sign In" bug: a screen title and a submit
    button both reading "Sign In". Must not silent-pick.
    """
    _make_session(
        tmp_path,
        last_marks=[
            # Screen title near the top
            _mark(1, "Sign In", [500, 100, 320, 80], stable_id="title_signin"),
            # Submit button near the bottom
            _mark(2, "Sign In", [400, 2600, 520, 120], stable_id="btn_signin"),
        ],
    )

    from simdrive.server import _resolve_target_xy
    from simdrive import session as session_mod
    from simdrive.errors import SimdriveError

    s = session_mod.get("f6test")

    with pytest.raises(SimdriveError) as exc_info:
        _resolve_target_xy(s, {"text": "Sign In"})

    err = exc_info.value
    assert err.code == "ambiguous_text_target", err.code
    cands = err.details.get("candidates")
    assert isinstance(cands, list)
    assert len(cands) == 2, f"Expected 2 candidates, got {len(cands)}"

    # Each candidate must carry the disambiguation keys.
    required_keys = {"stable_id", "mark", "bbox", "confidence", "text", "position_hint"}
    for c in cands:
        assert required_keys.issubset(c.keys()), (
            f"Candidate missing keys: {required_keys - c.keys()} ({c})"
        )
        assert c["text"] == "Sign In"

    # Position hints should differ — title is top, button is bottom.
    hints = {c["position_hint"] for c in cands}
    assert any("top" in h for h in hints), hints
    assert any("bottom" in h for h in hints), hints

    # Recovery string is present so agents know how to disambiguate.
    assert "stable_id" in err.message or "stable_id" in str(err.details.get("recovery", ""))


def test_one_exact_among_many_prefix_resolves_unambiguously(tmp_path):
    """One exact + several prefix matches → resolves to the exact (precedence)."""
    _make_session(
        tmp_path,
        last_marks=[
            _mark(1, "Sign In", [400, 2600, 520, 120], stable_id="btn_signin_exact"),
            _mark(2, "Sign In With Apple", [400, 2400, 520, 120]),
            _mark(3, "Sign In With Google", [400, 2200, 520, 120]),
            _mark(4, "Sign In Later", [400, 2000, 520, 120]),
        ],
    )

    from simdrive.server import _resolve_target_xy
    from simdrive import session as session_mod

    s = session_mod.get("f6test")
    cx, cy, how, mark = _resolve_target_xy(s, {"text": "Sign In"})

    # Must resolve to the exact match, not raise.
    assert mark is not None
    text = mark["text"] if isinstance(mark, dict) else getattr(mark, "text", None)
    assert text == "Sign In", f"Expected exact match 'Sign In', got {text!r}"
    sid = mark["stable_id"] if isinstance(mark, dict) else getattr(mark, "stable_id", None)
    assert sid == "btn_signin_exact"


def test_zero_matches_raises_target_not_found(tmp_path):
    """Zero matches → existing target_not_found (don't regress)."""
    _make_session(
        tmp_path,
        last_marks=[
            _mark(1, "Cancel", [100, 100, 200, 80]),
            _mark(2, "Submit", [100, 200, 200, 80]),
        ],
    )

    from simdrive.server import _resolve_target_xy
    from simdrive import session as session_mod
    from simdrive.errors import SimdriveError

    s = session_mod.get("f6test")
    with pytest.raises(SimdriveError) as exc_info:
        _resolve_target_xy(s, {"text": "NonexistentLabel"})

    assert exc_info.value.code == "target_not_found"


def test_one_prefix_only_match_resolves_unambiguously(tmp_path):
    """One prefix-only match → resolves to it (no ambiguity at the prefix tier)."""
    _make_session(
        tmp_path,
        last_marks=[
            _mark(1, "Search results", [100, 100, 400, 80], stable_id="title_search"),
            _mark(2, "Cancel", [800, 100, 200, 80]),
        ],
    )

    from simdrive.server import _resolve_target_xy
    from simdrive import session as session_mod

    s = session_mod.get("f6test")
    cx, cy, how, mark = _resolve_target_xy(s, {"text": "Search"})

    assert mark is not None
    sid = mark["stable_id"] if isinstance(mark, dict) else getattr(mark, "stable_id", None)
    assert sid == "title_search"


def test_three_exact_matches_returns_up_to_five_with_position_hints(tmp_path):
    """Three identical exact matches → 3 candidates, each with valid position_hint.

    Verifies the position_hint 9-cell grid: top-left / top-center / top-right /
    middle-left / middle-center / middle-right / bottom-left / bottom-center /
    bottom-right. Cap is 5 candidates; here we have 3 so all are returned.
    """
    # Screen: 1320 x 2868. Thirds: x ~ 440/880; y ~ 956/1912.
    _make_session(
        tmp_path,
        last_marks=[
            # Top-left
            _mark(1, "OK", [100, 100, 100, 60]),
            # Middle-center
            _mark(2, "OK", [610, 1400, 100, 60]),
            # Bottom-right
            _mark(3, "OK", [1100, 2700, 100, 60]),
        ],
        screenshot_w=1320,
        screenshot_h=2868,
    )

    from simdrive.server import _resolve_target_xy
    from simdrive import session as session_mod
    from simdrive.errors import SimdriveError

    s = session_mod.get("f6test")
    with pytest.raises(SimdriveError) as exc_info:
        _resolve_target_xy(s, {"text": "OK"})

    err = exc_info.value
    assert err.code == "ambiguous_text_target"
    cands = err.details["candidates"]
    assert len(cands) == 3

    # Map mark id → position_hint for assertion clarity.
    hint_by_id = {c["mark"]: c["position_hint"] for c in cands}
    assert hint_by_id[1] == "top-left", hint_by_id[1]
    assert hint_by_id[2] == "middle-center", hint_by_id[2]
    assert hint_by_id[3] == "bottom-right", hint_by_id[3]


def test_ambiguous_candidates_capped_at_five(tmp_path):
    """More than 5 exact matches → candidates list capped at 5."""
    marks = [
        _mark(i, "Tab", [100 + i * 50, 100, 40, 40]) for i in range(1, 8)
    ]
    _make_session(tmp_path, last_marks=marks)

    from simdrive.server import _resolve_target_xy
    from simdrive import session as session_mod
    from simdrive.errors import SimdriveError

    s = session_mod.get("f6test")
    with pytest.raises(SimdriveError) as exc_info:
        _resolve_target_xy(s, {"text": "Tab"})

    cands = exc_info.value.details["candidates"]
    assert len(cands) == 5, f"Expected cap at 5, got {len(cands)}"


def test_recovery_string_mentions_disambiguation(tmp_path):
    """The error must carry a `recovery` hint telling the agent how to disambiguate."""
    _make_session(
        tmp_path,
        last_marks=[
            _mark(1, "Sign In", [500, 100, 320, 80]),
            _mark(2, "Sign In", [400, 2600, 520, 120]),
        ],
    )

    from simdrive.server import _resolve_target_xy
    from simdrive import session as session_mod
    from simdrive.errors import SimdriveError

    s = session_mod.get("f6test")
    with pytest.raises(SimdriveError) as exc_info:
        _resolve_target_xy(s, {"text": "Sign In"})

    recovery = exc_info.value.details.get("recovery", "")
    assert "stable_id" in recovery
    assert "disambig" in recovery.lower() or "multiple" in recovery.lower() or "re-call" in recovery.lower()
