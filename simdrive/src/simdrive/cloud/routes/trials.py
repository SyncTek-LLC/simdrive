"""POST /v1/trials — issue a 14-day trial license key.

Rate limit: 5 trial requests per IP per day.
The signed key uses the server's private signing key (from env).
"""
from __future__ import annotations

import time
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from simdrive.cloud.db.models import TrialActivation, get_session
from simdrive.license.signer import sign_license

TRIAL_DURATION_SECONDS = 14 * 86400


router = APIRouter()


class TrialRequest(BaseModel):
    email: EmailStr


class TrialResponse(BaseModel):
    key: str
    expires_at: int
    message: str = "14-day Pro trial activated."


def _count_ip_trials_today(db: Session, ip: str) -> int:
    """Count trial activations from this IP in the last 24 hours."""
    since = datetime.utcfromtimestamp(time.time() - 86400)
    return (
        db.query(TrialActivation)
        .filter(
            TrialActivation.ip_address == ip,
            TrialActivation.issued_at >= since,
        )
        .count()
    )


def create_trials_router(signing_key, db_engine, max_per_ip: int = 5) -> APIRouter:
    """Factory that injects dependencies into the trials router.

    WHY factory: avoids module-level globals; dependencies are explicit
    and testable.
    """
    _router = APIRouter()

    @_router.post("/trials", response_model=TrialResponse)
    def post_trial(request: Request, body: TrialRequest) -> TrialResponse:
        ip = request.client.host if request.client else "unknown"
        db = get_session(db_engine)
        try:
            count = _count_ip_trials_today(db, ip)
            if count >= max_per_ip:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Trial limit reached: {max_per_ip} trials per IP per day.",
                )

            now = int(time.time())
            expires_at = now + TRIAL_DURATION_SECONDS

            key = sign_license(
                signing_key=signing_key,
                tier="trial",
                seats=1,
                customer_email=body.email,
                issued_at=now,
                expires_at=expires_at,
            )

            activation = TrialActivation(
                email=body.email,
                ip_address=ip,
                license_key=key,
                issued_at=datetime.utcfromtimestamp(now),
                expires_at=datetime.utcfromtimestamp(expires_at),
            )
            db.add(activation)
            db.commit()

            return TrialResponse(key=key, expires_at=expires_at)
        finally:
            db.close()

    return _router
