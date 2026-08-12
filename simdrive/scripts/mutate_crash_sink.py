#!/usr/bin/env python3
"""Targeted mutation test for the privacy-critical predicates in crash_sink.py.

Companion to mutate_telemetry.py — extends the mutation harness to the crash
sink so the "scrub-to-basename / drop-message / disabled-guard / no-fingerprint"
guarantees are proven test-guarded. Each mutant must be KILLED (tests fail); a
survivor is a real gap in the crash-sink tests.

Usage:  python3.11 scripts/mutate_crash_sink.py
Exit 0 = all mutants killed; exit 1 = at least one survived.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
TARGET = PKG / "src" / "simdrive" / "observability" / "crash_sink.py"
TESTS = ["tests/test_crash_sink.py"]

MUTANTS = [
    (
        "path scrub removed (basename → full path leaks username)",
        '                "file": os.path.basename(frame.filename or ""),',
        '                "file": (frame.filename or ""),  # MUTANT',
    ),
    (
        "exception message leaked into exc_type",
        '        "exc_type": type(exc).__name__,',
        '        "exc_type": f"{type(exc).__name__}: {exc}",  # MUTANT',
    ),
    (
        "disabled-env guard removed",
        '    if os.environ.get(_DISABLE_ENV) == "1":\n        return None',
        '    if False:\n        return None  # MUTANT',
    ),
    (
        "python fingerprint widened (major.minor → major.minor.micro)",
        '    return f"{sys.version_info.major}.{sys.version_info.minor}"',
        '    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"  # MUTANT',
    ),
]


def run_tests() -> bool:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *TESTS],
        cwd=PKG,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def main() -> int:
    original = TARGET.read_text(encoding="utf-8")
    if not run_tests():
        print("BASELINE RED — fix tests before mutation testing.")
        return 1
    print("baseline: GREEN")

    survivors = []
    try:
        for label, search, replace in MUTANTS:
            if search not in original:
                print(f"SKIP (anchor not found): {label}")
                survivors.append(label + " [anchor missing]")
                continue
            TARGET.write_text(original.replace(search, replace, 1), encoding="utf-8")
            passed = run_tests()
            print(f"  [{'SURVIVED' if passed else 'killed'}] {label}")
            if passed:
                survivors.append(label)
            TARGET.write_text(original, encoding="utf-8")
    finally:
        TARGET.write_text(original, encoding="utf-8")

    total = len(MUTANTS)
    print(f"\nmutation score: {total - len(survivors)}/{total} killed")
    if survivors:
        print("SURVIVORS (test gaps):")
        for s in survivors:
            print("  -", s)
        return 1
    print("all mutants killed — crash-sink scrub predicates are test-guarded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
