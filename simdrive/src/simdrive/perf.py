"""Performance monitoring — CPU%, memory RSS, thread count, footprint.

Pure simctl + ps based. No XCTest bridge. The simulator app runs as a host-side
macOS process, so once we have its PID via `simctl spawn launchctl list`, plain
`ps` gives us everything we need.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from typing import Optional


def _run(cmd: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def find_app_pid(udid: str, bundle_id: str) -> Optional[int]:
    """Resolve the host-side PID for `bundle_id` running inside `udid`.

    Strategy: `simctl spawn <udid> launchctl list` returns lines like
    `<pid>\t<status>\tUIKitApplication:<bundle>[uuid]`. Match the bundle id
    substring and parse the leading PID column.
    """
    res = _run(["xcrun", "simctl", "spawn", udid, "launchctl", "list"])
    if res.returncode != 0:
        return None
    for line in res.stdout.splitlines():
        if bundle_id not in line:
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            return int(parts[0])
        except ValueError:
            continue
    return None


def snapshot(udid: str, bundle_id: str) -> dict:
    """Return CPU%, memory RSS (MB), thread count for `bundle_id`.

    Returns a dict with: pid, cpu_pct, memory_rss_mb, threads, captured_at.
    Missing PID → pid=None, the rest 0.0/0; the caller decides whether that's
    an error (perf raises, perf_baseline stores it as the baseline shape).
    """
    captured_at = time.time()
    pid = find_app_pid(udid, bundle_id)
    if pid is None:
        return {
            "pid": None,
            "cpu_pct": 0.0,
            "memory_rss_mb": 0.0,
            "threads": 0,
            "captured_at": captured_at,
        }

    # F#9: Sample CPU over a ~200 ms window (3 samples ~100 ms apart) and
    # average the results. A single instant sample often returns 0.0 for an
    # app that is active but currently idle — the window captures bursts that
    # a snapshot would miss.
    _SAMPLE_WINDOW_MS = 200
    _SAMPLE_COUNT = 3
    _SAMPLE_SLEEP_S = (_SAMPLE_WINDOW_MS / 1000.0) / max(_SAMPLE_COUNT - 1, 1)

    cpu_samples: list[float] = []
    rss_mb = 0.0
    threads = 0

    for i in range(_SAMPLE_COUNT):
        res = _run(["ps", "-p", str(pid), "-o", "pcpu=", "-o", "rss="])
        if res.returncode == 0 and res.stdout.strip():
            parts = res.stdout.split()
            if len(parts) >= 2:
                try:
                    cpu_samples.append(float(parts[0]))
                    rss_mb = round(float(parts[1]) / 1024.0, 2)
                except ValueError:
                    pass
        if i < _SAMPLE_COUNT - 1:
            time.sleep(_SAMPLE_SLEEP_S)

    cpu_pct = round(sum(cpu_samples) / len(cpu_samples), 2) if cpu_samples else 0.0

    # `ps -M -p <pid>` lists each thread on its own line; first line is the
    # process header, remaining lines are threads.
    th_res = _run(["ps", "-M", "-p", str(pid)])
    if th_res.returncode == 0:
        lines = [ln for ln in th_res.stdout.splitlines() if ln.strip()]
        threads = max(0, len(lines) - 1)

    return {
        "pid": pid,
        "cpu_pct": cpu_pct,
        "memory_rss_mb": rss_mb,
        "threads": threads,
        "captured_at": captured_at,
        "sample_window_ms": _SAMPLE_WINDOW_MS,
    }


def memory_detail(udid: str, bundle_id: str) -> dict:
    """Detailed memory breakdown via macOS `footprint`.

    Returns either the parsed numbers OR `{"available": False, "reason": ...}`
    when the binary is missing — never raises.
    """
    if shutil.which("footprint") is None:
        return {"available": False, "reason": "footprint binary not in PATH"}

    pid = find_app_pid(udid, bundle_id)
    if pid is None:
        return {"available": False, "reason": f"no running PID for {bundle_id}"}

    res = _run(["/usr/bin/footprint", "-p", str(pid)], timeout=15.0)
    if res.returncode != 0:
        return {
            "available": False,
            "reason": f"footprint exited {res.returncode}: {res.stderr.strip()[:200]}",
        }

    return _parse_footprint(res.stdout, pid=pid)


def _parse_footprint(stdout: str, pid: int) -> dict:
    """Pull totals out of `footprint -p` stdout.

    Layout:
      header line:  `<Name> [<pid>]: 64-bit    Footprint: <N> MB`
      table rows:   `<dirty> <clean> <reclaimable> <regions> <category>`
      total row:    `<dirty>  <clean>  <reclaimable>  <regions>  TOTAL`
      auxiliary:    `phys_footprint: <N> MB`, `phys_footprint_peak: <N> MB`
    """
    import re

    def _val_to_mb(num: float, unit: str) -> float:
        unit = unit.upper()
        if unit.startswith("GB"):
            return round(num * 1024.0, 2)
        if unit.startswith("MB"):
            return round(num, 2)
        if unit.startswith("KB"):
            return round(num / 1024.0, 2)
        if unit.startswith("B"):
            return round(num / (1024.0 * 1024.0), 2)
        return round(num, 2)

    out = {
        "available": True,
        "pid": pid,
        "footprint_mb": 0.0,
        "dirty_mb": 0.0,
        "swapped_mb": 0.0,
        "clean_mb": 0.0,
        "reclaimable_mb": 0.0,
        "phys_footprint_peak_mb": 0.0,
        "captured_at": time.time(),
    }

    header_re = re.compile(r"Footprint:\s+([\d.]+)\s+(\w+)")
    aux_re = re.compile(r"phys_footprint(?:_peak)?:\s+([\d.]+)\s+(\w+)")
    total_re = re.compile(
        r"^\s*([\d.]+)\s+(\w+)\s+([\d.]+)\s+(\w+)\s+([\d.]+)\s+(\w+)\s+\d+\s+TOTAL\s*$"
    )

    for line in stdout.splitlines():
        m = header_re.search(line)
        if m and out["footprint_mb"] == 0.0:
            out["footprint_mb"] = _val_to_mb(float(m.group(1)), m.group(2))
            continue
        m = total_re.match(line)
        if m:
            out["dirty_mb"] = _val_to_mb(float(m.group(1)), m.group(2))
            out["clean_mb"] = _val_to_mb(float(m.group(3)), m.group(4))
            out["reclaimable_mb"] = _val_to_mb(float(m.group(5)), m.group(6))
            continue
        if line.strip().startswith("phys_footprint_peak"):
            m = aux_re.search(line)
            if m:
                out["phys_footprint_peak_mb"] = _val_to_mb(float(m.group(1)), m.group(2))
        elif line.strip().startswith("phys_footprint"):
            m = aux_re.search(line)
            if m and out["footprint_mb"] == 0.0:
                out["footprint_mb"] = _val_to_mb(float(m.group(1)), m.group(2))

    return out


def severity(delta: dict) -> str:
    """Classify a perf delta. Bands match the spec: high > medium > low."""
    if delta.get("memory_rss_mb", 0) > 50 or delta.get("threads", 0) > 10:
        return "high"
    if delta.get("cpu_pct", 0) > 25:
        return "medium"
    return "low"
