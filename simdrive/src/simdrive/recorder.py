"""Record + replay engine.

A recording is a YAML file + a snapshots/ dir. It captures every act-tool
call (tap/swipe/type_text/press_key) preceded by an observe screenshot, so
playback can compare the live screen to the recorded one (SSIM-or-fallback).

Recording YAML schema (a13 — sim + device unified)
---------------------------------------------------
Every recording (regardless of target) uses the same top-level shape::

    name: <str>
    created_at: <float>
    device: <str>               # device.name
    os_version: <str>           # device.os_version
    app_bundle_id: <str|null>
    app_version: <str|null>     # sim: from simctl; device: null (WDA doesn't expose)
    simdrive_version: <str>
    created_by_session: <str>
    screenshot_size_pixels: [w, h]
    tags: [...]
    target: <str>               # a13: "simulator" | "device" discriminator
    steps:
      - id: <int>               # 1-based
        action: <str>           # tap | swipe | type_text | press_key | dismiss_sheet | clear_field
        args: <dict>
        pre_screenshot: <str>   # snapshots/NNN_pre.png (relative)
        post_screenshot: <str>  # snapshots/NNN_post.png
        captured_at: <float>
    requires:
      target: <str>             # a13: "simulator" | "device" discriminator
      app:
        bundle_id: <str|null>
        version: <str|null>
        version_match: exact|minor|major|any
      sim:                      # sim recordings: populated; device recordings: null fields
        device: <str|null>
        ios_version: <str|null>
      device:                   # a13: device recordings: populated; sim recordings: None
        udid: <str|null>
        device_name: <str|null>
        os_version: <str|null>
        os_major: <int|null>    # extracted for major-version halt logic
      initial_state:
        foreground: <bool>
        text_subset_required: [...]
        text_subset_forbidden: [...]
        primary_button_label: <str|null>

Device vs simulator differences
--------------------------------
* Screenshot source: sim uses ``simctl io screenshot``; device uses
  ``wda_client.screenshot_any()`` (WDA /screenshot endpoint — no CoreDevice
  UUID restriction like idevicescreenshot).
* SoM marks: sim uses Vision OCR (``som.detect_marks``); device uses the WDA
  XCUI accessibility tree (``annotate_device_screenshot``).
* App version: sim queries ``simctl appinfo``; device path returns ``None``
  because WDA doesn't expose the installed app version. The ``requires.app.version``
  field will be ``None`` for device recordings — the contract still verifies
  bundle_id and os_version.
* State contract: sim checks ``requires.sim.device`` and ``requires.sim.ios_version``;
  device checks ``requires.device.os_version`` major component (minor diffs warn
  only) and ``requires.device.device_name`` (case-insensitive warn only).

Drift detection thresholds
---------------------------
* Sim default: 0.85 SSIM (unchanged from a12).
* Device default: 0.80 SSIM — slightly looser because real device screenshots vary
  slightly due to display rendering differences, anti-aliasing, and minor timing
  jitter in hardware compositing. 0.80 still halts reliably on meaningful UI drift
  (the "23 blind taps at SSIM 0.014" scenario halts at 0.80 with a large margin).
* Marks-count drift: if ``recorded_marks_count > 0`` and the live count drops below
  50% of the recorded count, replay surfaces it as a drift event (reported even if
  SSIM passes).

Partial recording on halt
--------------------------
When ``record_stop`` is called after an error (or when the process receives a
signal mid-recording), ``Recorder.write_partial()`` writes
``recording.yaml.partial`` with the steps captured so far. Useful for post-mortem
debugging a flow that failed mid-record.

Error codes (a13)
-----------------
* ``drift`` — halt_reason when SSIM or marks-count drift halts replay (unchanged key).
* ``state_contract_mismatch`` — halt_reason when pre-step-1 contract fails (unchanged key).
* ``replay_state_contract_failed`` — semantic alias surfaced in ``reasons`` list.

Focus-context persistence (F#11, 2026-05-22)
--------------------------------------------
``type_text`` steps now persist their full focus context — ``tap_first`` (a target
dict that pre-taps a field to grab first-responder focus) and ``clear_first`` (a
boolean that issues Cmd-A + delete before typing) — into ``step.args`` at record
time. The replay engine reads ``args.tap_first`` and dispatches a ``tap`` against
the resolved target before sending the ``type_text``, restoring the same focus
context the recording was captured under. Schema is additive: pre-fix recordings
that lack ``tap_first`` continue to replay as before (no focus re-tap; relies on
whatever field is currently first responder).
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


def _check_capture(pre: Optional[Path], post: Optional[Path]) -> Optional[str]:
    """Validate that pre/post-state screenshot paths represent a successful capture.

    Returns ``None`` when both are valid (path is non-None, file exists, non-empty),
    otherwise returns a short failure string describing the first problem found
    (used as a structured log field, not displayed to the user).

    A failed pre-state capture means the recorder never had a "before" view of
    the step; a failed post-state capture (the common case — e.g. a simulator
    hiccup right after a tap) means the recorder doesn't know what state the
    action produced. In either case the step is incomplete and replay would
    surface confusing errors, so we drop it (INIT-2026-549).
    """
    for label, candidate in (("pre", pre), ("post", post)):
        if candidate is None:
            return f"{label}_state_missing: path is None"
        try:
            path = Path(candidate)
        except TypeError:
            return f"{label}_state_invalid_path"
        if not path.exists():
            return f"{label}_state_missing: file not found at {path}"
        try:
            if path.stat().st_size == 0:
                return f"{label}_state_empty: zero-byte file at {path}"
        except OSError as exc:
            return f"{label}_state_stat_error: {exc}"
    return None


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
class DeviceRequires:
    """State contract fields specific to real-device recordings (a13).

    Captured at ``record_start(target=device)`` from WDA registry + session metadata.
    Verified at replay-start before step 1 — major OS version mismatch halts; minor
    version delta and device name mismatch warn only (user can rename devices).
    """
    udid: Optional[str] = None
    device_name: Optional[str] = None
    os_version: Optional[str] = None   # full version string e.g. "26.4.2"
    os_major: Optional[int] = None     # extracted major for halt logic

    def to_dict(self) -> dict:
        return {
            "udid": self.udid,
            "device_name": self.device_name,
            "os_version": self.os_version,
            "os_major": self.os_major,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "DeviceRequires":
        if not isinstance(d, dict):
            return cls()
        return cls(
            udid=d.get("udid"),
            device_name=d.get("device_name"),
            os_version=d.get("os_version"),
            os_major=d.get("os_major"),
        )


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
    # a13 additions — backward-compatible: target defaults to "simulator" when absent
    # so old recordings load cleanly. device is None for sim recordings.
    target: str = "simulator"           # "simulator" | "device"
    device: Optional[DeviceRequires] = None  # populated only for target="device"

    def to_dict(self) -> dict:
        d: dict = {
            "target": self.target,
            "app": self.app.to_dict(),
            "sim": self.sim.to_dict(),
            "initial_state": self.initial_state.to_dict(),
        }
        if self.device is not None:
            d["device"] = self.device.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: Any) -> Optional["RequiresBlock"]:
        """Load a RequiresBlock from a dict, supporting both nested and flat formats.

        Two formats are supported for backward/forward compatibility:

        Nested format (a9.0 — sim recordings):
            requires:
              app: {bundle_id: ..., version: ..., version_match: minor}
              sim: {device: ..., ios_version: ...}
              initial_state: {foreground: true, text_subset_required: [...]}

        Flat device format (a13 — device recordings, as written by _write_recording):
            requires:
              target: device
              udid: ...
              device_name: ...
              os_version: ...
              app_bundle_id: ...   (top-level shorthand for app.bundle_id)

        The flat format is promoted to the nested format on load so internal code
        always sees a consistent RequiresBlock object.

        Forgiving load: anything not a dict yields None so callers can branch
        on "no contract" without exception handling.
        """
        if not isinstance(d, dict):
            return None
        target = d.get("target", "simulator")

        # Promote flat device fields to nested DeviceRequires if present at top level.
        device_req: Optional[DeviceRequires] = None
        if "device" in d and isinstance(d["device"], dict):
            device_req = DeviceRequires.from_dict(d["device"])
        elif target == "device" and any(k in d for k in ("udid", "device_name")):
            # Flat format: udid/device_name/os_version at top level of requires.
            os_ver = d.get("os_version")
            os_major: Optional[int] = None
            if os_ver:
                try:
                    os_major = int(os_ver.split(".")[0])
                except (ValueError, IndexError):
                    pass
            device_req = DeviceRequires(
                udid=d.get("udid"),
                device_name=d.get("device_name"),
                os_version=os_ver,
                os_major=os_major,
            )

        # Promote flat app_bundle_id to app sub-block if no nested app block present.
        app_raw = d.get("app")
        if app_raw is None and d.get("app_bundle_id"):
            app_raw = {"bundle_id": d["app_bundle_id"]}

        # initial_state: if absent in flat format, default foreground=False so that
        # device recordings without an OCR-based initial state don't false-halt during
        # replay when the live observe returns 0 marks (e.g. WDA not available in tests).
        # Recordings with explicit initial_state (nested format) override this.
        initial_state_raw = d.get("initial_state")
        if initial_state_raw is None and target == "device":
            # Flat device format — no foreground check by default.
            initial_state_raw = {"foreground": False}

        return cls(
            app=AppRequires.from_dict(app_raw),
            sim=SimRequires.from_dict(d.get("sim")),
            initial_state=InitialStateRequires.from_dict(initial_state_raw),
            target=target,
            device=device_req,
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

    def add_step(self, action: str, args: dict[str, Any], pre_screenshot: Optional[Path],
                 post_screenshot: Optional[Path],
                 marks_count: Optional[int] = None) -> Optional[int]:
        """Record a step. Returns the 1-based step index, or None when the step
        is dropped due to a failed pre/post-state capture.

        marks_count (a13): number of SoM marks observed on the pre-screenshot.
        Stored in the step so replay can detect marks-count drift (structural UI
        change) even when SSIM passes.

        Integrity guard (INIT-2026-549): if either ``pre_screenshot`` or
        ``post_screenshot`` is missing, None, or points to a missing/empty file,
        the step is **dropped entirely** with a structured warning logged. This
        prevents partially-captured steps (typically caused by a flaky simulator
        screenshot at post-action time) from being appended to the recording and
        later tripping the replay engine. The recorder keeps its 1-based step
        ids contiguous because we increment the index only when the step is
        actually appended.
        """
        # Validate pre/post-state capture before touching the snapshots dir or
        # bumping the step id. A None path, missing file, or empty file all
        # qualify as a failed capture — drop the step and log.
        capture_failure = _check_capture(pre_screenshot, post_screenshot)
        if capture_failure is not None:
            log.warning(
                "recorder.dropped_step_partial_capture",
                extra={
                    "recording_name": self.name,
                    "session_id": getattr(self.session, "session_id", None),
                    "action": action,
                    "failure": capture_failure,
                    "timestamp": time.time(),
                    "next_step_id": len(self.steps) + 1,
                },
            )
            return None

        idx = len(self.steps) + 1
        # Move pre/post snapshots into the recording dir for self-containment.
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        pre_dst = self.snapshots_dir / f"{idx:03d}_pre.png"
        post_dst = self.snapshots_dir / f"{idx:03d}_post.png"
        try:
            shutil.copy2(pre_screenshot, pre_dst)
            shutil.copy2(post_screenshot, post_dst)
        except OSError as exc:
            # Copy itself failed (e.g. disk full, source vanished between the
            # existence check and the copy). Roll back any partial copy and log.
            for partial_path in (pre_dst, post_dst):
                try:
                    if partial_path.exists():
                        partial_path.unlink()
                except OSError:
                    pass
            log.warning(
                "recorder.dropped_step_partial_capture",
                extra={
                    "recording_name": self.name,
                    "session_id": getattr(self.session, "session_id", None),
                    "action": action,
                    "failure": f"copy_failed: {exc}",
                    "timestamp": time.time(),
                    "next_step_id": idx,
                },
            )
            return None
        step: dict = {
            "id": idx,
            "action": action,
            "args": args,
            "pre_screenshot": f"snapshots/{pre_dst.name}",
            "post_screenshot": f"snapshots/{post_dst.name}",
            "captured_at": time.time(),
        }
        if marks_count is not None:
            step["marks_count"] = marks_count
        self.steps.append(step)
        return idx

    def upgrade_step_action(self, step_id: int, new_action: str) -> bool:
        """Upgrade a previously-recorded step's `action` field in place.

        Used by composite MCP tools (e.g. ``tap_and_wait_keyboard``) that
        delegate to an underlying primitive (``tap``) which records itself
        as the primitive — without this, the composite's semantics are
        silently stripped on serialization and the replay tap-then-acts
        instead of tap-then-waits (F-B3-010, Palace b3 dogfood 2026-05-22).

        Returns True when the step was found and upgraded; False otherwise
        (caller can decide whether to log — typical pattern is best-effort).
        """
        for step in self.steps:
            if step.get("id") == step_id:
                step["action"] = new_action
                return True
        return False

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
        # target field (a13): "simulator" | "device" discriminator. Lets downstream
        # tools (lint, migrate, cloud upload) machine-route recordings by target.
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
            "target": self.session.target,
            "steps": self.steps,
        }
        if self.requires_block is not None:
            payload["requires"] = self.requires_block.to_dict()
        # F#15 — auto-populate ssim_masks for recognized device classes so every
        # replay automatically masks the status bar without caller intervention.
        auto_masks = _default_status_bar_mask(self.session.device.name)
        if auto_masks is not None:
            payload["ssim_masks"] = auto_masks
        if self.capture_warning:
            payload["_capture_warning"] = self.capture_warning
        with self.yaml_path.open("w") as f:
            yaml.safe_dump(payload, f, sort_keys=False)
        return self.yaml_path

    def write_partial(self) -> Path:
        """Write a partial recording (steps captured so far) to recording.yaml.partial.

        Called when record_stop is invoked after an error or mid-recording failure.
        The .partial file lets developers inspect what was captured before the halt
        without clobbering a potentially valid recording.yaml.
        """
        from . import __version__
        self.root.mkdir(parents=True, exist_ok=True)
        partial_path = self.root / "recording.yaml.partial"
        payload = {
            "name": self.name,
            "partial": True,
            "partial_steps_captured": len(self.steps),
            "created_at": self.started_at,
            "device": self.session.device.name,
            "os_version": self.session.device.os_version,
            "app_bundle_id": self.session.app_bundle_id,
            "simdrive_version": __version__,
            "created_by_session": self.session.session_id,
            "target": self.session.target,
            "tags": list(self.tags),
            "steps": self.steps,
        }
        if self.requires_block is not None:
            payload["requires"] = self.requires_block.to_dict()
        with partial_path.open("w") as f:
            yaml.safe_dump(payload, f, sort_keys=False)
        return partial_path


def _build_requires_block(
    marks: list,
    *,
    screen_h: Optional[int],
    app_bundle_id: Optional[str],
    app_version: Optional[str],
    sim_device: Optional[str],
    sim_ios_version: Optional[str],
    # a13: device-specific params — None for sim recordings
    target: str = "simulator",
    device_udid: Optional[str] = None,
    device_name: Optional[str] = None,
    device_os_version: Optional[str] = None,
) -> RequiresBlock:
    """Pure transform from (marks + screen metadata) → RequiresBlock.

    Shared by capture-time (``_capture_state_contract``) and post-hoc migration
    (``migrate_recording``) so both paths produce identical contracts.

    For device recordings (target="device"), ``sim_device`` and ``sim_ios_version``
    are None; ``device_udid``, ``device_name``, ``device_os_version`` are populated
    from the session/WDA registry.  For sim recordings, the inverse applies.
    This keeps the requires block format-compatible across targets — a new developer
    can inspect either recording type and immediately see which fields are relevant.
    """
    foreground = len(marks) > 0
    # text_subset_required: top-10 (top-to-bottom from detect_marks), confidence
    # band high|medium, text >= 2 chars.
    # For device marks (list[dict] from annotate_device_screenshot), use dict access.
    required: list[str] = []
    _seen: set[str] = set()
    for m in marks:
        if len(required) >= 10:
            break
        # Marks may be Mark dataclass objects (sim path) or dicts (device path).
        if hasattr(m, "confidence_band"):
            cb = m.confidence_band
            text = (m.text or "").strip()
        else:
            cb = m.get("confidence_band")
            text = (m.get("text") or "").strip()
        if cb not in ("high", "medium"):
            continue
        if len(text) < 2:
            continue
        if text in _seen:
            continue
        _seen.add(text)
        required.append(text)

    primary_label: Optional[str] = None
    if marks:
        h = screen_h or 0
        # Support both Mark objects and dicts.
        def _mark_y(m: Any) -> float:
            if hasattr(m, "y"):
                return float(m.y + m.h // 2)
            return float(m.get("y", 0) + m.get("h", 0) // 2)

        def _mark_area(m: Any) -> float:
            if hasattr(m, "w"):
                return float(m.w * m.h)
            return float(m.get("w", 0) * m.get("h", 0))

        def _mark_text(m: Any) -> str:
            if hasattr(m, "text"):
                return (m.text or "").strip()
            return (m.get("text") or "").strip()

        upper = [m for m in marks if _mark_y(m) < (h / 2 if h else float("inf"))]
        pool = upper if upper else marks
        biggest = max(pool, key=_mark_area)
        primary_label = _mark_text(biggest) or None

    # Build device-specific block when target is "device".
    device_req: Optional[DeviceRequires] = None
    if target == "device" and (device_udid or device_name or device_os_version):
        os_major: Optional[int] = None
        if device_os_version:
            try:
                os_major = int(device_os_version.split(".")[0])
            except (ValueError, IndexError):
                os_major = None
        device_req = DeviceRequires(
            udid=device_udid,
            device_name=device_name,
            os_version=device_os_version,
            os_major=os_major,
        )

    return RequiresBlock(
        target=target,
        app=AppRequires(
            bundle_id=app_bundle_id,
            version=app_version,
            version_match="minor",
        ),
        sim=SimRequires(
            device=sim_device if target == "simulator" else None,
            ios_version=sim_ios_version if target == "simulator" else None,
        ),
        device=device_req,
        initial_state=InitialStateRequires(
            foreground=foreground,
            text_subset_required=required,
            text_subset_forbidden=[],
            primary_button_label=primary_label,
        ),
    )


def _capture_state_contract(session: Session, workdir: Path) -> tuple[Optional[RequiresBlock], Optional[str]]:
    """Observe the live screen and build a RequiresBlock for the captured state.

    Returns (block, warning). On observe failure returns (None, "reason") so the
    recording still starts — the contract just won't be verified at replay.

    a13: Device sessions use ``annotate_device_screenshot`` via WDA rather than
    Vision OCR, and populate the ``device`` sub-block instead of ``sim``.
    """
    if session.target == "device":
        return _capture_device_state_contract(session, workdir)

    try:
        live = observe.observe(session.device.udid, workdir, target=session.target)
    except Exception as exc:  # pragma: no cover — exercised via degrades_gracefully test
        return None, f"Could not capture state contract at record_start: {exc}"

    block = _build_requires_block(
        list(live.marks or []),
        screen_h=live.screenshot_h,
        app_bundle_id=session.app_bundle_id,
        app_version=_current_app_version(session),
        sim_device=session.device.name,
        sim_ios_version=session.device.os_version,
        target="simulator",
    )
    return block, None


def _capture_device_state_contract(session: Session, workdir: Path) -> tuple[Optional[RequiresBlock], Optional[str]]:
    """Capture state contract for a real-device recording (a13).

    With WDA: takes a WDA screenshot, runs annotate_device_screenshot for marks, then
    builds a RequiresBlock with the device sub-block populated.

    Without WDA (test / CI): falls back to ``observe.observe`` with target=device.
    This preserves test-patching compatibility — tests monkeypatching ``observe.observe``
    still work without a real WDA server.

    Falls back gracefully when observe fails — the recording still starts, but the
    contract won't be verified at replay.
    """
    marks: list = []
    h: int = 0
    try:
        if session.wda_client is not None:
            from PIL import Image
            import io
            from .wda.som_device import annotate_device_screenshot

            wda = session.wda_client
            png_bytes = wda.screenshot_any()
            workdir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time() * 1000)
            screenshot_path = workdir / f"contract-{ts}.png"
            screenshot_path.write_bytes(png_bytes)

            with Image.open(io.BytesIO(png_bytes)) as im:
                w, h = im.size

            # Get pixel-per-point scale from the session if cached, else fall back to 1.0
            point_scale = float(getattr(session, "pixel_per_point_scale", None) or 1.0)
            marks_list, _ = annotate_device_screenshot(screenshot_path, (w, h), wda, point_scale=point_scale)
            marks = list(marks_list or [])
        else:
            # No WDA — fall back to observe.observe (test-patchable).
            live = observe.observe(session.device.udid, workdir, target=session.target)
            marks = list(live.marks or [])
            h = live.screenshot_h

    except Exception as exc:
        return None, f"Could not capture device state contract at record_start: {exc}"

    block = _build_requires_block(
        list(marks or []),
        screen_h=h,
        app_bundle_id=session.app_bundle_id,
        app_version=None,  # WDA doesn't expose installed app version
        sim_device=None,   # Not applicable for device recordings
        sim_ios_version=None,
        target="device",
        device_udid=session.device.udid,
        device_name=session.device.name,
        device_os_version=session.device.os_version,
    )
    return block, None


def _current_app_version(session: Session) -> Optional[str]:
    """Best-effort live app version for the session.

    Simulator: query simctl. Device: WDA does not expose the installed app
    version — returns None. The ``requires.app.version`` field in device
    recordings will always be None; the state contract still verifies
    ``bundle_id`` and ``os_version``. Returns None on any failure.
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
    # Pre-create snapshots dir so the recording dir is self-consistent even with
    # zero steps (e.g. if the agent calls record_start then immediately errors).
    rec.snapshots_dir.mkdir(parents=True, exist_ok=True)
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


