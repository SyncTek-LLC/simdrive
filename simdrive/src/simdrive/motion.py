"""Motion capture - measure animation instead of discarding it.

Why this module exists
----------------------
simdrive had no frame capture at all. `record_start` stores per-step pre/post
stills and `replay` compares them by SSIM, with deliberate hysteresis so that
"a single noisy frame (transient animation, status-bar tick, brief loading
flash)" cannot trip drift. That is correct for replay determinism, and it means
animation was systematically discarded rather than measured. Four targets in
the 2026-08-10 chaos-QA campaign were animation-shaped and none could be
settled: a button flickering between two labels, a mini-to-full player morph, a
skeleton that may or may not resolve, and a five-minute freeze that had to be
proved by hand with `ps` CPU% plus log silence.

This is a **separate channel from replay**. Nothing here changes drift
semantics or the recorder's animation suppression.

The design constraint
---------------------
Do not hand the agent raw frames. 120 screenshots is an unreadable,
budget-destroying payload. Capture returns *quantified* motion - a delta
series, a state count, a transition count, a settling time - plus one
representative image per distinct state.

Capture mechanics, as measured on iOS 26
----------------------------------------
`simctl io recordVideo` is **change-driven**: a static display emits almost
nothing, so a 2000 ms recording of a still screen produced a 67 ms clip with a
single frame. That is not a failure, it is evidence - the wall-clock remainder
is provably still, and `analyze_frames` reports it as such rather than
pretending the capture covered the whole window.

Frames are decoded with ffmpeg when it is available. Without it, capture falls
back to polling `simctl io screenshot`, which sustains roughly 1 fps on Apple
Silicon - enough to answer "did anything change at all", nowhere near enough to
resolve a several-Hz flicker. Every result therefore carries the *measured*
`effective_fps`, and callers that need a rate refuse rather than guess.
"""
from __future__ import annotations

import shutil
import signal
import subprocess
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Optional

from . import sim


class MotionError(RuntimeError):
    """Raised when a motion capture or analysis cannot be performed."""


Rect = tuple[int, int, int, int]

# Analysis resolution. Frames are cropped to the ROI and then reduced to this
# size before differencing: big enough that a label swap in a button-sized ROI
# is unmistakable, small enough that a 120-frame series costs milliseconds.
_ANALYSIS_SIZE = (64, 64)
# dhash grid; 8x8 comparisons over a 9-wide grayscale row = 64 bits.
_HASH_SIZE = 8
# Bits of dhash distance below which two frames are "the same state". Mid-
# animation frames sit above this; compression noise and antialiasing sit well
# below it.
_DEFAULT_HASH_THRESHOLD = 6
# Per-cell intensity tolerance (of 255) on the 8x8 brightness map that
# accompanies the hash. Wide enough to absorb h264 compression noise, tight
# enough to separate a control that merely changed shade.
_DEFAULT_INTENSITY_THRESHOLD = 24
# Normalised mean-absolute-difference below which a frame pair counts as still.
_DEFAULT_MOTION_THRESHOLD = 0.01
# How long the screen must hold still before we call it settled.
_DEFAULT_QUIET_MS = 300.0
_DEFAULT_MAX_REPRESENTATIVE_FRAMES = 8

# Decoded frames are downscaled to this long edge. ROI coordinates arrive in
# screenshot pixels and are rescaled to match (see reference_size).
_DECODE_LONG_EDGE = 800

# recordVideo needs a moment before it is actually capturing; without this the
# first few hundred ms of a short window are missing.
_RECORD_WARMUP_SEC = 0.6
_RECORD_STOP_TIMEOUT_SEC = 30.0

_MAX_DURATION_MS = 120_000


# ── frame primitives ────────────────────────────────────────────────────────


def _open_gray(path: Path):
    from PIL import Image
    with Image.open(path) as im:
        return im.convert("L").copy()


