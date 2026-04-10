"""stripe_webhook — Stripe → Keygen.sh license issuance webhook handler.

This module provides a Flask/FastAPI-compatible endpoint that listens for
Stripe events and drives license lifecycle operations against the Keygen.sh API.

Handled events:
  checkout.session.completed        → create a new Keygen.sh license for the customer
  customer.subscription.deleted     → suspend the customer's Keygen.sh license
  customer.subscription.updated     → update tier metadata when plan changes

Environment variables (required on the server):
  STRIPE_WEBHOOK_SECRET       — Stripe webhook signing secret (whsec_...)
  SPECTERQA_KEYGEN_ACCOUNT    — Keygen.sh account ID
  SPECTERQA_KEYGEN_API_TOKEN  — Keygen.sh product/admin API token

Deployment notes:
  This is a server-side component that should be deployed separately from the
  SpecterQA CLI (e.g. as a Railway service, Fly.io app, or serverless function).
  See docs/deployment/stripe_webhook.md for a full deployment guide.

  Recommended stack: FastAPI + uvicorn or Flask + gunicorn behind Railway.
  The module ships both a Flask blueprint AND a FastAPI router so the operator
  can pick whichever framework they prefer.

Usage (FastAPI):
  from specterqa.ios.license.stripe_webhook import router as stripe_router
  app.include_router(stripe_router, prefix="/webhooks")

Usage (Flask):
  from specterqa.ios.license.stripe_webhook import blueprint as stripe_bp
  app.register_blueprint(stripe_bp, url_prefix="/webhooks")
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger("specterqa.ios.license.stripe_webhook")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_KEYGEN_BASE = "https://api.keygen.sh/v1"
_PURCHASE_URL = "https://synctek.io/specterqa#pricing"

# Stripe price ID → Keygen tier name.
#
# SEC-HIGH-004: These MUST be configured via environment variables in production.
# The fallback strings ("price_indie", etc.) are placeholder sentinels that will
# never match real Stripe price IDs and are only present to keep the dict
# non-empty for local development. If ALL env vars are missing at import time,
# a warning is emitted so misconfigured deployments are caught early.
#
# Required env vars (set on the webhook server):
#   STRIPE_PRICE_INDIE       — e.g. price_1ABC...
#   STRIPE_PRICE_PRO         — e.g. price_2DEF...
#   STRIPE_PRICE_TEAM        — e.g. price_3GHI...
#   STRIPE_PRICE_ENTERPRISE  — e.g. price_4JKL...
_STRIPE_PRICE_INDIE = os.environ.get("STRIPE_PRICE_INDIE", "price_indie")
_STRIPE_PRICE_PRO = os.environ.get("STRIPE_PRICE_PRO", "price_pro")
_STRIPE_PRICE_TEAM = os.environ.get("STRIPE_PRICE_TEAM", "price_team")
_STRIPE_PRICE_ENTERPRISE = os.environ.get("STRIPE_PRICE_ENTERPRISE", "price_enterprise")

_PRICE_TIER_MAP: Dict[str, str] = {
    _STRIPE_PRICE_INDIE: "indie",
    _STRIPE_PRICE_PRO: "pro",
    _STRIPE_PRICE_TEAM: "team",
    _STRIPE_PRICE_ENTERPRISE: "enterprise",
}

# Warn at import time if none of the price env vars are configured — this
# indicates a misconfigured deployment where tier mapping will silently fail.
_price_env_vars = (
    "STRIPE_PRICE_INDIE",
    "STRIPE_PRICE_PRO",
    "STRIPE_PRICE_TEAM",
    "STRIPE_PRICE_ENTERPRISE",
)
if not any(os.environ.get(v) for v in _price_env_vars):
    logger.warning(
        "SEC-HIGH-004: None of the Stripe price ID environment variables are set "
        "(%s). The _PRICE_TIER_MAP will use placeholder sentinel values that will "
        "never match real Stripe price IDs. Configure these env vars before "
        "processing live Stripe events.",
        ", ".join(_price_env_vars),
    )

# Tier → max concurrent simulators (mirrors license_cmd.py)
_TIER_SIM_LIMITS: Dict[str, int] = {
    "indie": 2,
    "pro": 4,
    "team": 10,
    "enterprise": 0,  # 0 = unlimited
}


# ---------------------------------------------------------------------------
# Internal helpers — Keygen.sh API
# ---------------------------------------------------------------------------


def _keygen_headers() -> Dict[str, str]:
    """Return authorization headers for the Keygen.sh API."""
    token = os.environ.get("SPECTERQA_KEYGEN_API_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/vnd.api+json",
        "Accept": "application/vnd.api+json",
    }


def _keygen_account() -> str:
    """Return the Keygen.sh account ID from the environment."""
    account = os.environ.get("SPECTERQA_KEYGEN_ACCOUNT", "").strip()
    if not account:
        raise RuntimeError(
            "SPECTERQA_KEYGEN_ACCOUNT environment variable is not set. "
            "Configure it on the webhook server."
        )
    return account


def create_keygen_license(
    customer_email: str,
    tier: str,
    stripe_customer_id: str,
    stripe_subscription_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new Keygen.sh license for a customer.

    Args:
        customer_email: The customer's email address (used as license name).
        tier: License tier string (e.g. "indie", "pro", "team").
        stripe_customer_id: Stripe customer ID to embed in license metadata.
        stripe_subscription_id: Stripe subscription ID, if applicable.

    Returns:
        The created license dict from the Keygen.sh API response.

    Raises:
        RuntimeError: if the Keygen.sh API call fails.
    """
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError(
            "httpx is required for Keygen.sh API calls. "
            "Install it: pip install httpx"
        ) from exc

    account = _keygen_account()
    max_sims = _TIER_SIM_LIMITS.get(tier, 2)
    url = f"{_KEYGEN_BASE}/accounts/{account}/licenses"

    payload: Dict[str, Any] = {
        "data": {
            "type": "licenses",
            "attributes": {
                "name": customer_email,
                "metadata": {
                    "tier": tier,
                    "max_concurrent_sims": max_sims,
                    "stripe_customer_id": stripe_customer_id,
                    "stripe_subscription_id": stripe_subscription_id or "",
                    "customer_email": customer_email,
                },
            },
        }
    }

    try:
        resp = httpx.post(url, headers=_keygen_headers(), json=payload, timeout=15.0)
    except httpx.RequestError as exc:
        raise RuntimeError(f"Network error contacting Keygen.sh: {exc}") from exc

    if not resp.is_success:
        # SEC-HIGH-002: log full body internally but never expose it in the raised
        # exception — response bodies may contain sensitive Keygen.sh error details.
        logger.error(
            "Keygen license creation failed: status=%d body=%s",
            resp.status_code,
            resp.text,
        )
        raise RuntimeError(f"Keygen license creation failed (status {resp.status_code})")

    return resp.json()


