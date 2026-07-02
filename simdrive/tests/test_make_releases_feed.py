"""Tests for scripts/make_releases_feed.py — the WS-4 interim publishing-
pipeline generator (docs/design/ws4-releases-publishing-pipeline.md §4/§6).

Locks:
  * canonical byte encoding (json indent=2, sort_keys, trailing newline) —
    byte-identical to harness-lane's canonical producer for the same inputs
    (parity tests run whenever the canonical module is reachable);
  * produce-from-git-tag determinism (version = tag, released_at = tag commit
    date, simdrive-v* tag prefix — this repo's v* tags are legacy specterqa);
  * the mirror validator matching the frozen shared schema rules;
  * offline sign / verify / keygen helpers (canonical key-file conventions);
  * --dogfood: publish step 4 drives the SHIPPED consumer verify path with an
    injected staging keyset (Q2 adjudication: no env trust-anchor override);
  * --self-test: the §6.3 CI self-test — throwaway keypair, produce + sign a
    fixture, validator PASS + shipped-consumer verify, tamper-1-byte reject.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from nacl.signing import SigningKey

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "make_releases_feed.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("make_releases_feed", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mrf = _load_script()


# ---------------------------------------------------------------------------
# canonical module discovery (harness-lane's reference implementation)
# ---------------------------------------------------------------------------


def _canonical_module():
    """Import harness-lane's canonical update_check when reachable:
    $HEKA_CANONICAL_UPDATE_CHECK_DIR first (gate runs pin it to the reviewed
    commit), else the sibling harness checkout. None → parity tests skip."""
    candidates = []
    if os.environ.get("HEKA_CANONICAL_UPDATE_CHECK_DIR"):
        candidates.append(Path(os.environ["HEKA_CANONICAL_UPDATE_CHECK_DIR"]))
    candidates.append(Path.home() / "Documents" / "harness" / "core" / "lib")
    for d in candidates:
        target = d / "update_check.py"
        if not target.is_file():
            continue
        sys.path.insert(0, str(d))
        try:
            spec = importlib.util.spec_from_file_location(
                "canonical_update_check", target)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        except Exception:
            return None
        finally:
            sys.path.remove(str(d))
    return None


CANONICAL = _canonical_module()
needs_canonical = pytest.mark.skipif(
    CANONICAL is None,
    reason="canonical harness update_check module not reachable "
           "(set HEKA_CANONICAL_UPDATE_CHECK_DIR)")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _git_env(tmp_path: Path) -> dict:
    return {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_AUTHOR_DATE": "2026-06-15T12:00:00Z",
        "GIT_COMMITTER_DATE": "2026-06-15T12:00:00Z",
        "HOME": str(tmp_path), "PATH": "/usr/bin:/bin:/usr/local/bin",
    }


def _repo_with_tags(tmp_path: Path, *tags: str) -> Path:
    repo = tmp_path / "product-repo"
    repo.mkdir()
    env = _git_env(tmp_path)

    def g(*args):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True,
                       capture_output=True)

    g("init", "-q")
    (repo / "x").write_text("x")
    g("add", "x")
    g("commit", "-qm", "release")
    for tag in tags:
        g("tag", tag)
    return repo


def _feed_obj(**over) -> dict:
    obj = {
        "schema_version": 1,
        "product": "simdrive",
        "latest": "1.0.0",
        "min_supported": "1.0.0b8",
        "released_at": "2026-07-01T00:00:00Z",
        "notes_url": "https://simdrive.dev/changelog/",
        "channels": {"stable": "1.0.0", "beta": "1.0.1b1"},
        "key_id": "feed-2026-07",
    }
    obj.update(over)
    return obj


def _cli(*args: str, expect: int = 0) -> subprocess.CompletedProcess:
    proc = subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True)
    assert proc.returncode == expect, (proc.stdout, proc.stderr)
    return proc


# ---------------------------------------------------------------------------
# canonical bytes — the exact encoding the operator signs
# ---------------------------------------------------------------------------


class TestCanonicalBytes:

    def test_golden_bytes(self) -> None:
        """Exact golden encoding: indent=2, sort_keys, trailing newline.
        A drift here breaks byte-parity with the canonical producer."""
        raw = mrf.canonical_bytes({"b": 1, "a": {"y": 2, "x": 3}})
        assert raw == (
            b'{\n  "a": {\n    "x": 3,\n    "y": 2\n  },\n  "b": 1\n}\n')

    def test_deterministic(self) -> None:
        obj = _feed_obj()
        assert mrf.canonical_bytes(obj) == mrf.canonical_bytes(dict(obj))


# ---------------------------------------------------------------------------
# mirror validator — must match the frozen shared-schema rules
# ---------------------------------------------------------------------------


class TestValidateFeed:

    def test_accepts_frozen_contract_example(self) -> None:
        assert mrf.validate_feed(_feed_obj()) == []

    @pytest.mark.parametrize("mutation,fragment", [
        ({"schema_version": "1"}, "schema_version"),  # int, not str
        ({"product": ""}, "product"),
        ({"latest": "garbage!"}, "latest"),
        ({"min_supported": "2.0.0"}, "min_supported"),  # > latest
        ({"released_at": "yesterday"}, "released_at"),
        ({"notes_url": "http://simdrive.dev/x"}, "notes_url"),  # https only
        ({"channels": {"stable": "not.a.version!"}}, "channels"),
    ])
    def test_rejects(self, mutation, fragment) -> None:
        errors = mrf.validate_feed(_feed_obj(**mutation))
        assert errors and any(fragment in e for e in errors)

    def test_product_pinning(self) -> None:
        assert mrf.validate_feed(_feed_obj(), expected_product="simdrive") == []
        assert mrf.validate_feed(_feed_obj(), expected_product="harness")

    def test_unknown_major_is_the_skip_class(self) -> None:
        assert mrf.validate_feed(_feed_obj(schema_version=2)) == [
            mrf.UNSUPPORTED_MAJOR]


# ---------------------------------------------------------------------------
# produce — feed from a git tag (simdrive-v* prefix)
# ---------------------------------------------------------------------------


class TestProduce:

    def test_produce_from_simdrive_tag(self, tmp_path) -> None:
        repo = _repo_with_tags(tmp_path, "simdrive-v1.2.3")
        raw = mrf.produce_feed(repo=repo, product="simdrive",
                               notes_url="https://simdrive.dev/changelog/",
                               key_id="feed-2026-07")
        obj = json.loads(raw)
        assert obj["latest"] == "1.2.3"
        assert obj["channels"]["stable"] == "1.2.3"
        assert obj["released_at"] == "2026-06-15T12:00:00Z"  # tag date, UTC
        assert mrf.validate_feed(obj, expected_product="simdrive") == []
        # deterministic: producing twice is byte-identical (signable)
        assert raw == mrf.produce_feed(
            repo=repo, product="simdrive",
            notes_url="https://simdrive.dev/changelog/", key_id="feed-2026-07")

    def test_ignores_legacy_v_tags(self, tmp_path) -> None:
        """This repo carries legacy specterqa v* tags (v9.0.0 …) — the
        simdrive feed must never pick them up."""
        repo = _repo_with_tags(tmp_path, "v9.0.0", "simdrive-v1.2.3")
        raw = mrf.produce_feed(repo=repo, product="simdrive")
        assert json.loads(raw)["latest"] == "1.2.3"

    def test_picks_highest_version_not_latest_tag(self, tmp_path) -> None:
        # 1.10.0 sorts alphabetically BEFORE 1.2.x but is numerically highest
        repo = _repo_with_tags(
            tmp_path, "simdrive-v1.2.3", "simdrive-v1.10.0",
            "simdrive-v1.2.4b1", "simdrive-v1.2.2")
        raw = mrf.produce_feed(repo=repo, product="simdrive")
        assert json.loads(raw)["latest"] == "1.10.0"
        raw = mrf.produce_feed(repo=repo, product="simdrive",
                               tag="simdrive-v1.2.3")
        assert json.loads(raw)["latest"] == "1.2.3"

    def test_refuses_invalid(self, tmp_path) -> None:
        repo = _repo_with_tags(tmp_path, "simdrive-v1.2.3")
        with pytest.raises(ValueError):
            mrf.produce_feed(repo=repo, product="simdrive",
                             notes_url="http://insecure.example/")
        with pytest.raises(ValueError):
            mrf.produce_feed(repo=repo, product="simdrive",
                             min_supported="2.0.0")  # > latest

    def test_no_matching_tag_raises(self, tmp_path) -> None:
        repo = _repo_with_tags(tmp_path, "v9.0.0")  # legacy only
        with pytest.raises(ValueError):
            mrf.produce_feed(repo=repo, product="simdrive")


# ---------------------------------------------------------------------------
# keygen / sign / verify CLI (offline publisher steps; canonical file formats)
# ---------------------------------------------------------------------------


class TestPublisherCli:

    def test_keygen_sign_verify_tamper(self, tmp_path) -> None:
        keys = tmp_path / "keys"
        _cli("--keygen", "--key-id", "feed-2026-07", "--out-dir", str(keys))
        secret = keys / "feed-2026-07.secret"
        keyset = keys / "feed-2026-07.keyset.json"
        assert secret.is_file() and keyset.is_file()
        assert stat.S_IMODE(secret.stat().st_mode) == 0o600
        ks = json.loads(keyset.read_text())
        assert set(ks["keys"]) == {"feed-2026-07"}

        feed = tmp_path / "releases.json"
        feed.write_bytes(mrf.canonical_bytes(_feed_obj()))
        _cli("--sign", str(feed), "--key", str(secret))
        assert (tmp_path / "releases.json.sig").is_file()
        _cli("--verify", str(feed), "--keys", str(keyset))

        feed.write_bytes(feed.read_bytes()[:-2] + b"X\n")  # tamper one byte
        _cli("--verify", str(feed), "--keys", str(keyset), expect=1)

    def test_validate_cli(self, tmp_path) -> None:
        good = tmp_path / "good.json"
        good.write_bytes(mrf.canonical_bytes(_feed_obj()))
        _cli("--validate", str(good), "--product", "simdrive")
        bad = tmp_path / "bad.json"
        bad.write_bytes(mrf.canonical_bytes(_feed_obj(latest="nope!")))
        _cli("--validate", str(bad), expect=1)
        # product pin: a harness feed must not validate as simdrive's
        other = tmp_path / "other.json"
        other.write_bytes(mrf.canonical_bytes(_feed_obj(product="harness")))
        _cli("--validate", str(other), "--product", "simdrive", expect=1)


# ---------------------------------------------------------------------------
# --dogfood: publish step 4 drives the SHIPPED consumer verify path
# ---------------------------------------------------------------------------


def _staged_pair(tmp_path: Path, obj=None, tamper=False):
    """Stage releases.json + .sig + keyset, as the publish flow would."""
    sk = SigningKey.generate()
    raw = mrf.canonical_bytes(obj if obj is not None else _feed_obj())
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    if tamper:
        raw = raw[:-2] + b"X\n"
    (staging / "releases.json").write_bytes(raw)
    sig = mrf.b64url_encode(sk.sign(raw).signature)
    (staging / "releases.json.sig").write_text(sig + "\n")
    keyset = tmp_path / "staging-keyset.json"
    keyset.write_text(json.dumps(
        {"schema_version": 1,
         "keys": {"feed-2026-07": sk.verify_key.encode().hex()}}))
    return staging, keyset


class TestDogfood:

    def test_verifies_and_advises_via_shipped_consumer(self, tmp_path) -> None:
        staging, keyset = _staged_pair(tmp_path)
        proc = _cli("--dogfood", str(staging), "--keys", str(keyset),
                    "--current", "1.0.0b9", "--json")
        out = json.loads(proc.stdout)
        assert out["verified"] is True
        assert out["status"] == "update_available"
        assert out["latest"] == "1.0.0"

    def test_tampered_pair_fails_closed(self, tmp_path) -> None:
        staging, keyset = _staged_pair(tmp_path, tamper=True)
        proc = _cli("--dogfood", str(staging), "--keys", str(keyset),
                    "--current", "1.0.0b9", "--json", expect=1)
        out = json.loads(proc.stdout)
        assert out["verified"] is False

    def test_wrong_product_fails(self, tmp_path) -> None:
        staging, keyset = _staged_pair(tmp_path,
                                       obj=_feed_obj(product="harness"))
        _cli("--dogfood", str(staging), "--keys", str(keyset),
             "--current", "1.0.0b9", "--json", expect=1)


# ---------------------------------------------------------------------------
# --self-test: the §6.3 CI self-test, one command
# ---------------------------------------------------------------------------


class TestSelfTest:

    def test_self_test_passes(self) -> None:
        proc = _cli("--self-test")
        assert "self-test: PASS" in proc.stdout


# ---------------------------------------------------------------------------
# canonical parity — locked whenever the reference module is reachable
# ---------------------------------------------------------------------------


@needs_canonical
class TestCanonicalParity:

    def test_produce_byte_identical_to_canonical(self, tmp_path) -> None:
        """Same commit, same version → OUR bytes == canonical producer bytes.
        (Canonical picks v*; simdrive tags simdrive-v* — same commit tagged
        both ways must produce identical feeds.)"""
        repo = _repo_with_tags(tmp_path, "v1.2.3", "simdrive-v1.2.3")
        ours = mrf.produce_feed(repo=repo, product="simdrive",
                                notes_url="https://simdrive.dev/changelog/",
                                key_id="feed-2026-07")
        theirs = CANONICAL.produce_feed(
            repo=repo, product="simdrive", tag="v1.2.3",
            notes_url="https://simdrive.dev/changelog/",
            key_id="feed-2026-07")
        assert ours == theirs

    def test_canonical_validator_accepts_our_bytes(self, tmp_path) -> None:
        repo = _repo_with_tags(tmp_path, "simdrive-v1.2.3")
        obj = json.loads(mrf.produce_feed(
            repo=repo, product="simdrive",
            notes_url="https://simdrive.dev/changelog/"))
        assert CANONICAL.validate_feed(obj, expected_product="simdrive") == []

    def test_mirror_validator_matches_canonical_verdicts(self) -> None:
        cases = [_feed_obj(), _feed_obj(schema_version="1"),
                 _feed_obj(product=""), _feed_obj(latest="garbage!"),
                 _feed_obj(min_supported="2.0.0"),
                 _feed_obj(released_at="yesterday"),
                 _feed_obj(notes_url="http://simdrive.dev/x"),
                 _feed_obj(channels={"stable": "not.a.version!"}),
                 _feed_obj(schema_version=2), _feed_obj(future_field="x")]
        for obj in cases:
            ours = mrf.validate_feed(obj) == []
            theirs = CANONICAL.validate_feed(obj) == []
            assert ours == theirs, f"verdict diverges for {obj}"

    def test_our_sig_verifies_under_canonical_verifier(self, tmp_path) -> None:
        staging, keyset = _staged_pair(tmp_path)
        raw = (staging / "releases.json").read_bytes()
        sig = CANONICAL.b64url_decode(
            (staging / "releases.json.sig").read_text())
        keys = json.loads(keyset.read_text())["keys"]
        assert CANONICAL.verify_feed_bytes(raw, sig, keys) == "feed-2026-07"