def _apply_masks(im, masks: Optional[Sequence[Rect]]):
    """Blank each rectangle to a constant so the difference cancels there.

    Same approach as replay's ssim_masks: cheaper and more robust than
    weighting the region to zero after comparing.
    """
    if not masks:
        return im
    from PIL import ImageDraw
    out = im.copy()
    draw = ImageDraw.Draw(out)
    for x, y, w, h in masks:
        draw.rectangle([x, y, x + w, y + h], fill=128)
    return out


def _prepare(im, roi: Optional[Rect], masks: Optional[Sequence[Rect]]):
    im = _apply_masks(im, masks)
    if roi:
        x, y, w, h = roi
        im = im.crop((x, y, x + w, y + h))
    return im.resize(_ANALYSIS_SIZE)


def frame_delta(prepared_a, prepared_b) -> float:
    """Normalised mean absolute difference of two prepared frames, in [0, 1]."""
    from PIL import ImageChops, ImageStat
    diff = ImageChops.difference(prepared_a, prepared_b)
    return round(ImageStat.Stat(diff).mean[0] / 255.0, 5)


def dhash(prepared) -> int:
    """64-bit difference hash. Pure PIL - no numpy, no extra dependency."""
    small = prepared.resize((_HASH_SIZE + 1, _HASH_SIZE))
    # tobytes() rather than getdata(): identical row-major bytes for an "L"
    # image, and getdata() is deprecated in Pillow 12 while the package still
    # supports Pillow >= 10.
    px = small.tobytes()
    bits = 0
    for row in range(_HASH_SIZE):
        base = row * (_HASH_SIZE + 1)
        for col in range(_HASH_SIZE):
            bits = (bits << 1) | int(px[base + col] > px[base + col + 1])
    return bits


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def frame_signature(prepared) -> tuple[int, bytes]:
    """Perceptual signature of a frame: structure plus coarse intensity.

    dhash alone is blind to brightness-only changes - it encodes the sign of
    neighbouring pixel differences, so a solid block that changes shade keeps
    an identical hash. Real UI states differ that way constantly (a control
    going disabled, a row highlighting, a skeleton shimmering), so the
    signature carries an 8x8 intensity map alongside the hash.
    """
    return dhash(prepared), prepared.resize((_HASH_SIZE, _HASH_SIZE)).tobytes()


def signatures_match(
    a: tuple[int, bytes],
    b: tuple[int, bytes],
    hash_threshold: int = _DEFAULT_HASH_THRESHOLD,
    intensity_threshold: int = _DEFAULT_INTENSITY_THRESHOLD,
) -> bool:
    """Two frames are the same state only if structure AND intensity agree."""
    if hamming(a[0], b[0]) > hash_threshold:
        return False
    return all(abs(x - y) <= intensity_threshold for x, y in zip(a[1], b[1]))


def cluster_states(
    signatures: Sequence[tuple[int, bytes]],
    hash_threshold: int = _DEFAULT_HASH_THRESHOLD,
    intensity_threshold: int = _DEFAULT_INTENSITY_THRESHOLD,
) -> list[int]:
    """Assign each frame a state id by greedy perceptual clustering.

    Greedy and order-dependent by design: frames arrive in time order, and a
    frame that matches an earlier state must be recognised as a *return* to it.
    That return is exactly what separates a flicker (few states, many
    transitions) from a progression (many states, few transitions).
    """
    reps: list[tuple[int, bytes]] = []
    out: list[int] = []
    for sig in signatures:
        for idx, rep in enumerate(reps):
            if signatures_match(sig, rep, hash_threshold, intensity_threshold):
                out.append(idx)
                break
        else:
            reps.append(sig)
            out.append(len(reps) - 1)
    return out


def count_transitions(states: Sequence[int]) -> int:
    return sum(1 for i in range(1, len(states)) if states[i] != states[i - 1])


