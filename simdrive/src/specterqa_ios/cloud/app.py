"""Cloud FastAPI application factory.

WHY factory pattern (`create_app`): avoids module-level globals;
all dependencies (signing key, verify key, db engine, storage) are
injected at construction time — trivially testable and mockable.

DB choice: SQLite (sync SQLAlchemy) for cycle 1.
WHY: TestClient is synchronous; SQLite needs no daemon process.
Migration to async + Railway Postgres is scoped to cycle 2.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI

from specterqa_ios.cloud.config import CloudConfig
from specterqa_ios.cloud.db.models import get_engine, init_db
from specterqa_ios.cloud.routes.licenses import create_licenses_router
from specterqa_ios.cloud.routes.recordings import create_recordings_router
from specterqa_ios.cloud.routes.trials import create_trials_router
from specterqa_ios.cloud.storage.r2_stub import R2Stub
from specterqa_ios.license.keypair import (
    signing_key_from_hex,
    verify_key_from_hex,
)


def create_app(
    *,
    public_key_hex: Optional[str] = None,
    private_key_hex: Optional[str] = None,
    database_url: Optional[str] = None,
    storage_root: Optional[Path] = None,
    max_trials_per_ip: int = 5,
    _signing_key=None,   # override for tests: pass SigningKey directly
    _verify_key=None,    # override for tests: pass VerifyKey directly
) -> FastAPI:
    """Create and configure the cloud FastAPI application.

    Parameters
    ----------
    public_key_hex:
        Hex-encoded Ed25519 verify (public) key. Defaults to env var
        SIMDRIVE_PUBLIC_KEY_HEX, then the placeholder constant.
    private_key_hex:
        Hex-encoded Ed25519 signing (private) key. Defaults to env var
        SIMDRIVE_LICENSE_PRIVATE_KEY.
        If not provided, the /v1/trials and /v1/licenses/activate endpoints
        will not be functional (read-only mode).
    database_url:
        SQLAlchemy database URL. Defaults to in-memory SQLite for tests,
        or SIMDRIVE_DATABASE_URL env var for production.
    storage_root:
        Path for R2Stub local storage. Defaults to a temp directory.
    max_trials_per_ip:
        Rate-limit for trial activations per IP per day.
    """
    config = CloudConfig.from_env()

    # Resolve keys
    resolved_public_key_hex = (
        public_key_hex
        or config.public_key_hex
    )
    resolved_private_key_hex = (
        private_key_hex
        or config.private_key_hex
        or os.environ.get("SIMDRIVE_LICENSE_PRIVATE_KEY")
    )

    # Allow test overrides for signing/verify keys directly
    if _verify_key is not None and _signing_key is not None:
        verify_key = _verify_key
        signing_key = _signing_key
    elif _verify_key is not None:
        verify_key = _verify_key
        from specterqa_ios.license.keypair import generate_keypair
        signing_key, _ = generate_keypair()
    else:
        verify_key = verify_key_from_hex(resolved_public_key_hex)
        if resolved_private_key_hex:
            signing_key = signing_key_from_hex(resolved_private_key_hex)
        else:
            # No private key — generate a fresh keypair (dev mode)
            # Signing will work but the verify_key won't match the
            # public_key_hex embedded in clients. Set env var in production.
            from specterqa_ios.license.keypair import generate_keypair
            signing_key, verify_key = generate_keypair()

    # Resolve DB
    resolved_db_url = (
        database_url
        or config.database_url
        or "sqlite://"  # in-memory for tests
    )

    # Resolve storage
    if storage_root is None:
        _tmp_dir = tempfile.mkdtemp(prefix="simdrive_r2_")
        resolved_storage_root = Path(_tmp_dir)
    else:
        resolved_storage_root = storage_root

    # Initialize DB
    engine = get_engine(resolved_db_url)
    init_db(engine)

    # Initialize R2 stub
    r2 = R2Stub(storage_root=resolved_storage_root)

    # Build FastAPI app
    app = FastAPI(
        title="SimDrive Cloud API",
        description="Private API for SimDrive replay archive and license management.",
        version="1.0.0",
    )

    # Mount routers under /v1
    trials_router = create_trials_router(
        signing_key=signing_key,
        db_engine=engine,
        max_per_ip=max_trials_per_ip,
    )
    licenses_router = create_licenses_router(
        signing_key=signing_key,
        verify_key=verify_key,
        db_engine=engine,
    )
    recordings_router = create_recordings_router(
        verify_key=verify_key,
        r2=r2,
        db_engine=engine,
    )

    app.include_router(trials_router, prefix="/v1")
    app.include_router(licenses_router, prefix="/v1")
    app.include_router(recordings_router, prefix="/v1")

    return app
