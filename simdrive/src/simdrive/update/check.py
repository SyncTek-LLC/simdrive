"""Update-check consumer — fetch, verify, compare, advise. Never raises.

Behavior (see docs/design/ws4-update-check-feed-consumer.md):

* Gated by the single ``HEKA_TELEMETRY`` kill-switch and ``HEKA_OFFLINE`` —
  either off/set → the check is a hard no-op (no fetch attempted).
* Pulls ``releases.json`` + detached ``releases.json.sig`` over plain GET with
  ZERO user data on the wire.
* Verifies the Ed25519 signature over the exact raw bytes BEFORE parsing; an
  unverifiable feed — including a feed whose detached ``.sig`` is missing or
  unfetchable — is refused (fail-closed), never acted on.
* A verified feed for a DIFFERENT Heka product is skipped (fail-open product
  membership pin, mirroring the canonical consumer's ``expected_product``).
* Compares to the installed version (PEP 440) and returns an advisory. It never
  auto-installs.
* Network/timeout errors are non-fatal (fail-open silent skip), mirroring
  ``license.telemetry.send_trial_attribution``.
* Every real outbound call is appended to the local product-plane call log
  (``~/.heka/calls.jsonl``) so the user can audit exactly what left the machine.
  Records use the CANONICAL cross-lane shape frozen by the WS-3 entitlement
  contract §8 (WS3-ENTITLEMENT-CONTRACT-v1.md): ``{ts, product, kind, method,
  url, payload_shape, ok}`` (+ optional ``error``); extra fields are allowed
  and we keep ``result``/``user_data`` for continuity.
* Trust is MEMBERSHIP-pinned: the detached signature must verify under one of
  the embedded trusted public keys. The feed's ``key_id`` field is an
  ops/rotation hint only — it lives *inside* the signed JSON, so under
  verify-before-parse it cannot select a key and must never influence trust.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import requests
from nacl.signing import VerifyKey

from simdrive.license.keypair import verify_key_from_hex
from simdrive.license.telemetry import telemetry_killed
from simdrive.update.feed_key import UPDATE_FEED_PUBLIC_KEYS

# Default feed origin. A static, signed object — override for self-hosting.
_DEFAULT_FEED_URL = "https://releases.simdrive.dev/simdrive/releases.json"
_FEED_URL_ENV = "SIMDRIVE_UPDATE_FEED_URL"
_OFFLINE_ENV = "HEKA_OFFLINE"

# At most one real check per this interval (cached), unless forced.
_DEFAULT_INTERVAL_SECONDS = 24 * 3600

# Product-plane call log: path AND record shape are canonical per the frozen
# WS-3 entitlement contract §8 (all 3 Heka lanes write the same shape).
_CALL_LOG_NAME = "calls.jsonl"
_PRODUCT = "simdrive"

# Highest releases.json schema major this consumer understands. An unknown
# major means the producer changed the shape in a way we can't safely read, so
# we fail OPEN (skip the advisory) rather than guess — a shared cross-product
# rule agreed with the feed producer (harness-lane).
_SUPPORTED_SCHEMA_MAJOR = 1


def _state_dir() -> Path:
    """Local state/log dir: ``$HEKA_STATE_DIR`` or ``~/.heka``."""
    override = os.environ.get("HEKA_STATE_DIR")
    return Path(override) if override else Path.home() / ".heka"


def _feed_url() -> str:
    return os.environ.get(_FEED_URL_ENV) or _DEFAULT_FEED_URL


def update_disabled() -> bool:
    """True iff the update check must not fire.

    Routes through the single HEKA_TELEMETRY kill-switch (off → disabled) and
    honors HEKA_OFFLINE=1 (product-plane hard-mute). Default is enabled — the
    check carries no user data, so it is opt-OUT, not opt-in.
    """
    if telemetry_killed():
        return True
    if os.environ.get(_OFFLINE_ENV) == "1":
        return True
    return False


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def _trusted_verify_keys() -> List[VerifyKey]:
    keys: List[VerifyKey] = []
    for _key_id, hex_pub in UPDATE_FEED_PUBLIC_KEYS:
        try:
            keys.append(verify_key_from_hex(hex_pub))
        except Exception:
            continue  # skip a malformed embedded key rather than crash
    return keys


def _b64url_decode(s: str) -> bytes:
    s = s.strip()
    s += "=" * (-len(s) % 4)
    import base64
    return base64.urlsafe_b64decode(s)


def verify_feed(
    raw: bytes,
    sig_b64: str,
    *,
    verify_keys: Optional[List[VerifyKey]] = None,
) -> Optional[dict]:
    """Verify the detached Ed25519 signature over ``raw`` then parse it.

    Returns the parsed feed dict on success, or ``None`` if there is no trust
    anchor, the signature does not verify under any trusted key, or the body is
    not valid JSON. Fail-closed: an unverifiable feed yields ``None``.

    Trust is MEMBERSHIP-pinned: the signature is tried against every embedded
    trusted key. The feed's ``key_id`` is deliberately NOT used for selection —
    it is inside the payload we refuse to parse before verification, so using
    it would invert verify-before-parse; it remains an ops/rotation hint only.
    """
    keys = verify_keys if verify_keys is not None else _trusted_verify_keys()
    if not keys:
        return None  # no trust anchor → refuse (fail-closed)
    try:
        sig = _b64url_decode(sig_b64)
    except Exception:
        return None
    verified = False
    for vk in keys:
        try:
            vk.verify(raw, sig)
            verified = True
            break
        except Exception:
            continue
    if not verified:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------


@dataclass
class Advisory:
    """The result of comparing the installed version to the feed."""

    status: str  # "up_to_date" | "update_available" | "below_min" | "unknown"
    current: str
    latest: Optional[str] = None
    min_supported: Optional[str] = None
    message: str = ""


def _feed_schema_major(feed: dict) -> Optional[int]:
    """Extract the integer major of the feed's ``schema_version``, or None."""
    raw = feed.get("schema_version")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw.split(".", 1)[0])
        except (ValueError, AttributeError):
            return None
    return None