def find_settled_ms(
    deltas: Sequence[float],
    times_ms: Sequence[float],
    motion_threshold: float = _DEFAULT_MOTION_THRESHOLD,
    quiet_ms: float = _DEFAULT_QUIET_MS,
) -> Optional[float]:
    """Timestamp at which motion stopped, or None if it never did.

    "Stopped" means every subsequent frame pair is below `motion_threshold` and
    the quiet tail lasts at least `quiet_ms`. A shorter tail is not evidence of
    settling - it is evidence we stopped watching.
    """
    if not times_ms:
        return None
    if not deltas:
        return times_ms[0]
    last_motion = -1
    for i, d in enumerate(deltas):
        if d > motion_threshold:
            last_motion = i
    if last_motion == len(deltas) - 1:
        return None  # still moving at the last observed pair
    settle_index = last_motion + 1  # first frame of the quiet tail
    tail_ms = times_ms[-1] - times_ms[settle_index]
    if tail_ms < quiet_ms:
        return None
    return times_ms[settle_index]


# ── ROI handling ────────────────────────────────────────────────────────────


def scale_rect(rect: Rect, frame_size: tuple[int, int],
               reference_size: Optional[tuple[int, int]]) -> Rect:
    """Map a rectangle from screenshot-pixel space into decoded-frame space.

    Decoded frames are downscaled for speed, so an unscaled ROI would crop the
    wrong region - or fall off the frame entirely.
    """
    if not reference_size or reference_size == frame_size:
        return rect
    ref_w, ref_h = reference_size
    if ref_w <= 0 or ref_h <= 0:
        return rect
    sx = frame_size[0] / float(ref_w)
    sy = frame_size[1] / float(ref_h)
    x, y, w, h = rect
    return (round(x * sx), round(y * sy),
            max(1, round(w * sx)), max(1, round(h * sy)))


def _validate_rect(rect: Rect, frame_size: tuple[int, int], label: str) -> None:
    x, y, w, h = rect
    fw, fh = frame_size
    if w <= 0 or h <= 0:
        raise MotionError(f"{label} {list(rect)} has non-positive width/height")
    if x < 0 or y < 0 or x + w > fw or y + h > fh:
        raise MotionError(
            f"{label} {list(rect)} falls outside the {fw}x{fh} frame. ROI is in "
            "screenshot pixel coordinates; pass reference_size if the frames are "
            "a different size."
        )


# ── analysis ────────────────────────────────────────────────────────────────


