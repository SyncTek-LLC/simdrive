#!/usr/bin/env python3
"""Targeted mutation test for the privacy-critical predicates in telemetry.py.

There is no mutmut/cosmic-ray in this environment, so this applies a fixed set
of mutations to the load-bearing decision points of the opt-in / kill-switch
logic, runs the focused telemetry + NOSAAS test suites against each mutant, and
asserts every mutant is KILLED (tests fail). A surviving mutant = a real gap in
the tests guarding the promise.

Usage:  python3.11 scripts/mutate_telemetry.py
Exit 0 = all mutants killed; exit 1 = at least one survived.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
TARGET = PKG / "src" / "simdrive" / "license" / "telemetry.py"
TESTS = [
    "tests/test_trial_source_attribution.py",
    "tests/test_heka_sim_nosaas.py",
]

# (label, unique_search, replacement) — each flips one promise-critical decision.
MUTANTS = [
    (
        "default-off flipped to on (opt-in-by-default regression)",
        '    if not opt_out_path.exists():\n        return True  # DEFAULT OFF',
        '    if not opt_out_path.exists():\n        return False  # MUTANT',
    ),
    (
        "kill-switch neutered (never reports killed)",
        "    return raw.strip().lower() in _KILL_SWITCH_OFF_VALUES",
        "    return False  # MUTANT",
    ),
    (
        "durable opt-in (track=true) broken",
        '            if normalized == "true":\n                return False  # explicit, durable opt-in',
        '            if normalized == "true":\n                return True  # MUTANT',
    ),
    (
        "sender kill-switch guard removed",
        '    if telemetry_killed():\n        return False, "telemetry disabled (HEKA_TELEMETRY kill-switch)"\n    payload = build_payload(email, source)',
        '    if False:\n        return False, "telemetry disabled (HEKA_TELEMETRY kill-switch)"\n    payload = build_payload(email, source)',
    ),
    (
        "maybe_send opt-out gate bypassed",
        '    if is_opted_out(opt_out_path):\n        return (\n            "telemetry off',
        '    if False and is_opted_out(opt_out_path):\n        return (\n            "telemetry off',
    ),
]


def run_tests() -> bool:
    """Return True if the focused suites PASS."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *TESTS],
        cwd=PKG,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def main() -> int:
    original = TARGET.read_text(encoding="utf-8")

    # Sanity: baseline must be green before we mutate.
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
            status = "SURVIVED" if passed else "killed"
            print(f"  [{status}] {label}")
            if passed:
                survivors.append(label)
            TARGET.write_text(original, encoding="utf-8")
    finally:
        TARGET.write_text(original, encoding="utf-8")

    total = len(MUTANTS)
    killed = total - len(survivors)
    print(f"\nmutation score: {killed}/{total} killed")
    if survivors:
        print("SURVIVORS (test gaps):")
        for s in survivors:
            print("  -", s)
        return 1
    print("all mutants killed — promise-critical predicates are test-guarded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
