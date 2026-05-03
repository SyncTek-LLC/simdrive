"""Recording routes: upload, list, download, delete.

POST   /v1/recordings     — multipart upload (recording.yaml + screenshots)
GET    /v1/recordings     — list caller's recordings
GET    /v1/recordings/{id} — download as tar.gz (presigned URL redirect)
DELETE /v1/recordings/{id} — delete recording

Auth: Bearer license key. Trial tier is rejected (Pro+ required).

WHY Pro+ gate on /v1/recordings: trial users get the 29 MCP tools and the
journey runner, but cloud archival is a paid feature. This is the natural
upsell boundary per §3 of the engineering spec.
"""
from __future__ import annotations

import base64
import hashlib
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from simdrive.cloud.auth import make_license_bearer
from simdrive.cloud.db.models import Recording, get_session
from simdrive.cloud.storage.r2_stub import R2Stub

# Tiers that are allowed to use cloud recording storage
_PAID_TIERS = frozenset({"solo", "pro", "team", "enterprise"})


class RecordingUploadRequest(BaseModel):
    license_key: Optional[str] = None  # deprecated; auth is now via Bearer header
    recording_yaml: str
    screenshots: list[str] = []  # base64-encoded PNG bytes


class RecordingUploadResponse(BaseModel):
    recording_id: str
    url: str


class RecordingListItem(BaseModel):
    id: str
    journey_slug: Optional[str]
    created_at: str
    size_bytes: int
    screenshot_count: int


def _require_paid_tier(license_payload: dict) -> dict:
    """Raise 403 if the license tier is not a paid tier (trial is blocked).

    WHY 403 (not 402): RFC semantics — the request is authenticated but
    the caller lacks the required entitlement. 402 Payment Required is
    technically correct but poorly supported. 403 with a clear detail
    message is more actionable.
    """
    tier = license_payload.get("tier", "trial")
    if tier not in _PAID_TIERS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Cloud recording storage requires a paid tier (Solo, Pro, or Team). "
                f"Your license tier is '{tier}'. "
                f"Upgrade at https://simdrive.dev/pricing."
            ),
        )
    return license_payload


def create_recordings_router(
    verify_key,
    r2: R2Stub,   # accepts R2Stub or R2Client — same interface
    db_engine,
) -> APIRouter:
    """Factory that injects auth, storage, and db dependencies."""
    _router = APIRouter()
    _auth = make_license_bearer(verify_key)

    @_router.post("/recordings", response_model=RecordingUploadResponse)
    def post_recording(
        body: RecordingUploadRequest,
        license_payload: dict = Depends(_auth),
    ) -> RecordingUploadResponse:
        """Upload a recording yaml + screenshots to R2 storage.

        WHY UUID for recording_id: avoids enumeration; easy to generate
        without a database round-trip. The db row is written after storage
        so a failed upload leaves no orphan metadata.

        WHY Pro+ gate here: trial users get the journey runner but not
        cloud archival — this is the upgrade boundary.
        """
        # Enforce paid-tier requirement
        _require_paid_tier(license_payload)

        customer_email = license_payload.get("customer_email", "unknown")
        recording_id = str(uuid.uuid4())

        # Store recording YAML
        yaml_key = f"{customer_email}/{recording_id}/recording.yaml"
        r2.put_object(yaml_key, body.recording_yaml.encode("utf-8"))

        # Store screenshots
        screenshot_count = 0
        for idx, screenshot_b64 in enumerate(body.screenshots):
            try:
                screenshot_bytes = base64.b64decode(screenshot_b64)
                shot_key = f"{customer_email}/{recording_id}/screenshots/{idx:04d}.png"
                r2.put_object(shot_key, screenshot_bytes)
                screenshot_count += 1
            except Exception:
                pass  # Malformed base64 is skipped, not fatal

        # Presigned URL for the recording YAML
        url = r2.presigned_url(yaml_key, expires_in=3600 * 24 * 7)

        # Persist metadata
        db = get_session(db_engine)
        try:
            total_size = len(body.recording_yaml.encode("utf-8"))
            rec = Recording(
                id=recording_id,
                customer_email=customer_email,
                journey_slug=None,
                r2_key=yaml_key,
                size_bytes=total_size,
                screenshot_count=screenshot_count,
            )
            db.add(rec)
            db.commit()
        finally:
            db.close()

        return RecordingUploadResponse(recording_id=recording_id, url=url)

    @_router.get("/recordings", response_model=list[RecordingListItem])
    def list_recordings(
        license_payload: dict = Depends(_auth),
    ) -> list[RecordingListItem]:
        """List recordings belonging to the authenticated customer."""
        _require_paid_tier(license_payload)
        customer_email = license_payload.get("customer_email", "unknown")

        db = get_session(db_engine)
        try:
            rows = (
                db.query(Recording)
                .filter_by(customer_email=customer_email)
                .order_by(Recording.created_at.desc())
                .all()
            )
            return [
                RecordingListItem(
                    id=row.id,
                    journey_slug=row.journey_slug,
                    created_at=row.created_at.isoformat() if row.created_at else "",
                    size_bytes=row.size_bytes or 0,
                    screenshot_count=row.screenshot_count or 0,
                )
                for row in rows
            ]
        finally:
            db.close()

    @_router.get("/recordings/{recording_id}")
    def get_recording(
        recording_id: str,
        license_payload: dict = Depends(_auth),
    ) -> dict[str, Any]:
        """Download a recording — returns a presigned URL.

        WHY presigned URL instead of proxied download: avoids the API
        server becoming a bandwidth bottleneck. The client follows the
        redirect directly to R2.
        """
        _require_paid_tier(license_payload)
        customer_email = license_payload.get("customer_email", "unknown")

        db = get_session(db_engine)
        try:
            rec = db.query(Recording).filter_by(id=recording_id).first()
            if rec is None or rec.customer_email != customer_email:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Recording {recording_id!r} not found.",
                )

            try:
                url = r2.presigned_url(rec.r2_key, expires_in=3600)
            except FileNotFoundError:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Recording data for {recording_id!r} not found in storage.",
                )

            return {
                "recording_id": recording_id,
                "journey_slug": rec.journey_slug,
                "download_url": url,
                "size_bytes": rec.size_bytes,
                "screenshot_count": rec.screenshot_count,
            }
        finally:
            db.close()

    @_router.delete("/recordings/{recording_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
    def delete_recording(
        recording_id: str,
        license_payload: dict = Depends(_auth),
    ) -> Response:
        """Delete a recording and all its stored objects.

        WHY delete storage before DB: if the DB delete fails, the storage
        objects are gone — an acceptable inconsistency. If storage delete
        fails, the DB row stays intact and the user can retry or contact
        support. Storage orphans are preferable to DB orphans.
        """
        _require_paid_tier(license_payload)
        customer_email = license_payload.get("customer_email", "unknown")

        db = get_session(db_engine)
        try:
            rec = db.query(Recording).filter_by(id=recording_id).first()
            if rec is None or rec.customer_email != customer_email:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Recording {recording_id!r} not found.",
                )

            # Delete all objects under this recording's prefix
            prefix = f"{customer_email}/{recording_id}/"
            for key in r2.list_objects(prefix):
                r2.delete_object(key)

            # Also delete the yaml key directly (in case list_objects misses it)
            r2.delete_object(rec.r2_key)

            db.delete(rec)
            db.commit()
        finally:
            db.close()

        return Response(status_code=204)

    return _router
