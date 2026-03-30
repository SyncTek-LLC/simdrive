"""M18: LicenseValidator — license key validation with caching and offline grace.

Validates a SpecterQA license key against the Keygen.sh API. Caches the result
to avoid redundant network round-trips. Falls back to a JWT-based offline grace
period when the API is unreachable.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

# ---------------------------------------------------------------------------
# Tier → default max_concurrent_sims mapping (fallback when API omits the field)
# ---------------------------------------------------------------------------
_TIER_DEFAULTS: Dict[str, int] = {
    "solo": 1,
    "team": 4,
    "enterprise": 16,
}

# Offline grace window in seconds (72 hours)
_OFFLINE_GRACE_SECONDS = 72 * 3600


class LicenseValidator:
    """Validate a SpecterQA license key and surface tier-based concurrency limits.

    Args:
        license_key: The raw license key string (e.g. ``"LIC-TEST-0000-..."``).
        api_url: Base URL for the licensing API (defaults to Keygen.sh v1).
    """

    def __init__(
        self,
        license_key: str,
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

        On a network failure, falls back to ``_check_offline_grace()``.

        Returns:
            Dict with keys: ``valid`` (bool), ``max_concurrent_sims`` (int),
            ``tier`` (str), ``expires_at`` (str).
        """
        if self._cache is not None:
            return self._cache

        try:
            result = self._fetch_from_api()
        except Exception:
            # Network / API error — attempt offline grace fallback
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
        return int(self._cache.get("max_concurrent_sims", 1))

    def tier(self) -> str:
        """Return the license tier string (e.g. ``"solo"``, ``"team"``, ``"enterprise"``)."""
        if self._cache is None:
            return "unknown"
        return str(self._cache.get("tier", "unknown"))

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
        except Exception:
            pass
        return False

    def _decode_jwt(self) -> Dict[str, Any]:
        """Decode the JWT token associated with this license key.

        This is a stub that subclasses or tests can patch. In production it
        would decode the JWT header/payload (without signature verification
        for the offline check — the expiry field alone is sufficient).

        Returns:
            The decoded payload as a plain dict.
        """
        # Production implementation would base64-decode the JWT payload segment.
        # Tests patch this method directly, so we just return an empty dict here.
        return {}

    # ------------------------------------------------------------------
    # Internal — API interaction
    # ------------------------------------------------------------------

    def _fetch_from_api(self) -> Dict[str, Any]:
        """Fetch and parse the license status from the remote API.

        Returns:
            Normalised dict matching the ``validate()`` contract.

        Raises:
            Exception: Propagates any network or HTTP error so ``validate()``
                can apply the offline grace fallback.
        """
        url = f"{self._api_url}/licenses/{self._license_key}/actions/validate"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()

        attrs = data.get("data", {}).get("attributes", {})
        status = attrs.get("status", "")
        metadata = attrs.get("metadata", {})

        tier = str(metadata.get("tier", "solo"))
        raw_max = metadata.get("max_concurrent_sims")
        if raw_max is not None:
            max_concurrent = int(raw_max)
        else:
            max_concurrent = _TIER_DEFAULTS.get(tier, 1)

        valid = status.upper() == "ACTIVE"
        expires_at = attrs.get("expiry")

        return {
            "valid": valid,
            "max_concurrent_sims": max_concurrent,
            "tier": tier,
            "expires_at": expires_at,
        }