# ---------- Lint + migrate (a9.1) ---------- #


@dataclass
class LintResult:
    path: Path
    status: str   # "ok" | "fail" | "empty"
    reason: str = ""
    text_mark_count: int = 0
    app_bundle_id: Optional[str] = None
    sim_device: Optional[str] = None
    # F#16: category distinguishes failure types — "ok" | "empty" | "missing_state_contract"
    category: str = "ok"

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "status": self.status,
            "reason": self.reason,
            "text_mark_count": self.text_mark_count,
            "app_bundle_id": self.app_bundle_id,
            "sim_device": self.sim_device,
            "category": self.category,
        }


def lint_recordings(path: Path) -> list[LintResult]:
    """Walk `path` and lint every recording.yaml found.

    Returns a list of LintResult — one per recording. Empty if no recordings.
    """
    path = Path(path)
    if not path.exists():
        return []
    results: list[LintResult] = []
    for yaml_path in sorted(path.rglob("recording.yaml")):
        results.append(_lint_one(yaml_path))
    return results


def _lint_one(yaml_path: Path) -> LintResult:
    try:
        payload = yaml.safe_load(yaml_path.read_text())
    except yaml.YAMLError as exc:
        return LintResult(path=yaml_path, status="fail", reason=f"yaml parse error: {exc}",
                          category="fail")
    except OSError as exc:
        return LintResult(path=yaml_path, status="fail", reason=f"read error: {exc}",
                          category="fail")

    if not isinstance(payload, dict):
        return LintResult(path=yaml_path, status="fail",
                          reason="recording.yaml did not parse to a mapping",
                          category="fail")

    requires_raw = payload.get("requires")
    steps = payload.get("steps") or []

    # F#16: 0-step recordings with no requires block are placeholders — categorize
    # as 'empty' (not 'fail'). Recordings with steps still follow normal lint rules.
    # 0-step recordings that DO have a requires block fall through to normal lint.
    if len(steps) == 0 and requires_raw is None:
        return LintResult(
            path=yaml_path,
            status="empty",
            reason="recording has no steps (placeholder)",
            category="empty",
        )
    if requires_raw is None:
        return LintResult(
            path=yaml_path,
            status="fail",
            reason=f"no requires block — run `simdrive migrate-recording {yaml_path.parent.name}` to capture one",
            category="missing_state_contract",
        )

    block = RequiresBlock.from_dict(requires_raw)
    if block is None:
        return LintResult(path=yaml_path, status="fail",
                          reason="malformed requires block (not a mapping)",
                          category="missing_state_contract")

    return LintResult(
        path=yaml_path,
        status="ok",
        text_mark_count=len(block.initial_state.text_subset_required),
        app_bundle_id=block.app.bundle_id,
        sim_device=block.sim.device,
        category="ok",
    )


