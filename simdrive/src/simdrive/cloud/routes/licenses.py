"""License routes: activate + status.

POST /v1/licenses/activate — Stripe webhook target
GET  /v1/licenses/status  — client status check (returns server_time for skew defense)
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from simdrive.cloud.db.models import LicenseActivation, get_session
from simdrive.license.errors import LicenseError
from simdrive.license.signer import sign_license, VALID_TIERS
from simdrive.license.validator import validate_license

LICENSE_DURATION_SECONDS = 365 * 86400  # 1 year default; Stripe sets real expiry

# Pydantic validator for tier field
_VALID_TIER_LITERALS = Literal["trial", "solo", "pro", "team", "enterprise"]


class ActivateRequest(BaseModel):
    stripe_subscription_id: str
    email: EmailStr
    tier: _VALID_TIER_LITERALS = "solo"
    seats: int = 1


class ActivateResponse(BaseModel):
    key: str
    tier: str
    seats: int
    expires_at: int


class StatusResponse(BaseModel):
    valid: bool
    expires_at: Optional[int] = None
    server_time: int
    tier: Optional[str] = None


def create_licenses_router(signing_key, verify_key, db_engine) -> APIRouter:
    """Factory that injects dependencies into the licenses router."""
    _router = APIRouter()

    @_router.post("/licenses/activate", response_model=ActivateResponse)
    def post_activate(body: ActivateRequest) -> ActivateResponse:
        db = get_session(db_engine)
        try:
            now = int(time.time())
            expires_at = now + LICENSE_DURATION_SECONDS

            key = sign_license(
                signing_key=signing_key,
                tier=body.tier,
                seats=body.seats,
                customer_email=body.email,
                issued_at=now,
                expires_at=expires_at,
            )

            activation = LicenseActivation(
                stripe_subscription_id=body.stripe_subscription_id,
                email=body.email,
                tier=body.tier,
                seats=body.seats,
                license_key=key,
                activated_at=datetime.utcfromtimestamp(now),
                expires_at=datetime.utcfromtimestamp(expires_at),
            )
            db.add(activation)
            db.commit()

            return ActivateResponse(
                key=key,
                tier=body.tier,
                seats=body.seats,
                expires_at=expires_at,
            )
        finally:
            db.close()

    @_router.get("/licenses/status", response_model=StatusResponse)
    def get_status(key: str) -> StatusResponse:
        """Validate a license key and return status + server_time.

        server_time is returned so clients can calibrate clock-skew defense.
        This endpoint is intentionally public (no auth) — clients call it
        even when their local key has expired.
        """
        server_time = int(time.time())
        try:
            payload = validate_license(
                key,
                verify_key=verify_key,
                last_known_server_time=server_time,
            )
            return StatusResponse(
                valid=True,
                expires_at=payload["expires_at"],
                server_time=server_time,
                tier=payload.get("tier"),
            )
        except LicenseError:
            return StatusResponse(valid=False, server_time=server_time)

    return _router
