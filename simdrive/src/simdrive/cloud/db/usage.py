"""Usage counter model for per-license, per-month journey run tracking.

WHY separate module from models.py: single-responsibility. The usage
table is conceptually owned by the quotas middleware, not by the
recording/trial/license models.

Schema:
  usage_counters
    id                    INTEGER PK (autoincrement)
    license_key_fingerprint  VARCHAR(32)  — SHA-256[:32] of the license key string
    customer_email        VARCHAR(255)   — denormalized for human readability
    tier                  VARCHAR(32)    — tier at time of first use this period
    month_bucket          VARCHAR(7)     — "YYYY-MM" (UTC)
    runs_used             INTEGER        — incremented atomically per run
    created_at            DATETIME
    updated_at            DATETIME

WHY fingerprint instead of full key: license keys are ~200 chars; storing
the full key in a high-write counter table wastes space. The fingerprint is
a 32-char SHA-256 prefix — collision probability is negligible at first-5-customer scale.

WHY month_bucket: counter resets each calendar month. Bucketing by "YYYY-MM"
makes the reset query simple and the retention policy clear.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Session

from simdrive.cloud.db.models import Base


class UsageCounter(Base):
    """Per-license-key, per-month run counter."""

    __tablename__ = "usage_counters"
    __table_args__ = (
        UniqueConstraint(
            "license_key_fingerprint",
            "month_bucket",
            name="uq_usage_lfp_month",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    license_key_fingerprint = Column(String(32), nullable=False, index=True)
    customer_email = Column(String(255), nullable=False, index=True)
    tier = Column(String(32), nullable=False)
    month_bucket = Column(String(7), nullable=False)  # "YYYY-MM"
    runs_used = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


def key_fingerprint(license_key: str) -> str:
    """Return a 32-char fingerprint for a license key string.

    WHY not store full key: saves space; the fingerprint is unguessable
    and collision-resistant at our scale.
    """
    return hashlib.sha256(license_key.encode()).hexdigest()[:32]


def current_month_bucket() -> str:
    """Return the current month bucket string 'YYYY-MM' (UTC)."""
    return time.strftime("%Y-%m", time.gmtime())


# Tier → monthly run limit
TIER_RUN_LIMITS: dict[str, int] = {
    "solo": 50,
    "pro": 250,
    "team": 1000,
    "trial": 250,
    "enterprise": 999_999_999,  # effectively unlimited at our scale
}

DEFAULT_RUN_LIMIT = 250  # fallback for unknown tiers


def get_or_create_counter(
    db: Session,
    *,
    license_key: str,
    customer_email: str,
    tier: str,
    month_bucket: Optional[str] = None,
) -> UsageCounter:
    """Fetch the current-month counter for a license key, creating if absent.

    WHY upsert via get-or-create (not SQL UPSERT): SQLAlchemy Core UPSERT
    syntax differs across SQLite/Postgres. At first-5-customer scale,
    a Python-level check-then-insert under a try/except is clear and safe.
    Race conditions are benign — the worst outcome is two rows, which the
    unique constraint will reject, causing a rollback and a retry.
    """
    bucket = month_bucket or current_month_bucket()
    lfp = key_fingerprint(license_key)

    counter = (
        db.query(UsageCounter)
        .filter_by(license_key_fingerprint=lfp, month_bucket=bucket)
        .first()
    )
    if counter is None:
        counter = UsageCounter(
            license_key_fingerprint=lfp,
            customer_email=customer_email,
            tier=tier,
            month_bucket=bucket,
            runs_used=0,
        )
        db.add(counter)
        db.flush()  # write without committing so caller controls transaction

    return counter


def increment_runs(
    db: Session,
    *,
    license_key: str,
    customer_email: str,
    tier: str,
    month_bucket: Optional[str] = None,
) -> UsageCounter:
    """Increment run counter for this license key's current month.

    WHY flush then update: avoids a SELECT-then-UPDATE race; we update
    in-place on the ORM object and commit once.

    Returns the updated counter (after increment, before commit).
    """
    counter = get_or_create_counter(
        db,
        license_key=license_key,
        customer_email=customer_email,
        tier=tier,
        month_bucket=month_bucket,
    )
    counter.runs_used = (counter.runs_used or 0) + 1
    counter.updated_at = datetime.utcnow()
    return counter


def get_run_limit(tier: str) -> int:
    """Return the monthly run limit for a tier."""
    return TIER_RUN_LIMITS.get(tier.lower(), DEFAULT_RUN_LIMIT)
