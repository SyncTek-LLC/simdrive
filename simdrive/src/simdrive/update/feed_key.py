"""Ed25519 trust anchors for the signed update-check feed (WS-4).

The update feed (``releases.json``) is signed with a detached Ed25519 signature
over its exact raw bytes. The client holds a small set of trusted *public* keys
(current + next, for rotation) and verifies the feed against them before
trusting any field.

FAIL-CLOSED: until a real production feed-signing key is embedded here,
``UPDATE_FEED_PUBLIC_KEYS`` is EMPTY, so the consumer refuses every feed as
unverified rather than trusting an unsigned/unverifiable one. This is
deliberate — we never act on an unsigned feed. Provisioning the key is a GA
prerequisite tracked in the WS-4 design doc; the private half lives in the
operator secrets store (same handling as the license-signing key).

Rotation mirrors ``simdrive.license.public_key``: prepend the new
``(key_id, hex_pubkey)`` tuple, keep the prior one until no in-the-wild feed
references it, then remove it.

Trust model — MEMBERSHIP pinning, not key_id selection: a feed is trusted iff
its detached signature verifies under one of the public keys listed here. The
``key_id`` (both here and in the feed body) is an ops/rotation hint for humans
only; it is never used to pick the verification key, because the feed's
``key_id`` lives inside the signed JSON and verify-before-parse forbids reading
it pre-verification. An attacker advertising any ``key_id`` gains nothing
(test: ``TestKeyIdSemantics``).
"""
from __future__ import annotations

from typing import List, Tuple

# Ordered list of (key_id, hex_pubkey). FIRST entry is the active key.
# EMPTY = no trust anchor provisioned yet → update-check fails closed.
# NOTE: do NOT reuse the license-signing key here — the feed is a separate
# trust domain with its own rotation schedule.
UPDATE_FEED_PUBLIC_KEYS: List[Tuple[str, str]] = [
    # ("feed-2026-07", "<64-hex-ed25519-pubkey>"),  # provision before GA
]
