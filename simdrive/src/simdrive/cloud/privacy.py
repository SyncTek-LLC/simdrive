"""Privacy helpers for the cloud subpackage.

These utilities scrub sensitive fields out of error bodies, log payloads,
and structured exception details before they leak into logs, error
messages, or the response surface.

WHY this module: at the cloud edge, an HTTP response body or upstream
exception may carry credentials (license keys, bearer tokens, email
addresses). When we log them — even at DEBUG — those values end up in
log aggregators, crash reporters, and PR comments. The audit
(INIT-2026-549 W-F) flagged this as the highest-impact privacy issue
in the cloud module.

USAGE
-----

    from simdrive.cloud.privacy import scrub_body

    log.warning("upstream rejected request", extra={"body": scrub_body(body)})

    raise HTTPException(status_code=502, detail=scrub_body(upstream_text))

The scrubber is idempotent and safe to call on any JSON-shaped body,
arbitrary string, bytes, or None.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Union

# Field names whose VALUES we never want in logs or error envelopes.
# Match is case-insensitive on substrings — "License_Key", "AUTHORIZATION",
# "api-key" all hit. Tune by extending this list, never by widening the
# substring matcher (which would risk over-scrubbing innocuous fields).
SENSITIVE_KEY_SUBSTRINGS: tuple[str, ...] = (
    "email",
    "license_key",
    "license-key",
    "licensekey",
    "token",
    "signature",
    "bearer",
    "authorization",
    "secret",
    "password",
    "api_key",
    "api-key",
    "apikey",
    "private_key",
    "privatekey",
)

# Replacement marker used when a field is scrubbed. Stable string so log
# aggregators can grep for it.
_SCRUB_MARKER: str = "[redacted]"

# Regex that matches an HTTP "Authorization: Bearer <token>" header value
# anywhere inside a string body. Captures the leading scheme so we can
# preserve "Bearer " while redacting the secret.
_BEARER_RE = re.compile(
    r"(Bearer\s+)([A-Za-z0-9_\-\.=]+)",
    flags=re.IGNORECASE,
)

# License-key shape: payload_b64url.sig_b64url where both segments are
# long-ish URL-safe-base64. Conservatively require at least 40 chars on
# each side to avoid false positives like "a.b" or version strings.
_LICENSE_KEY_RE = re.compile(
    r"\b([A-Za-z0-9_\-]{40,}\.[A-Za-z0-9_\-]{40,})\b"
)


def _is_sensitive_key(name: str) -> bool:
    """Return True when ``name`` matches one of the sensitive substrings."""
    lowered = name.lower()
    return any(token in lowered for token in SENSITIVE_KEY_SUBSTRINGS)


def _scrub_str(value: str) -> str:
    """Mask Bearer tokens and standalone license keys inside a string."""
    scrubbed = _BEARER_RE.sub(r"\1" + _SCRUB_MARKER, value)
    scrubbed = _LICENSE_KEY_RE.sub(_SCRUB_MARKER, scrubbed)
    return scrubbed


def _scrub_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a new dict with sensitive values replaced by ``[redacted]``."""
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if _is_sensitive_key(str(key)):
            out[key] = _SCRUB_MARKER
        else:
            out[key] = scrub_body(value)
    return out


def _scrub_iterable(payload: Iterable[Any]) -> list[Any]:
    return [scrub_body(item) for item in payload]


def scrub_body(payload: Union[str, bytes, dict, list, tuple, None, Any]) -> Any:
    """Recursively scrub sensitive values from an HTTP body or log payload.

    Accepts strings (parses JSON-shaped strings when possible), bytes
    (decoded as UTF-8 with errors replaced), dicts (sensitive keys
    replaced), and lists/tuples (recursively scrubbed). Returns the same
    shape as the input so callers can pass the result straight back into
    a logger or HTTPException.

    The scrubber is permissive: anything it does not recognise is
    returned unchanged, on the assumption that it is not log-worthy.
    Tests assert no sensitive value survives this call for the body
    shapes the cloud actually produces.
    """
    if payload is None:
        return None

    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8", errors="replace")
        except Exception:
            return _SCRUB_MARKER

    if isinstance(payload, str):
        # Try JSON-decode for structured scrubbing; fall back to regex
        # masking on the raw string.
        stripped = payload.strip()
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(stripped)
            except Exception:
                return _scrub_str(payload)
            scrubbed = scrub_body(parsed)
            return json.dumps(scrubbed)
        return _scrub_str(payload)

    if isinstance(payload, dict):
        return _scrub_mapping(payload)

    if isinstance(payload, (list, tuple)):
        return _scrub_iterable(payload)

    return payload


# Public alias preserved for the audit spec which calls it ``_scrub_body``.
_scrub_body = scrub_body


__all__ = [
    "SENSITIVE_KEY_SUBSTRINGS",
    "scrub_body",
    "_scrub_body",
]
