"""Per-tier monthly quota enforcement for journey runs.

Tiers and monthly limits (per PLAN §6):
  Solo:       50 runs/mo
  Pro:       250 runs/mo
  Team:     1000 runs/mo
  Trial:     250 runs/mo (soft-cap on runs; Claude API $5/day cap is engine-side)
  Enterprise: unlimited (999,999,999 internal sentinel)

Usage counters are persisted per-license-key per-month in SQLite.
The fingerprint of the license key (SHA-256[:32]) is the DB key to avoid
storing the full ~200-char key in a high-write table.

WHY this file is the authoritative gate: the quota check happens at the
FastAPI route level via `Depends(make_quota_gate(...))`, not in a
Starlette middleware. This lets us return structured 429 responses and
skip the gate on public endpoints (GET /health, /v1/licenses/status).

INIT-2026-549 W-F:
Also exposes :func:`check_local_quota`, a network-free per-tool check
that Wave 2 wires into the MCP tool dispatch inside server.py. The
check reads from a locally-cached quota snapshot attached to the
session so it stays fast (no DB round-trip from inside a hot path).
"""
from __future__ import annotations

import calendar
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request, status

from simdrive.cloud.auth import make_license_bearer
from simdrive.cloud.db.models import get_session
from simdrive.cloud.db.usage import (
    get_or_create_counter,
    get_run_limit,
)
from simdrive.cloud.errors import QuotaExceededError, quota_exceeded


def _month_period() -> tuple[int, int]:
    """Return (period_start, period_end) as UTC unix timestamps for the current month.

    period_start = first second of current month (00:00:00 UTC)
    period_end   = last second of current month (23:59:59 UTC)
    """
    now = time.gmtime()
    year, month = now.tm_year, now.tm_mon
    _, last_day = calendar.monthrange(year, month)

    # Start: first day of month at midnight UTC
    start = calendar.timegm((year, month, 1, 0, 0, 0, 0, 0, 0))
    # End: last day of month at 23:59:59 UTC
    end = calendar.timegm((year, month, last_day, 23, 59, 59, 0, 0, 0))
    return start, end


def make_usage_checker(verify_key, db_engine):
    """Return a FastAPI dependency that reads usage without incrementing.

    Used by GET /v1/licenses/usage — read-only, no increment.
    """
    _auth = make_license_bearer(verify_key)

    def _check_usage(
        key: str,
        request: Request,
    ) -> dict:
        """Validate license key from query param and return usage info."""
        from simdrive.license.validator import validate_license
        from simdrive.license.errors import LicenseError

        try:
            payload = validate_license(
                key,
                verify_key=verify_key,
                last_known_server_time=int(time.time()),
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

        return {
            "period_start": period_start,
            "period_end": period_end,
            "runs_used": runs_used,
            "runs_limit": run_limit,
            "tier": tier,
            "percent_used": percent_used,
        }

    return _check_usage


def make_quota_gate(verify_key, db_engine):
    """Return a FastAPI dependency that checks quota and increments on POST /v1/runs/increment.

    This dependency is used on the increment endpoint.
    Raises HTTP 429 if the tier quota is exhausted.
    """
    _auth = make_license_bearer(verify_key)

    def _gate(
        request: Request,
        license_payload: dict = Depends(_auth),
    ) -> dict:
        """Check quota and increment counter. Raises 429 if exceeded."""
        customer_email = license_payload.get("customer_email", "unknown")
        tier = license_payload.get("tier", "solo")
        run_limit = get_run_limit(tier)

        # Reconstruct the raw key string from the Authorization header
        authorization = request.headers.get("Authorization", "")
        license_key = authorization[len("Bearer "):] if authorization.startswith("Bearer ") else ""

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
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        f"Quota exceeded: {current_runs}/{run_limit} runs used for "
                        f"{tier} tier this month. "
                        f"Resets {time.strftime('%Y-%m-%d', time.gmtime(period_end + 1))}."
                    ),
                    headers={"Retry-After": str(period_end + 1 - int(time.time()))},
                )

            # Increment
            counter.runs_used = current_runs + 1
            counter.updated_at = datetime.utcnow()
            db.commit()
        except HTTPException:
            db.close()
            raise
        finally:
            try:
                db.close()
            except Exception:
                pass

        return license_payload

    return _gate


