"""Tests for :mod:`simdrive._wait`.

Covers the sync :func:`wait_until` and async :func:`await_until` helpers.
Timing is mocked at :func:`time.monotonic` / :func:`time.sleep` (sync) and at
the event-loop clock (async) so the tests are deterministic and instant.
"""
from __future__ import annotations

import asyncio
from typing import Callable

import pytest

from simdrive._wait import await_until, wait_until
from simdrive.errors import WaitTimeoutError


# ── Helpers ──────────────────────────────────────────────────────────────────


class _FakeClock:
    """Monotonic clock + sleep tracker for deterministic sync tests."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_clock(monkeypatch) -> _FakeClock:
    clock = _FakeClock()
    monkeypatch.setattr("simdrive._wait.time.monotonic", clock.monotonic)
    monkeypatch.setattr("simdrive._wait.time.sleep", clock.sleep)
    return clock


def _predicate_after(n_calls: int, value=True) -> Callable[[], object]:
    """Predicate that returns falsy for the first ``n_calls`` and ``value`` after."""
    state = {"calls": 0}

    def _p():
        state["calls"] += 1
        if state["calls"] > n_calls:
            return value
        return None

    return _p


# ── Sync: wait_until ─────────────────────────────────────────────────────────


def test_returns_truthy_result_immediately(fake_clock: _FakeClock) -> None:
    """If predicate returns truthy on the first call, we return without sleeping."""
    result = wait_until(lambda: "ready", timeout=1.0)
    assert result == "ready"
    assert fake_clock.sleeps == []


def test_returns_first_truthy_value(fake_clock: _FakeClock) -> None:
    """The returned value is exactly the predicate's first truthy result."""
    sentinel = {"some": "object"}
    result = wait_until(lambda: sentinel, timeout=1.0)
    assert result is sentinel


def test_falsy_zero_is_treated_as_not_ready(fake_clock: _FakeClock) -> None:
    """Returning ``0`` (or empty list) should keep polling, not return."""
    state = {"calls": 0}

    def _p():
        state["calls"] += 1
        return 0 if state["calls"] < 3 else 42

    result = wait_until(_p, timeout=10.0)
    assert result == 42
    assert state["calls"] == 3


def test_backoff_grows_geometrically_until_max(fake_clock: _FakeClock) -> None:
    """Each iteration multiplies interval by backoff up to max_interval."""
    pred = _predicate_after(5)
    wait_until(
        pred,
        timeout=100.0,
        initial_interval=0.1,
        max_interval=1.0,
        backoff=2.0,
    )
    # Expect interval sequence: 0.1, 0.2, 0.4, 0.8, 1.0 (capped) — five sleeps
    # before the sixth call which succeeds.
    assert fake_clock.sleeps == pytest.approx([0.1, 0.2, 0.4, 0.8, 1.0])


def test_raises_wait_timeout_with_description(fake_clock: _FakeClock) -> None:
    """Timeout raises WaitTimeoutError and includes the description."""
    with pytest.raises(WaitTimeoutError) as excinfo:
        wait_until(
            lambda: None,
            timeout=0.5,
            initial_interval=0.1,
            max_interval=0.2,
            backoff=2.0,
            description="keyboard visible",
        )
    err = excinfo.value
    assert err.code == "wait_timeout"
    assert "keyboard visible" in err.message
    assert err.details["description"] == "keyboard visible"
    assert err.details["elapsed"] >= 0.5


def test_negative_timeout_rejected() -> None:
    """A negative timeout is a programmer error and is rejected eagerly."""
    with pytest.raises(ValueError):
        wait_until(lambda: True, timeout=-1.0)


def test_zero_timeout_calls_predicate_once(fake_clock: _FakeClock) -> None:
    """``timeout=0`` should still evaluate the predicate exactly once."""
    calls = {"n": 0}

    def _p():
        calls["n"] += 1
        return True

    wait_until(_p, timeout=0.0)
    assert calls["n"] == 1


def test_sleep_capped_by_remaining_deadline(fake_clock: _FakeClock) -> None:
    """The last sleep should not exceed the remaining time before the deadline."""
    with pytest.raises(WaitTimeoutError):
        wait_until(
            lambda: None,
            timeout=0.25,
            initial_interval=0.1,
            max_interval=1.0,
            backoff=10.0,
            description="never",
        )
    # No individual sleep should exceed the original timeout window.
    assert all(s <= 0.25 for s in fake_clock.sleeps)
    # Total slept ≈ timeout, never more than timeout + epsilon.
    assert sum(fake_clock.sleeps) <= 0.25 + 1e-9


# ── Async: await_until ───────────────────────────────────────────────────────


def test_await_until_returns_immediately() -> None:
    async def _go() -> object:
        return await await_until(lambda: "ready", timeout=1.0)

    assert asyncio.run(_go()) == "ready"


def test_await_until_supports_async_predicate() -> None:
    state = {"calls": 0}

    async def _p() -> object:
        state["calls"] += 1
        return None if state["calls"] < 3 else "done"

    async def _go() -> object:
        return await await_until(_p, timeout=1.0, initial_interval=0.001, max_interval=0.01)

    assert asyncio.run(_go()) == "done"
    assert state["calls"] == 3


def test_await_until_raises_wait_timeout() -> None:
    async def _go() -> object:
        return await await_until(
            lambda: None,
            timeout=0.05,
            initial_interval=0.01,
            max_interval=0.02,
            backoff=1.5,
            description="never visible",
        )

    with pytest.raises(WaitTimeoutError) as excinfo:
        asyncio.run(_go())
    assert excinfo.value.code == "wait_timeout"
    assert "never visible" in excinfo.value.message


def test_await_until_negative_timeout_rejected() -> None:
    async def _go() -> object:
        return await await_until(lambda: True, timeout=-0.1)

    with pytest.raises(ValueError):
        asyncio.run(_go())