def suspend_keygen_license_by_customer(stripe_customer_id: str) -> Optional[Dict[str, Any]]:
    """Suspend the Keygen.sh license associated with a Stripe customer.

    Looks up licenses by the ``stripe_customer_id`` metadata field, then
    suspends the first matching active license.

    Args:
        stripe_customer_id: The Stripe customer ID to look up.

    Returns:
        The updated license dict, or None if no matching license was found.

    Raises:
        RuntimeError: if a Keygen.sh API call fails.
    """
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("httpx required for Keygen.sh API calls.") from exc

    account = _keygen_account()
    # Search licenses by metadata
    search_url = (
        f"{_KEYGEN_BASE}/accounts/{account}/licenses"
        f"?metadata[stripe_customer_id]={stripe_customer_id}"
    )

    try:
        resp = httpx.get(search_url, headers=_keygen_headers(), timeout=15.0)
        resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise RuntimeError(f"Keygen.sh license lookup failed: {exc}") from exc

    licenses = resp.json().get("data", [])
    if not licenses:
        logger.warning("No Keygen.sh license found for stripe_customer_id=%s", stripe_customer_id)
        return None

    license_id = licenses[0].get("id")
    if not license_id:
        return None

    # Suspend the license
    suspend_url = f"{_KEYGEN_BASE}/accounts/{account}/licenses/{license_id}/actions/suspend"
    try:
        suspend_resp = httpx.post(suspend_url, headers=_keygen_headers(), timeout=15.0)
        suspend_resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise RuntimeError(f"Keygen.sh license suspension failed: {exc}") from exc

    logger.info("Suspended Keygen.sh license %s for customer %s", license_id, stripe_customer_id)
    return suspend_resp.json()


