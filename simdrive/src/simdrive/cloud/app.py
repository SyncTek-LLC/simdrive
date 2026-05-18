"""Cloud FastAPI application factory.

WHY factory pattern (`create_app`): avoids module-level globals;
all dependencies (signing key, verify key, db engine, storage) are
injected at construction time — trivially testable and mockable.

DB choice: SQLite (sync SQLAlchemy) for cycle 1+2.
WHY: TestClient is synchronous; SQLite needs no daemon process.
Migration to Railway Postgres is scoped to cycle 3 (production hardening).

Cycle 2 additions:
  - real R2 storage via create_storage_backend() (falls back to R2Stub)
  - GET /health endpoint for Railway healthcheck-driven deploys
  - POST /v1/runs/increment for per-tier quota tracking
  - GET /v1/licenses/usage for quota visibility
  - STORAGE_BACKEND env var selects backend (r2 vs stub)
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI

from simdrive.cloud.config import CloudConfig
from simdrive.cloud.db.models import get_engine, init_db
from simdrive.cloud.db.usage import UsageCounter  # noqa: F401 — ensures table is created
from simdrive.cloud.routes.licenses import create_licenses_router
from simdrive.cloud.routes.recordings import create_recordings_router
from simdrive.cloud.routes.runs import create_runs_router
from simdrive.cloud.routes.trials import create_trials_router
from simdrive.license.keypair import (
    signing_key_from_hex,
    verify_key_from_hex,
)

# Import version for the health endpoint
try:
    from importlib.metadata import version as _pkg_version
    _SIMDRIVE_VERSION = _pkg_version("simdrive")
except Exception:
    _SIMDRIVE_VERSION = "unknown"


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
        Path for R2Stub local storage fallback. Defaults to a temp directory.
        Ignored when R2Client is selected.
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
        from simdrive.license.keypair import generate_keypair
        signing_key, _ = generate_keypair()
    else:
        verify_key = verify_key_from_hex(resolved_public_key_hex)
        if resolved_private_key_hex:
            signing_key = signing_key_from_hex(resolved_private_key_hex)
        else:
            # No private key — generate a fresh keypair (dev mode)
            # Signing will work but the verify_key won't match the
            # public_key_hex embedded in clients. Set env var in production.
            from simdrive.license.keypair import generate_keypair
            signing_key, verify_key = generate_keypair()

    # Resolve DB
    resolved_db_url = (
        database_url
        or config.database_url
        or "sqlite://"  # in-memory for tests
    )

    # Initialize DB (creates all tables including usage_counters)
    engine = get_engine(resolved_db_url)
    init_db(engine)

    # Resolve storage backend (R2Client if env vars present, else R2Stub)
    from simdrive.cloud.storage.r2 import create_storage_backend
    if storage_root is None:
        _tmp_dir = tempfile.mkdtemp(prefix="simdrive_r2_")
        _storage_root = Path(_tmp_dir)
    else:
        _storage_root = storage_root

    r2 = create_storage_backend(storage_root=_storage_root)

    # Determine storage backend name for health endpoint
    backend_name = type(r2).__name__.lower()  # "r2stub" or "r2client"

    # Build FastAPI app
    app = FastAPI(
        title="SimDrive Cloud API",
        description="Private API for SimDrive replay archive and license management.",
        version=_SIMDRIVE_VERSION,
    )

    # Stash engine on app.state so tests/quota enforcement can reach it
    app.state.db_engine = engine
    app.state.storage_backend_name = backend_name
    app.state.version = _SIMDRIVE_VERSION

    # ---- Health endpoint (public, no auth) ----
    @app.get("/health")
    def health() -> dict:
        """Railway healthcheck endpoint.

        Returns status, version, db_reachable, and storage_backend name.
        Railway marks the deploy healthy when this returns 200.
        """
        db_ok = False
        try:
            from sqlalchemy import text
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            db_ok = False

        return {
            "status": "ok",
            "version": _SIMDRIVE_VERSION,
            "db_reachable": db_ok,
            "storage_backend": backend_name,
        }

    # ---- Mount routers under /v1 ----
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
    runs_router = create_runs_router(
        verify_key=verify_key,
        db_engine=engine,
    )

    app.include_router(trials_router, prefix="/v1")
    app.include_router(licenses_router, prefix="/v1")
    app.include_router(recordings_router, prefix="/v1")
    app.include_router(runs_router, prefix="/v1")

    return app


# Module-level app instance for uvicorn / Railway entrypoint.
# WHY: uvicorn expects `module:attribute`, e.g. `simdrive.cloud.app:app`.
# create_app() reads config from env vars at import time when used this way.
# For tests, always call create_app() directly to inject test dependencies.
app = create_app()
