"""M14: SimulatorPool — Semaphore-based concurrent simulator lease management.

Manages a bounded pool of iOS simulators. Callers acquire a lease (blocking
when the pool is at capacity) and release it when done. Thread-safe.
"""

from __future__ import annotations

import subprocess
import threading
import uuid
from typing import Any, Dict, List, Optional


class SimulatorPool:
    """Manages a bounded pool of iOS simulator leases.

    Args:
        max_concurrent: Maximum number of simulators that may run simultaneously.
        license_validator: Optional validator whose ``max_concurrent_sims()`` return
            value caps ``max_concurrent`` if lower.
    """

    def __init__(
        self,
        max_concurrent: int = 4,
        license_validator: Any = None,
    ) -> None:
        # If a license validator is provided and it caps lower, honour that.
        if license_validator is not None:
            try:
                cap = license_validator.max_concurrent_sims()
                if cap < max_concurrent:
                    max_concurrent = cap
            except Exception:
                pass

        self._max_concurrent: int = max_concurrent
        self._semaphore: threading.Semaphore = threading.Semaphore(max_concurrent)
        self._lock: threading.Lock = threading.Lock()
        # lease_id → lease dict
        self._active: Dict[str, Dict[str, str]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(self, device_name: str = "iPhone 15 Pro") -> Dict[str, str]:
        """Acquire a simulator lease, blocking if the pool is at capacity.

        Returns:
            A dict with keys: ``device_id``, ``udid``, ``name``, ``lease_id``.
        """
        self._semaphore.acquire()

        sim = self._create_simulator(device_name)
        lease_id = str(uuid.uuid4())

        lease: Dict[str, str] = {
            "device_id": sim["device_id"],
            "udid": sim["udid"],
            "name": sim.get("name", device_name),
            "lease_id": lease_id,
        }

        with self._lock:
            self._active[lease_id] = lease

        return lease

    def release(self, lease_id: str) -> None:
        """Release the lease identified by *lease_id*, freeing one pool slot.

        Raises:
            ValueError: If *lease_id* is not a currently active lease.
        """
        with self._lock:
            if lease_id not in self._active:
                raise ValueError(
                    f"Unknown lease_id {lease_id!r}. "
                    "It may have already been released or never existed."
                )
            lease = self._active.pop(lease_id)

        self._destroy_simulator(lease["udid"])
        self._semaphore.release()

    def available(self) -> int:
        """Return the number of free slots in the pool."""
        with self._lock:
            active_count = len(self._active)
        return self._max_concurrent - active_count

    def active_leases(self) -> List[Dict[str, str]]:
        """Return a snapshot list of all currently active lease dicts."""
        with self._lock:
            return list(self._active.values())

    def _select_input_backend(self) -> str:
        """Return the appropriate input backend name based on active lease count.

        Returns:
            ``"cgevents"`` when exactly one simulator is active (single-device
            path); ``"idb"`` when multiple simulators are active (multi-device
            path requiring idb's ``--udid`` targeting).
        """
        with self._lock:
            count = len(self._active)
        return "idb" if count > 1 else "cgevents"

    # ------------------------------------------------------------------
    # Internal simulator lifecycle (real xcrun calls; mocked in tests)
    # ------------------------------------------------------------------

    def _create_simulator(self, device_name: str) -> Dict[str, str]:
        """Create and boot a simulator using ``xcrun simctl``.

        Returns:
            Dict with ``device_id``, ``udid``, and ``name``.
        """
        create_result = subprocess.run(
            ["xcrun", "simctl", "create", device_name, "com.apple.CoreSimulator.SimDeviceType.iPhone-15-Pro"],
            capture_output=True,
            text=True,
            check=False,
        )
        udid = create_result.stdout.strip()

        subprocess.run(
            ["xcrun", "simctl", "boot", udid],
            capture_output=True,
            text=True,
            check=False,
        )

        return {
            "device_id": udid,
            "udid": udid,
            "name": device_name,
        }

    def _destroy_simulator(self, udid: str) -> None:
        """Shutdown and delete a simulator using ``xcrun simctl delete``."""
        subprocess.run(
            ["xcrun", "simctl", "delete", udid],
            capture_output=True,
            text=True,
            check=False,
        )