def _resolve_tier_from_session(session: Dict[str, Any]) -> str:
    """Determine the license tier from a Stripe checkout session object.

    Checks line items' price IDs against ``_PRICE_TIER_MAP``. Falls back to
    ``indie`` if the price cannot be resolved.
    """
    # The price ID may be nested under display_items or line_items depending on
    # the Stripe API version used when the event was captured.
    items = session.get("display_items") or []
    for item in items:
        price_id = (item.get("price") or {}).get("id") or (item.get("plan") or {}).get("id")
        if price_id and price_id in _PRICE_TIER_MAP:
            return _PRICE_TIER_MAP[price_id]

    # Attempt subscription line items
    subscription_data = session.get("subscription_data") or {}
    for item in subscription_data.get("items", []):
        price_id = item.get("price", {}).get("id", "")
        if price_id in _PRICE_TIER_MAP:
            return _PRICE_TIER_MAP[price_id]

    return "indie"  # safe default


# ---------------------------------------------------------------------------
# Stripe signature verification
# ---------------------------------------------------------------------------


def verify_stripe_signature(payload_bytes: bytes, sig_header: str, secret: str) -> Dict[str, Any]:
    """Verify a Stripe webhook signature and return the parsed event.

    Args:
        payload_bytes: Raw request body bytes.
        sig_header: Value of the ``Stripe-Signature`` HTTP header.
        secret: Webhook signing secret (``whsec_...``).

    Returns:
        The verified Stripe event dict.

    Raises:
        ValueError: on signature mismatch or expired timestamp.
        ImportError: if the ``stripe`` library is not installed.
    """
    try:
        import stripe  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError(
            "The stripe library is required for webhook signature verification.\n"
            "Install it: pip install stripe\n"
            "Or install the license extras: pip install 'specterqa-ios[license]'"
        ) from exc

    try:
        event = stripe.Webhook.construct_event(
            payload=payload_bytes,
            sig_header=sig_header,
            secret=secret,
        )
    except stripe.error.SignatureVerificationError as exc:
        raise ValueError(f"Stripe signature verification failed: {exc}") from exc

    return event  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Core event dispatcher
# ---------------------------------------------------------------------------


def handle_stripe_event(event: Dict[str, Any]) -> Dict[str, str]:
    """Dispatch a verified Stripe event to the appropriate handler.

    Args:
        event: A verified Stripe event dict (from ``verify_stripe_signature``).

    Returns:
        A status dict with ``status`` (``"handled"`` | ``"ignored"``) and
        optional ``detail`` string.
    """
    event_type = event.get("type", "")
    data_object = event.get("data", {}).get("object", {})

    logger.info("Handling Stripe event: %s", event_type)

    if event_type == "checkout.session.completed":
        return _handle_checkout_completed(data_object)
    elif event_type == "customer.subscription.deleted":
        return _handle_subscription_deleted(data_object)
    elif event_type == "customer.subscription.updated":
        return _handle_subscription_updated(data_object)
    else:
        logger.debug("Ignoring unhandled Stripe event type: %s", event_type)
        return {"status": "ignored", "detail": f"Event type {event_type!r} not handled"}


def _handle_checkout_completed(session: Dict[str, Any]) -> Dict[str, str]:
    """Handle ``checkout.session.completed`` — issue a new Keygen.sh license."""
    customer_email = session.get("customer_details", {}).get("email") or session.get("customer_email", "")
    stripe_customer_id = session.get("customer", "")
    stripe_subscription_id = session.get("subscription", "")

    if not customer_email:
        logger.error("checkout.session.completed missing customer email, session_id=%s", session.get("id"))
        return {"status": "error", "detail": "Missing customer email in session"}

    tier = _resolve_tier_from_session(session)

    try:
        result = create_keygen_license(
            customer_email=customer_email,
            tier=tier,
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=stripe_subscription_id,
        )
        license_id = result.get("data", {}).get("id", "unknown")
        logger.info(
            "Created Keygen.sh license %s (tier=%s) for %s", license_id, tier, customer_email
        )
        return {"status": "handled", "detail": f"License {license_id} created for {customer_email}"}
    except RuntimeError as exc:
        logger.error("Failed to create Keygen.sh license: %s", exc)
        return {"status": "error", "detail": str(exc)}


