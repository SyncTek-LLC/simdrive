"""RetryPolicy — consistent timeout and retry semantics for iOS backends.

Defines three route classes that map to real-world operation latencies:

* **FAST**   — health probes, app-state checks        → 2 s timeout
* **ACTION** — tap, swipe, type, screenshot            → 10 s timeout
* **IDLE**   — wait_idle, wait_for_element             → 30 s timeout

Usage (backend internals)::

    _POLICY = RetryPolicy()

    def health(self):
        return _POLICY.call(RetryPolicy.Route.FAST, self._get, "/health")

    def tap(self, x, y):
        return _POLICY.call(RetryPolicy.Route.ACTION, self._post, "/tap", {"x": x, "y": y})

Circuit breaker::

    _POLICY.record_success()        # on any successful call
    _POLICY.record_failure(exc)     # on ConnectionError / TimeoutError
    # After 3 consecutive failures, _POLICY.is_open() == True
    # _POLICY.call(...) raises SessionCrashedError immediately

INIT-2026-525 — SpecterQA iOS retry/timeout policy.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TypeVar

logger = logging.getLogger("specterqa.ios.backends.retry_policy")

_T = TypeVar("_T")


class SessionCrashedError(RuntimeError):
    """Raised when the circuit breaker has tripped after N consecutive failures."""


@dataclass(frozen=True)
class RetryPolicy:
    """Immutable retry/timeout configuration with a per-instance circuit breaker state.

    Because ``frozen=True`` prevents mutation, the circuit-breaker state is
    stored in a separate mutable ``_State`` object held in a non-frozen wrapper.
    Use :meth:`stateful` to create a stateful wrapper instead of bare instances
    when you need the circuit-breaker behaviour.
    """

    class Route(Enum):
        FAST = "fast"      # health, app_state → 2 s
        ACTION = "action"  # tap, swipe, type, screenshot → 10 s
        IDLE = "idle"      # wait_idle, wait_for_element → 30 s

    max_retries: int = 2
    base_backoff_s: float = 0.3
    circuit_breaker_threshold: int = 3  # consecutive failures before trip

    def timeout_for(self, route: "RetryPolicy.Route") -> float:
        """Return the timeout in seconds for *route*."""
        return {
            RetryPolicy.Route.FAST: 2.0,
            RetryPolicy.Route.ACTION: 10.0,
            RetryPolicy.Route.IDLE: 30.0,
        }[route]

    def should_retry(self, exc: Exception, attempt: int) -> bool:
        """Return True when the call should be retried.

        Args:
            exc:     The exception that was raised.
            attempt: Zero-based attempt index (0 = first attempt).

        Returns:
            ``True`` when ``attempt < max_retries`` and *exc* is retryable.
        """
        if attempt >= self.max_retries:
            return False
        return isinstance(exc, (ConnectionError, TimeoutError))

    def stateful(self) -> "_StatefulRetryPolicy":
        """Return a stateful wrapper that tracks circuit-breaker state."""
        return _StatefulRetryPolicy(policy=self)


class _CircuitBreakerState:
    """Mutable circuit-breaker state — not frozen so it can track counters."""

    def __init__(self, threshold: int) -> None:
        self._threshold = threshold
        self._consecutive_failures = 0
        self._open = False

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._open = False

    def record_failure(self, exc: Exception) -> None:
        if isinstance(exc, (ConnectionError, TimeoutError)):
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._threshold:
                self._open = True
                logger.error(
                    "Circuit breaker OPEN after %d consecutive failures",
                    self._consecutive_failures,
                )

    def is_open(self) -> bool:
        return self._open

    def reset(self) -> None:
        self._consecutive_failures = 0
        self._open = False


class _StatefulRetryPolicy:
    """RetryPolicy with integrated circuit-breaker state.

    This is the object backends should hold as an instance variable — it wraps
    the immutable ``RetryPolicy`` and carries the mutable breaker counters.

    Example::

        class XCTestBackend:
            _retry = RetryPolicy().stateful()

            def health(self):
                return self._retry.call(RetryPolicy.Route.FAST, self._get, "/health")
    """

    def __init__(self, policy: RetryPolicy) -> None:
        self._policy = policy
        self._breaker = _CircuitBreakerState(policy.circuit_breaker_threshold)

    # ------------------------------------------------------------------
    # Circuit-breaker interface (used by _require_session replacement)
    # ------------------------------------------------------------------

    def record_success(self) -> None:
        """Notify the breaker that the last call succeeded."""
        self._breaker.record_success()

    def record_failure(self, exc: Exception) -> None:
        """Notify the breaker that the last call failed with *exc*."""
        self._breaker.record_failure(exc)

    def is_open(self) -> bool:
        """Return True when the circuit is open (consecutive failures exceeded threshold)."""
        return self._breaker.is_open()

    def reset(self) -> None:
        """Manually reset the circuit breaker (e.g. after ios_stop_session)."""
        self._breaker.reset()

    # ------------------------------------------------------------------
    # Route helpers
    # ------------------------------------------------------------------

    def timeout_for(self, route: RetryPolicy.Route) -> float:
        return self._policy.timeout_for(route)

    # ------------------------------------------------------------------
    # call() — the primary interface
    # ------------------------------------------------------------------

    def call(
        self,
        route: RetryPolicy.Route,
        fn: Callable[..., _T],
        *args: Any,
        **kwargs: Any,
    ) -> _T:
        """Call *fn* with retry / circuit-breaker semantics.

        Args:
            route:   Operation class (FAST / ACTION / IDLE).
            fn:      Callable to invoke.
            *args:   Positional arguments forwarded to *fn*.
            **kwargs: Keyword arguments forwarded to *fn*.

        Returns:
            The return value of *fn*.

        Raises:
            SessionCrashedError: When the circuit breaker is open.
            Exception:           The last exception when all retries are exhausted.
        """
        if self._breaker.is_open():
            raise SessionCrashedError(
                "Circuit breaker is open — too many consecutive connection failures. "
                "Call ios_stop_session then ios_start_session to recover."
            )

        last_exc: Exception | None = None
        for attempt in range(self._policy.max_retries + 1):
            try:
                result = fn(*args, **kwargs)
                self._breaker.record_success()
                return result  # type: ignore[return-value]
            except (ConnectionError, TimeoutError) as exc:
                last_exc = exc
                self._breaker.record_failure(exc)
                if self._breaker.is_open():
                    raise SessionCrashedError(
                        "Circuit breaker OPEN — session unreachable."
                    ) from exc
                if self._policy.should_retry(exc, attempt):
                    wait = self._policy.base_backoff_s * (2 ** attempt)
                    logger.warning(
                        "RetryPolicy: %s error on attempt %d/%d, retrying in %.2fs: %s",
                        route.value,
                        attempt + 1,
                        self._policy.max_retries + 1,
                        wait,
                        exc,
                    )
                    time.sleep(wait)
                else:
                    raise
            except Exception as exc:
                # Non-retryable errors propagate immediately.
                raise

        # Exhausted retries — re-raise last retryable exception.
        raise last_exc  # type: ignore[misc]

    def __repr__(self) -> str:
        return (
            f"_StatefulRetryPolicy(max_retries={self._policy.max_retries}, "
            f"open={self._breaker.is_open()}, "
            f"consecutive_failures={self._breaker._consecutive_failures})"
        )
