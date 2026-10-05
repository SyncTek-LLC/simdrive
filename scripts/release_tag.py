#!/usr/bin/env python3
"""Derive the release tag for the publish workflow (FU-2026-072).

push (tag)         -> tag from GITHUB_REF; must equal simdrive-v<pyproject version>
workflow_dispatch  -> simdrive-v<pyproject version> (GITHUB_REF is a branch)

Prints the tag on stdout; exits non-zero with a message on any mismatch.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TAG_PREFIX = "simdrive-v"
_VERSION_RE = re.compile(r'^version\s*=\s*"(?P<ver>[^"]+)"', re.MULTILINE)


def _pyproject_version(text: str) -> str:
    m = _VERSION_RE.search(text)
    if not m:
        raise ValueError('pyproject.toml has no top-level version = "..." line')
    return m.group("ver")


def derive_tag(event: str, ref: str, pyproject_text: str) -> str:
    expected = f"{TAG_PREFIX}{_pyproject_version(pyproject_text)}"
    if event == "workflow_dispatch":
        return expected
    if event == "push":
        if not ref.startswith("refs/tags/"):
            raise ValueError(f"push ref {ref!r} is not a tag; refusing to derive a release tag")
        tag = ref.removeprefix("refs/tags/")
        if tag != expected:
            raise ValueError(f"version mismatch: tag={tag!r}, pyproject expects {expected!r}")
        return tag
    raise ValueError(f"unsupported event {event!r} (expected push or workflow_dispatch)")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--event", required=True)
    p.add_argument("--ref", required=True)
    p.add_argument("--pyproject", default="simdrive/pyproject.toml")
    args = p.parse_args(argv)
    try:
        tag = derive_tag(args.event, args.ref, Path(args.pyproject).read_text())
    except ValueError as exc:
        print(f"release_tag: {exc}", file=sys.stderr)
        return 1
    print(tag)
    return 0


if __name__ == "__main__":
    sys.exit(main())