def _installed_version() -> str:
    try:
        from simdrive import __version__
        return __version__
    except Exception:  # pragma: no cover - defensive
        return "0.0.0"


def evaluate(current: str, feed: dict) -> Advisory:
    """Compare the installed version against the feed (PEP 440 ordering)."""
    from packaging.version import InvalidVersion, parse

    latest = feed.get("latest")
    min_supported = feed.get("min_supported")
    try:
        cur = parse(current)
    except InvalidVersion:
        return Advisory("unknown", current, latest, min_supported,
                        f"cannot parse installed version {current!r}")

    def _p(v: Optional[str]):
        try:
            return parse(v) if v else None
        except InvalidVersion:
            return None

    latest_v = _p(latest)
    min_v = _p(min_supported)

    if min_v is not None and cur < min_v:
        return Advisory(
            "below_min", current, latest, min_supported,
            f"simdrive {current} is below the minimum supported {min_supported}"
            f" — please upgrade: pip install -U simdrive",
        )
    if latest_v is not None and cur < latest_v:
        return Advisory(
            "update_available", current, latest, min_supported,
            f"simdrive {latest} is available (you have {current}): "
            f"pip install -U simdrive",
        )
    return Advisory("up_to_date", current, latest, min_supported,
                    f"simdrive {current} is up to date")


# ---------------------------------------------------------------------------
# Fetch + product-plane call log + cadence
# ---------------------------------------------------------------------------


def _fetch(url: str, *, timeout: float) -> Optional[Tuple[bytes, Optional[str]]]:
    """GET the feed + detached sig. The GET carries no query params / body —
    zero user data on the wire.

    Returns ``None`` if the feed itself is unreachable (the fail-open network
    class), or ``(raw, sig_text_or_None)`` — a ``None`` sig means the feed was
    fetched but its signature was not. Per the frozen contract ("fail-closed
    on bad/MISSING signature", coordinator adjudication 2026-07-02) the caller
    must classify a missing sig as ``signature_unverified``, matching the
    canonical reference — NOT as a network skip.
    """
    try:
        r = requests.get(url, timeout=timeout)
        if not (200 <= r.status_code < 300):
            return None
    except requests.exceptions.RequestException:
        return None
    try:
        s = requests.get(url + ".sig", timeout=timeout)
        if not (200 <= s.status_code < 300):
            return r.content, None
        return r.content, s.text
    except requests.exceptions.RequestException:
        return r.content, None


def _call_record(ts: float, url: str, *, ok: bool, result: str, **extra) -> dict:
    """Build one canonical product-plane call record (WS-3 contract §8).

    Canonical keys: ts, product, kind, method, url (no query values),
    payload_shape (field NAMES only — [] for this zero-user-data GET), ok.
    ``result``/``user_data`` are retained additional fields.
    """
    rec = {
        "ts": ts,
        "product": _PRODUCT,
        "kind": "update_check",
        "method": "GET",
        "url": url,
        "payload_shape": [],
        "ok": ok,
        "result": result,
        "user_data": False,
    }
    rec.update(extra)
    return rec


