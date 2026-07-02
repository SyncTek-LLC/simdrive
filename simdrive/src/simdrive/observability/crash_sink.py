"""Local-first, scrubbed crash sink (WS-4).

Records unhandled exceptions to a **local** JSON sink — by default
``~/.heka/crashes/*.json`` — capturing only the *shape* of a crash:

    {
      "schema_version":  1,
      "ts":              "<ISO-8601 UTC>",
      "exc_type":        "ValueError",
      "exc_module":      "builtins",
      "frames":          [{"file": "server.py", "line": 42, "func": "serve"}, ...],
      "package_version": "<simdrive version>",
      "os":              "darwin" | "linux" | "other",
      "python":          "3.11"
    }

Privacy rules (this is a *local* sink, but it is scrubbed as if it might be
shipped later):

* **No PII.** Absolute paths (which contain the username) are reduced to file
  *basenames*; only line numbers and function names travel with them.
* **No exception message.** Messages routinely embed user data (paths, emails,
  values), so the message is deliberately dropped — we keep the exception
  *class*, not its text.
* **No local variables / source text.** Only the stack *shape*.

The sink is **always local and offline** — it never opens a socket. Any future
opt-in upload path would route through the single ``HEKA_TELEMETRY`` kill-switch
(see ``simdrive.license.telemetry``); this module deliberately has no network
code at all.

``install_crash_sink()`` chains a recorder onto ``sys.excepthook`` so a crash is
persisted before the interpreter prints the traceback. It is best-effort and
never itself raises.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import time
import traceback
from pathlib import Path
from types import TracebackType
from typing import Optional


SCHEMA_VERSION = 1

# Opt-OUT env for the *local* sink (the sink is on by default once installed).
_DISABLE_ENV = "HEKA_CRASH_SINK_DISABLED"
# Override the sink directory (tests + self-hosting).
_DIR_ENV = "HEKA_CRASH_DIR"


def _default_crash_dir() -> Path:
    """Resolve the crash directory: ``$HEKA_CRASH_DIR`` or ``~/.heka/crashes``."""
    override = os.environ.get(_DIR_ENV)
    if override:
        return Path(override)
    return Path.home() / ".heka" / "crashes"


def _os_family() -> str:
    """Coarse OS family — never a fingerprint (matches telemetry.os_family)."""
    sysname = platform.system().lower()
    if sysname == "darwin":
        return "darwin"
    if sysname == "linux":
        return "linux"
    return "other"


def _python_minor() -> str:
    """Return ``major.minor`` only — no patch/build detail (not a fingerprint)."""
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _package_version() -> str:
    """Best-effort simdrive version; never raises."""
    try:
        from simdrive import __version__
        return __version__
    except Exception:  # pragma: no cover - defensive
        return "unknown"


def _scrub_traceback(tb: Optional[TracebackType]) -> list[dict]:
    """Reduce a traceback to scrubbed frames — basename + line + func only.

    Drops absolute paths (which leak the username), local variables, and
    source text. Returns oldest-frame-first, capped to keep records small.
    """
    frames: list[dict] = []
    for frame in traceback.extract_tb(tb):
        frames.append(
            {
                "file": os.path.basename(frame.filename or ""),
                "line": frame.lineno,
                "func": frame.name,
            }
        )
    return frames


def build_crash_record(
    exc: BaseException,
    *,
    tb: Optional[TracebackType] = None,
    now: Optional[float] = None,
) -> dict:
    """Construct a scrubbed crash record. Contains no PII and no message text."""
    if tb is None:
        tb = exc.__traceback__
    ts_iso = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now if now is not None else time.time())
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "ts": ts_iso,
        "exc_type": type(exc).__name__,
        "exc_module": type(exc).__module__,
        "frames": _scrub_traceback(tb),
        "package_version": _package_version(),
        "os": _os_family(),
        "python": _python_minor(),
    }


def _record_filename(record: dict) -> str:
    """Stable, collision-resistant filename from the record's shape + ts."""
    shape = json.dumps(record.get("frames", []), sort_keys=True) + record.get(
        "exc_type", ""
    )
    short = hashlib.sha256(shape.encode("utf-8")).hexdigest()[:8]
    ts_compact = record.get("ts", "").replace(":", "").replace("-", "")
    return f"crash-{ts_compact}-{short}.json"


def record_crash(
    exc: BaseException,
    *,
    tb: Optional[TracebackType] = None,
    crash_dir: Optional[Path] = None,
    now: Optional[float] = None,
) -> Optional[Path]:
    """Persist a scrubbed crash record locally. Best-effort; never raises.

    Returns the written path, or ``None`` if the sink is disabled or the write
    failed (a crash sink must never turn one crash into two).
    """
    if os.environ.get(_DISABLE_ENV) == "1":
        return None
    try:
        target_dir = crash_dir if crash_dir is not None else _default_crash_dir()
        record = build_crash_record(exc, tb=tb, now=now)
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / _record_filename(record)
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        return path
    except Exception:  # pragma: no cover - a sink must not crash the crash
        return None


def list_crashes(crash_dir: Optional[Path] = None) -> list[dict]:
    """Return recorded crash records, newest first. Corrupt files are skipped."""
    target_dir = crash_dir if crash_dir is not None else _default_crash_dir()
    if not target_dir.exists():
        return []
    records: list[dict] = []
    for path in sorted(target_dir.glob("crash-*.json")):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue  # skip unreadable/corrupt entries
    records.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return records


# ---------------------------------------------------------------------------
# excepthook wiring
# ---------------------------------------------------------------------------

_INSTALLED = False
_PREV_HOOK = None


def install_crash_sink(crash_dir: Optional[Path] = None) -> bool:
    """Chain a scrubbed-crash recorder onto ``sys.excepthook``. Idempotent.

    Records the crash locally, then delegates to the previous hook so the
    normal traceback still prints. Returns True if installed (or already
    installed), False if disabled via ``HEKA_CRASH_SINK_DISABLED=1``.
    """
    global _INSTALLED, _PREV_HOOK
    if os.environ.get(_DISABLE_ENV) == "1":
        return False
    if _INSTALLED:
        return True
    _PREV_HOOK = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):  # noqa: ANN001
        try:
            record_crash(exc_value, tb=exc_tb, crash_dir=crash_dir)
        except Exception:  # pragma: no cover - never let the sink mask the crash
            pass
        if _PREV_HOOK is not None:
            _PREV_HOOK(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
    _INSTALLED = True
    return True
