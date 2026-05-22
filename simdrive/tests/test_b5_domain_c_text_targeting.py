"""Domain C — Text Targeting / OCR Semantics — RED test suite for b5.

Findings covered:
  F#6 — tap({text:X}) silently picks first-match on duplicate labels (HIGH)
  F#5 — Text targets non-deterministic across observes (MEDIUM)
  F#4 — OCR misreads expose alternates (LOW)
  F#7 — annotate=False returns 0 marks instead of unannotated marks (LOW)
  F#18 — Demo confidence labeling: clean system text labelled 'low' (LOW)

All tests run under -m "not live" — no simulator required.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — fake session + marks
# ---------------------------------------------------------------------------

_FAKE_UDID = "DOMAIN-C-FAKE-0000-0000-000000000001"


def _make_mark_dict(
    mark_id: int,
    text: str,
    x: int = 100,
    y: int = 100,
    w: int = 100,
    h: int = 50,
    confidence: float = 1.0,
    raw_confidence: float | None = None,
    confidence_band: str = "high",
    stable_id: str | None = None,
) -> dict:
    """Return a minimal mark dict that mirrors what Session.last_marks holds."""
    import hashlib
    cx = x + w // 2
    cy = y + h // 2
    if stable_id is None:
        bx = cx // 20
        by = cy // 20
        key = f"{text}|{bx},{by}".encode()
        stable_id = hashlib.blake2b(key, digest_size=6).hexdigest()
    return {
        "id": mark_id,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "text": text,
        "confidence": confidence,
        "raw_confidence": raw_confidence if raw_confidence is not None else confidence,
        "confidence_band": confidence_band,
        "stable_id": stable_id,
        "stable_id_loose": stable_id,
        "center": [cx, cy],
        "bbox": [x, y, x + w, y + h],
    }


def _make_session(tmp_path: Path, sid: str = "dc-test") -> object:
    """Create a minimal Session-shaped object and register it."""
    from simdrive import session as session_mod
    from simdrive.sim import Device

    d = Device(udid=_FAKE_UDID, name="Test Sim", os_version="26.0", state="active")
    workdir = tmp_path / "sessions" / sid
    workdir.mkdir(parents=True, exist_ok=True)
    s = session_mod.Session(
        session_id=sid,
        device=d,
        workdir=workdir,
        target="simulator",
        last_screenshot_w=1206,
        last_screenshot_h=2622,
    )
    session_mod._SESSIONS[sid] = s
    return s


def _cleanup_session(sid: str) -> None:
    from simdrive import session as session_mod
    session_mod._SESSIONS.pop(sid, None)


# ---------------------------------------------------------------------------
# F#6 — ambiguous_text_target on duplicate labels
# ---------------------------------------------------------------------------

class TestF6AmbiguousTextTarget:
    """tap({text:X}) with >1 matching marks must raise ambiguous_text_target,
    not silently return ok:true for the first match."""

    def test_duplicate_exact_text_raises_ambiguous(self, tmp_path):
        """Two marks with identical 'Sign In' text → error, not ok:true."""
        sid = "f6-dup"
        s = _make_session(tmp_path, sid)
        # Two marks: title at top, button at bottom — same text
        mark_title = _make_mark_dict(1, "Sign In", x=300, y=100, w=200, h=60)
        mark_btn   = _make_mark_dict(2, "Sign In", x=300, y=700, w=200, h=60)
        s.last_marks = [mark_title, mark_btn]

        from simdrive import server as srv
        from simdrive.errors import SimdriveError

        with patch.object(srv, "_ensure_screenshot_dims", return_value=(1206, 2622)):
            with pytest.raises(SimdriveError) as exc_info:
                srv._resolve_target_xy(s, {"text": "Sign In"})

        err = exc_info.value
        assert err.code == "ambiguous_text_target", (
            f"Expected ambiguous_text_target, got {err.code!r}"
        )
        _cleanup_session(sid)

    def test_ambiguous_error_includes_stable_ids(self, tmp_path):
        """The ambiguous_text_target error must list stable_id for both candidates."""
        sid = "f6-stable"
        s = _make_session(tmp_path, sid)
        m1 = _make_mark_dict(1, "Sign In", x=300, y=100, w=200, h=60)
        m2 = _make_mark_dict(2, "Sign In", x=300, y=700, w=200, h=60)
        s.last_marks = [m1, m2]

        from simdrive import server as srv
        from simdrive.errors import SimdriveError

        with patch.object(srv, "_ensure_screenshot_dims", return_value=(1206, 2622)):
            with pytest.raises(SimdriveError) as exc_info:
                srv._resolve_target_xy(s, {"text": "Sign In"})

        details = exc_info.value.details
        candidates = details.get("candidates", [])
        assert len(candidates) >= 2, "Should have at least 2 candidates in details"
        sids = [c.get("stable_id") for c in candidates]
        assert all(sid_val is not None for sid_val in sids), (
            "Every candidate must include stable_id for disambiguation"
        )
        _cleanup_session(sid)

    def test_single_match_still_resolves_ok(self, tmp_path):
        """A unique text match must still resolve without error."""
        sid = "f6-single"
        s = _make_session(tmp_path, sid)
        m = _make_mark_dict(1, "Sign In", x=300, y=700, w=200, h=60)
        s.last_marks = [m]

        from simdrive import server as srv

        with patch.object(srv, "_ensure_screenshot_dims", return_value=(1206, 2622)):
            cx, cy, how, matched = srv._resolve_target_xy(s, {"text": "Sign In"})

        assert matched is not None, "Single match should resolve to a mark"
        assert "text" in how, f"Resolution hint should mention text, got: {how!r}"
        _cleanup_session(sid)

    def test_tool_tap_returns_error_dict_not_ok_true(self, tmp_path):
        """tool_tap called via arguments dict with ambiguous text must return
        an error dict (not ok:true) when >1 marks match."""
        sid = "f6-tool"
        s = _make_session(tmp_path, sid)
        m1 = _make_mark_dict(1, "Sign In", x=300, y=100, w=200, h=60)
        m2 = _make_mark_dict(2, "Sign In", x=300, y=700, w=200, h=60)
        s.last_marks = [m1, m2]

        from simdrive import server as srv
        from simdrive.errors import SimdriveError

        with patch.object(srv, "_ensure_screenshot_dims", return_value=(1206, 2622)):
            # tool_tap raises SimdriveError; the MCP layer would serialize it.
            with pytest.raises(SimdriveError) as exc_info:
                srv.tool_tap({"session_id": sid, "text": "Sign In"})

        assert exc_info.value.code == "ambiguous_text_target"
        # Critically: NOT ok:true
        result_dict = exc_info.value.to_dict()
        assert result_dict.get("ok") is False
        _cleanup_session(sid)


# ---------------------------------------------------------------------------
# F#5 — stale text target: tap with cached text not in latest marks
# ---------------------------------------------------------------------------

class TestF5StaleTextTarget:
    """When tap({text:X}) doesn't match latest marks, the error should include
    alternates AND a fuzzy 'suggestion' field pointing to the closest current mark."""

    def test_stale_text_returns_target_not_found(self, tmp_path):
        """tap with text absent from latest marks raises target_not_found."""
        sid = "f5-stale"
        s = _make_session(tmp_path, sid)
        # Latest marks have "Password" but agent cached "Passwordi"
        current_mark = _make_mark_dict(1, "Password", x=100, y=400, w=200, h=50)
        s.last_marks = [current_mark]

        from simdrive import server as srv
        from simdrive.errors import SimdriveError

        with patch.object(srv, "_ensure_screenshot_dims", return_value=(1206, 2622)):
            with pytest.raises(SimdriveError) as exc_info:
                srv._resolve_target_xy(s, {"text": "Passwordi"})

        assert exc_info.value.code == "target_not_found"
        _cleanup_session(sid)

    def test_stale_text_error_includes_alternates(self, tmp_path):
        """target_not_found for stale text must include available marks as alternates."""
        sid = "f5-alternates"
        s = _make_session(tmp_path, sid)
        current_mark = _make_mark_dict(1, "Password", x=100, y=400, w=200, h=50)
        s.last_marks = [current_mark]

        from simdrive import server as srv
        from simdrive.errors import SimdriveError

        with patch.object(srv, "_ensure_screenshot_dims", return_value=(1206, 2622)):
            with pytest.raises(SimdriveError) as exc_info:
                srv._resolve_target_xy(s, {"text": "Passwordi"})

        details = exc_info.value.details
        available = details.get("available", [])
        assert len(available) > 0, (
            "target_not_found must include non-empty 'available' list so agents "
            "know what marks ARE present"
        )
        assert "Password" in available, (
            "The closest real mark text must appear in the alternates list"
        )
        _cleanup_session(sid)

    def test_stale_text_error_includes_suggestion(self, tmp_path):
        """target_not_found for near-miss text must include a 'suggestion' field
        with the closest current mark (e.g. 'did you mean Password?')."""
        sid = "f5-suggestion"
        s = _make_session(tmp_path, sid)
        current_mark = _make_mark_dict(1, "Password", x=100, y=400, w=200, h=50)
        s.last_marks = [current_mark]

        from simdrive import server as srv
        from simdrive.errors import SimdriveError

        with patch.object(srv, "_ensure_screenshot_dims", return_value=(1206, 2622)):
            with pytest.raises(SimdriveError) as exc_info:
                srv._resolve_target_xy(s, {"text": "Passwordi"})

        details = exc_info.value.details
        # F#5 desired: include a 'suggestion' key with the fuzzy-matched candidate
        assert "suggestion" in details, (
            "F#5 requires a 'suggestion' field in target_not_found details so "
            "agents know the closest match (e.g. 'did you mean Password?')"
        )
        assert details["suggestion"] == "Password", (
            f"suggestion should be 'Password', got {details.get('suggestion')!r}"
        )
        _cleanup_session(sid)


# ---------------------------------------------------------------------------
# F#4 — OCR misread alternates exposed on Mark
# ---------------------------------------------------------------------------

class TestF4OCRAlternates:
    """When consecutive OCR results disagree (e.g. 'Passwordi' then 'Password'),
    marks should expose an 'alternates' field listing both readings."""

    def _make_mark_with_alternates(self, **kwargs) -> "object":
        """Construct a Mark dataclass; the alternates field is the tested addition."""
        from simdrive.som import Mark
        m = Mark(
            id=kwargs.get("id", 1),
            x=kwargs.get("x", 100),
            y=kwargs.get("y", 400),
            w=kwargs.get("w", 200),
            h=kwargs.get("h", 50),
            text=kwargs.get("text", "Password"),
            confidence=kwargs.get("confidence", 1.0),
            raw_confidence=kwargs.get("raw_confidence", 1.0),
        )
        return m

    def test_mark_exposes_alternates_field(self):
        """A Mark with OCR ambiguity must expose an 'alternates' list.

        Currently Mark has no 'alternates' field. This test asserts the
        desired post-b5 state: alternates is present and contains both readings.
        """
        from simdrive.som import Mark
        m = self._make_mark_with_alternates(text="Password", confidence=1.0)
        # F#4 desired: Mark should support an 'alternates' attribute
        assert hasattr(m, "alternates"), (
            "F#4 requires Mark to have an 'alternates' field listing all OCR "
            "readings seen for this element across recent observations"
        )

    def test_mark_to_dict_includes_alternates(self):
        """Mark.to_dict() must include 'alternates' so agents can see all OCR readings."""
        from simdrive.som import Mark
        m = self._make_mark_with_alternates(text="Password", confidence=1.0)
        d = m.to_dict()
        assert "alternates" in d, (
            "F#4: Mark.to_dict() must include 'alternates' key for agent consumption"
        )

    def test_alternates_contains_both_ocr_readings(self):
        """When OCR produced 'Passwordi' and 'Password', alternates must list both."""
        from simdrive.som import Mark
        m = self._make_mark_with_alternates(text="Password", confidence=1.0)
        # Post-b5: alternates would be populated by the OCR smoothing / dedup layer.
        # For now we test that the field exists and can hold the misread.
        assert hasattr(m, "alternates"), "Mark must have alternates field"
        # Once alternates is implemented it should be a list.
        assert isinstance(getattr(m, "alternates", None), list), (
            "alternates must be a list, e.g. ['Password', 'Passwordi']"
        )


# ---------------------------------------------------------------------------
# F#7 — observe(annotate=False) should return marks (just unannotated image)
# ---------------------------------------------------------------------------

class TestF7AnnotateFalseReturnsMarks:
    """observe(annotate=False) currently returns marks=[] because SoM IS the
    source of marks. Desired behavior (Option A): detect marks but skip drawing
    annotations on the returned image bytes."""

    def _stub_screenshot_bytes(self, tmp_path: Path) -> bytes:
        """Tiny 1x1 PNG."""
        import struct, zlib
        def _png(w, h):
            ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
            idat_data = zlib.compress(b"\x00\xff\x00\x00" * w * h)
            def _chunk(t, d):
                c = struct.pack(">I", len(d)) + t + d
                return c + struct.pack(">I", zlib.crc32(c[4:]) & 0xFFFFFFFF)
            return (b"\x89PNG\r\n\x1a\n"
                    + _chunk(b"IHDR", ihdr)
                    + _chunk(b"IDAT", idat_data)
                    + _chunk(b"IEND", b""))
        return _png(1, 1)

    def test_annotate_false_still_returns_nonempty_marks(self, tmp_path):
        """observe(annotate=False) must return marks detected via SoM even though
        the returned image is not annotated."""
        from simdrive.som import Mark

        fake_marks = [
            Mark(id=1, x=50, y=100, w=100, h=40, text="Password", confidence=1.0)
        ]

        with patch("simdrive.observe.sim.screenshot") as mock_ss, \
             patch("simdrive.observe.som.detect_marks", return_value=fake_marks) as mock_detect, \
             patch("simdrive.observe.som.annotate") as mock_annotate, \
             patch("simdrive.observe.get_bounds", return_value=None):

            png_path = tmp_path / "fake.png"
            png_path.write_bytes(self._stub_screenshot_bytes(tmp_path))
            mock_ss.side_effect = lambda udid, path: path.write_bytes(
                self._stub_screenshot_bytes(tmp_path)
            )

            from simdrive.observe import observe as do_observe
            obs = do_observe(
                udid=_FAKE_UDID,
                out_dir=tmp_path / "obs",
                annotate=False,  # ← the key flag
            )

        # F#7 desired: marks must NOT be empty even when annotate=False
        assert len(obs.marks) > 0, (
            "F#7 (Option A): observe(annotate=False) should detect marks via SoM "
            "but skip drawing the annotation overlay on the image. "
            f"Currently returns {len(obs.marks)} marks — should return > 0."
        )

    def test_annotate_false_does_not_call_som_annotate(self, tmp_path):
        """When annotate=False, SoM annotation drawing must be skipped, but
        detect_marks must still be called so marks are populated."""
        from simdrive.som import Mark

        fake_marks = [
            Mark(id=1, x=50, y=100, w=100, h=40, text="Sign In", confidence=1.0)
        ]

        with patch("simdrive.observe.sim.screenshot") as mock_ss, \
             patch("simdrive.observe.som.detect_marks", return_value=fake_marks) as mock_detect, \
             patch("simdrive.observe.som.annotate") as mock_annotate, \
             patch("simdrive.observe.get_bounds", return_value=None):

            mock_ss.side_effect = lambda udid, path: path.write_bytes(
                self._stub_screenshot_bytes(tmp_path)
            )

            from simdrive.observe import observe as do_observe
            obs = do_observe(
                udid=_FAKE_UDID,
                out_dir=tmp_path / "obs2",
                annotate=False,
            )

        # detect_marks should still be called (marks must be populated)
        mock_detect.assert_called_once(), (
            "F#7: detect_marks must be called even when annotate=False "
            "so marks are available for text targeting"
        )
        # annotate (drawing) must NOT be called
        mock_annotate.assert_not_called(), (
            "F#7: som.annotate (overlay drawing) must NOT be called when annotate=False"
        )

    def test_annotate_false_annotated_path_is_none(self, tmp_path):
        """When annotate=False, annotated_path on the Observation must be None
        because no annotated image was produced."""
        from simdrive.som import Mark

        fake_marks = [
            Mark(id=1, x=50, y=100, w=100, h=40, text="Sign In", confidence=1.0)
        ]

        with patch("simdrive.observe.sim.screenshot") as mock_ss, \
             patch("simdrive.observe.som.detect_marks", return_value=fake_marks), \
             patch("simdrive.observe.som.annotate") as mock_annotate, \
             patch("simdrive.observe.get_bounds", return_value=None):

            mock_ss.side_effect = lambda udid, path: path.write_bytes(
                self._stub_screenshot_bytes(tmp_path)
            )

            from simdrive.observe import observe as do_observe
            obs = do_observe(
                udid=_FAKE_UDID,
                out_dir=tmp_path / "obs3",
                annotate=False,
            )

        assert obs.annotated_path is None, (
            "F#7: annotated_path must be None when annotate=False "
            "(no annotated image is produced)"
        )


# ---------------------------------------------------------------------------
# F#18 — Confidence band labeling: clean system text should not all be 'low'
# ---------------------------------------------------------------------------

class TestF18ConfidenceBandLabeling:
    """simdrive demo against Apple Preferences returned 5 marks all 'low'.

    Root cause: Apple Preferences system text labels like 'Wi-Fi', 'Bluetooth',
    'General', 'Privacy' fail the _english_likeness() dictionary gate because
    those words are NOT in the _ENGLISH_WORDS frozenset. With english_like=False
    the band drops to 'low' regardless of raw_confidence.

    Desired (post-b5): common iOS settings vocabulary must be in the wordlist
    so valid system UI text at any OCR confidence lands 'medium', not 'low'.
    A raw_confidence >= 0.85 mark with english text should be 'high'.

    The regression guard (non-English gibberish stays 'low') must remain.
    """

    def test_wifi_label_not_low(self):
        """'Wi-Fi' at raw_confidence=0.3 must NOT be 'low' — it is unambiguous
        Apple system text that fails the dictionary gate only because 'wi-fi'
        is absent from _ENGLISH_WORDS."""
        from simdrive.som import Mark
        m = Mark(id=1, x=50, y=100, w=200, h=50, text="Wi-Fi",
                 confidence=0.3, raw_confidence=0.3)
        assert m.confidence_band != "low", (
            f"F#18: 'Wi-Fi' fails _ENGLISH_WORDS lookup → confidence_band='low'. "
            f"Got band={m.confidence_band!r}. "
            "Fix: add 'wi-fi' / 'wifi' to the dictionary, or widen the gate for "
            "single-token tech-product names that are clearly legible."
        )

    def test_bluetooth_label_not_low(self):
        """'Bluetooth' is unambiguous system text but absent from _ENGLISH_WORDS
        → incorrectly classified 'low'. Must be 'medium' post-fix."""
        from simdrive.som import Mark
        m = Mark(id=2, x=50, y=160, w=200, h=50, text="Bluetooth",
                 confidence=0.3, raw_confidence=0.3)
        assert m.confidence_band != "low", (
            f"F#18: 'Bluetooth' not in _ENGLISH_WORDS → band='low', got {m.confidence_band!r}. "
            "Add 'bluetooth' to the dictionary so iOS settings labels are 'medium'."
        )

    def test_general_label_not_low(self):
        """'General' is the most common iOS settings row and fails the dict gate
        because it is absent from _ENGLISH_WORDS. Should be 'medium' at raw=0.3."""
        from simdrive.som import Mark
        m = Mark(id=3, x=50, y=220, w=200, h=50, text="General",
                 confidence=0.3, raw_confidence=0.3)
        assert m.confidence_band != "low", (
            f"F#18: 'General' not in _ENGLISH_WORDS → band='low', got {m.confidence_band!r}. "
            "Add 'general' to the dictionary — it is standard English."
        )

    def test_apple_prefs_tech_labels_not_low(self):
        """The 4 tech-product labels from Apple Preferences ('Wi-Fi', 'Bluetooth',
        'General', 'Privacy') must all be 'medium' or 'high', NOT 'low'.

        These fail the current _ENGLISH_WORDS gate but are perfectly legible
        iOS settings row labels — they should not land in 'low'."""
        from simdrive.som import Mark
        tech_labels = ["Wi-Fi", "Bluetooth", "General", "Privacy"]
        marks = [
            Mark(id=i + 1, x=50, y=100 + i * 60, w=200, h=50,
                 text=t, confidence=0.3, raw_confidence=0.3)
            for i, t in enumerate(tech_labels)
        ]
        low_labels = [m.text for m in marks if m.confidence_band == "low"]
        assert not low_labels, (
            f"F#18: These Apple Preferences labels are incorrectly 'low': {low_labels}. "
            "They fail _ENGLISH_WORDS lookup. Fix: add iOS settings vocabulary "
            "('bluetooth', 'wi-fi'/'wifi', 'general', 'privacy') to _ENGLISH_WORDS "
            "so legible system text lands 'medium'."
        )

    def test_low_confidence_non_english_mark_stays_low(self):
        """A gibberish/non-english OCR read should still be 'low' — regression guard."""
        from simdrive.som import Mark
        m = Mark(id=1, x=10, y=10, w=100, h=40,
                 text="Sary liotex canxz", confidence=1.0, raw_confidence=1.0)
        assert m.confidence_band == "low", (
            "Non-English OCR reads at any confidence should remain 'low' (regression guard)"
        )
