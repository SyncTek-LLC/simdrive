"""Bearer-token authentication middleware for the cloud API.

Auth model: the license key IS the bearer token. No separate API keys,
no OAuth, no signup. The server verifies the Ed25519 signature using the
same public key the client uses for offline validation.

WHY this approach: zero new identity surface, no token-rotation complexity,
keys are naturally expiring. The downside is key revocation requires
re-issuing; acceptable at first-5-customer scale.
"""
from __future__ import annotations

import time as _time
from typing import Optional

from fastapi import HTTPException, Request, status
from nacl.signing import VerifyKey

from simdrive.license.errors import LicenseError
from simdrive.license.validator import validate_license
from simdrive.observability.logger import get_logger

log = get_logger("simdrive.cloud.auth")


def make_license_bearer(verify_key: VerifyKey):
    """Return a FastAPI dependency function that validates Bearer license keys.

    WHY a function factory instead of a class with __call__: FastAPI's
    Depends() with a class instance runs into pydantic v2 introspection issues
    on Python 3.9 when the __call__ signature contains `Request`. Returning a
    plain function avoids that code path entirely.

    Usage::

        _auth = make_license_bearer(verify_key)

        @router.post("/protected")
        async def endpoint(license_payload: dict = Depends(_auth)):
            ...
    """

    def _validate(request: Request) -> dict:
        """Extract and validate Bearer token from Authorization header."""
        authorization: Optional[str] = request.headers.get("Authorization")

        if not authorization:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authorization header required. Use: Authorization: Bearer <license-key>",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authorization must use Bearer scheme.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        key = authorization[len("Bearer "):]

        try:
            # Server time is authoritative: no offline grace window on API side.
            payload = validate_license(
                key,
                verify_key=verify_key,
                last_known_server_time=int(_time.time()),
            )
            log.debug(
                "bearer auth accepted",
                extra={
                    "tier": payload.get("tier"),
                    "customer_email": payload.get("customer_email"),
                    "path": request.url.path,
                },
            )
        except LicenseError as exc:
            log.warning(
                "bearer auth rejected",
                extra={"reason": exc.code, "path": request.url.path},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=exc.message,
            ) from exc

        return payload

    return _validate


# Keep the class name as a thin alias for backward compatibility if needed
class LicenseBearer:
    """Thin wrapper; prefer make_license_bearer() for new code."""

    def __init__(self, verify_key: VerifyKey) -> None:
        self._fn = make_license_bearer(verify_key)

    def __call__(self, request: Request) -> dict:
        return self._fn(request)
