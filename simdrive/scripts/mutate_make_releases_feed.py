#!/usr/bin/env python3
"""Targeted mutation test for the WS-4 publishing pipeline's signing /
validation critical path (scripts/make_releases_feed.py).

Companion to mutate_update_check.py — proves the publish-flow promises are
test-guarded: canonical byte encoding (what the operator signs), the mirror
validator's contract rules, detached-sig integrity, the dogfood step's
fail-closed verification, and highest-version tag selection. Each mutant must
be KILLED (tests fail); a survivor is a real test gap.

Usage:  python3.11 scripts/mutate_make_releases_feed.py
Exit 0 = all mutants killed; exit 1 = at least one survived.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
TARGET = PKG.parent / "scripts" / "make_releases_feed.py"
TESTS = ["tests/test_make_releases_feed.py"]

MUTANTS = [
    (
        "canonical encoding drift: sort_keys dropped (signed bytes differ "
        "from the canonical producer's)",
        'json.dumps(obj, indent=2, sort_keys=True) + "\\n"',
        'json.dumps(obj, indent=2) + "\\n"  # MUTANT',
    ),
    (
        "https-only rule dropped from the mirror validator",
        '    if "notes_url" in obj and not str(obj["notes_url"]).startswith("https://"):',
        "    if False:  # MUTANT",
    ),
    (
        "min_supported>latest ordering check dropped",
        "            if latest is not None and min_v > latest:",
        "            if False:  # MUTANT",
    ),
    (
        "signature over the wrong bytes (whitespace-normalized before sign)",
        "    sig = SigningKey(seed).sign(raw).signature",
        "    sig = SigningKey(seed).sign(raw.rstrip()).signature  # MUTANT",
    ),
    (
        "dogfood step stops verifying (tampered staged pair accepted)",
        "    feed = consumer.verify_feed(raw, sig_text, verify_keys=keys)",
        "    feed = json.loads(raw)  # MUTANT",
    ),
    (
        "tag selection: alphabetically-last tag instead of highest version",
        "    return max(versioned)[1]",
        "    return versioned[-1][1]  # MUTANT",
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
    print("all mutants killed — publishing-pipeline signing/validation "
          "promises are test-guarded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