def analyze_frames(
    frame_paths: Sequence[Path],
    times_ms: Sequence[float],
    roi: Optional[Rect] = None,
    mask_regions: Optional[Sequence[Rect]] = None,
    reference_size: Optional[tuple[int, int]] = None,
    hash_threshold: int = _DEFAULT_HASH_THRESHOLD,
    motion_threshold: float = _DEFAULT_MOTION_THRESHOLD,
    quiet_ms: float = _DEFAULT_QUIET_MS,
    representative_dir: Optional[Path] = None,
    max_representative_frames: int = _DEFAULT_MAX_REPRESENTATIVE_FRAMES,
    requested_duration_ms: Optional[float] = None,
) -> dict:
    """Turn a frame sequence into numbers the model can reason over.

    `roi` and `mask_regions` are in screenshot-pixel coordinates; pass
    `reference_size` when the frames were decoded at a different scale.
    """
    if not frame_paths:
        raise MotionError("no frames to analyse - the capture produced nothing")
    if len(times_ms) != len(frame_paths):
        raise MotionError(
            f"frame/timestamp mismatch: {len(frame_paths)} frames, {len(times_ms)} timestamps"
        )

    first = _open_gray(Path(frame_paths[0]))
    frame_size = first.size

    roi_in_frame = None
    if roi:
        roi_in_frame = scale_rect(tuple(roi), frame_size, reference_size)  # type: ignore[arg-type]
        _validate_rect(roi_in_frame, frame_size, "roi")
    masks_in_frame = None
    if mask_regions:
        masks_in_frame = [scale_rect(tuple(m), frame_size, reference_size) for m in mask_regions]
        for m in masks_in_frame:
            _validate_rect(m, frame_size, "mask_region")

    prepared = [_prepare(first, roi_in_frame, masks_in_frame)]
    for p in frame_paths[1:]:
        prepared.append(_prepare(_open_gray(Path(p)), roi_in_frame, masks_in_frame))

    deltas = [frame_delta(prepared[i - 1], prepared[i]) for i in range(1, len(prepared))]
    states = cluster_states([frame_signature(p) for p in prepared],
                            hash_threshold=hash_threshold)
    transitions = count_transitions(states)

    warnings: list[str] = []
    settled = find_settled_ms(deltas, times_ms, motion_threshold, quiet_ms)
    captured_ms = (times_ms[-1] - times_ms[0]) if len(times_ms) > 1 else 0.0
    if requested_duration_ms and captured_ms + quiet_ms < requested_duration_ms:
        # simctl recordVideo is change-driven: no frames means no display change.
        # The unrecorded remainder is evidence of stillness, not a gap.
        warnings.append(
            f"capture stopped producing frames after {captured_ms:.0f} ms of a "
            f"{requested_duration_ms:.0f} ms window - the display was static for the "
            "remainder (simctl recordVideo only emits frames when the screen changes)"
        )
        if settled is None:
            settled = times_ms[-1]

    gaps = [times_ms[i] - times_ms[i - 1] for i in range(1, len(times_ms))]
    effective_fps = (len(times_ms) - 1) / (captured_ms / 1000.0) if captured_ms > 0 else 0.0

    reps: list[dict] = []
    if representative_dir is not None:
        reps = _save_representative_frames(
            frame_paths, times_ms, states, representative_dir, max_representative_frames,
        )

    return {
        "frames": len(frame_paths),
        "frame_size": [frame_size[0], frame_size[1]],
        "roi": list(roi) if roi else None,
        "roi_in_frame": list(roi_in_frame) if roi_in_frame else None,
        "captured_duration_ms": round(captured_ms, 1),
        "effective_fps": round(effective_fps, 2),
        "delta_series": deltas,
        "mean_delta": round(sum(deltas) / len(deltas), 5) if deltas else 0.0,
        "max_delta": max(deltas) if deltas else 0.0,
        "distinct_states": len(set(states)),
        "state_sequence": states,
        "transitions": transitions,
        "settled_at_ms": settled,
        "max_gap_ms": round(max(gaps), 1) if gaps else 0.0,
        "representative_frames": reps,
        "warnings": warnings,
    }


def _save_representative_frames(
    frame_paths: Sequence[Path],
    times_ms: Sequence[float],
    states: Sequence[int],
    dest_dir: Path,
    cap: int,
) -> list[dict]:
    """Copy the first frame of each distinct state. A handful, never all of them."""
    import shutil as _shutil
    dest_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    seen: set[int] = set()
    for idx, state in enumerate(states):
        if state in seen:
            continue
        seen.add(state)
        if len(out) >= cap:
            break
        target = dest_dir / f"state_{state:02d}.png"
        _shutil.copyfile(frame_paths[idx], target)
        out.append({"state": state, "at_ms": round(times_ms[idx], 1), "path": str(target)})
    return out


# ── verdicts built on top of the analysis ───────────────────────────────────


def flicker_verdict(
    analysis: dict,
    duration_ms: float,
    min_transitions: int = 4,
    min_revisits: int = 2,
    min_transitions_per_second: float = 0.5,
) -> dict:
    """Is the ROI oscillating, as opposed to changing?

    A flicker is *few* states visited *many* times. The discriminator is
    revisits: a wizard stepping A->B->C->D has transitions == states - 1 and
    never returns, while A->B->A->B->A has far more transitions than states.
    One there-and-back (A->B->A) is a change and a revert, not a flicker, so
    two revisits are required.

    The rate floor is deliberately low. Revisits already exclude legitimate
    progressions, and a capture window carries no user input, so a region that
    bounces between two states three times is anomalous whether it does so at
    5 Hz or at 1 Hz. An earlier 1.0/s floor was measured vetoing a real
    oscillation (5 transitions between 2 states over 6 s = 0.83/s) and was the
    only thing standing between that capture and a correct verdict.
    """
    transitions = int(analysis.get("transitions", 0))
    states = int(analysis.get("distinct_states", 0))
    revisits = transitions - max(0, states - 1)
    seconds = max(duration_ms, 1.0) / 1000.0
    rate = transitions / seconds

    flickering = (
        states >= 2
        and transitions >= min_transitions
        and revisits >= min_revisits
        and rate >= min_transitions_per_second
    )
    return {
        "flickering": flickering,
        "transitions": transitions,
        "states": states,
        "revisits": revisits,
        "transitions_per_second": round(rate, 2),
        "period_ms": _period_ms(analysis),
    }


