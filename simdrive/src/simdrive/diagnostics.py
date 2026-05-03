"""Environment readiness, app state, app listing, crash report retrieval."""
from __future__ import annotations

import json
import plistlib
import subprocess
from pathlib import Path
from typing import Optional

from . import hid_inject


def _run(cmd: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


# ----------------------------- doctor ---------------------------------- #


def doctor() -> dict:
    """Probe Xcode CLT / simctl runtimes / booted devices / native HID helper.

    Each check returns regardless of pass/fail; ok = all individual checks ok.
    """
    checks: list[dict] = []

    # 1. xcode-select -p returns a path
    res = _run(["xcode-select", "-p"], timeout=5.0)
    xcode_path = res.stdout.strip()
    checks.append({
        "name": "xcode_select",
        "ok": res.returncode == 0 and bool(xcode_path),
        "detail": xcode_path if xcode_path else (res.stderr.strip() or "no path"),
    })

    # 2. simctl runtimes
    res = _run(["xcrun", "simctl", "list", "runtimes", "--json"], timeout=10.0)
    runtimes_count = 0
    detail = ""
    if res.returncode == 0:
        try:
            data = json.loads(res.stdout)
            runtimes_count = len(data.get("runtimes", []))
            detail = f"{runtimes_count} runtime(s)"
        except json.JSONDecodeError as exc:
            detail = f"runtimes JSON parse failed: {exc}"
    else:
        detail = res.stderr.strip() or "simctl list runtimes failed"
    checks.append({
        "name": "simctl_runtimes",
        "ok": runtimes_count > 0,
        "detail": detail,
    })

    # 3. booted devices
    res = _run(["xcrun", "simctl", "list", "devices", "booted", "--json"], timeout=10.0)
    booted: list[str] = []
    detail = ""
    if res.returncode == 0:
        try:
            data = json.loads(res.stdout)
            for runtime, devs in data.get("devices", {}).items():
                for d in devs:
                    if str(d.get("state", "")).lower() == "booted":
                        booted.append(d.get("udid", ""))
            detail = f"{len(booted)} booted: {booted}"
        except json.JSONDecodeError as exc:
            detail = f"booted devices JSON parse failed: {exc}"
    else:
        detail = res.stderr.strip() or "simctl list devices booted failed"
    checks.append({
        "name": "simctl_booted_devices",
        "ok": len(booted) > 0,
        "detail": detail,
    })

    # 4. native HID helper presence (the bundled simdrive-input binary)
    hid_ok = hid_inject.available()
    bin_path = hid_inject._binary_path()
    checks.append({
        "name": "hid_helper",
        "ok": hid_ok,
        "detail": str(bin_path) if bin_path else "simdrive-input binary missing",
    })

    return {"ok": all(c["ok"] for c in checks), "checks": checks}


# ----------------------------- app_state ------------------------------- #


def app_state(udid: str, bundle_id: str) -> dict:
    """Heuristic app lifecycle state.

    simctl doesn't expose true foreground/background distinction without an
    XCTest bridge. Practical heuristic: presence in `launchctl list` → the app
    has a process and is foreground (most common case); absence → not-running.
    "background" / "suspended" are reserved for future bridge-backed paths.
    """
    res = _run(["xcrun", "simctl", "spawn", udid, "launchctl", "list"])
    if res.returncode != 0:
        return {
            "state": "not-running",
            "bundle_id": bundle_id,
            "pid": None,
            "detail": res.stderr.strip()[:200],
        }
    for line in res.stdout.splitlines():
        if bundle_id not in line:
            continue
        parts = line.split()
        pid: Optional[int] = None
        if parts:
            try:
                pid = int(parts[0])
            except ValueError:
                pid = None
        return {
            "state": "foreground",
            "bundle_id": bundle_id,
            "pid": pid,
        }
    return {"state": "not-running", "bundle_id": bundle_id, "pid": None}


# ------------------------------- apps ---------------------------------- #


def list_apps(udid: str) -> list[dict]:
    """Parse `xcrun simctl listapps <udid>` (returns plist) into a flat list.

    Each entry: bundle_id, name, version, path.
    """
    res = _run(["xcrun", "simctl", "listapps", udid], timeout=15.0)
    if res.returncode != 0:
        # listapps emits the plist on stderr in some Xcode versions; try that.
        body = res.stdout if res.stdout.strip() else res.stderr
        if not body.strip():
            return []
    else:
        body = res.stdout

    # `simctl listapps` emits OpenStep ASCII plist, which Python's plistlib
    # can't read. Round-trip through `plutil -convert json` to get a JSON
    # form Python can parse. Try plistlib first (binary/XML) and json second
    # (in case a future Xcode version changes format) before falling back to plutil.
    data = None
    try:
        data = plistlib.loads(body.encode("utf-8"))
    except Exception:
        try:
            data = json.loads(body)
        except Exception:
            try:
                conv = subprocess.run(
                    ["plutil", "-convert", "json", "-o", "-", "-"],
                    input=body, capture_output=True, text=True,
                    timeout=10.0, check=False,
                )
                if conv.returncode == 0 and conv.stdout.strip():
                    data = json.loads(conv.stdout)
            except Exception:
                return []
    if data is None:
        return []

    out: list[dict] = []
    if not isinstance(data, dict):
        return out
    for bundle_id, info in data.items():
        if not isinstance(info, dict):
            continue
        out.append({
            "bundle_id": bundle_id,
            "name": info.get("CFBundleDisplayName") or info.get("CFBundleName") or "",
            "version": info.get("CFBundleShortVersionString") or info.get("CFBundleVersion") or "",
            "path": info.get("Path") or "",
        })
    out.sort(key=lambda a: a["name"].lower())
    return out


# ----------------------------- crashes --------------------------------- #


_DIAGNOSTIC_REPORTS_DIR = Path.home() / "Library" / "Logs" / "DiagnosticReports"


def _ips_header(path: Path) -> dict:
    """Read the JSON-on-first-line preamble of a .ips file. {} on parse failure."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            first = f.readline().strip()
        if not first:
            return {}
        return json.loads(first)
    except Exception:
        return {}


def _ips_body_backtrace(path: Path) -> list[str]:
    """Pull the first ~10 lines of the crashing thread's backtrace from the body.

    .ips files are usually one-line-JSON header + multi-line JSON body. We try
    to load the body as JSON; if that fails, fall back to the raw first lines
    of the file (still useful context for an agent to scan).
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    # Body starts after first newline.
    body_start = text.find("\n")
    if body_start < 0:
        return []
    body = text[body_start + 1:].strip()
    if not body:
        return []
    try:
        raw = json.loads(body)
    except json.JSONDecodeError:
        return body.splitlines()[:10]

    crashing = raw.get("crashing_thread")
    threads = raw.get("threads") or []
    for t in threads:
        if isinstance(t, dict) and t.get("triggered") is True:
            bt = t.get("frames") or t.get("backtrace") or []
            return [str(line) for line in bt[:10]]
    if isinstance(crashing, int):
        for t in threads:
            if isinstance(t, dict) and t.get("id") == crashing:
                bt = t.get("frames") or t.get("backtrace") or []
                return [str(line) for line in bt[:10]]
    return []


def list_crashes(
    since_ts: float = 0.0,
    bundle_id: Optional[str] = None,
    max_results: int = 10,
    reports_dir: Optional[Path] = None,
) -> list[dict]:
    """Return up to `max_results` `.ips` reports newer than `since_ts`,
    optionally filtered by `bundle_id`. Sorted newest-first."""
    base = reports_dir or _DIAGNOSTIC_REPORTS_DIR
    if not base.exists():
        return []
    candidates: list[tuple[float, Path]] = []
    for p in base.iterdir():
        if p.suffix != ".ips":
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime < since_ts:
            continue
        candidates.append((mtime, p))

    candidates.sort(key=lambda pair: pair[0], reverse=True)

    out: list[dict] = []
    for mtime, p in candidates:
        header = _ips_header(p)
        crash_bundle = header.get("bundleID") or header.get("bundle_id") or header.get("app_name") or ""
        if bundle_id and bundle_id not in crash_bundle:
            continue
        out.append({
            "path": str(p),
            "name": p.name,
            "timestamp": header.get("timestamp", ""),
            "exception": header.get("exception", "") or header.get("bug_type", ""),
            "bundle_id": crash_bundle,
            "mtime": mtime,
            "backtrace_first_lines": _ips_body_backtrace(p),
        })
        if len(out) >= max_results:
            break
    return out
