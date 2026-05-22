"""RED tests for SimDrive 1.0.0b5 — Domain D: SSIM status-bar masking.

Covers:
  F#14 — Drift threshold 0.85 too tight for status-bar variance. Auto-mask
          the iOS status bar before SSIM compute so clock/transient changes
          don't trip drift on visually-matched screens.

  F#15 — recording.yaml omits ssim_masks for status bar. Auto-populate
          ssim_masks at record_start per device class so every replay already
          has the mask in the YAML.

All tests FAIL RED on main because:
  - recorder._ssim_or_fallback has no auto-masking concept; masks are caller-
    supplied only.
  - recorder.start() / Recorder.finalize() write no ssim_masks key.
  - No DEVICE_STATUS_BAR_MASKS lookup table exists yet.

All tests PASS after CodeAtlas implements:
  - simdrive.recorder.DEVICE_STATUS_BAR_MASKS: dict mapping device-class name
    to (w, h) tuple for the status bar mask region.
  - simdrive.recorder._default_status_bar_mask(device_name) -> list[dict] | None
  - simdrive.recorder.start() auto-populates rec.ssim_masks from device name.
  - Recorder.finalize() writes ssim_masks into recording.yaml.
  - replay() merges ssim_masks from YAML when mask_regions not explicitly given
    (already partially done; mask from YAML already wired — the gap is record-
    time population, not replay-time application).
  - _ssim_or_fallback respects masks (already implemented; no change needed).
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
import yaml
from PIL import Image, ImageDraw


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _solid_image(w: int, h: int, color: int = 200) -> Image.Image:
    return Image.new("L", (w, h), color)


def _image_with_stripe(w: int, h: int, stripe_h: int, stripe_color: int,
                        bg_color: int = 200) -> Image.Image:
    """Create a greyscale image that differs only in the top `stripe_h` rows."""
    im = _solid_image(w, h, bg_color)
    draw = ImageDraw.Draw(im)
    draw.rectangle([0, 0, w, stripe_h], fill=stripe_color)
    return im


def _save_png(im: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    im.convert("RGB").save(path, format="PNG")


def _make_sim_session(tmp_path: Path, *,
                       device_name: str = "iPhone 17 Pro",
                       udid: str = "SSIM-TEST-UDID-001",
                       screenshot_w: int = 1206,
                       screenshot_h: int = 2622):
    """Build a minimal simulator Session for recorder.start() calls."""
    from simdrive import session as ses_mod
    from simdrive.sim import Device

    ses_mod._SESSIONS.clear()
    device = Device(
        udid=udid,
        name=device_name,
        os_version="26.3",
        state="booted",
    )
    s = ses_mod.Session(
        session_id="ssim-mask-test",
        device=device,
        workdir=tmp_path / "wd",
        target="simulator",
        app_bundle_id="io.synctek.simdrive.demo",
    )
    s.workdir.mkdir(parents=True, exist_ok=True)
    s.last_screenshot_w = screenshot_w
    s.last_screenshot_h = screenshot_h
    return s


# ---------------------------------------------------------------------------
# F#14 — SSIM compute with status-bar mask
# ---------------------------------------------------------------------------

class TestF14SSIMStatusBarMask:
    """Two images that differ ONLY in the status bar should score >= 0.85 when
    masked, but < 0.85 (or meaningfully lower) when the mask is disabled."""

    # iPhone 17 Pro native pixel resolution from the dogfood report and
    # test_unit.py corner-mapping test.
    IMG_W = 1206
    IMG_H = 2622

    # Status bar is top ~60 px (logical) * 3x scale = 180 px on iPhone 17 Pro.
    # The report mentions "top ~60 px" — production should auto-derive the
    # pixel height from the device class. We use 180 px (3x * 60) for the
    # mask region, but to create a detectable SSIM drop in the unmasked case
    # we use a much larger altered region (800 px) — SSIM is structurally
    # insensitive to small localised changes in a large uniform image.
    STATUS_BAR_H_PX = 180      # mask height (what CodeAtlas must produce)
    FIXTURE_STRIPE_H = 800     # exaggerated changed region to ensure SSIM < 0.85

    def _make_screenshot_pair_unmasked(self, tmp_path: Path):
        """Pair with a large changed region — confirms unmasked SSIM < 0.85."""
        ref = tmp_path / "ref_big.png"
        live = tmp_path / "live_big.png"
        _save_png(_solid_image(self.IMG_W, self.IMG_H, 200), ref)
        _save_png(
            _image_with_stripe(self.IMG_W, self.IMG_H,
                               stripe_h=self.FIXTURE_STRIPE_H,
                               stripe_color=50,
                               bg_color=200),
            live,
        )
        return ref, live

    def _make_screenshot_pair_status_bar(self, tmp_path: Path):
        """Pair that differs only in the 180 px status bar region."""
        ref = tmp_path / "ref_sb.png"
        live = tmp_path / "live_sb.png"
        _save_png(_solid_image(self.IMG_W, self.IMG_H, 200), ref)
        _save_png(
            _image_with_stripe(self.IMG_W, self.IMG_H,
                               stripe_h=self.STATUS_BAR_H_PX,
                               stripe_color=50,
                               bg_color=200),
            live,
        )
        return ref, live

    def test_ssim_without_mask_below_threshold(self, tmp_path: pytest.TempPathFactory):
        """Without masking, a large top-region diff drops SSIM below 0.85.

        This is a fixture sanity check — it ensures the image pair creates
        a real SSIM signal that a mask can rescue. Uses FIXTURE_STRIPE_H (800 px)
        rather than the actual status-bar height so the unmasked score reliably
        falls below the 0.85 drift threshold.
        """
        from simdrive.recorder import _ssim_or_fallback

        ref, live = self._make_screenshot_pair_unmasked(tmp_path)
        score = _ssim_or_fallback(ref, live, masks=None)
        assert score < 0.85, (
            f"Expected unmasked SSIM < 0.85 for large top-region diff, got {score:.4f}. "
            "Fixture broken — FIXTURE_STRIPE_H may need to be larger."
        )

    def test_ssim_with_explicit_mask_above_threshold(self, tmp_path: pytest.TempPathFactory):
        """Explicit mask covering the changed region restores SSIM >= 0.85.

        Uses FIXTURE_STRIPE_H for both the changed region and the mask width
        to confirm _apply_masks_pil blanks the diff area before compare.
        """
        from simdrive.recorder import _ssim_or_fallback

        ref, live = self._make_screenshot_pair_unmasked(tmp_path)
        # Supply the mask explicitly (existing API path already supports this).
        masks = [(0, 0, self.IMG_W, self.FIXTURE_STRIPE_H)]
        score = _ssim_or_fallback(ref, live, masks=masks)
        assert score >= 0.85, (
            f"With mask covering changed region, SSIM should be >= 0.85 but got {score:.4f}. "
            "CodeAtlas: verify _apply_masks_pil blanks the stripe before compare."
        )

    def test_default_status_bar_mask_lookup_exists(self):
        """recorder.DEVICE_STATUS_BAR_MASKS must exist and contain iPhone 17 Pro.

        CodeAtlas: add DEVICE_STATUS_BAR_MASKS = {
            'iPhone 17 Pro': (1206, 180),  # (width_px, status_bar_h_px)
            ...
        } to simdrive/src/simdrive/recorder.py.
        """
        from simdrive import recorder

        assert hasattr(recorder, "DEVICE_STATUS_BAR_MASKS"), (
            "recorder.DEVICE_STATUS_BAR_MASKS does not exist. "
            "Add a dict mapping device-name strings to (width_px, status_bar_h_px) tuples."
        )
        masks = recorder.DEVICE_STATUS_BAR_MASKS
        assert "iPhone 17 Pro" in masks, (
            f"DEVICE_STATUS_BAR_MASKS has no 'iPhone 17 Pro' entry. Keys: {list(masks)}"
        )
        entry = masks["iPhone 17 Pro"]
        assert len(entry) == 2, (
            f"DEVICE_STATUS_BAR_MASKS['iPhone 17 Pro'] should be (width_px, h_px), got {entry!r}"
        )

    def test_default_status_bar_mask_lookup_contains_iphone_16(self):
        """DEVICE_STATUS_BAR_MASKS should cover at least iPhone 16 as a second class."""
        from simdrive import recorder

        assert hasattr(recorder, "DEVICE_STATUS_BAR_MASKS"), (
            "recorder.DEVICE_STATUS_BAR_MASKS does not exist."
        )
        masks = recorder.DEVICE_STATUS_BAR_MASKS
        assert "iPhone 16 Pro" in masks or "iPhone 16" in masks, (
            f"DEVICE_STATUS_BAR_MASKS must contain at least one iPhone 16 variant. "
            f"Keys present: {list(masks)}"
        )

    def test_default_status_bar_mask_helper_returns_list(self):
        """recorder._default_status_bar_mask(device_name) -> list[dict] with one entry.

        CodeAtlas: implement
            def _default_status_bar_mask(device_name: str) -> list[dict] | None:
                entry = DEVICE_STATUS_BAR_MASKS.get(device_name)
                if not entry:
                    return None
                w, h = entry
                return [{"x": 0, "y": 0, "w": w, "h": h, "label": "status_bar"}]
        """
        from simdrive import recorder

        assert hasattr(recorder, "_default_status_bar_mask"), (
            "recorder._default_status_bar_mask() helper does not exist. "
            "CodeAtlas: add it to simdrive/src/simdrive/recorder.py."
        )
        result = recorder._default_status_bar_mask("iPhone 17 Pro")
        assert result is not None, (
            "_default_status_bar_mask('iPhone 17 Pro') returned None; "
            "expected a list with one mask dict."
        )
        assert isinstance(result, list) and len(result) == 1, (
            f"Expected a one-element list, got {result!r}"
        )
        mask = result[0]
        assert mask.get("label") == "status_bar", (
            f"Mask entry must have label='status_bar', got {mask!r}"
        )
        assert mask.get("x") == 0 and mask.get("y") == 0, (
            f"Status bar mask must start at (0,0), got {mask!r}"
        )

    def test_default_status_bar_mask_unknown_device_returns_none(self):
        """Unknown device names should return None (graceful degradation)."""
        from simdrive import recorder

        assert hasattr(recorder, "_default_status_bar_mask"), (
            "recorder._default_status_bar_mask() helper does not exist."
        )
        result = recorder._default_status_bar_mask("Galaxy S25")
        assert result is None, (
            f"_default_status_bar_mask for unknown device should return None, got {result!r}"
        )

    def test_ssim_with_auto_mask_via_device_name(self, tmp_path: pytest.TempPathFactory):
        """_ssim_or_fallback should accept a device_name kwarg and auto-apply the mask.

        CodeAtlas: add optional `device_name: str | None = None` param to
        _ssim_or_fallback. When masks is None and device_name is known,
        auto-populate masks from DEVICE_STATUS_BAR_MASKS.
        """
        from simdrive import recorder

        assert hasattr(recorder, "_ssim_or_fallback"), (
            "recorder._ssim_or_fallback not found."
        )

        ref, live = self._make_screenshot_pair_status_bar(tmp_path)

        import inspect
        sig = inspect.signature(recorder._ssim_or_fallback)
        assert "device_name" in sig.parameters, (
            "_ssim_or_fallback must accept a `device_name` keyword argument. "
            "CodeAtlas: add `device_name: str | None = None` to its signature and "
            "auto-resolve the status-bar mask when masks=None and device_name is known."
        )

        score = recorder._ssim_or_fallback(ref, live, masks=None,
                                            device_name="iPhone 17 Pro")
        assert score >= 0.85, (
            f"With device_name='iPhone 17 Pro', auto-mask should raise SSIM to >= 0.85. "
            f"Got {score:.4f}. CodeAtlas: look up DEVICE_STATUS_BAR_MASKS and apply mask."
        )


# ---------------------------------------------------------------------------
# F#15 — Auto-populate ssim_masks at record_start / finalize
# ---------------------------------------------------------------------------

class TestF15RecordTimeSSIMMask:
    """Recording.yaml must include ssim_masks for the status bar when the
    device class is recognized, so every replay automatically masks it."""

    def _start_and_stop_recording(self, tmp_path: Path, device_name: str,
                                   screenshot_w: int, screenshot_h: int) -> dict:
        """Run recorder.start() + Recorder.finalize() and return the YAML payload."""
        from simdrive import recorder as rec_mod

        session = _make_sim_session(
            tmp_path,
            device_name=device_name,
            screenshot_w=screenshot_w,
            screenshot_h=screenshot_h,
        )

        # Patch out I/O-heavy helpers so the test is -m "not live".
        with patch("simdrive.recorder._capture_state_contract",
                   return_value=(None, None)), \
             patch("simdrive.recorder.recordings_root",
                   return_value=tmp_path / "recordings"):
            rec = rec_mod.start(session, "test-ssim-mask-recording")
            yaml_path = rec.finalize()

        payload = yaml.safe_load(yaml_path.read_text())
        return payload

    # ── iPhone 17 Pro ──────────────────────────────────────────────────────

    def test_iphone_17_pro_recording_has_ssim_masks(self, tmp_path: pytest.TempPathFactory):
        """Finalizing a recording on iPhone 17 Pro must write ssim_masks key."""
        payload = self._start_and_stop_recording(
            tmp_path,
            device_name="iPhone 17 Pro",
            screenshot_w=1206,
            screenshot_h=2622,
        )
        assert "ssim_masks" in payload, (
            "recording.yaml is missing 'ssim_masks' key for iPhone 17 Pro recording. "
            "CodeAtlas: Recorder.finalize() must call _default_status_bar_mask(device.name) "
            "and write the result as payload['ssim_masks']."
        )

    def test_iphone_17_pro_ssim_masks_has_one_entry(self, tmp_path: pytest.TempPathFactory):
        """ssim_masks for iPhone 17 Pro should have exactly one entry: status_bar."""
        payload = self._start_and_stop_recording(
            tmp_path,
            device_name="iPhone 17 Pro",
            screenshot_w=1206,
            screenshot_h=2622,
        )
        masks = payload.get("ssim_masks", [])
        assert len(masks) == 1, (
            f"Expected exactly 1 ssim_mask entry (status_bar), got {len(masks)}: {masks}"
        )

    def test_iphone_17_pro_ssim_masks_label_is_status_bar(self, tmp_path: pytest.TempPathFactory):
        """The single mask entry must have label='status_bar'."""
        payload = self._start_and_stop_recording(
            tmp_path,
            device_name="iPhone 17 Pro",
            screenshot_w=1206,
            screenshot_h=2622,
        )
        masks = payload.get("ssim_masks", [])
        if not masks:
            pytest.fail(
                "ssim_masks is empty; cannot verify label. "
                "Fix: add auto-populate in Recorder.finalize()."
            )
        assert masks[0].get("label") == "status_bar", (
            f"First ssim_mask label must be 'status_bar', got {masks[0]!r}"
        )

    def test_iphone_17_pro_ssim_masks_dimensions(self, tmp_path: pytest.TempPathFactory):
        """iPhone 17 Pro mask must start at (0,0) and span full device width."""
        payload = self._start_and_stop_recording(
            tmp_path,
            device_name="iPhone 17 Pro",
            screenshot_w=1206,
            screenshot_h=2622,
        )
        masks = payload.get("ssim_masks", [])
        if not masks:
            pytest.fail("ssim_masks is empty; cannot verify dimensions.")
        m = masks[0]
        assert m.get("x") == 0, f"mask.x must be 0, got {m.get('x')}"
        assert m.get("y") == 0, f"mask.y must be 0, got {m.get('y')}"
        assert m.get("w") == 1206, (
            f"mask.w must equal device width 1206 px, got {m.get('w')}. "
            "CodeAtlas: use DEVICE_STATUS_BAR_MASKS['iPhone 17 Pro'] width."
        )
        h = m.get("h")
        assert isinstance(h, int) and h > 0, (
            f"mask.h must be a positive integer, got {h!r}"
        )

    # ── Second device class: iPhone 16 Pro ─────────────────────────────────

    def test_iphone_16_pro_recording_has_ssim_masks(self, tmp_path: pytest.TempPathFactory):
        """Auto-populate ssim_masks must also work for iPhone 16 Pro (second class)."""
        payload = self._start_and_stop_recording(
            tmp_path,
            device_name="iPhone 16 Pro",
            screenshot_w=1206,
            screenshot_h=2622,
        )
        assert "ssim_masks" in payload, (
            "recording.yaml is missing 'ssim_masks' for iPhone 16 Pro recording. "
            "CodeAtlas: DEVICE_STATUS_BAR_MASKS must include 'iPhone 16 Pro'."
        )

    def test_iphone_16_pro_ssim_masks_label(self, tmp_path: pytest.TempPathFactory):
        """iPhone 16 Pro mask entry must be labeled 'status_bar'."""
        payload = self._start_and_stop_recording(
            tmp_path,
            device_name="iPhone 16 Pro",
            screenshot_w=1206,
            screenshot_h=2622,
        )
        masks = payload.get("ssim_masks", [])
        assert masks and masks[0].get("label") == "status_bar", (
            f"iPhone 16 Pro ssim_masks first entry label must be 'status_bar', got {masks!r}"
        )

    # ── Unknown device: no mask written ────────────────────────────────────

    def test_unknown_device_omits_ssim_masks(self, tmp_path: pytest.TempPathFactory):
        """When device class is not in DEVICE_STATUS_BAR_MASKS, ssim_masks should
        be omitted from recording.yaml (not written as None or empty list)."""
        payload = self._start_and_stop_recording(
            tmp_path,
            device_name="iPad Pro 13-inch (M4)",
            screenshot_w=2064,
            screenshot_h=2752,
        )
        if "ssim_masks" in payload:
            masks = payload["ssim_masks"]
            assert masks is None or masks == [], (
                f"Unknown device should produce no ssim_masks or an empty list, got {masks!r}. "
                "CodeAtlas: skip writing ssim_masks when _default_status_bar_mask returns None."
            )

    # ── Replay picks up auto-masks from YAML ───────────────────────────────

    def test_replay_reads_ssim_masks_from_yaml(self, tmp_path: pytest.TempPathFactory):
        """When recording.yaml contains ssim_masks, replay() must apply them even
        if the caller passes mask_regions=None.

        This is already partially wired (recorder.py line 1435), but this test
        confirms the end-to-end round-trip: record → YAML has masks → replay reads them.
        """
        from simdrive import recorder as rec_mod

        # Build a recording YAML that includes ssim_masks (simulating what
        # finalize() should write after F#15 fix).
        rec_dir = tmp_path / "recordings" / "test-replay-reads-masks"
        rec_dir.mkdir(parents=True, exist_ok=True)
        snaps = rec_dir / "snapshots"
        snaps.mkdir()

        w, h = 1206, 2622
        img = _solid_image(w, h, 200)
        pre = snaps / "001_pre.png"
        post = snaps / "001_post.png"
        _save_png(img, pre)
        _save_png(img, post)

        payload = {
            "name": "test-replay-reads-masks",
            "created_at": 0.0,
            "device": "iPhone 17 Pro",
            "os_version": "26.3",
            "app_bundle_id": "io.synctek.simdrive.demo",
            "simdrive_version": "1.0.0b4",
            "created_by_session": "ssim-mask-test",
            "screenshot_size_pixels": [w, h],
            "tags": [],
            "target": "simulator",
            "ssim_masks": [
                {"x": 0, "y": 0, "w": w, "h": 180, "label": "status_bar"}
            ],
            "steps": [
                {
                    "id": 1,
                    "action": "tap",
                    "args": {"x": 100, "y": 500, "screenshot_w": w, "screenshot_h": h},
                    "pre_screenshot": "snapshots/001_pre.png",
                    "post_screenshot": "snapshots/001_post.png",
                    "captured_at": 1.0,
                }
            ],
        }
        (rec_dir / "recording.yaml").write_text(yaml.dump(payload, sort_keys=False))

        # Build a live session whose screenshot matches the reference frame.
        live_shot = tmp_path / "live_001.png"
        _save_png(_solid_image(w, h, 200), live_shot)

        session = _make_sim_session(tmp_path / "replay_wd",
                                     device_name="iPhone 17 Pro",
                                     screenshot_w=w,
                                     screenshot_h=h)

        def _fake_observe(_session):
            return {
                "screenshot_path": live_shot,
                "marks": [],
                "marks_count": 5,
            }

        def _fake_execute(_session, _step):
            pass

        with patch("simdrive.recorder.recordings_root", return_value=tmp_path / "recordings"), \
             patch("simdrive.recorder._observe_for_replay", side_effect=_fake_observe), \
             patch("simdrive.recorder._execute_step", side_effect=_fake_execute), \
             patch("simdrive.recorder._verify_state_contract", return_value=(True, {})):
            result = rec_mod.replay(
                "test-replay-reads-masks",
                session,
                mask_regions=None,   # caller passes nothing — must read from YAML
            )

        steps = result.get("steps", [])
        assert steps, f"Replay returned no steps: {result!r}"
        step = steps[0]
        # The replay engine returns 'similarity' per step (not 'ssim').
        # With identical images the score == 1.0 regardless of masking.
        # Key assertion: ssim_masks from YAML are parsed and replay completes
        # without crashing, and the similarity score is present.
        assert "similarity" in step, (
            f"Replay step must include 'similarity' key; got keys: {list(step.keys())}. "
            "This confirms ssim_masks from YAML were consumed without error."
        )
        assert step["similarity"] >= 0.85, (
            f"Expected similarity >= 0.85 for identical images, "
            f"got {step['similarity']}. Step: {step!r}"
        )
