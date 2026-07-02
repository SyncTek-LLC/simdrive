"""Signed pull-based update-check consumer (WS-4).

simdrive pulls a static, Ed25519-signed ``releases.json`` feed, verifies it
locally, and prints an *advisory* — it never auto-installs and never sends user
data. This is a **product-plane** call (severable, fail-open, no user code/data),
gated by the single ``HEKA_TELEMETRY`` kill-switch and ``HEKA_OFFLINE``.

See simdrive/docs/design/ws4-update-check-feed-consumer.md for the contract.
"""
from __future__ import annotations

from .check import (
    Advisory,
    check_for_update,
    evaluate,
    update_disabled,
    verify_feed,
)

__all__ = [
    "Advisory",
    "check_for_update",
    "evaluate",
    "update_disabled",
    "verify_feed",
]
