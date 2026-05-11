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
from .observability.logger import get_logger
from .session import Session

log = get_logger("simdrive.recorder")

_RECORDINGS_ROOT_ENV = "SIMDRIVE_HOME"


def recordings_root() -> Path:
    import os
    base = os.environ.get(_RECORDINGS_ROOT_ENV) or str(Path.home() / ".simdrive")
    return Path(base) / "recordings"


# ---------- State contract (a9.0) ---------- #
#
# Captured automatically at record_start, verified at replay step -1. Halts
# replay on mismatch so a divergent app state (e.g. a permission alert sitting
# in front of the recorded UI) can't silently re-execute taps into the wrong
# targets. See simdrive/docs/FEATURE_REQUEST_STATE_CONTRACT_2026_05_11.md.


_VERSION_MATCH_MODES = {"exact", "minor", "major", "any"}


@dataclass
class AppRequires:
    bundle_id: Optional[str] = None
    version: Optional[str] = None
    version_match: str = "minor"   # exact | minor | major | any

    def to_dict(self) -> dict:
        return {
            "bundle_id": self.bundle_id,
            "version": self.version,
            "version_match": self.version_match,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "AppRequires":
        if not isinstance(d, dict):
            return cls()
        vm = d.get("version_match", "minor")
        if vm not in _VERSION_MATCH_MODES:
            vm = "minor"
        return cls(
            bundle_id=d.get("bundle_id"),
            version=d.get("version"),
            version_match=vm,
        )


@dataclass
class SimRequires:
    device: Optional[str] = None
    ios_version: Optional[str] = None   # raw or predicate like ">=18.0"

    def to_dict(self) -> dict:
        return {"device": self.device, "ios_version": self.ios_version}

    @classmethod
    def from_dict(cls, d: Any) -> "SimRequires":
        if not isinstance(d, dict):
            return cls()
        return cls(device=d.get("device"), ios_version=d.get("ios_version"))


@dataclass
class InitialStateRequires:
    foreground: bool = True
    text_subset_required: list[str] = field(default_factory=list)
    text_subset_forbidden: list[str] = field(default_factory=list)
    primary_button_label: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "foreground": self.foreground,
            "text_subset_required": list(self.text_subset_required),
            "text_subset_forbidden": list(self.text_subset_forbidden),
            "primary_button_label": self.primary_button_label,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "InitialStateRequires":
        if not isinstance(d, dict):
            return cls()
        return cls(
            foreground=bool(d.get("foreground", True)),
            text_subset_required=list(d.get("text_subset_required") or []),
            text_subset_forbidden=list(d.get("text_subset_forbidden") or []),
            primary_button_label=d.get("primary_button_label"),
        )


@dataclass
class RequiresBlock:
    app: AppRequires
    sim: SimRequires
    initial_state: InitialStateRequires

    def to_dict(self) -> dict:
        return {
            "app": self.app.to_dict(),
            "sim": self.sim.to_dict(),
            "initial_state": self.initial_state.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: Any) -> Optional["RequiresBlock"]:
        # Forgiving load: anything not a dict yields None so callers can branch
        # on "no contract" without exception handling.
        if not isinstance(d, dict):
            return None
        return cls(
            app=AppRequires.from_dict(d.get("app")),
            sim=SimRequires.from_dict(d.get("sim")),
            initial_state=InitialStateRequires.from_dict(d.get("initial_state")),
        )


@dataclass
class Recorder:
    name: str
    session: Session
    root: Path  # the recording directory (root/<name>/)
    steps: list[dict] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)
    requires_block: Optional[RequiresBlock] = None
    # Set when capture-time observe fails — rides along to the replay result
    # so the agent sees why the contract is missing.
    capture_warning: Optional[str] = None

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
        app_version: Optional[str] = None
        if self.session.target == "simulator" and self.session.app_bundle_id:
            try:
                app_version = sim.get_app_version(self.session.device.udid, self.session.app_bundle_id)
            except Exception:
                app_version = None
        payload = {
            "name": self.name,
            "created_at": self.started_at,
            "device": self.session.device.name,
            "os_version": self.session.device.os_version,
            "app_bundle_id": self.session.app_bundle_id,
            "app_version": app_version,
            "simdrive_version": __version__,
            "created_by_session": self.session.session_id,
            "screenshot_size_pixels": screenshot_size,
            "tags": list(self.tags),
            "steps": self.steps,
        }
        if self.requires_block is not None:
            payload["requires"] = self.requires_block.to_dict()
        with self.yaml_path.open("w") as f:
            yaml.safe_dump(payload, f, sort_keys=False)
        return self.yaml_path


