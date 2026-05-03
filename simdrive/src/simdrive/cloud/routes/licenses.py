"""License routes: activate, status, and usage.

POST /v1/licenses/activate — Stripe webhook target
GET  /v1/licenses/status  — client status check (returns server_time for skew defense)
GET  /v1/licenses/usage   — per-license monthly run usage + quota
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from simdrive.cloud.db.models import LicenseActivation, get_session
from simdrive.cloud.db.usage import get_or_create_counter, get_run_limit
from simdrive.cloud.middleware.quotas import _month_period
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


class UsageResponse(BaseModel):
    period_start: int
    period_end: int
    runs_used: int
    runs_limit: int
    tier: str
    percent_used: float


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

    @_router.get("/licenses/usage", response_model=UsageResponse)
    def get_usage(key: str, request: Request) -> UsageResponse:
        """Return per-license monthly run usage and quota.

        This endpoint is public (key passed as query param, not Bearer header)
        so the client can poll usage without needing to reconstruct headers.

        Returns:
          period_start — UTC unix timestamp of start of current month
          period_end   — UTC unix timestamp of end of current month
          runs_used    — runs consumed this month
          runs_limit   — tier limit for this month
          tier         — license tier
          percent_used — (runs_used / runs_limit) * 100 rounded to 2 dp
        """
        server_time = int(time.time())
        try:
            payload = validate_license(
                key,
                verify_key=verify_key,
                last_known_server_time=server_time,
            )
        except LicenseError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=exc.message,
            ) from exc

        customer_email = payload.get("customer_email", "unknown")
        tier = payload.get("tier", "solo")
        run_limit = get_run_limit(tier)

        db = get_session(db_engine)
        try:
            counter = get_or_create_counter(
                db,
                license_key=key,
                customer_email=customer_email,
                tier=tier,
            )
            db.commit()
            runs_used = counter.runs_used or 0
        finally:
            db.close()

        period_start, period_end = _month_period()
        percent_used = round((runs_used / run_limit) * 100.0, 2) if run_limit > 0 else 0.0

        return UsageResponse(
            period_start=period_start,
            period_end=period_end,
            runs_used=runs_used,
            runs_limit=run_limit,
            tier=tier,
            percent_used=percent_used,
        )

    return _router
