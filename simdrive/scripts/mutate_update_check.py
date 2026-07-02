#!/usr/bin/env python3
"""Targeted mutation test for the trust/privacy predicates in update/check.py.

Companion to mutate_telemetry.py / mutate_crash_sink.py — proves the signed
update-check consumer's promises are test-guarded: signature membership
pinning, HEKA_OFFLINE mute, and the canonical zero-user-data call-log record.
Each mutant must be KILLED (tests fail); a survivor is a real test gap.

Usage:  python3.11 scripts/mutate_update_check.py
Exit 0 = all mutants killed; exit 1 = at least one survived.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
TARGET = PKG / "src" / "simdrive" / "update" / "check.py"
TESTS = ["tests/test_update_check.py"]

MUTANTS = [
    (
        "signature requirement removed (unverified feed trusted)",
        "    verified = False",
        "    verified = True  # MUTANT",
    ),
    (
        "user data creeps into payload_shape (no longer zero-user-data)",
        '        "payload_shape": [],',
        '        "payload_shape": ["version"],  # MUTANT',
    ),
    (
        "call-log method drifts off the canonical record",
        '        "method": "GET",',
        '        "method": "POST",  # MUTANT',
    ),
    (
        "HEKA_OFFLINE mute removed",
        '    if os.environ.get(_OFFLINE_ENV) == "1":\n        return True',
        "    if False:\n        return True  # MUTANT",
    ),
    (
        "refusal logged as ok=True (audit log lies)",
        'ok=False, result="signature_unverified"',
        'ok=True, result="signature_unverified"',
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
    print("all mutants killed — update-check trust/privacy predicates are test-guarded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
