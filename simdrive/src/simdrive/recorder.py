"""Record + replay engine.

A recording is a YAML file + a snapshots/ dir. It captures every act-tool
call (tap/swipe/type_text/press_key) preceded by an observe screenshot, so
playback can compare the live screen to the recorded one (SSIM-or-fallback).
"""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from . import act, errors, observe, sim, som
from .session import Session


_RECORDINGS_ROOT_ENV = "SIMDRIVE_HOME"


def recordings_root() -> Path:
    import os
    base = os.environ.get(_RECORDINGS_ROOT_ENV) or str(Path.home() / ".simdrive")
    return Path(base) / "recordings"


@dataclass
class Recorder:
    name: str
    session: Session
    root: Path  # the recording directory (root/<name>/)
    steps: list[dict] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)

    @property
    def yaml_path(self) -> Path:
        return self.root / "recording.yaml"

    @property
    def snapshots_dir(self) -> Path:
        return self.root / "snapshots"

    def add_step(self, action: str, args: dict[str, Any], pre_screenshot: Path, post_screenshot: Path) -> int:
        idx = len(self.steps) + 1
        # Move pre/post snapshots into the recording dir for self-containment.
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        pre_dst = self.snapshots_dir / f"{idx:03d}_pre.png"
        post_dst = self.snapshots_dir / f"{idx:03d}_post.png"
        shutil.copy2(pre_screenshot, pre_dst)
        shutil.copy2(post_screenshot, post_dst)
        self.steps.append(
            {
                "id": idx,
                "action": action,
                "args": args,
                "pre_screenshot": f"snapshots/{pre_dst.name}",
                "post_screenshot": f"snapshots/{post_dst.name}",
                "captured_at": time.time(),
            }
        )
        return idx

    def finalize(self) -> Path:
        from . import __version__
        self.root.mkdir(parents=True, exist_ok=True)
        screenshot_size: Optional[list[int]] = None
        if self.session.last_screenshot_w and self.session.last_screenshot_h:
            screenshot_size = [self.session.last_screenshot_w, self.session.last_screenshot_h]
        payload = {
            "name": self.name,
            "created_at": self.started_at,
            "device": self.session.device.name,
            "os_version": self.session.device.os_version,
            "app_bundle_id": self.session.app_bundle_id,
            "simdrive_version": __version__,
            "created_by_session": self.session.session_id,
            "screenshot_size_pixels": screenshot_size,
            "tags": list(self.tags),
            "steps": self.steps,
        }
        with self.yaml_path.open("w") as f:
            yaml.safe_dump(payload, f, sort_keys=False)
        return self.yaml_path


def start(session: Session, name: str, tags: Optional[list[str]] = None) -> Recorder:
    if session.recorder is not None:
        raise errors.already_recording(session.session_id, session.recorder.name)
    root = recordings_root() / name
    if root.exists():
        # Overwrite with timestamped suffix to avoid silent collision.
        root = recordings_root() / f"{name}-{int(time.time())}"
    root.mkdir(parents=True, exist_ok=True)
    rec = Recorder(name=name, session=session, root=root, tags=list(tags or []))
    session.recorder = rec
    return rec


def stop(session: Session) -> Path:
    if session.recorder is None:
        raise errors.not_recording(session.session_id)
    rec = session.recorder
    yaml_path = rec.finalize()
    session.recorder = None
    return yaml_path


# ---------- Replay ---------- #


MaskRect = tuple[int, int, int, int]


def _normalize_masks(raw: Any) -> Optional[list[MaskRect]]:
    if not raw:
        return None
    out: list[MaskRect] = []
    for r in raw:
        if isinstance(r, dict):
            out.append((int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])))
        else:
            x, y, w, h = r[0], r[1], r[2], r[3]
            out.append((int(x), int(y), int(w), int(h)))
    return out


def _apply_masks_pil(im, masks: Optional[list[MaskRect]]):
    # Blanks each mask rectangle to constant 128 in BOTH images so the diff cancels —
    # cheaper and more robust than weighting masked regions to zero post-compare.
    if not masks:
        return im
    from PIL import ImageDraw
    out = im.copy()
    draw = ImageDraw.Draw(out)
    for x, y, w, h in masks:
        draw.rectangle([x, y, x + w, y + h], fill=128)
    return out


def _ssim_or_fallback(a: Path, b: Path, masks: Optional[list[MaskRect]] = None) -> float:
    """Return a similarity score in [0, 1].

    Uses skimage SSIM if available (strictly better at detecting structural
    changes); otherwise falls back to a perceptual-hash–style block-difference
    metric. Both metrics yield ~1.0 for identical screens and drop sharply for
    visually different ones.
    """
    try:
        from skimage.metrics import structural_similarity as ssim  # type: ignore
        from PIL import Image
        import numpy as np  # type: ignore

        ima_pil = Image.open(a).convert("L")
        imb_pil = Image.open(b).convert("L")
        if imb_pil.size != ima_pil.size:
            imb_pil = imb_pil.resize(ima_pil.size)
        ima_pil = _apply_masks_pil(ima_pil, masks)
        imb_pil = _apply_masks_pil(imb_pil, masks)
        ima = np.array(ima_pil)
        imb = np.array(imb_pil)
        score, _ = ssim(ima, imb, full=True)
        return float(score)
    except Exception:
        return _block_similarity(a, b, masks=masks)