class MigrationError(Exception):
    """Raised when migrate_recording cannot proceed."""


@dataclass
class MigrationResult:
    name: str
    migrated: bool
    reason: str = ""
    dry_run: bool = False
    text_mark_count: int = 0
    primary_button_label: Optional[str] = None
    backup_path: Optional[Path] = None


def migrate_recording(name: str, *, force: bool = False,
                      dry_run: bool = False) -> MigrationResult:
    """Backfill a `requires:` block onto an old recording by OCR'ing step-0.

    Idempotent: no-op when `requires:` already present (unless force=True).
    """
    rec_dir = recordings_root() / name
    yaml_path = rec_dir / "recording.yaml"
    if not yaml_path.exists():
        raise MigrationError(f"recording not found at {yaml_path}")

    payload = yaml.safe_load(yaml_path.read_text())
    if not isinstance(payload, dict):
        raise MigrationError("recording.yaml did not parse to a mapping")

    if payload.get("requires") is not None and not force:
        return MigrationResult(name=name, migrated=False,
                               reason="already migrated (use --force to overwrite)")

    steps = payload.get("steps") or []
    if not steps:
        raise MigrationError("cannot migrate — no step-0 screenshot to OCR")

    pre_rel = steps[0].get("pre_screenshot")
    if not pre_rel:
        raise MigrationError("cannot migrate — step-0 has no pre_screenshot")

    pre_path = rec_dir / pre_rel
    if not pre_path.exists():
        raise MigrationError(f"cannot migrate — pre_screenshot missing at {pre_path}")

    marks = som.detect_marks(pre_path)

    # Screen dimensions: prefer step args, fall back to PIL probe.
    args0 = steps[0].get("args") or {}
    screen_h = args0.get("screenshot_h")
    if not screen_h:
        from PIL import Image
        with Image.open(pre_path) as im:
            _, screen_h = im.size

    block = _build_requires_block(
        marks,
        screen_h=screen_h,
        app_bundle_id=payload.get("app_bundle_id"),
        app_version=payload.get("app_version"),
        sim_device=payload.get("device"),
        sim_ios_version=payload.get("os_version"),
    )

    result = MigrationResult(
        name=name,
        migrated=True,
        dry_run=dry_run,
        text_mark_count=len(block.initial_state.text_subset_required),
        primary_button_label=block.initial_state.primary_button_label,
    )

    if dry_run:
        return result

    # Backup before mutation — non-obvious recovery aid if migration mangles the file.
    backup = yaml_path.with_suffix(".yaml.pre-migrate.bak")
    shutil.copy2(yaml_path, backup)
    result.backup_path = backup

    payload["requires"] = block.to_dict()
    with yaml_path.open("w") as f:
        yaml.safe_dump(payload, f, sort_keys=False)
    return result