def _handle_subscription_deleted(subscription: Dict[str, Any]) -> Dict[str, str]:
    """Handle ``customer.subscription.deleted`` — suspend the customer's license."""
    stripe_customer_id = subscription.get("customer", "")

    if not stripe_customer_id:
        return {"status": "error", "detail": "Missing customer ID in subscription event"}

    try:
        result = suspend_keygen_license_by_customer(stripe_customer_id)
        if result is None:
            return {"status": "ignored", "detail": f"No license found for customer {stripe_customer_id}"}
        return {"status": "handled", "detail": f"License suspended for customer {stripe_customer_id}"}
    except RuntimeError as exc:
        logger.error("Failed to suspend Keygen.sh license: %s", exc)
        return {"status": "error", "detail": str(exc)}


def _find_keygen_license_id_by_customer(stripe_customer_id: str) -> Optional[str]:
    """Look up the Keygen.sh license ID for a Stripe customer.

    Args:
        stripe_customer_id: The Stripe customer ID stored in license metadata.

    Returns:
        The Keygen.sh license ID string, or None if no match is found.

    Raises:
        RuntimeError: if the Keygen.sh API call fails.
    """
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("httpx required for Keygen.sh API calls.") from exc

    account = _keygen_account()
    search_url = (
        f"{_KEYGEN_BASE}/accounts/{account}/licenses"
        f"?metadata[stripe_customer_id]={stripe_customer_id}"
    )
    try:
        resp = httpx.get(search_url, headers=_keygen_headers(), timeout=15.0)
        resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise RuntimeError(f"Keygen.sh license lookup failed: {exc}") from exc

    licenses = resp.json().get("data", [])
    if not licenses:
        return None
    return licenses[0].get("id")


def _update_keygen_license_tier(license_id: str, new_tier: str) -> Dict[str, Any]:
    """Update the tier metadata on an existing Keygen.sh license.

    Args:
        license_id: The Keygen.sh license ID.
        new_tier: The new tier string (e.g. ``"pro"``).

    Returns:
        The updated license dict from the Keygen.sh API.

    Raises:
        RuntimeError: if the Keygen.sh API call fails.
    """
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("httpx required for Keygen.sh API calls.") from exc

    account = _keygen_account()
    max_sims = _TIER_SIM_LIMITS.get(new_tier, 2)
    url = f"{_KEYGEN_BASE}/accounts/{account}/licenses/{license_id}"
    payload: Dict[str, Any] = {
        "data": {
            "type": "licenses",
            "id": license_id,
            "attributes": {
                "metadata": {
                    "tier": new_tier,
                    "max_concurrent_sims": max_sims,
                },
            },
        }
    }
    try:
        resp = httpx.patch(url, headers=_keygen_headers(), json=payload, timeout=15.0)
        resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise RuntimeError(f"Keygen.sh license update failed: {exc}") from exc

    return resp.json()


