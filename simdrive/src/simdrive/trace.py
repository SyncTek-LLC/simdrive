"""Asynchronous Instruments traces on real devices via ``xcrun xctrace``.

``xctrace record`` runs for the whole capture, longer than an MCP call should
block, so ``start`` spawns it detached and keeps state on disk under
``~/.simdrive/traces/<id>/`` (``state.json``, ``record.log``, ``trace.trace``).
``status`` / ``stop`` / ``export`` work from that state, so they also work after
an MCP server restart.

xctrace quirks handled here:
  * ``xctrace export`` writes nothing to stdout unless ``--output`` is given.
  * ``xctrace export`` of a time-profile table can exit 139 (SIGSEGV) on
    xctrace 26 after writing a complete file. A non-empty output file with
    exit 139 is reported as success with a warning.
  * ``xctrace record`` finalizes the trace on SIGINT; SIGKILL leaves it unusable.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

DEFAULT_TEMPLATE = "Power Profiler"
_TIME_LIMIT_RE = re.compile(r"^\d+(ms|s|m|h)$")
_EXPORT_SEGFAULT_RC = (139, -11)


class TraceError(RuntimeError):
    """Raised when a trace operation cannot proceed."""


def _root() -> Path:
    base = os.environ.get("SIMDRIVE_HOME") or str(Path.home() / ".simdrive")
    return Path(base) / "traces"


def _trace_dir(trace_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", trace_id or ""):
        raise TraceError(f"invalid trace_id {trace_id!r}")
    return _root() / trace_id


def _state_path(trace_id: str) -> Path:
    return _trace_dir(trace_id) / "state.json"


def _load(trace_id: str) -> dict:
    p = _state_path(trace_id)
    if not p.exists():
        raise TraceError(f"no trace with id {trace_id!r} under {_root()}")
    return json.loads(p.read_text(encoding="utf-8"))


def _save(state: dict) -> None:
    p = _state_path(state["trace_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(p)


def _pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie child still answers kill(0); reap it if it is ours.
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    except ChildProcessError:
        pass
    return True


def _log_tail(path: Path, n: int = 20) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def start(device: str, attach: str, template: str = DEFAULT_TEMPLATE,
          time_limit: str = "60s") -> dict:
    """Start ``xctrace record`` detached. ``attach`` is a process name or pid."""
    if not device:
        raise TraceError("device (udid or name) is required")
    if not attach:
        raise TraceError("attach (process name or pid) is required")
    if not _TIME_LIMIT_RE.match(time_limit or ""):
        raise TraceError(f"time_limit {time_limit!r} must look like 30s, 5m, 500ms or 1h")
    trace_id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    d = _trace_dir(trace_id)
    d.mkdir(parents=True, exist_ok=False)
    output = d / "trace.trace"
    log = d / "record.log"
    cmd = ["xcrun", "xctrace", "record", "--device", device, "--template", template,
           "--attach", str(attach), "--time-limit", time_limit, "--output", str(output)]
    with log.open("w", encoding="utf-8") as fh:
        proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    state = {
        "trace_id": trace_id, "pid": proc.pid, "cmd": cmd, "device": device,
        "attach": str(attach), "template": template, "time_limit": time_limit,
        "output": str(output), "log": str(log), "started_at": time.time(),
        "status": "recording", "exports": [],
    }
    _save(state)
    return state


def status(trace_id: str) -> dict:
    """Refresh and return the trace state (recording / finished / failed / stopped)."""
    state = _load(trace_id)
    if state["status"] == "recording" and not _pid_alive(state.get("pid")):
        out = Path(state["output"])
        state["status"] = "finished" if out.exists() else "failed"
        state["ended_at"] = time.time()
        _save(state)
    state["log_tail"] = _log_tail(Path(state["log"]))
    return state


def stop(trace_id: str, wait_s: float = 30.0) -> dict:
    """SIGINT the recorder so xctrace finalizes the file, then wait for it."""
    state = _load(trace_id)
    pid = state.get("pid")
    if state["status"] == "recording" and _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline and _pid_alive(pid):
            time.sleep(0.25)
        if _pid_alive(pid):
            state["warning"] = (
                f"xctrace still running {wait_s:.0f}s after SIGINT; left running so "
                "the trace is not corrupted. Call trace_status later."
            )
            _save(state)
            return status(trace_id) | {"warning": state["warning"]}
        state["status"] = "stopped" if Path(state["output"]).exists() else "failed"
        state["ended_at"] = time.time()
        _save(state)
    return status(trace_id)


def export(trace_id: str, xpath: Optional[str] = None, toc: bool = False,
           name: Optional[str] = None, timeout_s: float = 300.0) -> dict:
    """Run ``xctrace export`` into a file in the trace directory.

    Exactly one of ``xpath`` or ``toc`` must be given. Returns the output path,
    size and any warning.
    """
    if bool(xpath) == bool(toc):
        raise TraceError("pass exactly one of xpath or toc=true")
    state = status(trace_id)
    if state["status"] == "recording":
        raise TraceError("trace is still recording; call trace_stop first")
    trace_file = Path(state["output"])
    if not trace_file.exists():
        raise TraceError(f"trace file missing: {trace_file}")
    d = _trace_dir(trace_id)
    stem = name or ("toc" if toc else f"export-{len(state.get('exports', [])) + 1}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", stem):
        raise TraceError(f"invalid export name {stem!r}")
    out = d / f"{stem}.xml"
    cmd = ["xcrun", "xctrace", "export", "--input", str(trace_file), "--output", str(out)]
    cmd += ["--toc"] if toc else ["--xpath", xpath]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    size = out.stat().st_size if out.exists() else 0
    warning = None
    if res.returncode != 0:
        if res.returncode in _EXPORT_SEGFAULT_RC and size > 0:
            warning = (f"xctrace export exited {res.returncode} (segfault) after writing "
                       f"{size} bytes; the file may be complete. Inspect before trusting it.")
        else:
            raise TraceError(
                f"xctrace export exited {res.returncode}: "
                f"{(res.stderr or res.stdout or '').strip() or '<no output>'}"
            )
    entry = {"path": str(out), "bytes": size, "xpath": xpath, "toc": toc,
             "returncode": res.returncode, "warning": warning}
    full = _load(trace_id)
    full.setdefault("exports", []).append(entry)
    _save(full)
    return entry


def import_file(path: str, template: str = DEFAULT_TEMPLATE, timeout_s: float = 600.0) -> dict:
    """Import an on-device Performance Trace into an Instruments trace.

    A ``.aar`` archive is unpacked with ``aa extract`` first; the extracted
    file is then passed to ``xctrace import --template``.
    """
    src = Path(path).expanduser()
    if not src.exists():
        raise TraceError(f"file not found: {src}")
    trace_id = time.strftime("%Y%m%d-%H%M%S-imp-") + secrets.token_hex(3)
    d = _trace_dir(trace_id)
    d.mkdir(parents=True, exist_ok=False)
    source = src
    if src.suffix == ".aar":
        extract_dir = d / "extracted"
        extract_dir.mkdir()
        res = subprocess.run(["aa", "extract", "-i", str(src), "-d", str(extract_dir)],
                             capture_output=True, text=True, timeout=timeout_s, check=False)
        if res.returncode != 0:
            raise TraceError(f"aa extract exited {res.returncode}: {(res.stderr or '').strip()}")
        source = _pick_extracted(extract_dir)
    output = d / "trace.trace"
    res = subprocess.run(["xcrun", "xctrace", "import", "--input", str(source),
                          "--template", template, "--output", str(output)],
                         capture_output=True, text=True, timeout=timeout_s, check=False)
    if res.returncode != 0 or not output.exists():
        raise TraceError(f"xctrace import exited {res.returncode}: "
                         f"{(res.stderr or res.stdout or '').strip() or '<no output>'}")
    log = d / "record.log"
    log.write_text((res.stdout or "") + (res.stderr or ""), encoding="utf-8")
    state = {"trace_id": trace_id, "pid": None, "cmd": None, "device": None,
             "attach": None, "template": template, "time_limit": None,
             "output": str(output), "log": str(log), "started_at": time.time(),
             "status": "imported", "source": str(src), "exports": []}
    _save(state)
    return state


_IMPORTABLE_SUFFIXES = (".atrc", ".ktrace", ".trace")


def _pick_extracted(extract_dir: Path) -> Path:
    entries = sorted(extract_dir.rglob("*"))
    for suffix in _IMPORTABLE_SUFFIXES:
        for e in entries:
            if e.suffix == suffix:
                return e
    top = [e for e in extract_dir.iterdir() if not e.name.startswith(".")]
    if len(top) == 1:
        return top[0]
    raise TraceError(
        f"could not pick a trace file from {extract_dir}: found {[e.name for e in top]}"
    )
