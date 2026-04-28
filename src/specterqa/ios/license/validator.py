"""M18: LicenseValidator — license key validation with caching and offline grace.

Validates a SpecterQA license key against the Keygen.sh API. Caches the result
to avoid redundant network round-trips. Falls back to a JWT-based offline grace
period when the API is unreachable.

BYOK enforcement:
  Before any test run, call ``check_byok()`` or ``assert_ready_for_run()``.
  If ``ANTHROPIC_API_KEY`` is not set, a ``LicenseBYOKError`` is raised with an
  actionable message.

Trial mode:
  When no license is present, 1 simulator is allowed and runs are capped at
  ``TRIAL_MAX_RUNS`` per session (tracked in-process via a module-level counter).

Dogfood bypass (v0.1.0):
  Set SPECTERQA_IOS_LICENSE=founder to bypass all API validation with a
  pre-configured founder tier grant (valid through 2027-01-01, 4 sims).
  If no license key and no env var, trial mode allows 1 simulator with a warning.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import warnings
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("specterqa.ios.license.validator")

# ---------------------------------------------------------------------------
# Security: license key sanitization
# ---------------------------------------------------------------------------

# SEC-CRIT-001: Only alphanumeric characters, hyphens, and underscores are
# permitted in license keys. This prevents path traversal attacks when the key
# is interpolated into API URLs (e.g. /licenses/{key}/actions/validate).
_LICENSE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,256}$")


def _sanitize_license_key(key: str) -> str:
    """Validate the license key format, raising ValueError on unsafe input.

    Rejects any key containing path-unsafe characters (``/``, ``..``, ``\\``)
    or that does not match the expected alphanumeric-plus-dashes pattern.

    Args:
        key: The raw license key string.

    Returns:
        The key unchanged if validation passes.

    Raises:
        ValueError: if the key contains invalid characters.
    """
    if not _LICENSE_KEY_RE.match(key):
        raise ValueError(
            f"Invalid license key format: {key!r}. "
            "Keys must be 8–256 characters and contain only letters, digits, "
            "hyphens, and underscores."
        )
    return key


# ---------------------------------------------------------------------------
# Trial run counter — module-level, reset per process
# ---------------------------------------------------------------------------

_trial_run_count: int = 0
TRIAL_MAX_RUNS: int = 3

# ---------------------------------------------------------------------------
# Dogfood / trial constants
# ---------------------------------------------------------------------------

_DOGFOOD_LICENSE_VALUE = "founder"
_DOGFOOD_RESULT: Dict[str, Any] = {
    "valid": True,
    "max_concurrent_sims": 4,
    "tier": "founder",
    "expires_at": "2027-01-01",
}
_TRIAL_RESULT: Dict[str, Any] = {
    "valid": True,
    "max_concurrent_sims": 1,
    "tier": "trial",
    "expires_at": None,
}

# ---------------------------------------------------------------------------
# Tier → default max_concurrent_sims mapping (fallback when API omits the field)
# ---------------------------------------------------------------------------
_TIER_DEFAULTS: Dict[str, int] = {
    "trial": 1,
    "indie": 2,
    "pro": 4,
    "solo": 1,
    "team": 10,
    "enterprise": 0,  # 0 == unlimited
    "founder": 4,
}

# Offline grace window in seconds (72 hours)
_OFFLINE_GRACE_SECONDS = 72 * 3600


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LicenseBYOKError(RuntimeError):
    """Raised when ANTHROPIC_API_KEY is not set and a test run is attempted."""


class TrialLimitError(RuntimeError):
    """Raised when the trial run limit is exceeded."""


# ---------------------------------------------------------------------------
# BYOK helper (module-level, reusable)
# ---------------------------------------------------------------------------


def check_byok() -> None:
    """Raise ``LicenseBYOKError`` if ``ANTHROPIC_API_KEY`` is not set.

    Call this before initiating any test run.

    Raises:
        LicenseBYOKError: with an actionable message directing the user to set
            the environment variable and pointing to console.anthropic.com.
    """
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        raise LicenseBYOKError(
            "BYOK required. Set ANTHROPIC_API_KEY environment variable before running tests.\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...\n"
            "Get an API key at: https://console.anthropic.com/"
        )


# ---------------------------------------------------------------------------
# Trial limit helper (module-level)
# ---------------------------------------------------------------------------


def consume_trial_run() -> int:
    """Increment the in-process trial run counter and enforce the run cap.

    Returns:
        The new run count after incrementing.

    Raises:
        TrialLimitError: when the run count exceeds ``TRIAL_MAX_RUNS``.
    """
    global _trial_run_count
    _trial_run_count += 1
    if _trial_run_count > TRIAL_MAX_RUNS:
        raise TrialLimitError(
            f"Trial limit reached: {TRIAL_MAX_RUNS} runs per session in trial mode.\n"
            "Activate a license to unlock unlimited runs:\n"
            "  specterqa-ios license activate <key>\n"
            "Purchase a license at: https://synctek.io/specterqa#pricing"
        )
    return _trial_run_count


def _reset_trial_counter() -> None:
    """Reset the trial run counter. Primarily for use in tests.

    SEC-MED-003: renamed from ``reset_trial_counter`` to private convention to
    prevent library callers from bypassing the trial run limit.
    """
    global _trial_run_count
    _trial_run_count = 0


# ---------------------------------------------------------------------------
# LicenseValidator
# ---------------------------------------------------------------------------


class LicenseValidator:
    """Validate a SpecterQA license key and surface tier-based concurrency limits.

    Args:
        license_key: The raw license key string (e.g. ``"LIC-TEST-0000-..."``).
        api_url: Base URL for the licensing API (defaults to Keygen.sh v1).
    """

    def __init__(
        self,
        license_key: str = "",
        api_url: str = "https://api.keygen.sh/v1",
    ) -> None:
        self._license_key: str = license_key
        self._api_url: str = api_url.rstrip("/")
        self._cache: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self) -> Dict[str, Any]:
        """Validate the license key against the API, caching the result.

        Bypass paths (v0.1.0 dogfood):
        - If ``SPECTERQA_IOS_LICENSE=founder`` env var is set, returns a
          pre-configured founder grant without any network call.
        - If no license key and no env var, returns trial mode (1 simulator)
          with a printed warning.

        On a network failure, falls back to ``_check_offline_grace()``.

        Returns:
            Dict with keys: ``valid`` (bool), ``max_concurrent_sims`` (int),
            ``tier`` (str), ``expires_at`` (str).
        """
        if self._cache is not None:
            return self._cache

        # Dogfood bypass — SPECTERQA_IOS_LICENSE=founder skips API entirely
        env_license = os.environ.get("SPECTERQA_IOS_LICENSE", "").strip()
        if env_license.lower() == _DOGFOOD_LICENSE_VALUE:
            self._cache = dict(_DOGFOOD_RESULT)
            return self._cache

        # Trial mode — no key and no env var → 1 simulator, warn but allow
        if not self._license_key and not env_license:
            warnings.warn(
                "No SpecterQA license key found. Running in trial mode (1 simulator, "
                f"{TRIAL_MAX_RUNS} runs/session). "
                "Activate a license to unlock more: specterqa-ios license activate <key>\n"
                f"Purchase at: https://synctek.io/specterqa#pricing",
                UserWarning,
                stacklevel=2,
            )
            self._cache = dict(_TRIAL_RESULT)
            return self._cache

        try:
            result = self._fetch_from_api()
        except Exception as exc:  # noqa: BLE001 — network layer raises many exception types
            # Network / API error — attempt offline grace fallback
            logger.debug("License API request failed (%s), using offline grace", exc)
            offline_ok = self._check_offline_grace()
            result = {
                "valid": offline_ok,
                "max_concurrent_sims": _TIER_DEFAULTS.get("solo", 1),
                "tier": "offline",
                "expires_at": None,
            }

        self._cache = result
        return result

    def is_valid(self) -> bool:
        """Return ``True`` if the cached (or freshly fetched) license is active."""
        return bool(self._cache.get("valid", False)) if self._cache else False

    def max_concurrent_sims(self) -> int:
        """Return the maximum concurrent simulator count allowed by this license."""
        if self._cache is None:
            return 1
        raw = self._cache.get("max_concurrent_sims", 1)
        # 0 encodes "unlimited" in TIER_DEFAULTS — return a high practical cap
        return int(raw) if int(raw) > 0 else 999

    def tier(self) -> str:
        """Return the license tier string (e.g. ``"indie"``, ``"pro"``, ``"team"``)."""
        if self._cache is None:
            return "unknown"
        return str(self._cache.get("tier", "unknown"))

    def assert_ready_for_run(self) -> None:
        """Assert both a valid license and ANTHROPIC_API_KEY before a test run.

        For trial-mode licenses this also consumes a run slot from the session
        budget (``TRIAL_MAX_RUNS`` runs allowed before upgrade is required).

        Raises:
            LicenseBYOKError: when ``ANTHROPIC_API_KEY`` is absent.
            TrialLimitError: when a trial run exceeds ``TRIAL_MAX_RUNS``.
        """
        # BYOK check is universal — applies to all tiers including trial
        check_byok()

        # Consume a trial slot when running in trial mode
        result = self.validate()
        if result.get("tier") == "trial":
            consume_trial_run()

    # ------------------------------------------------------------------
    # Offline grace
    # ------------------------------------------------------------------

    def _check_offline_grace(self) -> bool:
        """Return ``True`` if the cached JWT token is still within the offline grace window.

        The grace window is 72 hours from the token's ``iat`` (issued-at) timestamp.
        The ``offline_exp`` field in the token payload is used directly if present.
        """
        try:
            payload = self._decode_jwt()
            now = datetime.now(timezone.utc).timestamp()
            offline_exp = payload.get("offline_exp")
            if offline_exp is not None:
                return float(offline_exp) > now
            # Fall back: iat + 72h
            iat = payload.get("iat")
            if iat is not None:
                return (float(iat) + _OFFLINE_GRACE_SECONDS) > now
        except (ValueError, KeyError, AttributeError, TypeError):
            pass
        return False

    def _decode_jwt(self) -> Dict[str, Any]:
        """Decode the JWT payload segment from the license key.

        Keygen.sh may issue license keys in JWT format (``header.payload.sig``).
        When the key has at least two dot-separated segments this method
        base64url-decodes the second segment and parses it as JSON, returning
        the payload dict.  Keys that are not JWT-shaped (opaque tokens, legacy
        Keygen short keys) gracefully return ``{}``.

        Signature verification is intentionally skipped here: the offline grace
        check only reads expiry timestamps — the Keygen.sh server already
        verified the key when it was activated online.

        Returns:
            The decoded payload as a plain dict, or ``{}`` when the key is not
            in JWT format or the payload cannot be decoded.
        """
        parts = self._license_key.split(".")
        if len(parts) < 2:
            return {}
        # Re-add base64 padding stripped by JWT spec (length must be multiple of 4)
        payload_b64 = parts[1]
        padding_needed = (-len(payload_b64)) % 4
        payload_b64 += "=" * padding_needed
        # Cap payload at 2KB before decoding — pre-sanitizer-loosen forward guard
        # against a future malformed/oversized JWT being parsed.  A real Keygen
        # JWT payload is ~400-600 bytes; 2KB leaves comfortable headroom.
        if len(payload_b64) > 2048:
            return {}
        try:
            return json.loads(base64.urlsafe_b64decode(payload_b64))
        except Exception:  # noqa: BLE001
            return {}

    # ------------------------------------------------------------------
    # Internal — API interaction
    # ------------------------------------------------------------------

    def _fetch_from_api(self) -> Dict[str, Any]:
        """Fetch and parse the license status from the remote API.

        Uses httpx when ``SPECTERQA_KEYGEN_ACCOUNT`` is set (full Keygen.sh v1
        account-scoped URL). Falls back to the legacy ``requests``-based path
        (``/licenses/{key}/actions/validate``) when the account env var is absent,
        preserving backwards compatibility with callers prior to v11.4.0.

        Returns:
            Normalised dict matching the ``validate()`` contract.

        Raises:
            Exception: Propagates any network or HTTP error so ``validate()``
                can apply the offline grace fallback.
        """
        account = os.environ.get("SPECTERQA_KEYGEN_ACCOUNT", "").strip()

        if account:
            return self._fetch_via_httpx(account)
        else:
            return self._fetch_from_api_requests()

    def _fetch_via_httpx(self, account: str) -> Dict[str, Any]:
        """Fetch via httpx using the account-scoped Keygen.sh v1 URL."""
        try:
            import httpx
        except ImportError:
            # httpx not installed — fall through to requests
            return self._fetch_from_api_requests()

        # SEC-CRIT-001: sanitize key before interpolating into URL
        safe_key = _sanitize_license_key(self._license_key)
        url = f"{self._api_url}/accounts/{account}/licenses/{safe_key}/validate"
        response = httpx.get(url, timeout=15.0)
        response.raise_for_status()
        return self._parse_api_response(response.json())

    def _fetch_from_api_requests(self) -> Dict[str, Any]:
        """Fetch using the requests library (legacy path, no account segment)."""
        import requests  # type: ignore[import-untyped]

        # SEC-CRIT-001: sanitize key before interpolating into URL
        safe_key = _sanitize_license_key(self._license_key)
        url = f"{self._api_url}/licenses/{safe_key}/actions/validate"
        # 15s timeout matches the httpx-path budget; without it a hung Keygen.sh
        # API would block the entire SpecterQA process indefinitely on every
        # license check (SEC-MED + SEC-LOW-003).
        response = requests.get(url, timeout=15.0)
        response.raise_for_status()
        return self._parse_api_response(response.json())

    @staticmethod
    def _parse_api_response(data: Dict[str, Any]) -> Dict[str, Any]:
        """Parse a Keygen.sh validation response into the internal contract dict."""
        attrs = data.get("data", {}).get("attributes", {})
        status = attrs.get("status", "")
        metadata = attrs.get("metadata", {})

        tier = str(metadata.get("tier", "indie")).lower()
        raw_max = metadata.get("max_concurrent_sims")
        if raw_max is not None:
            max_concurrent = int(raw_max)
        else:
            max_concurrent = _TIER_DEFAULTS.get(tier, 1)

        # Top-level meta.valid takes precedence (Keygen validate endpoint)
        meta_valid = data.get("meta", {}).get("valid")
        if meta_valid is not None:
            valid = bool(meta_valid)
        else:
            valid = status.upper() in ("ACTIVE", "VALID")

        expires_at = attrs.get("expiry")

        return {
            "valid": valid,
            "max_concurrent_sims": max_concurrent,
            "tier": tier,
            "expires_at": expires_at,
        }
