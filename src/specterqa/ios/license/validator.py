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

import logging
import os
import warnings
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("specterqa.ios.license.validator")

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


def reset_trial_counter() -> None:
    """Reset the trial run counter. Primarily for use in tests."""
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
        except (ValueError, KeyError, AttributeError):
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

        Uses httpx when ``SPECTERQA_KEYGEN_ACCOUNT`` is set (full Keygen.sh v1
        account-scoped URL). Falls back to the legacy ``requests``-based path
        (``/licenses/{key}/actions/validate``) when the account env var is absent,
        preserving backwards compatibility with pre-INIT-2026-525 callers.

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

        url = f"{self._api_url}/accounts/{account}/licenses/{self._license_key}/validate"
        response = httpx.get(url, timeout=15.0)
        response.raise_for_status()
        return self._parse_api_response(response.json())

    def _fetch_from_api_requests(self) -> Dict[str, Any]:
        """Fetch using the requests library (legacy path, no account segment)."""
        import requests  # type: ignore[import-untyped]

        url = f"{self._api_url}/licenses/{self._license_key}/actions/validate"
        response = requests.get(url)
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