def _period_ms(analysis: dict) -> Optional[float]:
    """Mean duration of a full oscillation cycle, or None with too few transitions.

    Two transitions make one cycle (A->B->A), so the period is twice the mean
    interval between transitions.
    """
    states = analysis.get("state_sequence") or []
    times = analysis.get("_times_ms")
    transitions_at: list[float] = []
    if times and len(times) == len(states):
        for i in range(1, len(states)):
            if states[i] != states[i - 1]:
                transitions_at.append(times[i])
    else:
        captured = float(analysis.get("captured_duration_ms") or 0.0)
        n = len(states)
        if n > 1 and captured > 0:
            step = captured / (n - 1)
            for i in range(1, n):
                if states[i] != states[i - 1]:
                    transitions_at.append(i * step)
    if len(transitions_at) < 2:
        return None
    gaps = [transitions_at[i] - transitions_at[i - 1] for i in range(1, len(transitions_at))]
    return round(2.0 * sum(gaps) / len(gaps), 1)


# ── capture ─────────────────────────────────────────────────────────────────


def resolve_source(source: str) -> str:
    """Decide between the video and screenshot capture paths.

    "auto" prefers video and silently degrades; an explicit "video" fails loudly
    rather than quietly handing back a 1 fps series the caller will misread.
    """
    if source not in ("auto", "video", "screenshots"):
        raise MotionError(f"unknown capture source {source!r}: expected auto|video|screenshots")
    have_ffmpeg = shutil.which("ffmpeg") is not None
    if source == "screenshots":
        return "screenshots"
    if source == "video":
        if not have_ffmpeg:
            raise MotionError(_FFMPEG_MISSING)
        return "video"
    return "video" if have_ffmpeg else "screenshots"


_FFMPEG_MISSING = (
    "ffmpeg is required to decode a screen recording into frames and was not found "
    "on PATH. Recovery: `brew install ffmpeg`, or pass source='screenshots' for a "
    "much slower (~1 fps) sampling fallback that can answer liveness but not flicker."
)


def record_video(udid: str, duration_ms: int, dest: Path) -> Path:
    """Record the simulator display for `duration_ms` and return the clip path."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        ["xcrun", "simctl", "io", udid, "recordVideo", "--codec", "h264", "--force", str(dest)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        time.sleep(_RECORD_WARMUP_SEC + duration_ms / 1000.0)
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            _, err = proc.communicate(timeout=_RECORD_STOP_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise MotionError("simctl recordVideo did not stop within 30s")
    if not dest.exists() or dest.stat().st_size == 0:
        detail = (err or b"").decode("utf-8", "replace").strip()[:300]
        raise MotionError(f"simctl recordVideo produced no output: {detail}")
    return dest


def decode_frames(video: Path, fps: int, out_dir: Path) -> list[Path]:
    """Decode a clip into evenly-spaced PNG frames, downscaled for analysis."""
    if shutil.which("ffmpeg") is None:
        raise MotionError(_FFMPEG_MISSING)
    out_dir.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video),
         "-vf", f"fps={fps},scale=-2:'min({_DECODE_LONG_EDGE},ih)'",
         str(out_dir / "f_%05d.png")],
        capture_output=True, text=True, timeout=180.0, check=False,
    )
    frames = sorted(out_dir.glob("f_*.png"))
    if res.returncode != 0 and not frames:
        raise MotionError(f"ffmpeg failed to decode {video}: {res.stderr.strip()[:300]}")
    if not frames:
        raise MotionError(f"ffmpeg produced no frames from {video}")
    return frames


def sample_screenshots(udid: str, out_dir: Path, duration_ms: int,
                       fps: int) -> tuple[list[Path], list[float]]:
    """Fallback capture: poll `simctl io screenshot` for the window.

    Returns real elapsed timestamps, not the nominal schedule - simctl
    screenshot sustains roughly 1 fps, and reporting the requested rate would
    let a caller read a several-Hz verdict off a handful of samples.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    interval = 1.0 / max(1, fps)
    paths: list[Path] = []
    times: list[float] = []
    start = time.time()
    deadline = start + duration_ms / 1000.0
    i = 0
    while True:
        # Stamp before the call: simctl captures near the start of it, and a
        # post-call stamp would report ~1s of latency as elapsed frame time.
        taken_at = (time.time() - start) * 1000.0
        dest = out_dir / f"f_{i:05d}.png"
        sim.screenshot(udid, dest)
        paths.append(dest)
        times.append(taken_at)
        i += 1
        if time.time() >= deadline:
            break
        next_tick = start + i * interval
        if next_tick > time.time():
            time.sleep(min(next_tick - time.time(), max(0.0, deadline - time.time())))
    return paths, times