# ---------- Replay ---------- #

# F#15 — device-class → (width_px, status_bar_h_px) for auto-masking.
# Status bar height: ~60 logical px * device scale (3x for Pro models, 2x for standard).
DEVICE_STATUS_BAR_MASKS: dict[str, tuple[int, int]] = {
    "iPhone 17 Pro":      (1206, 180),  # 3x scale
    "iPhone 17 Pro Max":  (1320, 180),
    "iPhone 17":          (1179, 160),  # 2x scale
    "iPhone 16 Pro":      (1206, 180),
    "iPhone 16 Pro Max":  (1320, 180),
    "iPhone 16":          (1179, 160),
    "iPhone 16 Plus":     (1290, 160),
    "iPhone 15 Pro":      (1179, 180),
    "iPhone 15 Pro Max":  (1290, 180),
    "iPhone 15":          (1179, 160),
}


def _default_status_bar_mask(device_name: str) -> "list[dict] | None":
    """Return a one-element ssim_masks list for the device's status bar, or None.

    The returned dict uses the schema expected by _normalize_masks:
    {"x": int, "y": int, "w": int, "h": int, "label": str}
    """
    entry = DEVICE_STATUS_BAR_MASKS.get(device_name)
    if not entry:
        return None
    w, h = entry
    return [{"x": 0, "y": 0, "w": w, "h": h, "label": "status_bar"}]


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


