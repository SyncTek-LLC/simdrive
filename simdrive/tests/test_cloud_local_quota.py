"""Tests for the network-free per-tool quota check.

Wave 2 calls ``check_local_quota(tool_name, session)`` from inside the
MCP tool dispatch — the check must:
  - Raise QuotaExceededError when the session's local snapshot says
    runs_used >= runs_limit.
  - Allow the call (return None) when within quota.
  - Allow the call when there is no snapshot at all (fresh session); the
    authoritative cloud-side gate will catch it on the next increment.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


class TestCheckLocalQuota:
    def test_under_quota_returns_none(self) -> None:
        from simdrive.cloud.middleware.quotas import (
            LocalQuotaSnapshot,
            check_local_quota,
        )

        session = SimpleNamespace(
            quota_snapshot=LocalQuotaSnapshot(
                tier="pro", runs_used=10, runs_limit=250
            )
        )
        assert check_local_quota("record_start", session) is None

    def test_over_quota_raises_quota_exceeded(self) -> None:
        from simdrive.cloud.errors import QuotaExceededError
        from simdrive.cloud.middleware.quotas import (
            LocalQuotaSnapshot,
            check_local_quota,
        )

        session = SimpleNamespace(
            quota_snapshot=LocalQuotaSnapshot(
                tier="solo", runs_used=50, runs_limit=50
            )
        )
        with pytest.raises(QuotaExceededError) as exc_info:
            check_local_quota("replay", session)
        assert exc_info.value.code == "cloud_quota_exceeded"
        details = exc_info.value.details
        assert details["tool_name"] == "replay"
        assert details["tier"] == "solo"
        assert details["runs_used"] == 50
        assert details["runs_limit"] == 50

    def test_no_snapshot_is_passthrough(self) -> None:
        """A session with no cached quota info must NOT block — the
        cloud-side gate is the authoritative enforcer."""
        from simdrive.cloud.middleware.quotas import check_local_quota

        session = SimpleNamespace()
        assert check_local_quota("record_start", session) is None

    def test_none_session_is_passthrough(self) -> None:
        from simdrive.cloud.middleware.quotas import check_local_quota

        assert check_local_quota("record_start", None) is None

    def test_dict_session_with_quota_snapshot(self) -> None:
        from simdrive.cloud.errors import QuotaExceededError
        from simdrive.cloud.middleware.quotas import check_local_quota

        session = {
            "quota_snapshot": {
                "tier": "trial",
                "runs_used": 250,
                "runs_limit": 250,
            }
        }
        with pytest.raises(QuotaExceededError):
            check_local_quota("observe", session)

    def test_dict_session_under_quota(self) -> None:
        from simdrive.cloud.middleware.quotas import check_local_quota

        session = {
            "quota_snapshot": {
                "tier": "team",
                "runs_used": 999,
                "runs_limit": 1000,
            }
        }
        assert check_local_quota("tap", session) is None

    def test_local_quota_attr_alias_works(self) -> None:
        """Session can carry the snapshot under ``local_quota`` instead of
        ``quota_snapshot`` — both should work."""
        from simdrive.cloud.errors import QuotaExceededError
        from simdrive.cloud.middleware.quotas import (
            LocalQuotaSnapshot,
            check_local_quota,
        )

        session = SimpleNamespace(
            local_quota=LocalQuotaSnapshot(tier="solo", runs_used=51, runs_limit=50)
        )
        with pytest.raises(QuotaExceededError):
            check_local_quota("record_start", session)

    def test_malformed_snapshot_is_passthrough(self) -> None:
        """A snapshot missing required fields should not throw KeyError;
        the check treats it as 'no snapshot'."""
        from simdrive.cloud.middleware.quotas import check_local_quota

        session = {"quota_snapshot": {"tier": "pro"}}  # missing runs_used/limit
        assert check_local_quota("record_start", session) is None

    def test_snapshot_remaining_and_over_limit(self) -> None:
        from simdrive.cloud.middleware.quotas import LocalQuotaSnapshot

        s = LocalQuotaSnapshot(tier="pro", runs_used=200, runs_limit=250)
        assert s.remaining == 50
        assert s.over_limit is False

        s2 = LocalQuotaSnapshot(tier="pro", runs_used=260, runs_limit=250)
        assert s2.remaining == 0
        assert s2.over_limit is True


class TestQuotaErrorEnvelope:
    def test_quota_exceeded_envelope_shape(self) -> None:
        from simdrive.cloud.errors import quota_exceeded

        err = quota_exceeded(
            tool_name="record_start",
            tier="solo",
            runs_used=50,
            runs_limit=50,
        )
        env = err.to_dict()
        assert env["ok"] is False
        assert env["error"]["code"] == "cloud_quota_exceeded"
        assert env["error"]["details"]["tool_name"] == "record_start"
        assert "simdrive.dev/pricing" in err.message
