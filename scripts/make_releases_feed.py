#!/usr/bin/env python3
"""make_releases_feed.py — simdrive's interim WS-4 publishing-pipeline tool.

Implements the operator-facing steps of the frozen publish flow
(simdrive/docs/design/ws4-releases-publishing-pipeline.md §4):

    --produce    step 1: emit canonical releases.json from a simdrive-v* tag
    --validate   step 2: mirror of the canonical shared-schema validator
    --sign       step 3: offline detached Ed25519 signature (base64url)
    --dogfood    step 4: drive the SHIPPED consumer verify path against a
                 staged pair with an injected staging keyset (Q2 adjudication:
                 no env trust-anchor override — key injection happens here,
                 in-process, via the consumer's verify_keys= parameter)
    --keygen / --verify: publisher key helpers (canonical file conventions)
    --self-test  the §6.3 CI self-test: throwaway keypair, produce + sign a
                 fixture, validate + shipped-consumer verify, tamper → reject

INTERIM: harness-lane's canonical producer (`harness update-check --produce`,
core/lib/update_check.py) is the long-term generator; this script exists so a
simdrive release cut needs no harness checkout. Encoding, validation rules,
and key-file formats deliberately MIRROR the canonical module — parity is
locked by tests/test_make_releases_feed.py::TestCanonicalParity whenever the
reference module is reachable. NOTE: the canonical producer picks bare v*
tags; this repo's v* tags are legacy specterqa releases, so the simdrive feed
is produced from simdrive-v* tags (raised to harness-lane via Q4).

The feed-signing private key never enters CI, is NOT the license-signing key,
and follows the §3 custody/rotation runbook.
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_SHIPPED_SRC = REPO / "simdrive" / "src"

SCHEMA_MAJOR = 1
UNSUPPORTED_MAJOR = "schema_version: unsupported major (fail-open skip)"
_REQUIRED_KEYS = ("schema_version", "product", "latest", "released_at")
DEFAULT_TAG_PREFIX = "simdrive-v"
DEFAULT_PRODUCT = "simdrive"


# ---------------------------------------------------------------------------
# codecs — byte-compatible with the canonical producer
# ---------------------------------------------------------------------------


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_decode(text: str) -> bytes:
    text = text.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        raise ValueError("signature is not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def canonical_bytes(obj: dict) -> bytes:
    """The exact encoding the operator signs. Producing twice must be
    byte-identical; consumers verify received bytes as-is, never re-encode."""
    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# version ordering — PEP 440-lite, mirroring the canonical module EXACTLY.
# The mirror must be no more lenient than the reference: publish step 2 has to
# refuse everything the canonical validator refuses (e.g. .post1/.dev/epoch).
# ---------------------------------------------------------------------------

_VERSION_RE = re.compile(
    r"^(?P<release>\d+(?:\.\d+)*)(?:(?P<pre_kind>a|b|rc)(?P<pre_num>\d+))?$")
_PRE_RANK = {"a": 0, "b": 1, "rc": 2, None: 3}  # final release sorts last


def parse_version(s: str) -> tuple:
    m = _VERSION_RE.match(str(s).strip())
    if not m:
        raise ValueError(f"unparseable version: {s!r}")
    release = tuple(int(p) for p in m.group("release").split("."))
    release = release + (0,) * (4 - len(release))  # 1.9 < 1.10, 1.0 == 1.0.0
    return (release, _PRE_RANK[m.group("pre_kind")],
            int(m.group("pre_num") or 0))


# ---------------------------------------------------------------------------
# mirror validator (publish step 2) — rules mirror the canonical validate_feed
# ---------------------------------------------------------------------------


def validate_feed(obj, expected_product: str | None = None) -> list:
    """Returns a list of error strings — empty means valid. An unsupported
    schema major returns EXACTLY [UNSUPPORTED_MAJOR] (the fail-open skip
    class), matching the canonical validator."""
    if not isinstance(obj, dict):
        return ["feed: not a JSON object"]
    sv = obj.get("schema_version")
    if not isinstance(sv, int) or sv < 1:
        return ["schema_version: must be an integer >= 1"]
    if sv != SCHEMA_MAJOR:
        return [UNSUPPORTED_MAJOR]

    errors = []
    for key in _REQUIRED_KEYS:
        if key not in obj:
            errors.append(f"{key}: required key missing")
    if errors:
        return errors

    product = obj["product"]
    if not isinstance(product, str) or not product:
        errors.append("product: must be a non-empty string")
    elif expected_product is not None and product != expected_product:
        errors.append(
            f"product: feed is for {product!r}, expected {expected_product!r}")

    latest = None
    try:
        latest = parse_version(obj["latest"])
    except ValueError:
        errors.append(f"latest: unparseable version {obj['latest']!r}")

    if "min_supported" in obj:
        try:
            min_v = parse_version(obj["min_supported"])
            if latest is not None and min_v > latest:
                errors.append("min_supported: exceeds latest")
        except ValueError:
            errors.append(
                f"min_supported: unparseable version {obj['min_supported']!r}")

    ra = obj["released_at"]
    try:
        _dt.datetime.fromisoformat(str(ra).replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"released_at: not ISO 8601 ({ra!r})")

    if "notes_url" in obj and not str(obj["notes_url"]).startswith("https://"):
        errors.append("notes_url: must be https://")

    channels = obj.get("channels", {})
    if not isinstance(channels, dict):
        errors.append("channels: must be an object")
    else:
        for name, ver in channels.items():
            try:
                parse_version(ver)
            except ValueError:
                errors.append(f"channels[{name}]: unparseable version {ver!r}")

    if "key_id" in obj and not isinstance(obj["key_id"], str):
        errors.append("key_id: must be a string")
    return errors


# ---------------------------------------------------------------------------
# produce (publish step 1) — feed from a simdrive-v* git tag, deterministic
# ---------------------------------------------------------------------------


def _git(repo, *args) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _tag_version(tag: str, prefix: str) -> str:
    if tag.startswith(prefix):
        return tag[len(prefix):]
    if tag.startswith("v"):
        return tag[1:]
    return tag


def _pick_tag(repo, prefix: str) -> str:
    """Highest version among <prefix>* tags — NOT the most recent commit; a
    hotfix tag on an old branch must not hide a newer release. Bare v* tags
    are ignored (legacy specterqa releases in this repo)."""
    tags = [t for t in _git(repo, "tag", "--list", f"{prefix}*").splitlines()
            if t]
    versioned = []
    for t in tags:
        try:
            versioned.append((parse_version(t[len(prefix):]), t))
        except ValueError:
            continue
    if not versioned:
        raise ValueError(f"no {prefix}* version tags found in {repo}")
    return max(versioned)[1]


def _tag_date_utc(repo, tag: str) -> str:
    iso = _git(repo, "log", "-1", "--format=%cI", tag)
    dt = _dt.datetime.fromisoformat(iso).astimezone(_dt.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def produce_feed(*, repo, product: str = DEFAULT_PRODUCT,
                 tag: str | None = None,
                 tag_prefix: str = DEFAULT_TAG_PREFIX,
                 min_supported: str | None = None,
                 notes_url: str | None = None,
                 channels: dict | None = None,
                 key_id: str | None = None) -> bytes:
    """Emit canonical releases.json bytes from a git tag. Deterministic:
    version from the tag name, released_at from the tag's commit date —
    producing twice yields byte-identical output, so the operator signs
    exactly what was produced. Validates before emitting; raises ValueError
    on any contract violation."""
    tag = tag or _pick_tag(repo, tag_prefix)
    version = _tag_version(tag, tag_prefix)
    obj = {
        "schema_version": SCHEMA_MAJOR,
        "product": product,
        "latest": version,
        "released_at": _tag_date_utc(repo, tag),
        "channels": channels or {"stable": version},
    }
    if min_supported is not None:
        obj["min_supported"] = min_supported
    if notes_url is not None:
        obj["notes_url"] = notes_url
    if key_id is not None:
        obj["key_id"] = key_id
    errors = validate_feed(obj, expected_product=product)
    if errors:
        raise ValueError("produced feed fails validation: " + "; ".join(errors))
    return canonical_bytes(obj)


# ---------------------------------------------------------------------------
# publisher key helpers (offline; the private key never enters CI)
# ---------------------------------------------------------------------------


def _nacl():
    try:
        from nacl.signing import SigningKey, VerifyKey  # noqa: PLC0415
        return SigningKey, VerifyKey
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise SystemExit(
            "PyNaCl is required for signing/verification "
            "(pip install pynacl)") from exc


def load_keyset_file(path) -> dict:
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    keys = obj.get("keys", obj) if isinstance(obj, dict) else {}
    if not keys or not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in keys.items()):
        raise ValueError(f"keyset {path}: expected {{key_id: hex_pubkey}}")
    return keys


def keygen(key_id: str, out_dir: Path) -> Path:
    """Write <key_id>.secret (hex seed, 0600) + <key_id>.keyset.json —
    the canonical publisher key-file conventions."""
    SigningKey, _ = _nacl()
    sk = SigningKey(os.urandom(32))
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    secret = out_dir / f"{key_id}.secret"
    secret.touch(mode=0o600, exist_ok=True)
    secret.write_text(bytes(sk).hex() + "\n", encoding="utf-8")
    secret.chmod(0o600)
    keyset = out_dir / f"{key_id}.keyset.json"
    keyset.write_text(json.dumps(
        {"schema_version": 1,
         "keys": {key_id: sk.verify_key.encode().hex()}},
        indent=2) + "\n", encoding="utf-8")
    return keyset


def sign_feed(feed_path: Path, secret_path: Path,
              sig_path: Path | None = None) -> Path:
    """Detached base64url Ed25519 signature over the EXACT feed bytes."""
    SigningKey, _ = _nacl()
    raw = Path(feed_path).read_bytes()
    seed = bytes.fromhex(Path(secret_path).read_text().strip())
    sig = SigningKey(seed).sign(raw).signature
    out = Path(sig_path or (str(feed_path) + ".sig"))
    out.write_text(b64url_encode(sig) + "\n", encoding="utf-8")
    return out


def verify_pair(feed_path: Path, keyset: dict,
                sig_path: Path | None = None) -> str | None:
    """Membership-pinned verification: try every keyset key against the raw
    bytes; returns the key_id that verified, or None. The feed's own key_id
    claim never selects (it sits inside the bytes being verified)."""
    _, VerifyKey = _nacl()
    raw = Path(feed_path).read_bytes()
    sig = b64url_decode(
        Path(sig_path or (str(feed_path) + ".sig")).read_text(encoding="utf-8"))
    for key_id, pub_hex in keyset.items():
        try:
            VerifyKey(bytes.fromhex(pub_hex)).verify(raw, sig)
            return key_id
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# --dogfood (publish step 4): the SHIPPED consumer verifies the staged pair
# ---------------------------------------------------------------------------


def dogfood(staging_dir: Path, keyset: dict, current: str) -> dict:
    """Run the shipped consumer's verify path (simdrive.update.check) against
    a staged releases.json/.sig pair with the staging keyset injected via the
    verify_keys= parameter — the Q2-adjudicated test-only injection. This is
    step 4 because `requests` (the consumer's transport) has no file://
    adapter: the staged pair is fed to the verify path directly, which is
    exactly the code real clients run after their fetch."""
    if str(_SHIPPED_SRC) not in sys.path:
        sys.path.insert(0, str(_SHIPPED_SRC))
    from nacl.signing import VerifyKey  # noqa: PLC0415
    from simdrive.update import check as consumer  # noqa: PLC0415

    staging_dir = Path(staging_dir)
    raw = (staging_dir / "releases.json").read_bytes()
    sig_text = (staging_dir / "releases.json.sig").read_text(encoding="utf-8")
    keys = [VerifyKey(bytes.fromhex(h)) for h in keyset.values()]

    feed = consumer.verify_feed(raw, sig_text, verify_keys=keys)
    if feed is None:
        return {"verified": False, "status": "unverified",
                "message": "staged pair refused by the shipped consumer"}
    errors = validate_feed(feed, expected_product=DEFAULT_PRODUCT)
    if errors:
        return {"verified": False, "status": "invalid",
                "message": "; ".join(errors)}
    adv = consumer.evaluate(current, feed)
    return {"verified": True, "status": adv.status, "latest": adv.latest,
            "current": current, "message": adv.message}


# ---------------------------------------------------------------------------
# --self-test (§6.3): throwaway keypair → produce → sign → validate →
# shipped-consumer verify → tamper one byte → must reject. No secrets needed.
# ---------------------------------------------------------------------------


def self_test() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        repo = tmp / "repo"
        repo.mkdir()
        env = {"GIT_AUTHOR_NAME": "ci", "GIT_AUTHOR_EMAIL": "ci@ci",
               "GIT_COMMITTER_NAME": "ci", "GIT_COMMITTER_EMAIL": "ci@ci",
               "HOME": td, "PATH": os.environ.get("PATH", "")}

        def g(*args):
            subprocess.run(["git", *args], cwd=repo, env=env, check=True,
                           capture_output=True)

        g("init", "-q")
        (repo / "x").write_text("x")
        g("add", "x")
        g("commit", "-qm", "release")
        g("tag", "simdrive-v9.9.9")

        keyset_path = keygen("feed-selftest", tmp / "keys")
        keyset = load_keyset_file(keyset_path)

        raw = produce_feed(repo=repo, product="simdrive",
                           notes_url="https://simdrive.dev/changelog/",
                           key_id="feed-selftest")
        errors = validate_feed(json.loads(raw), expected_product="simdrive")
        if errors:
            print(f"self-test: FAIL (validator: {errors})", file=sys.stderr)
            return 1

        staging = tmp / "staging"
        staging.mkdir()
        feed_path = staging / "releases.json"
        feed_path.write_bytes(raw)
        sign_feed(feed_path, tmp / "keys" / "feed-selftest.secret")

        result = dogfood(staging, keyset, current="1.0.0")
        if not (result["verified"] and result["status"] == "update_available"
                and result["latest"] == "9.9.9"):
            print(f"self-test: FAIL (dogfood: {result})", file=sys.stderr)
            return 1

        feed_path.write_bytes(raw[:-2] + b"X\n")  # tamper exactly one byte
        if verify_pair(feed_path, keyset) is not None:
            print("self-test: FAIL (tampered feed verified!)", file=sys.stderr)
            return 1
        tampered = dogfood(staging, keyset, current="1.0.0")
        if tampered["verified"]:
            print("self-test: FAIL (consumer accepted tampered feed!)",
                  file=sys.stderr)
            return 1

    print("self-test: PASS (keygen → produce → validate → sign → "
          "shipped-consumer verify → tamper-1-byte reject)")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="make_releases_feed",
        description="simdrive releases.json publishing pipeline "
                    "(interim generator + offline signing + dogfood).")
    p.add_argument("--produce", action="store_true",
                   help="emit releases.json from a simdrive-v* git tag")
    p.add_argument("--repo", default=str(REPO), help="repo for --produce")
    p.add_argument("--tag", default=None,
                   help="tag for --produce (default: highest simdrive-v* tag)")
    p.add_argument("--tag-prefix", default=DEFAULT_TAG_PREFIX)
    p.add_argument("--product", default=DEFAULT_PRODUCT)
    p.add_argument("--min-supported", default=None)
    p.add_argument("--notes-url", default=None)
    p.add_argument("--channel", action="append", default=[],
                   metavar="NAME=VERSION")
    p.add_argument("--key-id", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--validate", metavar="FEED",
                   help="validate a releases.json (mirror of the canonical "
                        "validator)")
    p.add_argument("--sign", metavar="FEED",
                   help="sign FEED with --key (offline publisher step)")
    p.add_argument("--key", default=None, help="secret seed file for --sign")
    p.add_argument("--verify", metavar="FEED",
                   help="verify FEED against its detached .sig (needs --keys)")
    p.add_argument("--sig", default=None, help="sig path (default FEED.sig)")
    p.add_argument("--keys", default=None,
                   help="keyset JSON ({key_id: hex_pubkey}) for "
                        "--verify/--dogfood")
    p.add_argument("--keygen", action="store_true")
    p.add_argument("--out-dir", default=None, help="key dir for --keygen")
    p.add_argument("--dogfood", metavar="DIR",
                   help="verify a staged releases.json/.sig pair via the "
                        "SHIPPED consumer (needs --keys and --current)")
    p.add_argument("--current", default=None,
                   help="installed version to advise against for --dogfood")
    p.add_argument("--json", action="store_true", help="machine output")
    p.add_argument("--self-test", action="store_true",
                   help="run the CI self-test (throwaway keypair, no secrets)")
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    if args.self_test:
        return self_test()

    if args.keygen:
        if not args.key_id or not args.out_dir:
            print("--keygen requires --key-id and --out-dir", file=sys.stderr)
            return 2
        keyset = keygen(args.key_id, Path(args.out_dir))
        print(f"keypair written to {args.out_dir} "
              f"({args.key_id}.secret is the OFFLINE private half — "
              f"never commit it, never give it to CI); keyset: {keyset}")
        return 0

    if args.validate:
        obj = json.loads(Path(args.validate).read_text(encoding="utf-8"))
        errors = validate_feed(obj, expected_product=args.product)
        for e in errors:
            print(f"INVALID: {e}", file=sys.stderr)
        if not errors:
            print(f"valid: {args.validate}")
        return 1 if errors else 0

    if args.sign:
        if not args.key:
            print("--sign requires --key <secretfile>", file=sys.stderr)
            return 2
        out = sign_feed(Path(args.sign), Path(args.key),
                        Path(args.out) if args.out else None)
        print(f"signed: {out}")
        return 0

    if args.verify:
        if not args.keys:
            print("--verify requires --keys <keysetfile>", file=sys.stderr)
            return 2
        key_id = verify_pair(Path(args.verify), load_keyset_file(args.keys),
                             Path(args.sig) if args.sig else None)
        if key_id is None:
            print("UNVERIFIED: signature does not match any trusted key",
                  file=sys.stderr)
            return 1
        print(f"verified: {args.verify} (key: {key_id})")
        return 0

    if args.dogfood:
        if not args.keys or not args.current:
            print("--dogfood requires --keys and --current", file=sys.stderr)
            return 2
        result = dogfood(Path(args.dogfood), load_keyset_file(args.keys),
                         args.current)
        print(json.dumps(result) if args.json else result["message"])
        return 0 if result["verified"] else 1

    if args.produce:
        channels = {}
        for spec in args.channel:
            name, _, ver = spec.partition("=")
            channels[name] = ver
        raw = produce_feed(repo=args.repo, product=args.product,
                           tag=args.tag, tag_prefix=args.tag_prefix,
                           min_supported=args.min_supported,
                           notes_url=args.notes_url,
                           channels=channels or None, key_id=args.key_id)
        if args.out:
            Path(args.out).write_bytes(raw)
            print(f"produced: {args.out}")
        else:
            sys.stdout.write(raw.decode("utf-8"))
        return 0

    _build_parser().print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
