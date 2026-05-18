"""Cloud API configuration — loaded from environment variables.

WHY environment-based config: no secrets in code; Railway injects env vars
at deploy time. The same pattern used by forgeos-engine.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CloudConfig:
    """Configuration for the cloud FastAPI server.

    All values default to development-safe defaults; production overrides
    via environment variables.
    """

    # License signing
    public_key_hex: str = field(
        default_factory=lambda: os.environ.get(
            "SIMDRIVE_PUBLIC_KEY_HEX",
            "0000000000000000000000000000000000000000000000000000000000000001",
        )
    )
    private_key_hex: Optional[str] = field(
        default_factory=lambda: os.environ.get("SIMDRIVE_LICENSE_PRIVATE_KEY")
    )

    # Storage
    storage_root: str = field(
        default_factory=lambda: os.environ.get(
            "SIMDRIVE_STORAGE_ROOT", "/tmp/simdrive-cloud"
        )
    )

    # Rate limiting
    trial_max_per_ip_per_day: int = 5

    # Database
    # WHY sqlite not sqlite+aiosqlite: the sync TestClient and sync SQLAlchemy
    # ORM is used throughout. aiosqlite would require async routes/tests.
    # Migration to Postgres is scoped to cycle 3.
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "SIMDRIVE_DATABASE_URL", "sqlite:///./simdrive_cloud.db"
        )
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 8080

    @classmethod
    def from_env(cls) -> "CloudConfig":
        """Create config from environment variables."""
        return cls()


# Tier storage quotas in bytes
TIER_QUOTA_BYTES: dict[str, Optional[int]] = {
    "trial": 100 * 1024 * 1024,         # 100 MB
    "solo": 100 * 1024 * 1024,           # 100 MB
    "pro": 1024 * 1024 * 1024,           # 1 GB
    "team": 10 * 1024 * 1024 * 1024,    # 10 GB
    "enterprise": None,                   # unlimited
}
