"""POST /v1/recordings — upload a replay archive to R2 (stub in cycle 1).

Auth: Bearer license key.
The recording.yaml + base64-encoded screenshots are stored via R2Stub.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from simdrive.cloud.auth import make_license_bearer
from simdrive.cloud.db.models import Recording, get_session
from simdrive.cloud.storage.r2_stub import R2Stub


class RecordingUploadRequest(BaseModel):
    license_key: str
    recording_yaml: str
    screenshots: list[str] = []  # base64-encoded PNG bytes


class RecordingUploadResponse(BaseModel):
    recording_id: str
    url: str


def create_recordings_router(
    verify_key,
    r2: R2Stub,
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
        """
        customer_email = license_payload.get("customer_email", "unknown")
        recording_id = str(uuid.uuid4())

        # Store recording YAML
        yaml_key = f"{customer_email}/{recording_id}/recording.yaml"
        r2.put_object(yaml_key, body.recording_yaml.encode("utf-8"))

        # Store screenshots
        import base64
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

    return _router