def _ssim_or_fallback(a: Path, b: Path, masks: Optional[list[MaskRect]] = None,
                      device_name: Optional[str] = None) -> float:
    """Return a similarity score in [0, 1].

    Uses skimage SSIM if available (strictly better at detecting structural
    changes); otherwise falls back to a perceptual-hash–style block-difference
    metric. Both metrics yield ~1.0 for identical screens and drop sharply for
    visually different ones.

    If *masks* is None and *device_name* is a recognised device class,
    the status-bar mask is auto-applied (F#14).
    """
    if masks is None and device_name is not None:
        default = _default_status_bar_mask(device_name)
        if default:
            masks = _normalize_masks(default)
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
    constraint fails. The mismatch_dict carries ``expected``, ``actual``, and a
    ``remedy`` hint.

    a13: Device sessions check the ``requires.device`` sub-block (OS major version
    halts; minor version and device name warn only). Target discriminator mismatch
    (recording target != session target) halts with ``replay_state_contract_failed``.
    """
    expected: dict[str, Any] = {}
    actual: dict[str, Any] = {}
    reasons: list[str] = []
    warnings: list[str] = []  # non-halting issues surfaced in the result

    # Target discriminator check (a13): a device recording replayed against a
    # simulator session (or vice versa) is almost certainly a mistake. Halt loudly.
    block_target = getattr(block, "target", "simulator")
    if block_target and block_target != session.target:
        expected["target"] = block_target
        actual["target"] = session.target
        reasons.append(
            f"replay_state_contract_failed: recording target is {block_target!r} "
            f"but session target is {session.target!r}. "
            "Re-record against the correct target or use a matching session."
        )
        return False, {
            "expected": expected,
            "actual": actual,
            "reasons": reasons,
            "remedy": (
                f"Recording was captured on target={block_target!r}. "
                f"Start a {block_target!r} session to replay this recording."
            ),
        }

    # App bundle
    if block.app.bundle_id:
        actual_bundle = session.app_bundle_id
        expected["app_bundle_id"] = block.app.bundle_id
        actual["app_bundle_id"] = actual_bundle
        if actual_bundle != block.app.bundle_id:
            reasons.append(
                f"app_bundle_id mismatch: expected {block.app.bundle_id!r}, got {actual_bundle!r}"
            )

    # App version (sim only — device recordings always have version=None)
    if block.app.version is not None and block.app.version_match != "any":
        live_version = _current_app_version(session)
        expected["app.version"] = f"{block.app.version} (match: {block.app.version_match})"
        actual["app.version"] = live_version
        if not _version_matches(block.app.version_match, block.app.version, live_version):
            reasons.append(
                f"app.version: expected {block.app.version} ({block.app.version_match}), got {live_version}"
            )

    # Sim-specific checks
    if session.target == "simulator":
        if block.sim.device:
            live_device = session.device.name or ""
            expected["sim.device"] = block.sim.device
            actual["sim.device"] = live_device
            if live_device.lower() != block.sim.device.lower():
                reasons.append(f"sim.device: expected {block.sim.device!r}, got {live_device!r}")

        if block.sim.ios_version:
            live_os = session.device.os_version or ""
            expected["sim.ios_version"] = block.sim.ios_version
            actual["sim.ios_version"] = live_os
            if not _ios_version_matches(block.sim.ios_version, live_os):
                reasons.append(
                    f"sim.ios_version: expected {block.sim.ios_version}, got {live_os!r}"
                )

    # Device-specific checks (a13)
    if session.target == "device" and block.device is not None:
        live_os = session.device.os_version or ""
        rec_os_major = block.device.os_major

        # UDID check: if the recording has a UDID, verify it matches the live session.
        # This ensures a recording captured on device A isn't inadvertently replayed on
        # device B where the UI layout or screen size may differ. Operators can suppress
        # this by clearing the udid field in the requires block with migrate_recording --force.
        if block.device.udid:
            live_udid = session.device.udid or ""
            expected["device.udid"] = block.device.udid
            actual["device.udid"] = live_udid
            if live_udid != block.device.udid:
                reasons.append(
                    f"replay_state_contract_failed: device udid mismatch — "
                    f"recorded on {block.device.udid!r}, current device is {live_udid!r}. "
                    "Re-record on this device or clear the udid constraint "
                    "via `simdrive migrate-recording <name> --force`."
                )

        if rec_os_major is not None and live_os:
            try:
                live_os_major = int(live_os.split(".")[0])
            except (ValueError, IndexError):
                live_os_major = None

            if live_os_major is not None and live_os_major != rec_os_major:
                # Major version mismatch — halt. This is the primary guard against
                # "26.x" recordings replaying against "27.x" devices where UI flows
                # may have fundamentally changed.
                expected["device.os_major"] = rec_os_major
                actual["device.os_major"] = live_os_major
                reasons.append(
                    f"replay_state_contract_failed: device OS major mismatch — "
                    f"recorded on iOS {rec_os_major}.x, current device is {live_os!r}. "
                    "Re-record on the matching major OS version."
                )
            elif live_os != (block.device.os_version or ""):
                # Minor version differs — warn only (26.4.2 vs 26.4.3 is acceptable)
                warnings.append(
                    f"device.os_version minor diff: recorded {block.device.os_version!r}, "
                    f"live {live_os!r} — continuing (minor version tolerance)"
                )

        # Device name: warn only — user may have renamed the device
        if block.device.device_name:
            live_name = session.device.name or ""
            if live_name.lower() != block.device.device_name.lower():
                warnings.append(
                    f"device.device_name: recorded {block.device.device_name!r}, "
                    f"live {live_name!r} — continuing (device names may differ)"
                )

    # Observe live state for the initial_state checks.
    # Device path: use annotate_device_screenshot to get marks.
    # If observe fails and we already have hard failures (reasons), return early
    # without blocking on observe — the contract is already broken.
    observe_failed: Optional[str] = None
    live_marks: list = []
    try:
        live_marks = _observe_live_marks(session, workdir)
    except Exception as exc:
        observe_warning = f"observe failed: {exc}"
        if reasons:
            # Hard failures already recorded — observe failure is advisory.
            warnings.append(observe_warning)
        else:
            # Only observe failed — treat as a contract failure so replay halts.
            observe_failed = observe_warning

    if observe_failed:
        result = {
            "expected": expected,
            "actual": actual,
            "reasons": [observe_failed],
            "remedy": "Could not observe live state. Verify the simulator/device is reachable.",
        }
        if warnings:
            result["warnings"] = warnings
        return False, result

    # Marks may be Mark objects (sim path) or dicts (device path).
    live_texts = [
        (m.text if hasattr(m, "text") else m.get("text") or "")
        for m in live_marks
    ]

    # Only apply initial_state checks when they are actually set in the contract.
    # Device recordings captured via WDA may not have text_subset_required (empty list)
    # so these checks are no-ops, preserving backward compat.
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
        present_label = any(
            label in (m.text if hasattr(m, "text") else m.get("text") or "")
            for m in live_marks
        )
        actual["initial_state.primary_button_present"] = present_label
        if not present_label:
            reasons.append(f"initial_state.primary_button_label {label!r} not seen on screen")

    if not reasons:
        # Success — but may have non-halting warnings (e.g. minor OS version diff).
        if warnings:
            return True, {"warnings": warnings}
        return True, None

    # Remedy: alert-shaped if any forbidden text looks like an alert button,
    # OR any live mark text is an alert button label.
    def _mark_text_str(m: Any) -> str:
        if hasattr(m, "text"):
            return m.text or ""
        return m.get("text") or ""

    alert_shaped = any(
        (t or "").strip().lower() in _ALERT_TEXTS for t in (forbidden or [])
    ) or any(
        _mark_text_str(m).strip().lower() in _ALERT_TEXTS for m in live_marks
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

    result = {
        "expected": expected,
        "actual": actual,
        "reasons": reasons,
        "remedy": remedy,
    }
    if warnings:
        result["warnings"] = warnings
    return False, result


def _observe_live_marks(session: Session, workdir: Path) -> list:
    """Return live marks for state-contract verification.

    Sim path: Vision OCR via ``observe.observe`` → list[Mark].
    Device path with WDA: WDA XCUI tree via ``annotate_device_screenshot`` → list[dict].
    Device path without WDA (test fallback): falls back to ``observe.observe`` which
    can be monkeypatched in tests.

    Both paths return objects with ``.text`` or ``.get("text")`` access patterns.
    """
    if session.target == "device" and session.wda_client is not None:
        from PIL import Image
        import io
        from .wda.som_device import annotate_device_screenshot

        wda = session.wda_client
        png_bytes = wda.screenshot_any()
        workdir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time() * 1000)
        screenshot_path = workdir / f"contract-verify-{ts}.png"
        screenshot_path.write_bytes(png_bytes)
        with Image.open(io.BytesIO(png_bytes)) as im:
            w, h = im.size
        point_scale = float(getattr(session, "pixel_per_point_scale", None) or 1.0)
        marks, _ = annotate_device_screenshot(screenshot_path, (w, h), wda, point_scale=point_scale)
        return list(marks or [])

    live = observe.observe(session.device.udid, workdir, target=session.target)
    return list(live.marks or [])


_DEVICE_DRIFT_THRESHOLD = 0.80
"""Default SSIM drift threshold for real-device replay.

