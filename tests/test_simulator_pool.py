"""Tests for M14: SimulatorPool — concurrent simulator lease management.

TDD Phase — INIT-2026-492.
These tests are written BEFORE implementation exists and are importable even
when the implementation module is absent.

Module under test (to be created by CodeAtlas):
  specterqa/ios/parallel/pool.py  —  SimulatorPool
"""

from __future__ import annotations

import threading
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard — tests remain importable without implementation.
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.parallel.pool import SimulatorPool  # type: ignore[import]

    _POOL_AVAILABLE = True
except ImportError:
    _POOL_AVAILABLE = False
    SimulatorPool = None  # type: ignore[assignment,misc]

needs_pool = pytest.mark.skipif(
    not _POOL_AVAILABLE,
    reason="specterqa.ios.parallel.pool not yet implemented",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQUIRED_LEASE_KEYS = {"device_id", "udid", "name", "lease_id"}


def _make_pool(max_concurrent: int = 4, license_validator=None) -> "SimulatorPool":
    """Return a SimulatorPool with _create_simulator and _destroy_simulator mocked."""
    pool = SimulatorPool(max_concurrent=max_concurrent, license_validator=license_validator)
    # Prevent real xcrun calls in tests that don't explicitly care about simctl.
    pool._create_simulator = MagicMock(  # type: ignore[method-assign]
        side_effect=lambda device_name: {
            "device_id": str(uuid.uuid4()),
            "udid": str(uuid.uuid4()),
            "name": device_name or "iPhone 15 Pro",
        }
    )
    pool._destroy_simulator = MagicMock()  # type: ignore[method-assign]
    return pool


# ===========================================================================
# TestSimulatorPoolAcquire — core lease acquisition behaviour
# ===========================================================================


@needs_pool
class TestSimulatorPoolAcquire:
    """acquire() returns a properly shaped lease dict."""

    def test_acquire_returns_required_keys(self):
        """acquire() returns a dict containing device_id, udid, name, lease_id."""
        pool = _make_pool()
        lease = pool.acquire(device_name="iPhone 15 Pro")
        assert isinstance(lease, dict), "acquire() must return a dict"
        missing = _REQUIRED_LEASE_KEYS - lease.keys()
        assert not missing, f"Lease dict missing keys: {missing}"

    def test_acquire_lease_id_is_string(self):
        """lease_id in the returned dict is a non-empty string."""
        pool = _make_pool()
        lease = pool.acquire()
        assert isinstance(lease["lease_id"], str)
        assert len(lease["lease_id"]) > 0

    def test_acquire_device_id_is_string(self):
        """device_id in the returned dict is a non-empty string."""
        pool = _make_pool()
        lease = pool.acquire()
        assert isinstance(lease["device_id"], str)
        assert len(lease["device_id"]) > 0

    def test_acquire_blocks_when_at_max_concurrent(self):
        """acquire() blocks a third caller when max_concurrent=2 and 2 leases are active.

        Uses a thread to verify the call is blocked for at least a short time
        before a release unblocks it.
        """
        pool = _make_pool(max_concurrent=2)

        # Fill all slots
        lease_a = pool.acquire()
        pool.acquire()

        results: list[dict] = []
        errors: list[Exception] = []
        acquired_event = threading.Event()

        def blocked_acquire():
            try:
                lease = pool.acquire()
                results.append(lease)
                acquired_event.set()
            except Exception as exc:
                errors.append(exc)
                acquired_event.set()

        t = threading.Thread(target=blocked_acquire, daemon=True)
        t.start()

        # Give the thread a moment — it should still be blocked
        time.sleep(0.1)
        assert not acquired_event.is_set(), "acquire() returned immediately despite pool being at capacity"

        # Unblock by releasing one slot
        pool.release(lease_a["lease_id"])
        acquired_event.wait(timeout=2.0)

        assert not errors, f"Blocked acquire raised: {errors[0]}"
        assert len(results) == 1, "Blocked acquire should have succeeded after release"
        assert _REQUIRED_LEASE_KEYS <= results[0].keys()


# ===========================================================================
# TestSimulatorPoolRelease — slot reclamation
# ===========================================================================


@needs_pool
class TestSimulatorPoolRelease:
    """release() reclaims a slot and updates available count."""

    def test_release_frees_a_slot(self):
        """available() increases by 1 after a lease is released."""
        pool = _make_pool(max_concurrent=2)
        lease = pool.acquire()
        before = pool.available()
        pool.release(lease["lease_id"])
        after = pool.available()
        assert after == before + 1, f"available() did not increase after release: {before} → {after}"

    def test_release_invalid_lease_id_raises(self):
        """release() with an unrecognised lease_id raises ValueError."""
        pool = _make_pool()
        with pytest.raises(ValueError):
            pool.release("nonexistent-lease-id-xyz")


# ===========================================================================
# TestSimulatorPoolAvailable — slot counter accuracy
# ===========================================================================


@needs_pool
class TestSimulatorPoolAvailable:
    """available() returns the correct count of free slots."""

    def test_available_starts_at_max_concurrent(self):
        """A freshly constructed pool reports available == max_concurrent."""
        pool = _make_pool(max_concurrent=3)
        assert pool.available() == 3

    def test_available_decreases_after_acquire(self):
        """Each acquire() decrements available() by 1."""
        pool = _make_pool(max_concurrent=4)
        for expected in [3, 2, 1]:
            pool.acquire()
            assert pool.available() == expected


# ===========================================================================
# TestSimulatorPoolActiveLeases — lease tracking
# ===========================================================================


@needs_pool
class TestSimulatorPoolActiveLeases:
    """active_leases() reflects the current set of outstanding leases."""

    def test_active_leases_empty_initially(self):
        """A freshly constructed pool has no active leases."""
        pool = _make_pool()
        assert pool.active_leases() == []

    def test_active_leases_contains_acquired_lease(self):
        """After acquire(), the lease appears in active_leases()."""
        pool = _make_pool()
        lease = pool.acquire()
        leases = pool.active_leases()
        lease_ids = [lease_item["lease_id"] for lease_item in leases]
        assert lease["lease_id"] in lease_ids

    def test_active_leases_empty_after_all_released(self):
        """active_leases() is empty once every lease is released."""
        pool = _make_pool(max_concurrent=3)
        l1 = pool.acquire()
        l2 = pool.acquire()
        pool.release(l1["lease_id"])
        pool.release(l2["lease_id"])
        assert pool.active_leases() == []


# ===========================================================================
# TestSimulatorPoolInputBackendSelection — backend routing
# ===========================================================================


@needs_pool
class TestSimulatorPoolInputBackendSelection:
    """_select_input_backend returns the correct backend per lease count."""

    def test_select_cgevents_for_single_lease(self):
        """With exactly 1 active lease, _select_input_backend returns 'cgevents'."""
        pool = _make_pool()
        pool.acquire()
        assert pool._select_input_backend() == "cgevents"

    def test_select_idb_for_multiple_leases(self):
        """With >1 active lease, _select_input_backend returns 'idb'."""
        pool = _make_pool(max_concurrent=4)
        pool.acquire()
        pool.acquire()
        assert pool._select_input_backend() == "idb"


# ===========================================================================
# TestSimulatorPoolLicenseEnforcement — validator caps max_concurrent
# ===========================================================================


@needs_pool
class TestSimulatorPoolLicenseEnforcement:
    """license_validator caps max_concurrent even if constructor says higher."""

    def test_license_caps_max_concurrent(self):
        """If the validator reports max_concurrent_sims=2 but pool was constructed
        with max_concurrent=4, pool.available() must be 2 (not 4)."""
        mock_validator = MagicMock()
        mock_validator.max_concurrent_sims.return_value = 2
        mock_validator.is_valid.return_value = True

        pool = _make_pool(max_concurrent=4, license_validator=mock_validator)
        assert pool.available() <= 2, (
            f"Pool should be capped at 2 by license validator, but available() returned {pool.available()}"
        )


# ===========================================================================
# TestSimulatorPoolSimctlCalls — subprocess interactions
# ===========================================================================


@needs_pool
class TestSimulatorPoolSimctlCalls:
    """_create_simulator and _destroy_simulator invoke the correct xcrun commands."""

    def test_create_simulator_calls_simctl_create_and_boot(self):
        """_create_simulator calls 'xcrun simctl create' followed by
        'xcrun simctl boot' on the resulting UDID."""
        pool = SimulatorPool(max_concurrent=1)
        fake_udid = "FAKE-UDID-1234-5678"

        create_result = MagicMock()
        create_result.returncode = 0
        create_result.stdout = f"{fake_udid}\n"

        boot_result = MagicMock()
        boot_result.returncode = 0
        boot_result.stdout = ""

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [create_result, boot_result]
            pool._create_simulator("iPhone 15 Pro")

        assert mock_run.call_count >= 2, "Expected at least create + boot subprocess calls"
        all_cmd_strings = [" ".join(c.args[0]) if c.args else "" for c in mock_run.call_args_list]
        assert any("simctl" in s and "create" in s for s in all_cmd_strings), (
            f"simctl create not found in calls: {all_cmd_strings}"
        )
        assert any("simctl" in s and "boot" in s for s in all_cmd_strings), (
            f"simctl boot not found in calls: {all_cmd_strings}"
        )

    def test_destroy_simulator_calls_simctl_delete(self):
        """_destroy_simulator calls 'xcrun simctl delete <udid>'."""
        pool = SimulatorPool(max_concurrent=1)
        target_udid = "DEAD-UDID-ABCD-9999"

        delete_result = MagicMock()
        delete_result.returncode = 0

        with patch("subprocess.run", return_value=delete_result) as mock_run:
            pool._destroy_simulator(target_udid)

        assert mock_run.called, "_destroy_simulator did not call subprocess.run"
        cmd = mock_run.call_args.args[0] if mock_run.call_args.args else []
        cmd_str = " ".join(cmd)
        assert "simctl" in cmd_str, f"'simctl' not in delete command: {cmd_str!r}"
        assert "delete" in cmd_str, f"'delete' not in delete command: {cmd_str!r}"
        assert target_udid in cmd_str, f"UDID not passed to delete command: {cmd_str!r}"


# ===========================================================================
# TestSimulatorPoolLeaseUniqueness — ID uniqueness and thread-safety
# ===========================================================================


@needs_pool
class TestSimulatorPoolLeaseUniqueness:
    """Lease IDs are unique across concurrent acquisitions."""

    def test_lease_ids_are_unique(self):
        """Two separate acquire() calls produce different lease_ids."""
        pool = _make_pool(max_concurrent=4)
        lease_a = pool.acquire()
        lease_b = pool.acquire()
        assert lease_a["lease_id"] != lease_b["lease_id"], "Two leases produced the same lease_id — IDs must be unique"

    def test_pool_is_thread_safe(self):
        """Concurrent acquire/release from multiple threads does not corrupt
        the available count or produce duplicate lease IDs."""
        pool = _make_pool(max_concurrent=8)
        collected_ids: list[str] = []
        lock = threading.Lock()
        errors: list[Exception] = []

        def worker():
            try:
                lease = pool.acquire()
                with lock:
                    collected_ids.append(lease["lease_id"])
                time.sleep(0.02)
                pool.release(lease["lease_id"])
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert not errors, f"Thread-safety errors: {errors}"
        # All IDs should be unique
        assert len(collected_ids) == len(set(collected_ids)), "Duplicate lease IDs produced under concurrent load"
        # Pool should be fully drained back to max_concurrent
        assert pool.available() == 8, f"Pool available count wrong after all threads finished: {pool.available()}"
