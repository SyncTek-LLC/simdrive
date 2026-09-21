"""Observe the current simulator state — screenshot + optional SoM annotation + logs.

Coordinate-space contract (a12, F-008)
--------------------------------------
* ``Observation.marks`` (list[Mark]) bbox and center are in SCREENSHOT PIXEL
  coordinates — they match the ``(screenshot_w, screenshot_h)`` fields of the
  Observation.  Vision OCR normalised coords are converted to pixels inside
  ``som.detect_marks`` before a Mark is constructed.
* Callers storing marks in ``Session.last_marks`` MUST convert to ``list[dict]``
  via ``[m.to_dict() for m in obs.marks]`` so the resolver always receives dicts
  (device path also returns dicts from annotate_device_screenshot).
  This is the a12 normalisation — see server.py.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from PIL import Image

from . import ax, sim, som
from .observability.logger import get_logger
from .observability.metrics import record_histogram
from .som import Mark
from .window import WindowBounds, get_bounds

log = get_logger("simdrive.observe")

# Confidence-band ordering for `confidence_floor` filtering. A floor of "med"
# keeps marks whose band rank >= rank("med"); a floor of "high" keeps only
# the top tier. Anything ranked below is dropped.
_BAND_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}
# Public-facing floor aliases — "med" is the common shorthand callers reach
# for; we accept it alongside the canonical "medium".
_FLOOR_ALIASES: dict[str, str] = {
    "low": "low",
    "med": "medium",
    "medium": "medium",
    "high": "high",
}

ConfidenceFloor = Literal["low", "med", "medium", "high"]


@dataclass
class Observation:
    screenshot_path: Path
    annotated_path: Path | None  # SoM-annotated copy; None if SoM disabled
    screenshot_w: int
    screenshot_h: int
    window_bounds: WindowBounds | None
    captured_at: float
    marks: list[Mark] = field(default_factory=list)
    recent_logs: str | None = None
    # Token-efficiency knobs (PR A). Default off so every existing
    # caller keeps the legacy behavior — server.py routes new args through here.
    compact: bool = False
    capture_observability: bool = False
    # INIT-2026-641 Wave 2 — AX-primary perception, visible degradation.
    #
    # `resolution_method` is ALWAYS present (never inferred from mark
    # contents) so a caller — human or agent — can tell what actually
    # produced this observation without cross-referencing per-mark `source`
    # fields:
    #   "ax"             — clean host-AX walk, merged onto OCR.
    #   "ax_reactivated" — AX recovered after a reactivate/retry (spike's
    #                      live "AXWindows went empty mid-session" finding).
    #   "ocr"            — AX unavailable, exhausted its retry budget, routed
    #                      away from (multi-sim/headless guard), or this is a
    #                      device-target observation (host AX is
    #                      simulator-only; OCR/WDA marks are the only path).
    # `degraded` is True whenever this observation did NOT get a clean "ax"
    # read; `degraded_reason` names why. The whole point (Chairman condition
    # of approval): a silent AX->OCR slide must never be invisible.
    resolution_method: str = "ocr"
    degraded: bool = False
    degraded_reason: str | None = None

    def to_dict(self) -> dict:
        if self.compact:
            mark_dicts: list[dict] = [m.to_compact_dict() for m in self.marks]
        else:
            mark_dicts = [m.to_dict() for m in self.marks]
        payload: dict = {
            "screenshot_path": str(self.screenshot_path),
            "annotated_path": str(self.annotated_path) if self.annotated_path else None,
            "screenshot_size_pixels": [self.screenshot_w, self.screenshot_h],
            "window_bounds_macos": (
                {
                    "x": self.window_bounds.x,
                    "y": self.window_bounds.y,
                    "width": self.window_bounds.width,
                    "height": self.window_bounds.height,
                }
                if self.window_bounds
                else None
            ),
            "captured_at": self.captured_at,
            "marks": mark_dicts,
            "recent_logs": self.recent_logs,
            "resolution_method": self.resolution_method,
            "degraded": self.degraded,
        }
        if self.degraded_reason:
            payload["degraded_reason"] = self.degraded_reason
        if self.capture_observability:
            # One entry per *returned* mark, ordered to align with `marks[i]`.
            # Audit finding #10: agents debugging "why is this low-confidence?"
            # get the band derivation without reading the OCR-gating source.
            payload["_observability"] = [_mark_observability(m) for m in self.marks]
        return payload


def _mark_observability(m: Mark) -> dict:
    """Per-mark derivation breadcrumb for `capture_observability=True`.

    Surfaces the inputs the band classifier used (raw OCR score + dictionary
    fence outcome) so callers can reason about why a mark landed in `low`
    even when the OCR engine reported raw_confidence ~= 1.0.
    """
    raw = float(m.raw_confidence or 0.0)
    english_like = som._english_likeness(m.text or "")
    dictionary_check = "passed" if english_like else "failed"
    if not english_like:
        reason = "dictionary gate failed — text does not read as English"
    elif raw >= 0.85:
        reason = "raw_confidence >= 0.85 and dictionary gate passed"
    else:
        reason = "raw_confidence < 0.85; clamped to medium"
    return {
        "mark_id": m.id,
        "raw_confidence": round(raw, 3),
        "clamped_confidence": round(float(m.confidence), 3),
        "confidence_band": m.confidence_band,
        "dictionary_check": dictionary_check,
        "reason": reason,
    }


def _apply_filters(
    marks: list[Mark],
    confidence_floor: ConfidenceFloor | None,
    mark_limit: int | None,
) -> list[Mark]:
    """Apply `confidence_floor` then `mark_limit` to a list of marks.

    `confidence_floor`: drop marks whose band ranks below the requested floor.
    None (default) keeps everything. Accepts "low", "med"/"medium", "high".
    `mark_limit`: keep only the top-N marks sorted by (band rank desc, area desc).
    Applied AFTER floor filtering so callers asking for `floor="high",
    mark_limit=10` get the 10 highest-area high-confidence marks.
    """
    out = marks
    if confidence_floor is not None:
        canonical = _FLOOR_ALIASES.get(confidence_floor)
        if canonical is None:
            raise ValueError(
                f"confidence_floor must be one of low/med/medium/high; got {confidence_floor!r}"
            )
        floor_rank = _BAND_RANK[canonical]
        out = [m for m in out if _BAND_RANK.get(m.confidence_band, 0) >= floor_rank]
    if mark_limit is not None:
        if mark_limit < 0:
            raise ValueError(f"mark_limit must be >= 0; got {mark_limit}")
        # Sort by band rank (desc), then area (desc) as tie-break — bigger marks
        # are usually more agent-actionable than a stray pixel of OCR noise.
        out = sorted(
            out,
            key=lambda m: (_BAND_RANK.get(m.confidence_band, 0), m.w * m.h),
            reverse=True,
        )[:mark_limit]
        # Restore original (reading-order) sort within the truncated slice so
        # downstream `marks[i].id` semantics still match top-to-bottom intuition.
        out.sort(key=lambda m: m.id)
    return out


# Collapse canary thresholds — see the AX merge block in observe().
# Deliberately conservative: a legitimate merge folds a label's fragments
# together, it does not erase three quarters of the screen.
_COLLAPSE_CANARY_RATIO = 0.25
_COLLAPSE_CANARY_MIN_OCR_MARKS = 8


def observe(
    udid: str,
    out_dir: Path,
    annotate: bool = True,
    capture_logs: bool = False,
    log_lines: int = 50,
    log_predicate: str | None = None,
    target: str = "simulator",
    compact: bool = False,
    confidence_floor: ConfidenceFloor | None = None,
    mark_limit: int | None = None,
    capture_observability: bool = False,
    device_name: str | None = None,
    allow_ax: bool = True,
) -> Observation:
    """Capture a screenshot + measure it; optionally annotate with SoM marks; optionally tail logs.

    `target` selects the backend: "simulator" (default) or "device" (real iPhone/iPad).

    Token-efficiency knobs (PR A) — all default off / no-op so
    existing callers see no behavior change:
    * `compact`: emit the slim 6-key mark dict (`to_compact_dict`) instead of the
      full 9-key diagnostic dict. ~5-6x reduction in JSON payload on dense screens.
    * `confidence_floor`: drop marks whose band ranks below "low"/"med"/"high".
      Default None keeps every band; "high" is the common agent setting.
    * `mark_limit`: cap the returned mark list to the top-N by (band, area).
      Applied AFTER `confidence_floor`, so `floor="high", limit=10` returns the
      10 largest high-confidence marks.
    * `capture_observability`: include a `_observability` array on the
      ``to_dict()`` payload — one entry per *returned* mark, surfacing the
      band derivation (raw confidence, dictionary-check outcome, reason).
      Default off; useful for debugging unexpected band assignments.

    AX-primary perception (INIT-2026-641 Wave 2) — all backward compatible:
    * `device_name`: the Simulator window title fragment (e.g. "iPhone 17 Pro
      Max") used to scope the host-AX walk. Required for AX to run at all —
      when None (every pre-Wave-2 caller), this function behaves exactly as
      before: OCR only, `resolution_method="ocr"`, `degraded=False`. This is
      the safe default, not a routing decision — the actual multi-sim/
      headless routing decision belongs to the caller (server.py computes
      `allow_ax` from live session state; see its docstring there).
    * `allow_ax`: caller-computed routing gate. When False, AX is never
      attempted (not even `ax.is_available()`) — used for multi-sim fleets
      and headless CI where a host-AX call could cross-target another
      session's on-screen window. Default True is safe because `device_name`
      being None already no-ops AX for legacy callers.

    Degradation is always visible: `resolution_method` ("ax" / "ax_reactivated"
    / "ocr") and `degraded`/`degraded_reason` are on every `Observation`,
    never inferred from mark contents. AX failures of every kind (unavailable,
    reactivate-retry exhausted, unexpected exception) fall back to OCR marks
    rather than raising to the caller — this function must never crash an
    agent's turn because host AX blinked.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    _t_start = time.time()
    ts = int(_t_start * 1000)
    raw_path = out_dir / f"observe-{ts}.png"
    if target == "device":
        from . import device  # avoid import cost when not used
        device.screenshot(udid, raw_path)
    else:
        sim.screenshot(udid, raw_path)
    with Image.open(raw_path) as im:
        w, h = im.size

    bounds: WindowBounds | None
    try:
        bounds = get_bounds()
    except Exception:
        bounds = None

    marks: list[Mark] = []
    annotated_path: Path | None = None
    # F#7 (b5): always detect marks so text targeting works regardless of annotate flag.
    # When annotate=True, also draw the SoM overlay and set annotated_path.
    # When annotate=False, skip drawing — marks are still returned, annotated_path stays None.
    marks = som.detect_marks(raw_path)

    resolution_method = "ocr"
    degraded = False
    degraded_reason: str | None = None

    if target == "simulator" and device_name:
        if not allow_ax:
            # Explicit routing decision, computed by the caller from live
            # session state (multi-sim fleet, or a headless/no-GUI session) —
            # NOT a config flag this function reads. AX entry points
            # (`ax.is_available`, `ax.select_window`) are never touched: a
            # multi-sim host-AX call risks cross-targeting another session's
            # on-screen window, and a headless sim has no window for it
            # anyway. OCR is the correct primary here, not a degraded
            # fallback from a failed attempt.
            degraded = True
            degraded_reason = "ax_routing_disabled_multi_sim_or_headless"
        elif not ax.is_available():
            degraded = True
            degraded_reason = "ax_unavailable"
        else:
            try:
                ax_result = ax.observe_pixel_elements(device_name, w, h)
                ocr_only_count = len(marks)
                marks = som.merge_ax_and_ocr(
                    marks, ax_result["elements"], screen_size=(w, h),
                )
                resolution_method = ax_result["resolution_method"]
                # Collapse canary. A merge should refine the mark list, not
                # empty it: when an AX element swallows the content inside it
                # the screen silently loses most of its detail while the
                # response still looks like a clean AX read. That is how a
                # Palace catalog came back with 5 marks where OCR alone found
                # 43, with degraded=false. The merge rule now requires label
                # correspondence before consuming by containment, so this
                # should not fire — it is here so the next container-shaped
                # regression announces itself instead of reading as healthy.
                if (
                    ocr_only_count >= _COLLAPSE_CANARY_MIN_OCR_MARKS
                    and len(marks) < ocr_only_count * _COLLAPSE_CANARY_RATIO
                ):
                    degraded = True
                    degraded_reason = (
                        f"ax_merge_collapsed: {ocr_only_count} OCR marks -> "
                        f"{len(marks)} after AX merge"
                    )
                    log.warning(
                        "AX merge collapsed the mark list; treating as degraded",
                        extra={
                            "udid": udid,
                            "device_name": device_name,
                            "ocr_marks": ocr_only_count,
                            "merged_marks": len(marks),
                        },
                    )
                if resolution_method == "ax_reactivated":
                    degraded = True
                    degraded_reason = "ax_reactivated"
                    log.info(
                        "AX perception recovered after a reactivate/retry",
                        extra={"udid": udid, "device_name": device_name},
                    )
            except ax.AXError as exc:
                # Reactivate-retry budget exhausted (or AX failed outright
                # mid-walk). Fall back to OCR-only marks already computed
                # above — never raise to the caller.
                degraded = True
                degraded_reason = f"ax_exhausted: {exc}"
                log.warning(
                    "AX perception exhausted its retry budget; falling back to OCR",
                    extra={"udid": udid, "device_name": device_name, "error": str(exc)},
                )
            except Exception as exc:  # noqa: BLE001 — AX must never crash observe()
                degraded = True
                degraded_reason = f"ax_error: {exc}"
                log.warning(
                    "AX perception raised an unexpected error; falling back to OCR",
                    extra={"udid": udid, "device_name": device_name, "error": str(exc)},
                )

    if annotate and marks:
        annotated_path = out_dir / f"observe-{ts}-som.png"
        # Annotate the *unfiltered* image so the on-disk PNG keeps the full
        # context for human review — filtering is for the JSON payload only.
        # Annotated AFTER the AX merge so the overlay draws real control
        # rects, not glyph-only OCR boxes, when AX perception succeeded.
        som.annotate(raw_path, marks, annotated_path)
    # Apply token-efficiency filters AFTER annotation so the PNG retains
    # every detected mark, but the in-memory + JSON `marks` list reflects
    # what the agent actually receives.
    marks = _apply_filters(marks, confidence_floor, mark_limit)

    logs_text: str | None = None
    if capture_logs:
        try:
            if target == "device":
                from . import device
                logs_text = device.get_log_tail(udid, lines=log_lines, predicate=log_predicate)
            else:
                logs_text = sim.get_log_tail(udid, lines=log_lines, predicate=log_predicate)
        except Exception as exc:
            logs_text = f"<log capture failed: {exc}>"

    captured_at = time.time()
    latency_ms = (captured_at - _t_start) * 1000.0
    record_histogram("observe_latency_ms", latency_ms)
    log.debug(
        "observe complete",
        extra={"udid": udid, "latency_ms": round(latency_ms, 1),
               "marks_count": len(marks), "target": target},
    )

    obs = Observation(
        screenshot_path=raw_path,
        annotated_path=annotated_path,
        screenshot_w=w,
        screenshot_h=h,
        window_bounds=bounds,
        captured_at=captured_at,
        marks=marks,
        recent_logs=logs_text,
        compact=compact,
        capture_observability=capture_observability,
        resolution_method=resolution_method,
        degraded=degraded,
        degraded_reason=degraded_reason,
    )

    # Persist a sidecar JSON next to the screenshot so anyone reading the
    # session directory has the full structured observation, not just pixels.
    sidecar = raw_path.with_suffix(".json")
    try:
        sidecar.write_text(json.dumps(obs.to_dict(), indent=2))
    except Exception:
        pass  # never let sidecar persistence fail the observe call

    return obs