Slightly looser than the simulator default (0.85) because real device screenshots
can vary slightly due to display rendering differences, anti-aliasing, and minor
timing jitter in hardware compositing. 0.80 still halts reliably on meaningful UI
drift — the "23 blind taps at SSIM 0.014" failure mode halts at 0.80 with a factor
of 50x margin. Set ``drift_threshold`` explicitly in the replay call to override.
"""

_MARKS_DRIFT_RATIO_THRESHOLD = 0.50
"""Marks-count drift threshold: if live marks drop below this fraction of the
recorded marks count, it signals structural UI drift (e.g. app navigated to a
completely different screen) even when SSIM passes (e.g. similar background color).
Surfaced as ``marks_count_drift`` in step_result; halts when ``on_drift="halt"``.
"""

_DRIFT_HYSTERESIS_FRAMES = 2
"""Number of consecutive sub-threshold SSIM frames required to declare drift.

A single noisy frame (transient animation, status-bar tick, brief loading flash)
shouldn't halt a replay — we require two consecutive sub-threshold captures
before halting. When the first sample is below threshold, the replay engine
recaptures a fresh screenshot and re-compares; only when *both* samples fall
below the threshold do we treat it as real drift. Marks-count drift remains a
single-frame check because a structural mark drop is far harder to fluke than
a pixel-level SSIM dip.

(INIT-2026-549) — set to 2; raising this would lengthen recovery time on
genuinely drifted screens without meaningfully improving false-positive rate.
"""


def replay(name: str, session: Session, on_drift: str = "halt",
           drift_threshold: Optional[float] = None,
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

    a13: drift_threshold defaults to 0.80 for device sessions and 0.85 for
    simulator sessions when not explicitly provided. Pass a numeric value to
    override. Marks-count drift is also checked per step — if the live marks
    count drops below 50% of the recorded count, it's reported as drift.
    """
    if on_drift not in {"halt", "warn", "force"}:
        raise ValueError("on_drift must be halt|warn|force")

    rec_dir = recordings_root() / name
    yaml_path = rec_dir / "recording.yaml"
    if not yaml_path.exists():
        raise errors.recording_not_found(name, str(yaml_path))
    payload = yaml.safe_load(yaml_path.read_text())
    steps = payload.get("steps", [])

    # Resolve effective drift threshold: explicit arg > target-based default.
    if drift_threshold is None:
        effective_threshold = (
            _DEVICE_DRIFT_THRESHOLD if session.target == "device" else 0.85
        )
    else:
        effective_threshold = float(drift_threshold)

    masks = _normalize_masks(mask_regions)
    if masks is None:
        masks = _normalize_masks(payload.get("ssim_masks"))

    # State-contract verification (a9.0 / a13) — happens BEFORE any step executes.
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
        if ok and mismatch and mismatch.get("warnings"):
            # Contract passed but has non-halting warnings (e.g. minor OS version diff).
            state_warning = "; ".join(mismatch["warnings"])
        if not ok:
            summary = "; ".join(mismatch.get("reasons", []) or [])[:300]
            if halt_on_state_mismatch:
                out = {
                    "ok": False,
                    "halted_at": 0,
                    "halt_reason": "state_contract_mismatch",
                    "threshold": effective_threshold,
                    "steps_planned": steps_planned,
                    "steps": [],
                    "expected": mismatch["expected"],
                    "actual": mismatch["actual"],
                    "reasons": mismatch.get("reasons", []),
                    "remedy": mismatch["remedy"],
                }
                if mismatch.get("warnings"):
                    out["_simdrive_warning"] = "; ".join(mismatch["warnings"])
                return out
            state_warning = f"state_contract_mismatch: {summary}"
            # Non-fatal: also surface non-halting device warnings
            if mismatch.get("warnings"):
                state_warning += " | " + "; ".join(mismatch["warnings"])

    results: list[dict] = []
    drift_events: list[dict] = []

    for step in steps:
        # marks_count: stored at step level (recorder a13 path) or in step.args
        # (TestAtlas fixture format). Read from both for compat.
        recorded_marks_count = (
            step.get("marks_count")
            or (step.get("args") or {}).get("marks_count")
        )
        live_obs = _observe_for_replay(session)
        rec_pre = rec_dir / step["pre_screenshot"]
        score = _ssim_or_fallback(live_obs["screenshot_path"], rec_pre, masks=masks)
        log.debug(
            "replay.ssim_compare",
            extra={
                "recording_name": name,
                "step_id": step["id"],
                "action": step["action"],
                "ssim": round(score, 4),
                "threshold": effective_threshold,
                "sample": 1,
            },
        )
        # Hysteresis (INIT-2026-549): a single noisy sub-threshold frame
        # shouldn't halt replay. When the first sample is under threshold we
        # recapture a fresh screenshot, recompute, and only declare drift when
        # *both* samples fail. We retain the lower of the two scores as the
        # reported similarity so step_result still surfaces the worst-case dip.
        first_score: float = score
        recheck_score: Optional[float] = None
        if score < effective_threshold:
            live_obs = _observe_for_replay(session)
            recheck_score = _ssim_or_fallback(
                live_obs["screenshot_path"], rec_pre, masks=masks
            )
            log.debug(
                "replay.ssim_compare",
                extra={
                    "recording_name": name,
                    "step_id": step["id"],
                    "action": step["action"],
                    "ssim": round(recheck_score, 4),
                    "threshold": effective_threshold,
                    "sample": 2,
                },
            )
            score = min(first_score, recheck_score)
            drifted = recheck_score < effective_threshold
        else:
            drifted = False

        # Marks-count drift check (a13): structural UI change that SSIM misses.
        # Only fires when BOTH the recording has marks AND live has some marks (> 0).
        # If live_marks_count == 0 we treat it as "marks unavailable" (e.g. no annotate
        # pass, test environment) and skip the check. This avoids false-positives when
        # the observe path doesn't return marks (e.g. annotate=False, WDA unavailable).
        marks_count_drift = False
        live_marks_count = live_obs.get("marks_count", 0)
        marks_drift_info: Optional[dict] = None
        if (recorded_marks_count is not None and recorded_marks_count > 0
                and live_marks_count > 0
                and (live_marks_count / recorded_marks_count) < _MARKS_DRIFT_RATIO_THRESHOLD):
            marks_count_drift = True
            marks_drift_info = {
                "recorded_marks_count": recorded_marks_count,
                "live_marks_count": live_marks_count,
                "ratio": round(live_marks_count / recorded_marks_count, 3),
                "threshold_ratio": _MARKS_DRIFT_RATIO_THRESHOLD,
            }
            drift_events.append({
                "step_id": step["id"],
                "kind": "marks_count_drift",
                **marks_drift_info,
            })

        step_result: dict = {
            "id": step["id"],
            "action": step["action"],
            "similarity": round(score, 4),
            "drifted": drifted or marks_count_drift,
            "marks_count_drift": marks_count_drift,
            "executed": False,
            "error": None,
        }
        if marks_drift_info:
            step_result["marks_drift_info"] = marks_drift_info

        if (drifted or marks_count_drift) and on_drift == "halt":
            if drifted:
                if recheck_score is not None:
                    # Hysteresis halt: include both consecutive sub-threshold
                    # scores so a triaging operator can see we didn't fluke it.
                    step_result["error"] = (
                        f"replay_drift_detected: SSIM {first_score:.3f} then "
                        f"{recheck_score:.3f} < {effective_threshold} "
                        f"(2 consecutive sub-threshold samples) "
                        f"at step {step['id']} ({step['action']}); halted"
                    )
                else:
                    step_result["error"] = (
                        f"replay_drift_detected: SSIM {score:.3f} < {effective_threshold} "
                        f"at step {step['id']} ({step['action']}); halted"
                    )
            else:
                step_result["error"] = (
                    f"replay_drift_detected: marks count dropped from "
                    f"{recorded_marks_count} to {live_marks_count} "
                    f"(ratio {live_marks_count / recorded_marks_count:.2f} < "
                    f"{_MARKS_DRIFT_RATIO_THRESHOLD}) at step {step['id']}; halted"
                )
            results.append(step_result)
            out = {
                "ok": False,
                "halted_at": step["id"],
                "halt_reason": "drift",
                "threshold": effective_threshold,
                "steps_planned": steps_planned,
                "steps": results,
                "drift_events": drift_events,
                # Diagnostic fields for the failing step (per-spec)
                "step_id": step["id"],
                "ssim": round(score, 4),
                "expected_screenshot_path": str(rec_pre),
                "actual_screenshot_path": str(live_obs["screenshot_path"]),
            }
            if state_warning:
                out["_simdrive_warning"] = state_warning
            return out

        # Execute the step against the live session.
        try:
            _execute_step_for_session(step, session, live_obs=live_obs)
            step_result["executed"] = True
        except Exception as exc:
            step_result["error"] = str(exc)
            results.append(step_result)
            out = {
                "ok": False,
                "halted_at": step["id"],
                "halt_reason": "execute_error",
                "threshold": effective_threshold,
                "steps_planned": steps_planned,
                "steps": results,
                "drift_events": drift_events,
            }
            if state_warning:
                out["_simdrive_warning"] = state_warning
            return out

        results.append(step_result)

    out = {
        "ok": True,
        "halted_at": None,
        "halt_reason": None,
        "threshold": effective_threshold,
        "steps_planned": steps_planned,
        "steps": results,
        "drift_events": drift_events,
    }
    if state_warning:
        out["_simdrive_warning"] = state_warning
    return out