def _capture_state_contract(session: Session, workdir: Path) -> tuple[Optional[RequiresBlock], Optional[str]]:
    """Observe the live screen and build a RequiresBlock for the captured state.

    Returns (block, warning). On observe failure returns (None, "reason") so the
    recording still starts — the contract just won't be verified at replay.
    """
    try:
        live = observe.observe(session.device.udid, workdir, target=session.target)
    except Exception as exc:  # pragma: no cover — exercised via degrades_gracefully test
        return None, f"Could not capture state contract at record_start: {exc}"

    marks = list(live.marks or [])
    # initial_state fields
    foreground = len(marks) > 0
    # text_subset_required: top-10 (top-to-bottom from detect_marks), confidence
    # band high|medium, text >= 2 chars
    required: list[str] = []
    for m in marks:
        if len(required) >= 10:
            break
        if getattr(m, "confidence_band", None) not in ("high", "medium"):
            continue
        text = (m.text or "").strip()
        if len(text) < 2:
            continue
        required.append(text)

    primary_label: Optional[str] = None
    if marks:
        screen_h = live.screenshot_h or 0
        upper = [m for m in marks if (m.y + m.h // 2) < (screen_h / 2 if screen_h else float("inf"))]
        pool = upper if upper else marks
        biggest = max(pool, key=lambda m: m.w * m.h)
        primary_label = (biggest.text or "").strip() or None

    block = RequiresBlock(
        app=AppRequires(
            bundle_id=session.app_bundle_id,
            version=_current_app_version(session),
            version_match="minor",
        ),
        sim=SimRequires(
            device=session.device.name,
            ios_version=session.device.os_version,
        ),
        initial_state=InitialStateRequires(
            foreground=foreground,
            text_subset_required=required,
            text_subset_forbidden=[],
            primary_button_label=primary_label,
        ),
    )
    return block, None


def _current_app_version(session: Session) -> Optional[str]:
    """Best-effort live app version for the session.

    Simulator: query simctl. Device: not implemented (would need WDA / devicectl
    plumbing that doesn't exist yet). Returns None on any failure.
    """
    if session.target != "simulator" or not session.app_bundle_id:
        return None
    try:
        return sim.get_app_version(session.device.udid, session.app_bundle_id)
    except Exception:
        return None


def start(session: Session, name: str, tags: Optional[list[str]] = None) -> Recorder:
    if session.recorder is not None:
        raise errors.already_recording(session.session_id, session.recorder.name)
    root = recordings_root() / name
    if root.exists():
        # Overwrite with timestamped suffix to avoid silent collision.
        root = recordings_root() / f"{name}-{int(time.time())}"
    root.mkdir(parents=True, exist_ok=True)
    rec = Recorder(name=name, session=session, root=root, tags=list(tags or []))
    block, warning = _capture_state_contract(session, root / "_capture")
    rec.requires_block = block
    rec.capture_warning = warning
    session.recorder = rec
    log.info("recording started", extra={"recording_name": name, "session_id": session.session_id})
    return rec


def stop(session: Session) -> Path:
    if session.recorder is None:
        raise errors.not_recording(session.session_id)
    rec = session.recorder
    log.info("recording stopping", extra={"recording_name": rec.name, "session_id": session.session_id})
    yaml_path = rec.finalize()
    session.recorder = None
    log.debug("recording finalized", extra={"yaml_path": str(yaml_path)})
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


_ALERT_TEXTS = {"don't allow", "dont allow", "allow", "ok", "cancel"}


def _split_semver_predicate(raw: str) -> tuple[str, str]:
    """Parse `">=18.0"` → (">=", "18.0"). Returns ("==", raw) when no operator."""
    raw = raw.strip()
    for op in (">=", "<=", "==", "!=", ">", "<"):
        if raw.startswith(op):
            return op, raw[len(op):].strip()
    return "==", raw


def _semver_tuple(v: str) -> tuple[int, ...]:
    parts = []
    for chunk in v.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            # Stop at the first non-numeric component (e.g. "18.0-beta")
            break
    return tuple(parts) or (0,)


def _ios_version_matches(predicate: str, actual: str) -> bool:
    op, target = _split_semver_predicate(predicate)
    if op == "==":
        # Plain equality is case-insensitive string match (covers "18.0" vs "18.0").
        if predicate.strip() == actual.strip():
            return True
    a = _semver_tuple(actual)
    t = _semver_tuple(target)
    # Pad with zeros so (18,) compares against (18, 0).
    n = max(len(a), len(t))
    a = a + (0,) * (n - len(a))
    t = t + (0,) * (n - len(t))
    if op == "==":
        return a == t
    if op == "!=":
        return a != t
    if op == ">=":
        return a >= t
    if op == "<=":
        return a <= t
    if op == ">":
        return a > t
    if op == "<":
        return a < t
    return False


def _version_matches(mode: str, expected: Optional[str], actual: Optional[str]) -> bool:
    if mode == "any" or expected is None:
        return True
    if actual is None:
        return False
    if mode == "exact":
        return expected == actual
    a = _semver_tuple(actual)
    e = _semver_tuple(expected)
    if mode == "major":
        return a[:1] == e[:1]
    # default "minor"
    a2 = a + (0,) * max(0, 2 - len(a))
    e2 = e + (0,) * max(0, 2 - len(e))
    return a2[:2] == e2[:2]


def _verify_state_contract(session: Session, block: RequiresBlock,
                           workdir: Path) -> tuple[bool, Optional[dict]]:
    """Check the live state against the recorded contract.

    Returns (True, None) on full match; (False, mismatch_dict) when any
    constraint fails. The mismatch_dict carries `expected`, `actual`, and a
    `remedy` hint.
    """
    expected: dict[str, Any] = {}
    actual: dict[str, Any] = {}
    reasons: list[str] = []

    # App bundle
    if block.app.bundle_id:
        actual_bundle = session.app_bundle_id
        expected["app.bundle_id"] = block.app.bundle_id
        actual["app.bundle_id"] = actual_bundle
        if actual_bundle != block.app.bundle_id:
            reasons.append(
                f"app.bundle_id: expected {block.app.bundle_id!r}, got {actual_bundle!r}"
            )

    # App version
    if block.app.version is not None and block.app.version_match != "any":
        live_version = _current_app_version(session)
        expected["app.version"] = f"{block.app.version} (match: {block.app.version_match})"
        actual["app.version"] = live_version
        if not _version_matches(block.app.version_match, block.app.version, live_version):
            reasons.append(
                f"app.version: expected {block.app.version} ({block.app.version_match}), got {live_version}"
            )

    # Sim device
    if block.sim.device:
        live_device = session.device.name or ""
        expected["sim.device"] = block.sim.device
        actual["sim.device"] = live_device
        if live_device.lower() != block.sim.device.lower():
            reasons.append(f"sim.device: expected {block.sim.device!r}, got {live_device!r}")

    # iOS version
    if block.sim.ios_version:
        live_os = session.device.os_version or ""
        expected["sim.ios_version"] = block.sim.ios_version
        actual["sim.ios_version"] = live_os
        if not _ios_version_matches(block.sim.ios_version, live_os):
            reasons.append(
                f"sim.ios_version: expected {block.sim.ios_version}, got {live_os!r}"
            )

    # Observe live state for the initial_state checks
    try:
        live = observe.observe(session.device.udid, workdir, target=session.target)
        live_marks = list(live.marks or [])
    except Exception as exc:
        return False, {
            "expected": expected,
            "actual": actual,
            "reasons": reasons + [f"observe failed: {exc}"],
            "remedy": "Could not observe live state. Verify the simulator/device is reachable.",
        }

    live_texts = [(m.text or "") for m in live_marks]

    if block.initial_state.foreground:
        expected["initial_state.foreground"] = True
        actual["initial_state.foreground"] = len(live_marks) > 0
        if not live_marks:
            reasons.append("initial_state.foreground: expected app on screen, got empty observation")

    required = block.initial_state.text_subset_required
    if required:
        missing = [t for t in required if not any(t in lt for lt in live_texts)]
        expected["initial_state.text_subset_required"] = list(required)
        actual["initial_state.text_subset_present"] = [t for t in required if t not in missing]
        if missing:
            reasons.append(f"initial_state.text_subset_required missing: {missing}")

    forbidden = block.initial_state.text_subset_forbidden
    if forbidden:
        present = [t for t in forbidden if any(t in lt for lt in live_texts)]
        expected["initial_state.text_subset_forbidden"] = list(forbidden)
        actual["initial_state.text_subset_present_forbidden"] = present
        if present:
            reasons.append(f"initial_state.text_subset_forbidden present: {present}")

    if block.initial_state.primary_button_label:
        label = block.initial_state.primary_button_label
        expected["initial_state.primary_button_label"] = label
        # v1 relaxation: accept any band so we don't false-halt when OCR
        # downgrades the same text from high → medium between sessions.
        present = any(label in (m.text or "") for m in live_marks)
        actual["initial_state.primary_button_present"] = present
        if not present:
            reasons.append(f"initial_state.primary_button_label {label!r} not seen on screen")

    if not reasons:
        return True, None

    # Remedy: alert-shaped if any forbidden text looks like an alert button,
    # OR any live mark text is an alert button label.
    alert_shaped = any(
        (t or "").strip().lower() in _ALERT_TEXTS for t in (forbidden or [])
    ) or any(
        (m.text or "").strip().lower() in _ALERT_TEXTS for m in live_marks
    )
    if alert_shaped:
        remedy = (
            "App appears to be showing a permission alert. Pre-grant via "
            "`xcrun simctl privacy <udid> grant ...` before launching, then retry."
        )
    else:
        remedy = (
            "State at replay-start differs from capture-time. Reset the app to "
            "its captured state, or re-record."
        )

    return False, {
        "expected": expected,
        "actual": actual,
        "reasons": reasons,
        "remedy": remedy,
    }


def replay(name: str, session: Session, on_drift: str = "halt",
           drift_threshold: float = 0.85,
           mask_regions: Optional[list] = None,
           halt_on_state_mismatch: bool = True) -> dict:
    """Replay a recording against the current session.

    on_drift ∈ {"halt", "warn", "force"} controls what happens when the live
    screenshot's similarity to the recorded pre-screenshot is below threshold.

    mask_regions: list of (x, y, w, h) tuples or {x,y,w,h} dicts to exclude
    from the similarity compute (e.g. status-bar clock). When None, falls back
    to the YAML's `ssim_masks` field if present.

    halt_on_state_mismatch (a9.0): when True (default), verify the live state
    against the recording's `requires:` block before step 1. Halt with
    halt_reason="state_contract_mismatch" on any failure. When False, mismatch
    is reported via `_simdrive_warning` and replay proceeds.
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

    # State-contract verification (a9.0) — happens BEFORE any step executes.
    requires_block = RequiresBlock.from_dict(payload.get("requires"))
    state_warning: Optional[str] = None
    steps_planned = len(steps)
    if requires_block is None:
        state_warning = (
            "Recording has no `requires:` block. State contract not verified. "
            "Run `simdrive migrate-recording " + name + "` to capture one."
        )
    else:
        ok, mismatch = _verify_state_contract(session, requires_block,
                                              session.workdir / "replay")
        if not ok:
            summary = "; ".join(mismatch.get("reasons", []) or [])[:300]
            if halt_on_state_mismatch:
                return {
                    "ok": False,
                    "halted_at": 0,
                    "halt_reason": "state_contract_mismatch",
                    "threshold": drift_threshold,
                    "steps_planned": steps_planned,
                    "steps": [],
                    "expected": mismatch["expected"],
                    "actual": mismatch["actual"],
                    "reasons": mismatch.get("reasons", []),
                    "remedy": mismatch["remedy"],
                }
            state_warning = f"state_contract_mismatch: {summary}"

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
            out = {
                "ok": False,
                "halted_at": step["id"],
                "halt_reason": "drift",
                "threshold": drift_threshold,
                "steps_planned": steps_planned,
                "steps": results,
            }
            if state_warning:
                out["_simdrive_warning"] = state_warning
            return out

        try:
            _execute_step(step, session, live_observation=live)
            step_result["executed"] = True
        except Exception as exc:
            step_result["error"] = str(exc)
            results.append(step_result)
            out = {
                "ok": False,
                "halted_at": step["id"],
                "halt_reason": "execute_error",
                "threshold": drift_threshold,
                "steps_planned": steps_planned,
                "steps": results,
            }
            if state_warning:
                out["_simdrive_warning"] = state_warning
            return out

        results.append(step_result)

    out = {
        "ok": True,
        "halted_at": None,
        "halt_reason": None,
        "threshold": drift_threshold,
        "steps_planned": steps_planned,
        "steps": results,
    }
    if state_warning:
        out["_simdrive_warning"] = state_warning
    return out


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
