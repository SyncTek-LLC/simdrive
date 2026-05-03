"""POST /v1/runs/increment — increment a license key's monthly run counter.

This endpoint is called by the simdrive client at the start of each
journey run. It atomically increments the counter and enforces the
per-tier monthly quota.

HTTP 200 → run allowed; counter incremented.
HTTP 429 → quota exhausted; Retry-After header gives seconds to next reset.

Auth: Bearer license key.

WHY a dedicated endpoint instead of incrementing in POST /v1/recordings:
1. Not every run produces an archived recording (e.g., `simdrive run` without
   `--cloud-upload`). Usage should count all runs, not just archival uploads.
2. Single-responsibility: the quota gate is a separate concern from storage.
3. Clients can call this at run-start to get an early rejection instead of
   discovering the quota issue after completing a 3-minute journey.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from simdrive.cloud.auth import make_license_bearer
from simdrive.cloud.db.models import get_session
from simdrive.cloud.db.usage import (
    get_or_create_counter,
    get_run_limit,
    increment_runs,
)
from simdrive.cloud.middleware.quotas import _month_period


def create_runs_router(verify_key, db_engine) -> APIRouter:
    """Factory that injects auth and db dependencies."""
    _router = APIRouter()
    _auth = make_license_bearer(verify_key)

    @_router.post("/runs/increment")
    def post_increment(
        request: Request,
        license_payload: dict = Depends(_auth),
    ) -> dict[str, Any]:
        """Increment this license key's run counter for the current month.

        Returns the updated usage summary.
        Raises 429 with Retry-After if quota is exhausted.
        """
        customer_email = license_payload.get("customer_email", "unknown")
        tier = license_payload.get("tier", "solo")
        run_limit = get_run_limit(tier)

        # Extract the raw license key from the Authorization header
        authorization = request.headers.get("Authorization", "")
        license_key = (
            authorization[len("Bearer "):]
            if authorization.startswith("Bearer ")
            else ""
        )

        db = get_session(db_engine)
        try:
            counter = get_or_create_counter(
                db,
                license_key=license_key,
                customer_email=customer_email,
                tier=tier,
            )
            db.flush()

            current_runs = counter.runs_used or 0
            if current_runs >= run_limit:
                period_start, period_end = _month_period()
                seconds_until_reset = max(0, period_end + 1 - int(time.time()))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        f"Quota exceeded: {current_runs}/{run_limit} runs used "
                        f"for {tier} tier this month. "
                        f"Resets in {seconds_until_reset // 86400} days."
                    ),
                    headers={"Retry-After": str(seconds_until_reset)},
                )

            # Increment the counter
            from datetime import datetime
            counter.runs_used = current_runs + 1
            counter.updated_at = datetime.utcnow()
            db.commit()

            import calendar as _cal
            period_start, period_end = _month_period()
            new_runs = counter.runs_used
            percent_used = round((new_runs / run_limit) * 100.0, 2) if run_limit > 0 else 0.0

            return {
                "runs_used": new_runs,
                "runs_limit": run_limit,
                "tier": tier,
                "percent_used": percent_used,
                "period_start": period_start,
                "period_end": period_end,
            }
        except HTTPException:
            db.close()
            raise
        finally:
            try:
                db.close()
            except Exception:
                pass

    return _router