def _observe_for_replay(session: Session) -> dict:
    """Take a screenshot for replay comparison; return a dict with ``screenshot_path``
    and ``marks_count``.

    Sim path: uses ``observe.observe`` (Vision OCR, simctl screenshot).
    Device path with WDA: uses ``wda_client.screenshot_any()`` + ``annotate_device_screenshot``.
    Device path without WDA (e.g., in tests): falls back to ``observe.observe`` with
    ``target=device`` which uses the idevicescreenshot path. This fallback preserves
    test-patching compatibility — tests that monkeypatch ``observe.observe`` still work.

    Both paths return a common dict so the replay loop is target-agnostic.
    """
    if session.target == "device" and session.wda_client is not None:
        from PIL import Image
        import io
        from .wda.som_device import annotate_device_screenshot

        wda = session.wda_client
        png_bytes = wda.screenshot_any()
        obs_dir = session.workdir / "replay"
        obs_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time() * 1000)
        screenshot_path = obs_dir / f"replay-{ts}.png"
        screenshot_path.write_bytes(png_bytes)
        with Image.open(io.BytesIO(png_bytes)) as im:
            w, h = im.size
        point_scale = float(getattr(session, "pixel_per_point_scale", None) or 1.0)
        marks, _ = annotate_device_screenshot(screenshot_path, (w, h), wda, point_scale=point_scale)
        return {
            "screenshot_path": screenshot_path,
            "marks_count": len(marks or []),
            "marks": list(marks or []),
            "screenshot_w": w,
            "screenshot_h": h,
        }

    # Sim path OR device path without WDA client (test/CI fallback).
    live = observe.observe(session.device.udid, session.workdir / "replay",
                           target=session.target)
    return {
        "screenshot_path": live.screenshot_path,
        "marks_count": len(live.marks or []),
        "marks": list(live.marks or []),
        "screenshot_w": live.screenshot_w,
        "screenshot_h": live.screenshot_h,
    }


