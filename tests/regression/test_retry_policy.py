"""Regression tests for RetryPolicy and circuit breaker.

Covers:
  - Connection errors are retried exactly 2 times (max_retries=2) before propagating
  - A FAST route has a 2 s timeout, not 10 s (via timeout_for)
  - Circuit breaker trips after 3 consecutive ConnectionError failures
  - Circuit breaker resets after a successful call

INIT-2026-525 — SpecterQA iOS retry/timeout policy.
"""

from __future__ import annotations

import pytest

from specterqa.ios.backends.retry_policy import (
    RetryPolicy,
    SessionCrashedError,
    _StatefulRetryPolicy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_policy(**kwargs) -> _StatefulRetryPolicy:
    """Return a stateful policy with fast backoff for test speed."""
    base = RetryPolicy(base_backoff_s=0.0, **kwargs)  # zero backoff so tests are instant
    return base.stateful()


def _raise_connection_error(*args, **kwargs):
    raise ConnectionError("runner refused")


def _raise_timeout_error(*args, **kwargs):
    raise TimeoutError("runner timed out")


# ---------------------------------------------------------------------------
# RetryPolicy.timeout_for
# ---------------------------------------------------------------------------

class TestTimeoutFor:
    def test_fast_route_is_2s(self):
        policy = RetryPolicy()
        assert policy.timeout_for(RetryPolicy.Route.FAST) == 2.0

    def test_action_route_is_10s(self):
        policy = RetryPolicy()
        assert policy.timeout_for(RetryPolicy.Route.ACTION) == 10.0

    def test_idle_route_is_30s(self):
        policy = RetryPolicy()
        assert policy.timeout_for(RetryPolicy.Route.IDLE) == 30.0

    def test_fast_timeout_differs_from_action(self):
        """FAST (2 s) must not equal ACTION (10 s) — the original regression."""
        policy = RetryPolicy()
        assert policy.timeout_for(RetryPolicy.Route.FAST) != policy.timeout_for(RetryPolicy.Route.ACTION)


# ---------------------------------------------------------------------------
# Retry count — ConnectionError is retried exactly max_retries times
# ---------------------------------------------------------------------------

class TestRetryCount:
    def test_connection_error_retried_exactly_twice_then_raises(self):
        """max_retries=2 means 3 total attempts (1 initial + 2 retries) then raise.

        With circuit_breaker_threshold=4 (above max_retries+1=3), the circuit breaker
        does not trip on the third attempt, so ConnectionError propagates directly.
        """
        policy = _make_policy(max_retries=2, circuit_breaker_threshold=4)
        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("refuse")

        with pytest.raises(ConnectionError):
            policy.call(RetryPolicy.Route.ACTION, flaky)

        assert call_count == 3, (
            f"Expected 3 total attempts (1 + 2 retries), got {call_count}"
        )

    def test_timeout_error_retried_same_as_connection_error(self):
        """Timeout errors are retried exactly like ConnectionError."""
        policy = _make_policy(max_retries=2, circuit_breaker_threshold=4)
        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            raise TimeoutError("timed out")

        with pytest.raises(TimeoutError):
            policy.call(RetryPolicy.Route.ACTION, flaky)

        assert call_count == 3

    def test_non_retryable_error_not_retried(self):
        """ValueError must propagate immediately — no retry."""
        policy = _make_policy(max_retries=2)
        call_count = 0

        def boom():
            nonlocal call_count
            call_count += 1
            raise ValueError("bad input")

        with pytest.raises(ValueError):
            policy.call(RetryPolicy.Route.ACTION, boom)

        assert call_count == 1, "Non-retryable error should not be retried"

    def test_success_on_second_attempt(self):
        """If the call succeeds on attempt 2, no exception is raised."""
        policy = _make_policy(max_retries=2)
        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("not ready")
            return "ok"

        result = policy.call(RetryPolicy.Route.ACTION, flaky)
        assert result == "ok"
        assert call_count == 2

    def test_max_retries_zero_means_no_retry(self):
        policy = _make_policy(max_retries=0)
        call_count = 0

        def boom():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("refuse")

        with pytest.raises(ConnectionError):
            policy.call(RetryPolicy.Route.ACTION, boom)

        assert call_count == 1


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def test_breaker_trips_after_threshold_consecutive_failures(self):
        """After circuit_breaker_threshold=3 consecutive ConnectionErrors → SessionCrashedError."""
        policy = _make_policy(max_retries=0, circuit_breaker_threshold=3)

        # 3 consecutive failures
        for _ in range(3):
            try:
                policy.call(RetryPolicy.Route.ACTION, _raise_connection_error)
            except (ConnectionError, SessionCrashedError):
                pass

        assert policy.is_open(), "Circuit breaker should be open after 3 consecutive failures"

        # Next call raises SessionCrashedError immediately (breaker is open)
        with pytest.raises(SessionCrashedError):
            policy.call(RetryPolicy.Route.ACTION, lambda: "ok")

    def test_breaker_not_tripped_below_threshold(self):
        """2 consecutive failures (threshold=3) should not open the breaker."""
        policy = _make_policy(max_retries=0, circuit_breaker_threshold=3)

        for _ in range(2):
            try:
                policy.call(RetryPolicy.Route.ACTION, _raise_connection_error)
            except ConnectionError:
                pass

        assert not policy.is_open()

    def test_success_resets_consecutive_counter(self):
        """2 failures followed by success resets the streak; 2 more failures don't trip."""
        policy = _make_policy(max_retries=0, circuit_breaker_threshold=3)

        for _ in range(2):
            try:
                policy.call(RetryPolicy.Route.ACTION, _raise_connection_error)
            except ConnectionError:
                pass

        # Successful call resets the counter
        result = policy.call(RetryPolicy.Route.ACTION, lambda: "alive")
        assert result == "alive"
        assert not policy.is_open()

        # 2 more failures should not trip (counter reset)
        for _ in range(2):
            try:
                policy.call(RetryPolicy.Route.ACTION, _raise_connection_error)
            except ConnectionError:
                pass

        assert not policy.is_open()

    def test_manual_reset_closes_open_breaker(self):
        policy = _make_policy(max_retries=0, circuit_breaker_threshold=2)

        for _ in range(2):
            try:
                policy.call(RetryPolicy.Route.ACTION, _raise_connection_error)
            except (ConnectionError, SessionCrashedError):
                pass

        assert policy.is_open()

        policy.reset()
        assert not policy.is_open()

        # Should succeed after reset
        result = policy.call(RetryPolicy.Route.ACTION, lambda: "recovered")
        assert result == "recovered"

    def test_breaker_raises_session_crashed_when_open(self):
        policy = _make_policy(max_retries=0, circuit_breaker_threshold=1)

        try:
            policy.call(RetryPolicy.Route.ACTION, _raise_connection_error)
        except (ConnectionError, SessionCrashedError):
            pass

        with pytest.raises(SessionCrashedError, match="Circuit breaker"):
            policy.call(RetryPolicy.Route.ACTION, lambda: "ok")


# ---------------------------------------------------------------------------
# RetryPolicy.should_retry
# ---------------------------------------------------------------------------

class TestShouldRetry:
    def test_connection_error_retryable(self):
        p = RetryPolicy(max_retries=2)
        assert p.should_retry(ConnectionError("x"), attempt=0)

    def test_timeout_error_retryable(self):
        p = RetryPolicy(max_retries=2)
        assert p.should_retry(TimeoutError("x"), attempt=0)

    def test_value_error_not_retryable(self):
        p = RetryPolicy(max_retries=2)
        assert not p.should_retry(ValueError("x"), attempt=0)

    def test_exhausted_attempts_not_retried(self):
        p = RetryPolicy(max_retries=2)
        assert not p.should_retry(ConnectionError("x"), attempt=2)


# ---------------------------------------------------------------------------
# Stateful wrapper interface
# ---------------------------------------------------------------------------

class TestStatefulInterface:
    def test_stateful_returns_wrapper(self):
        policy = RetryPolicy()
        s = policy.stateful()
        assert isinstance(s, _StatefulRetryPolicy)

    def test_timeout_for_delegates_to_policy(self):
        s = RetryPolicy().stateful()
        assert s.timeout_for(RetryPolicy.Route.FAST) == 2.0

    def test_record_success_clears_failure_count(self):
        s = _make_policy(max_retries=0, circuit_breaker_threshold=2)
        s.record_failure(ConnectionError("x"))
        s.record_success()
        assert not s.is_open()

    def test_record_failure_increments_counter(self):
        s = _make_policy(max_retries=0, circuit_breaker_threshold=2)
        s.record_failure(ConnectionError("x"))
        s.record_failure(ConnectionError("y"))
        assert s.is_open()