def _block_similarity(a: Path, b: Path, grid: int = 32, threshold: int = 8,
                      masks: Optional[list[MaskRect]] = None) -> float:
    """Block-average perceptual similarity, no numpy required.

    Downsamples both images to grid×grid grayscale, then counts blocks that
    differ by more than `threshold` (out of 255). Returns the *fraction of
    matching blocks* — 1.0 = identical, ~0.5 = totally different. Much more
    discriminating than mean-abs-diff for "is this the same screen."
    """
    from PIL import Image
    ima_full = Image.open(a).convert("L")
    imb_full = Image.open(b).convert("L")
    if imb_full.size != ima_full.size:
        imb_full = imb_full.resize(ima_full.size)
    # Mask at full resolution so the rectangle pixels are the same in both before
    # the downsample averages them with surroundings.
    ima_full = _apply_masks_pil(ima_full, masks)
    imb_full = _apply_masks_pil(imb_full, masks)
    ima = ima_full.resize((grid, grid))
    imb = imb_full.resize((grid, grid))
    pa, pb = ima.tobytes(), imb.tobytes()
    matches = sum(1 for x, y in zip(pa, pb) if abs(x - y) <= threshold)
    return matches / float(grid * grid)


def replay(name: str, session: Session, on_drift: str = "halt",
           drift_threshold: float = 0.85,
           mask_regions: Optional[list] = None) -> dict:
    """Replay a recording against the current session.

    on_drift ∈ {"halt", "warn", "force"} controls what happens when the live
    screenshot's similarity to the recorded pre-screenshot is below threshold.

    mask_regions: list of (x, y, w, h) tuples or {x,y,w,h} dicts to exclude
    from the similarity compute (e.g. status-bar clock). When None, falls back
    to the YAML's `ssim_masks` field if present.
    """
    if on_drift not in {"halt", "warn", "force"}:
        raise ValueError("on_drift must be halt|warn|force")

    rec_dir = recordings_root() / name
    yaml_path = rec_dir / "recording.yaml"
    if not yaml_path.exists():
        raise errors.recording_not_found(name, str(yaml_path))
    payload = yaml.safe_load(yaml_path.read_text())
    steps = payload.get("steps", [])

    masks = _normalize_masks(mask_regions)
    if masks is None:
        masks = _normalize_masks(payload.get("ssim_masks"))

    steps_planned = len(steps)
    results: list[dict] = []
    for step in steps:
        live = observe.observe(session.device.udid, session.workdir / "replay")
        rec_pre = rec_dir / step["pre_screenshot"]
        score = _ssim_or_fallback(live.screenshot_path, rec_pre, masks=masks)
        drifted = score < drift_threshold

        step_result = {
            "id": step["id"],
            "action": step["action"],
            "similarity": round(score, 4),
            "drifted": drifted,
            "executed": False,
            "error": None,
        }

        if drifted and on_drift == "halt":
            step_result["error"] = f"drift {score:.3f} < {drift_threshold}; halted"
            results.append(step_result)
            return {
                "ok": False,
                "halted_at": step["id"],
                "halt_reason": "drift",
                "threshold": drift_threshold,
                "steps_planned": steps_planned,
                "steps": results,
            }

        try:
            _execute_step(step, session, live_observation=live)
            step_result["executed"] = True
        except Exception as exc:
            step_result["error"] = str(exc)
            results.append(step_result)
            return {
                "ok": False,
                "halted_at": step["id"],
                "halt_reason": "execute_error",
                "threshold": drift_threshold,
                "steps_planned": steps_planned,
                "steps": results,
            }

        results.append(step_result)

    return {
        "ok": True,
        "halted_at": None,
        "halt_reason": None,
        "threshold": drift_threshold,
        "steps_planned": steps_planned,
        "steps": results,
    }


def _execute_step(step: dict, session: Session, live_observation: Optional[observe.Observation] = None) -> None:
    action = step["action"]
    args = step.get("args", {})
    udid = session.device.udid
    if action == "tap":
        # Prefer stable_id resolution against the live screen so a 1px layout shift
        # doesn't silently mistap. Fall back through stable_id_loose, then to the
        # recorded pixel coords + screenshot dims.
        if live_observation is not None:
            stable_id = args.get("stable_id")
            if stable_id:
                live_mark = som.find_by_stable_id(live_observation.marks, stable_id)
                if live_mark is not None:
                    cx, cy = live_mark.center
                    act.tap(cx, cy, live_observation.screenshot_w, live_observation.screenshot_h, udid=udid)
                    return
            stable_id_loose = args.get("stable_id_loose")
            if stable_id_loose:
                live_mark = som.find_by_stable_id_loose(live_observation.marks, stable_id_loose)
                if live_mark is not None:
                    cx, cy = live_mark.center
                    act.tap(cx, cy, live_observation.screenshot_w, live_observation.screenshot_h, udid=udid)
                    return
        act.tap(args["x"], args["y"], args["screenshot_w"], args["screenshot_h"], udid=udid)
    elif action == "swipe":
        act.swipe(
            args["x1"], args["y1"], args["x2"], args["y2"],
            args["screenshot_w"], args["screenshot_h"],
            args.get("duration_ms", 300),
            udid=udid,
        )
    elif action == "type_text":
        act.type_text(args["text"], udid=udid)
    elif action == "press_key":
        act.press_key(args["key"], udid=udid)
    else:
        raise ValueError(f"unsupported replay action: {action}")