def _handle_subscription_updated(subscription: Dict[str, Any]) -> Dict[str, str]:
    """Handle ``customer.subscription.updated`` — propagate tier changes to Keygen.

    SEC-HIGH-003: This was previously a no-op. It now:
    1. Extracts the new price ID from the updated subscription's items.
    2. Maps it to a tier via ``_PRICE_TIER_MAP``.
    3. Finds the existing Keygen license for this customer.
    4. Updates the license metadata with the new tier and sim limit.
    """
    stripe_customer_id = subscription.get("customer", "")
    if not stripe_customer_id:
        return {"status": "error", "detail": "Missing customer ID in subscription.updated event"}

    # Extract the current price ID from subscription items
    items = subscription.get("items", {}).get("data", [])
    new_price_id = ""
    for item in items:
        new_price_id = (item.get("price") or item.get("plan") or {}).get("id", "")
        if new_price_id:
            break

    new_tier = _PRICE_TIER_MAP.get(new_price_id, "")
    if not new_tier:
        logger.warning(
            "subscription.updated: price_id=%r not in _PRICE_TIER_MAP, skipping tier update "
            "for customer %s",
            new_price_id,
            stripe_customer_id,
        )
        return {
            "status": "ignored",
            "detail": f"Price ID {new_price_id!r} not mapped to a known tier",
        }

    try:
        license_id = _find_keygen_license_id_by_customer(stripe_customer_id)
    except RuntimeError as exc:
        logger.error("Failed to find Keygen license for customer %s: %s", stripe_customer_id, exc)
        return {"status": "error", "detail": str(exc)}

    if not license_id:
        logger.warning(
            "subscription.updated: no Keygen license found for customer %s", stripe_customer_id
        )
        return {
            "status": "ignored",
            "detail": f"No license found for customer {stripe_customer_id}",
        }

    try:
        _update_keygen_license_tier(license_id, new_tier)
    except RuntimeError as exc:
        logger.error(
            "Failed to update Keygen license %s to tier %s: %s", license_id, new_tier, exc
        )
        return {"status": "error", "detail": str(exc)}

    logger.info(
        "Updated Keygen license %s to tier=%s for customer %s",
        license_id,
        new_tier,
        stripe_customer_id,
    )
    return {
        "status": "handled",
        "detail": f"License {license_id} updated to tier {new_tier!r} for customer {stripe_customer_id}",
    }


# ---------------------------------------------------------------------------
# FastAPI router (optional — only registered when fastapi is installed)
# ---------------------------------------------------------------------------

try:
    from fastapi import APIRouter, Header, HTTPException, Request  # type: ignore[import-untyped]
    from fastapi.responses import JSONResponse  # type: ignore[import-untyped]

    router = APIRouter()

    @router.post("/stripe")
    async def stripe_webhook_fastapi(
        request: Request,
        stripe_signature: str = Header(None, alias="Stripe-Signature"),
    ) -> JSONResponse:
        """FastAPI endpoint — POST /webhooks/stripe.

        Verify the Stripe signature, dispatch the event, and return a 200.
        Returns 400 on signature failure, 200 on handled or ignored events.
        """
        secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
        if not secret:
            raise HTTPException(status_code=500, detail="STRIPE_WEBHOOK_SECRET not configured")

        payload_bytes = await request.body()

        try:
            event = verify_stripe_signature(payload_bytes, stripe_signature or "", secret)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except ImportError as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        result = handle_stripe_event(dict(event))
        return JSONResponse(content=result, status_code=200)

except ImportError:
    # FastAPI not installed — router stub keeps imports safe
    router = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Flask blueprint (optional — only registered when flask is installed)
# ---------------------------------------------------------------------------

try:
    from flask import Blueprint, Response, request as flask_request  # type: ignore[import-untyped]

    blueprint = Blueprint("stripe_webhook", __name__)

    @blueprint.route("/stripe", methods=["POST"])
    def stripe_webhook_flask() -> Response:
        """Flask endpoint — POST /webhooks/stripe.

        Verify the Stripe signature, dispatch the event, and return a 200 JSON.
        Returns 400 on signature failure.
        """
        from flask import jsonify  # type: ignore[import-untyped]

        secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
        if not secret:
            return jsonify({"error": "STRIPE_WEBHOOK_SECRET not configured"}), 500  # type: ignore[return-value]

        payload_bytes = flask_request.get_data()
        sig_header = flask_request.headers.get("Stripe-Signature", "")

        try:
            event = verify_stripe_signature(payload_bytes, sig_header, secret)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400  # type: ignore[return-value]
        except ImportError as exc:
            return jsonify({"error": str(exc)}), 500  # type: ignore[return-value]

        result = handle_stripe_event(dict(event))
        return jsonify(result), 200  # type: ignore[return-value]

except ImportError:
    # Flask not installed — blueprint stub keeps imports safe
    blueprint = None  # type: ignore[assignment]
