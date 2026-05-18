"""SQLAlchemy models for the cloud API database.

DB choice (cycle 1): plain sqlite3 via SQLAlchemy Core (sync) for simplicity.
WHY not async: TestClient is synchronous; adding aiosqlite for cycle 1 would
require an async test client, adding complexity for no cycle-1 gain.
Migration to async + Postgres is scoped to cycle 2 (cloud completion sprint).

Tables:
  trial_activations  — tracks trial keys issued per email/IP for rate limiting
  license_activations — tracks activated license keys (Stripe → license)
  recordings         — metadata for uploaded replay archives
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session


class Base(DeclarativeBase):
    pass


class TrialActivation(Base):
    """Records a trial key issuance for rate-limiting and dedup."""

    __tablename__ = "trial_activations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), nullable=False, index=True)
    ip_address = Column(String(64), nullable=False, index=True)
    license_key = Column(Text, nullable=False)
    issued_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)


class LicenseActivation(Base):
    """Records a purchased license activation (Stripe → signed key)."""

    __tablename__ = "license_activations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stripe_subscription_id = Column(String(255), nullable=False, unique=True)
    email = Column(String(255), nullable=False, index=True)
    tier = Column(String(32), nullable=False)
    seats = Column(Integer, nullable=False, default=1)
    license_key = Column(Text, nullable=False)
    activated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)


class Recording(Base):
    """Metadata for an uploaded replay archive stored in R2."""

    __tablename__ = "recordings"

    id = Column(String(64), primary_key=True)  # UUID or hash
    customer_email = Column(String(255), nullable=False, index=True)
    journey_slug = Column(String(255), nullable=True)
    r2_key = Column(String(512), nullable=False)  # key in R2Stub / R2
    size_bytes = Column(Integer, nullable=False, default=0)
    screenshot_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


def get_engine(database_url: str = "sqlite:///./simdrive_cloud_test.db"):
    """Create and return a SQLAlchemy engine.

    WHY StaticPool for in-memory SQLite: when database_url is "sqlite://" the
    default pool creates a new connection per request, which means init_db()
    creates tables on one connection and the route handler sees a different
    (empty) database. StaticPool shares a single connection across all
    requests — safe for single-process tests.
    """
    from sqlalchemy.pool import StaticPool
    connect_args: dict = {"check_same_thread": False}
    kwargs: dict = {"connect_args": connect_args}
    if database_url in ("sqlite://", "sqlite:///"):
        kwargs["poolclass"] = StaticPool
    return create_engine(database_url, **kwargs)


def init_db(engine) -> None:
    """Create all tables if they don't exist."""
    Base.metadata.create_all(bind=engine)


def get_session(engine) -> Session:
    """Return a new SQLAlchemy session."""
    return Session(engine)
