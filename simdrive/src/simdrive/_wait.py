"""Condition-polling helpers.

Replaces the scattered ``time.sleep(0.6)`` magic-number pattern with explicit
predicates that describe *what* we are waiting for. A timeout raises
:class:`simdrive.errors.WaitTimeoutError` with the caller-supplied description
so the resulting envelope is diagnosable end-to-end.

Public API:

- :func:`wait_until` — synchronous polling with exponential backoff.
- :func:`await_until` — async variant using :func:`asyncio.sleep`.

Both return the predicate's first truthy result (typed via ``TypeVar``).
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, TypeVar

from .errors import WaitTimeoutError

T = TypeVar("T")


def _next_interval(interval: float, backoff: float, max_interval: float) -> float:
    return min(interval * backoff, max_interval)


def wait_until(
    predicate: Callable[[], T | None],
    *,
    timeout: float,
    initial_interval: float = 0.05,
    max_interval: float = 0.5,
    backoff: float = 1.5,
    description: str = "condition",
) -> T:
    """Poll ``predicate`` until it returns a truthy value, with exponential backoff.

    Returns the predicate's truthy result. Raises
    :class:`simdrive.errors.WaitTimeoutError` if ``timeout`` elapses first.
    Backoff multiplies the interval by ``backoff`` each attempt, capped at
    ``max_interval``. ``description`` is included in the timeout error message.
    """
    if timeout < 0:
        raise ValueError(f"timeout must be >= 0, got {timeout!r}")
    deadline = time.monotonic() + timeout
    interval = initial_interval
    while True:
        result = predicate()
        if result:
            return result
        now = time.monotonic()
        if now >= deadline:
            elapsed = timeout - (deadline - now) + (now - deadline)
            raise WaitTimeoutError(description=description, elapsed=max(elapsed, timeout))
        sleep_for = min(interval, deadline - now)
        if sleep_for > 0:
            time.sleep(sleep_for)
        interval = _next_interval(interval, backoff, max_interval)


async def await_until(
    predicate: Callable[[], Awaitable[T | None] | T | None],
    *,
    timeout: float,
    initial_interval: float = 0.05,
    max_interval: float = 0.5,
    backoff: float = 1.5,
    description: str = "condition",
) -> T:
    """Async variant of :func:`wait_until`.

    ``predicate`` may be a plain callable or an async callable; awaitables are
    awaited before truthiness is checked.
    """
    if timeout < 0:
        raise ValueError(f"timeout must be >= 0, got {timeout!r}")
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    interval = initial_interval
    while True:
        result = predicate()
        if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
            result = await result  # type: ignore[assignment]
        if result:
            return result  # type: ignore[return-value]
        now = loop.time()
        if now >= deadline:
            raise WaitTimeoutError(description=description, elapsed=timeout)
        sleep_for = min(interval, deadline - now)
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)
        interval = _next_interval(interval, backoff, max_interval)