def capture_motion(
    udid: str,
    out_dir: Path,
    duration_ms: int,
    fps: int = 30,
    roi: Optional[Rect] = None,
    mask_regions: Optional[Sequence[Rect]] = None,
    source: str = "auto",
    reference_size: Optional[tuple[int, int]] = None,
    keep_frames: bool = False,
    **analysis_kwargs: Any,
) -> dict:
    """Record the display, decode it, and return quantified motion.

    Frames are deleted after analysis unless `keep_frames` - only the
    representative frames survive, because the payload budget is the point.
    """
    if duration_ms <= 0 or duration_ms > _MAX_DURATION_MS:
        raise MotionError(
            f"duration_ms must be between 1 and {_MAX_DURATION_MS}; got {duration_ms}"
        )
    resolved = resolve_source(source)
    capture_id = uuid.uuid4().hex[:8]
    work = Path(out_dir) / capture_id
    frames_dir = work / "frames"

    if resolved == "video":
        clip = record_video(udid, duration_ms, work / "capture.mp4")
        frames = decode_frames(clip, fps, frames_dir)
        times = [i * 1000.0 / fps for i in range(len(frames))]
        try:
            clip.unlink()
        except OSError:
            pass
    else:
        frames, times = sample_screenshots(udid, frames_dir, duration_ms, fps)

    analysis = analyze_frames(
        frames, times,
        roi=roi,
        mask_regions=mask_regions,
        reference_size=reference_size,
        representative_dir=work / "states",
        requested_duration_ms=float(duration_ms),
        **analysis_kwargs,
    )

    if not keep_frames:
        shutil.rmtree(frames_dir, ignore_errors=True)

    warnings = list(analysis["warnings"])
    if resolved == "screenshots":
        warnings.append(
            f"captured by polling simctl screenshots at {analysis['effective_fps']} fps "
            "(ffmpeg absent). Adequate for liveness; too slow to resolve flicker or "
            "animation smoothness."
        )
    analysis["warnings"] = warnings
    analysis.update({
        "source": resolved,
        "fps": fps,
        "requested_duration_ms": duration_ms,
        "capture_id": capture_id,
        "artifacts_dir": str(work),
    })
    return analysis


def liveness_verdict(analysis: dict,
                     motion_threshold: float = _DEFAULT_MOTION_THRESHOLD) -> bool:
    """Did the UI change at all in response to the stimulus?"""
    return float(analysis.get("max_delta", 0.0)) > motion_threshold


__all__ = [
    "MotionError", "analyze_frames", "capture_motion", "cluster_states",
    "count_transitions", "decode_frames", "dhash", "find_settled_ms",
    "flicker_verdict", "frame_delta", "frame_signature", "hamming",
    "liveness_verdict", "record_video", "resolve_source", "sample_screenshots",
    "scale_rect", "signatures_match",
]