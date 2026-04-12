"""webhook_server — standalone FastAPI server for Stripe → Keygen.sh webhooks.

Provides a self-contained HTTP server that:
- Verifies Stripe webhook signatures
- Dispatches events to the handler in ``stripe_webhook.py``
- Exposes a health check endpoint for Railway / Fly.io / k8s

Environment variables:
  STRIPE_WEBHOOK_SECRET   — Stripe webhook signing secret (whsec_...)
  PORT                    — TCP port to bind (default: 8500)

Deployment (Railway entry point):
  uvicorn specterqa.ios.license.webhook_server:app --host 0.0.0.0 --port $PORT

Or via the Railway Procfile / start command:
  python -m specterqa.ios.license.webhook_server
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("specterqa.ios.license.webhook_server")

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

try:
    from fastapi import FastAPI, Header, HTTPException, Request
    from fastapi.responses import JSONResponse

    from specterqa.ios.license.stripe_webhook import (
        handle_stripe_event,
        verify_stripe_signature,
    )

    app = FastAPI(
        title="SpecterQA Stripe Webhook Server",
        description=(
            "Receives Stripe events and drives Keygen.sh license lifecycle operations. "
            "See stripe_webhook.py for handled event types."
        ),
        version="1.0.0",
    )

    # -----------------------------------------------------------------------
    # Health check — required by Railway and load balancers
    # -----------------------------------------------------------------------

    @app.get("/health")
    async def health() -> JSONResponse:
        """Return 200 when the server is up and the webhook secret is configured."""
        secret_configured = bool(os.environ.get("STRIPE_WEBHOOK_SECRET"))
        return JSONResponse(
            content={
                "status": "ok",
                "stripe_webhook_secret_configured": secret_configured,
            },
            status_code=200,
        )

    # -----------------------------------------------------------------------
    # Stripe webhook endpoint
    # -----------------------------------------------------------------------

    @app.post("/stripe/webhook")
    async def stripe_webhook(
        request: Request,
        stripe_signature: str = Header(None, alias="Stripe-Signature"),
    ) -> JSONResponse:
        """Receive and process a Stripe webhook event.

        Verifies the ``Stripe-Signature`` header using ``STRIPE_WEBHOOK_SECRET``,
        then dispatches the event to ``handle_stripe_event``.

        Returns:
            200 JSON response with ``status`` ("handled" | "ignored" | "error").
            400 on signature verification failure.
            500 when ``STRIPE_WEBHOOK_SECRET`` is not configured.
        """
        secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
        if not secret:
            logger.error("STRIPE_WEBHOOK_SECRET is not set — cannot verify Stripe signature")
            raise HTTPException(
                status_code=500,
                detail="STRIPE_WEBHOOK_SECRET not configured on this server",
            )

        payload_bytes = await request.body()

        try:
            event = verify_stripe_signature(
                payload_bytes=payload_bytes,
                sig_header=stripe_signature or "",
                secret=secret,
            )
        except ValueError as exc:
            logger.warning("Stripe signature verification failed: %s", exc)
            raise HTTPException(status_code=400, detail=str(exc))
        except ImportError as exc:
            logger.error("stripe library not installed: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

        result = handle_stripe_event(dict(event))
        return JSONResponse(content=result, status_code=200)

except ImportError as _import_err:
    # FastAPI is not installed — provide a clear error rather than a silent stub.
    import sys

    logger.error(
        "FastAPI is not installed. Install it to run the webhook server: "
        "pip install fastapi uvicorn  (or pip install 'specterqa-ios[license]')"
    )

    # Provide a stub ``app`` so imports don't crash in environments that only
    # need to import the module for inspection (e.g. doc generators).
    app = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Entry point — Railway-compatible
# ---------------------------------------------------------------------------

def main() -> None:
    """Start the webhook server via uvicorn.

    Reads PORT from the environment (Railway sets this automatically).
    Falls back to 8500 if PORT is not set.
    """
    try:
        import uvicorn
    except ImportError:
        raise SystemExit(
            "uvicorn is required to run the webhook server.\n"
            "Install it: pip install uvicorn  (or pip install 'specterqa-ios[license]')"
        )

    if app is None:
        raise SystemExit(
            "FastAPI is not installed — cannot start the webhook server.\n"
            "Install it: pip install fastapi uvicorn"
        )

    port = int(os.environ.get("PORT", "8500"))
    logger.info("Starting SpecterQA Stripe webhook server on port %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