def _log_call(record: dict, *, state_dir: Optional[Path] = None) -> None:
    """Append one product-plane call record to ~/.heka/calls.jsonl."""
    try:
        d = state_dir if state_dir is not None else _state_dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / _CALL_LOG_NAME).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:  # pragma: no cover - logging must never break the check
        pass


def _cache_path(state_dir: Path) -> Path:
    return state_dir / "update_check_state.json"


def _read_cache(state_dir: Path) -> dict:
    try:
        return json.loads(_cache_path(state_dir).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_cache(state_dir: Path, data: dict) -> None:
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        _cache_path(state_dir).write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:  # pragma: no cover
        pass


def check_for_update(
    *,
    current: Optional[str] = None,
    feed_url: Optional[str] = None,
    verify_keys: Optional[List[VerifyKey]] = None,
    now: Optional[float] = None,
    force: bool = False,
    timeout: float = 3.0,
    interval_seconds: int = _DEFAULT_INTERVAL_SECONDS,
    state_dir: Optional[Path] = None,
) -> dict:
    """Run the update check. Never raises. Returns a result dict:

    ``{"checked": bool, "status": str, "message": str, "reason"?: str}``

    where ``status`` is one of the :class:`Advisory` statuses, ``"disabled"``,
    ``"skipped"``, or ``"unverified"``.
    """
    ts = now if now is not None else time.time()
    sdir = state_dir if state_dir is not None else _state_dir()
    cur = current if current is not None else _installed_version()

    if update_disabled():
        return {"checked": False, "status": "disabled",
                "message": "update check disabled (HEKA_TELEMETRY/HEKA_OFFLINE)"}

    # Cadence: reuse a fresh cached advisory unless forced.
    cache = _read_cache(sdir)
    last = cache.get("last_check_ts", 0)
    if not force and (ts - last) < interval_seconds and cache.get("advisory"):
        adv = cache["advisory"]
        return {"checked": False, "status": adv.get("status", "unknown"),
                "message": adv.get("message", ""), "reason": "cached"}

    url = feed_url if feed_url is not None else _feed_url()
    fetched = _fetch(url, timeout=timeout)
    if fetched is None:
        _log_call(_call_record(ts, url, ok=False, result="skipped_unreachable",
                               error="unreachable"), state_dir=sdir)
        return {"checked": True, "status": "skipped",
                "message": "update check skipped (feed unreachable)",
                "reason": "network"}

    raw, sig = fetched
    feed = verify_feed(raw, sig, verify_keys=verify_keys) if sig is not None else None
    if feed is None:
        _log_call(_call_record(ts, url, ok=False, result="signature_unverified",
                               error="signature_unverified"), state_dir=sdir)
        return {"checked": True, "status": "unverified",
                "message": "update feed refused (signature not verified)",
                "reason": "signature"}

    # schema_version gate: an unknown major means the shape may have changed in
    # a way we can't safely read → fail OPEN (skip), never guess. Only applied
    # after the signature verified, so a forged version can't trigger it.
    schema_major = _feed_schema_major(feed)
    if schema_major is None or schema_major > _SUPPORTED_SCHEMA_MAJOR:
        # The fetch itself succeeded and verified; skipping is a local
        # decision, so ok=True with the reason in the retained extras.
        _log_call(_call_record(ts, url, ok=True, result="skipped_schema",
                               schema_major=schema_major), state_dir=sdir)
        return {"checked": True, "status": "skipped",
                "message": "update check skipped (unknown feed schema)",
                "reason": "schema"}

    # Product membership pin (mirrors the canonical consumer's
    # validate_feed(expected_product=...)): a correctly-signed feed for a
    # DIFFERENT Heka product must never drive a simdrive advisory — if one
    # operator key ever signs several products' feeds, a swapped feed would
    # otherwise verify. Fail-open skip; applied only after the signature
    # verified, so a forged product can't trigger it.
    if feed.get("product") != _PRODUCT:
        _log_call(_call_record(ts, url, ok=True, result="skipped_product",
                               feed_product=str(feed.get("product"))),
                  state_dir=sdir)
        return {"checked": True, "status": "skipped",
                "message": "update check skipped (feed is for a different product)",
                "reason": "product"}

    adv = evaluate(cur, feed)
    _log_call(_call_record(ts, url, ok=True, result="ok",
                           advisory_status=adv.status), state_dir=sdir)
    _write_cache(sdir, {"last_check_ts": ts,
                        "advisory": {"status": adv.status, "message": adv.message}})
    return {"checked": True, "status": adv.status, "message": adv.message}
