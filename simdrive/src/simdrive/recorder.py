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

from . import act, observe, sim
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

    @property
    def yaml_path(self) -> Path:
        return self.root / "recording.yaml"

    @property
    def snapshots_dir(self) -> Path:
        return self.root / "snapshots"

    def add_step(self, action: str, args: dict[str, Any], pre_screenshot: Path, post_screenshot: Path) -> None:
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

    def finalize(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "name": self.name,
            "created_at": self.started_at,
            "device": self.session.device.name,
            "os_version": self.session.device.os_version,
            "app_bundle_id": self.session.app_bundle_id,
            "steps": self.steps,
        }
        with self.yaml_path.open("w") as f:
            yaml.safe_dump(payload, f, sort_keys=False)
        return self.yaml_path


def start(session: Session, name: str) -> Recorder:
    if session.recorder is not None:
        raise RuntimeError(f"session {session.session_id} already recording {session.recorder.name!r}")
    root = recordings_root() / name
    if root.exists():
        # Overwrite with timestamped suffix to avoid silent collision.
        root = recordings_root() / f"{name}-{int(time.time())}"
    root.mkdir(parents=True, exist_ok=True)
    rec = Recorder(name=name, session=session, root=root)
    session.recorder = rec
    return rec


def stop(session: Session) -> Path:
    if session.recorder is None:
        raise RuntimeError(f"session {session.session_id} is not recording")
    rec = session.recorder
    yaml_path = rec.finalize()
    session.recorder = None
    return yaml_path


# ---------- Replay ---------- #


def _ssim_or_fallback(a: Path, b: Path) -> float:
    """Return a similarity score in [0, 1]. Uses skimage SSIM if available,
    otherwise falls back to a coarse pixel-mean diff so replay still works."""
    try:
        from skimage.metrics import structural_similarity as ssim  # type: ignore
        from PIL import Image
        import numpy as np  # type: ignore

        ima = np.array(Image.open(a).convert("L"))
        imb = np.array(Image.open(b).convert("L"))
        if ima.shape != imb.shape:
            from PIL import Image as _Im
            imb = np.array(_Im.open(b).convert("L").resize(ima.shape[::-1]))
        score, _ = ssim(ima, imb, full=True)
        return float(score)
    except Exception:
        # Cheap fallback: 1 - normalized mean absolute diff
        from PIL import Image
        ima = Image.open(a).convert("L")
        imb = Image.open(b).convert("L").resize(ima.size)
        pa = ima.tobytes()
        pb = imb.tobytes()
        n = len(pa)
        if n == 0:
            return 0.0
        diff = sum(abs(x - y) for x, y in zip(pa, pb)) / n
        return max(0.0, 1.0 - diff / 255.0)


def replay(name: str, session: Session, on_drift: str = "halt", drift_threshold: float = 0.85) -> dict:
    """Replay a recording against the current session.

    on_drift ∈ {"halt", "warn", "force"} controls what happens when the live
    screenshot's similarity to the recorded pre-screenshot is below threshold.
    """
    if on_drift not in {"halt", "warn", "force"}:
        raise ValueError("on_drift must be halt|warn|force")

    rec_dir = recordings_root() / name
    yaml_path = rec_dir / "recording.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"recording not found: {yaml_path}")
    payload = yaml.safe_load(yaml_path.read_text())
    steps = payload.get("steps", [])

    results: list[dict] = []
    for step in steps:
        live = observe.observe(session.device.udid, session.workdir / "replay")
        rec_pre = rec_dir / step["pre_screenshot"]
        score = _ssim_or_fallback(live.screenshot_path, rec_pre)
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
            return {"ok": False, "halted_at": step["id"], "steps": results}

        try:
            _execute_step(step, session)
            step_result["executed"] = True
        except Exception as exc:
            step_result["error"] = str(exc)
            results.append(step_result)
            return {"ok": False, "halted_at": step["id"], "steps": results}

        results.append(step_result)

    return {"ok": True, "steps": results}


def _execute_step(step: dict, session: Session) -> None:
    action = step["action"]
    args = step.get("args", {})
    if action == "tap":
        act.tap(args["x"], args["y"], args["screenshot_w"], args["screenshot_h"])
    elif action == "swipe":
        act.swipe(
            args["x1"], args["y1"], args["x2"], args["y2"],
            args["screenshot_w"], args["screenshot_h"],
            args.get("duration_ms", 300),
        )
    elif action == "type_text":
        act.type_text(args["text"])
    elif action == "press_key":
        act.press_key(args["key"])
    else:
        raise ValueError(f"unsupported replay action: {action}")