# ---------------------------------------------------------------------------
# Wave 2 hook: local (network-free) per-tool quota check
# ---------------------------------------------------------------------------


@dataclass
class LocalQuotaSnapshot:
    """In-memory snapshot of a customer's current quota state.

    Stashed onto the MCP session by the auth bootstrap so the per-tool
    quota check can be done without hitting the DB or the cloud. The
    bootstrap layer refreshes this snapshot on a coarse cadence (e.g.
    once per session start + once every N tool calls), trading freshness
    for not paying a round-trip on every dispatch.
    """

    tier: str
    runs_used: int
    runs_limit: int

    @property
    def remaining(self) -> int:
        return max(0, self.runs_limit - self.runs_used)

    @property
    def over_limit(self) -> bool:
        return self.runs_used >= self.runs_limit


def _resolve_snapshot(session: Any) -> Optional[LocalQuotaSnapshot]:
    """Find a LocalQuotaSnapshot on the session in either attr or dict form.

    Sessions in this codebase are not always typed (the MCP server uses a
    dataclass, the tests use a SimpleNamespace, callers may use a dict).
    Look in the common spots and normalise to a LocalQuotaSnapshot before
    returning. Returns None if no quota info is attached at all (caller
    decides whether to treat that as "allow" or "block").
    """
    if session is None:
        return None

    candidate = None
    if isinstance(session, dict):
        candidate = session.get("quota_snapshot") or session.get("local_quota")
    else:
        candidate = getattr(session, "quota_snapshot", None) or getattr(
            session, "local_quota", None
        )

    if candidate is None:
        return None
    if isinstance(candidate, LocalQuotaSnapshot):
        return candidate
    if isinstance(candidate, dict):
        try:
            return LocalQuotaSnapshot(
                tier=str(candidate["tier"]),
                runs_used=int(candidate["runs_used"]),
                runs_limit=int(candidate["runs_limit"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
    return None


def check_local_quota(tool_name: str, session: Any) -> None:
    """Raise QuotaExceededError when the cached quota for ``session`` is exhausted.

    Wave 2 calls this from inside the MCP tool dispatch
    (``simdrive/server.py``) BEFORE invoking the tool body. The check
    is intentionally cheap and network-free — it reads only from the
    session-local snapshot maintained by the auth/refresh bootstrap.

    Behaviour:
      - ``session`` has no snapshot at all (e.g. a brand-new session that
        has not yet refreshed): returns None so the tool proceeds. The
        authoritative cloud-side gate (``make_quota_gate``) still
        enforces the limit on the next /v1/runs/increment, so missing
        snapshots can never grant unlimited access.
      - ``session`` has a snapshot with ``runs_used >= runs_limit``:
        raises :class:`QuotaExceededError` with details containing the
        tool name, tier, and the used/limit counters.
      - Anything else: returns None.

    Parameters
    ----------
    tool_name:
        Name of the MCP tool being dispatched (e.g. "record_start").
        Surfaced in the error message so the user knows which call was
        blocked.
    session:
        The MCP session object. May be a dict, a dataclass, or a
        ``SimpleNamespace`` — anything with either a
        ``quota_snapshot`` / ``local_quota`` attribute or item.

    Raises
    ------
    QuotaExceededError
        When the cached snapshot indicates the quota is exhausted.
    """
    snapshot = _resolve_snapshot(session)
    if snapshot is None:
        return None

    if snapshot.over_limit:
        raise quota_exceeded(
            tool_name=tool_name,
            tier=snapshot.tier,
            runs_used=snapshot.runs_used,
            runs_limit=snapshot.runs_limit,
        )

    return None


__all__ = [
    "LocalQuotaSnapshot",
    "QuotaExceededError",
    "check_local_quota",
    "make_quota_gate",
    "make_usage_checker",
]