def _mark_center_compat(m) -> tuple[int, int]:
    """Return (cx, cy) pixel centre from a Mark dataclass or dict mark.

    Mirror of server._mark_center, duplicated here to avoid a server↔recorder import.
    """
    if isinstance(m, dict):
        c = m.get("center")
        if c and len(c) >= 2:
            return int(c[0]), int(c[1])
        bbox = m.get("bbox") or [0, 0, 0, 0]
        return int(bbox[0] + bbox[2] // 2), int(bbox[1] + bbox[3] // 2)
    return m.center


def _resolve_focus_target(target: dict, marks: list) -> Optional[tuple[int, int]]:
    """Resolve a recorded ``tap_first`` (or other focus-context) target dict to pixel (x, y).

    Accepts the same shapes as ``server._resolve_target_xy``:
      * ``{x, y}``         → returned as-is
      * ``{mark}``         → centre of mark with matching numeric id
      * ``{stable_id}``    → centre of mark with matching stable_id
      * ``{stable_id_loose}`` → centre of mark with matching stable_id_loose
      * ``{text}``         → centre of first mark whose text matches (or aliases)

    Returns ``None`` when the target shape is unrecognised or the live ``marks``
    don't contain a matching mark. The caller decides whether to fall back to
    typing without a focus tap (legacy behaviour) or surface the miss.

    F#11 (2026-05-22): introduced so the replay engine can re-tap the focus target
    a ``type_text`` was originally recorded with — previously dropped, making
    multi-field forms un-replayable.
    """
    if not isinstance(target, dict):
        return None
    if "x" in target and "y" in target:
        return int(target["x"]), int(target["y"])
    marks = marks or []
    if "mark" in target:
        m = som.find_by_mark_id(marks, int(target["mark"]))
        return _mark_center_compat(m) if m is not None else None
    if "stable_id" in target:
        m = som.find_by_stable_id(marks, str(target["stable_id"]))
        return _mark_center_compat(m) if m is not None else None
    if "stable_id_loose" in target:
        m = som.find_by_stable_id_loose(marks, str(target["stable_id_loose"]))
        return _mark_center_compat(m) if m is not None else None
    if "text" in target:
        m = som.find_by_text(marks, str(target["text"]))
        return _mark_center_compat(m) if m is not None else None
    return None


def _execute_step(step: dict, session: Session, live_observation: Optional[observe.Observation] = None) -> None:
    """Execute a single step against the session (sim path, legacy entry point).

    Kept for backward-compat with tests that call _execute_step directly.
    New code should use ``_execute_step_for_session`` which handles both targets.
    """
    _execute_step_for_session(step, session)


def _execute_step_for_session(step: dict, session: Session, live_obs: Optional[dict] = None) -> None:
    """Execute a single recorded step against the current session.

    Dispatches to the device WDA path or sim HID path based on session.target.

    Device path (a13): uses ``session.wda_client`` for tap/swipe/type_text/press_key
    — the same WDA client already open from session_start. Pixel coords from the
    recording are divided by the session's pixel-per-point scale before WDA dispatch.

    Sim path: delegates to ``act.*`` which routes through hid_inject or cliclick.

    live_obs: optional dict from ``_observe_for_replay`` containing ``marks`` (for
    stable_id resolution on sim) and ``screenshot_w/h``. When None on sim path,
    falls back to recorded coords only.
    """
    action = step["action"]
    args = step.get("args", {})
    udid = session.device.udid

    if session.target == "device":
        _execute_step_device(step, session)
        return

    # Sim path — stable_id resolution from live_obs marks if available.
    if action == "tap":
        if live_obs is not None:
            marks = live_obs.get("marks") or []
            sw = live_obs.get("screenshot_w", args.get("screenshot_w", 0))
            sh = live_obs.get("screenshot_h", args.get("screenshot_h", 0))
            stable_id = args.get("stable_id")
            if stable_id:
                live_mark = som.find_by_stable_id(marks, stable_id)
                if live_mark is not None:
                    cx, cy = live_mark.center
                    act.tap(cx, cy, sw, sh, udid=udid)
                    return
            stable_id_loose = args.get("stable_id_loose")
            if stable_id_loose:
                live_mark = som.find_by_stable_id_loose(marks, stable_id_loose)
                if live_mark is not None:
                    cx, cy = live_mark.center
                    act.tap(cx, cy, sw, sh, udid=udid)
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
        # F#11: re-tap the recorded focus target before typing so multi-field forms
        # replay faithfully. Pre-fix, tap_first was dropped at record time so this
        # branch never saw it; legacy recordings without tap_first fall through to
        # type_text alone, preserving the old behaviour.
        tap_first = args.get("tap_first")
        if tap_first:
            live_marks = (live_obs or {}).get("marks") if live_obs else None
            xy = _resolve_focus_target(tap_first, live_marks or [])
            if xy is not None:
                sw = (live_obs or {}).get("screenshot_w") if live_obs else None
                sh = (live_obs or {}).get("screenshot_h") if live_obs else None
                sw = sw or args.get("screenshot_w") or 0
                sh = sh or args.get("screenshot_h") or 0
                act.tap(xy[0], xy[1], sw, sh, udid=udid)
        act.type_text(args["text"], udid=udid)
    elif action == "press_key":
        act.press_key(args["key"], udid=udid)
    else:
        raise ValueError(f"unsupported replay action: {action}")


def _execute_step_device(step: dict, session: Session) -> None:
    """Execute a single recorded step against a real device via WDA (a13).

    Pixel coordinates from the recording are converted to logical points using
    the session's cached pixel-per-point scale before being sent to WDA.
    This is the same conversion that tool_tap/tool_swipe/tool_type_text use at
    record time, so replay reproduces the exact same touch location.

    Supported actions: tap, swipe, type_text, press_key.
    No stable_id resolution for device replay — WDA coordinates are absolute
    point coordinates derived from the recording's pixel coords + scale factor.

    When ``wda_client`` is None (e.g., in unit tests that don't mock WDA),
    falls back to the sim ``act.*`` dispatch path. This allows tests to verify
    the replay logic without spinning up a real WDA server.
    """
    action = step["action"]
    args = step.get("args", {})
    wda = session.wda_client

    if wda is None:
        # Test-compat fallback: dispatch through sim act.* which can be monkeypatched.
        udid = session.device.udid
        if action == "tap":
            act.tap(args["x"], args["y"], args["screenshot_w"], args["screenshot_h"], udid=udid)
        elif action == "swipe":
            act.swipe(
                args["x1"], args["y1"], args["x2"], args["y2"],
                args["screenshot_w"], args["screenshot_h"],
                args.get("duration_ms", 300),
                udid=udid,
            )
        elif action == "type_text":
            # F#11 (device/wda-None fallback): re-tap focus target before typing.
            tap_first = args.get("tap_first")
            if tap_first:
                xy = _resolve_focus_target(tap_first, getattr(session, "last_marks", []) or [])
                if xy is not None:
                    sw = args.get("screenshot_w") or 0
                    sh = args.get("screenshot_h") or 0
                    act.tap(xy[0], xy[1], sw, sh, udid=udid)
            act.type_text(args["text"], udid=udid)
        elif action == "press_key":
            act.press_key(args["key"], udid=udid)
        else:
            raise ValueError(f"unsupported device replay action: {action!r}")
        return

    # Pixel-per-point scale: cached on session; 1.0 fallback is safe (will just
    # send slightly-off coordinates on hi-DPI devices, better than crashing).
    scale = float(getattr(session, "pixel_per_point_scale", None) or 1.0)

    if action == "tap":
        x_pt = float(args["x"]) / scale
        y_pt = float(args["y"]) / scale
        wda.tap(x_pt, y_pt)

    elif action == "swipe":
        wda.swipe(
            float(args["x1"]) / scale,
            float(args["y1"]) / scale,
            float(args["x2"]) / scale,
            float(args["y2"]) / scale,
            duration_ms=int(args.get("duration_ms", 300)),
        )

    elif action == "type_text":
        # F#11: re-tap the recorded focus target before typing on real devices so
        # multi-field forms replay faithfully. Pixel coords are divided by the
        # session's pixel-per-point scale before being sent to WDA — the same
        # conversion tool_type_text uses at record time.
        tap_first = args.get("tap_first")
        if tap_first:
            xy = _resolve_focus_target(tap_first, getattr(session, "last_marks", []) or [])
            if xy is not None:
                wda.tap(float(xy[0]) / scale, float(xy[1]) / scale)
        wda.type_text(args["text"])

    elif action == "press_key":
        wda.press_key(args["key"])

    else:
        raise ValueError(f"unsupported device replay action: {action!r}")
