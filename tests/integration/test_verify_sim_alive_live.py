"""Live regression test for `_verify_sim_alive`.

Regression target: confirm that a transient SpringBoard kill on a booted simulator
does NOT cause `_verify_sim_alive` to mis-classify the sim as dead within its 15s
poll budget. SpringBoard self-respawns within ~5–10s on iOS simulators; the sim
state in `simctl list devices` stays `Booted` throughout, so the function must
return (True, ...) regardless of SpringBoard's transient absence.

Skip logic: requires a booted iOS simulator. Marked `live` so it's only collected
when the live test surface is explicitly exercised.

Run with:
    pytest tests/integration/test_verify_sim_alive_live.py -v -m live
"""

from __future__ import annotations

import subprocess
import time

import pytest


def _booted_udid() -> str | None:
    try:
        result = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "booted", "-j"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        import json
        data = json.loads(result.stdout)
        for devices in data.get("devices", {}).values():
            for d in devices:
                if d.get("state") == "Booted":
                    return d.get("udid")
    except Exception:
        return None
    return None


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(_booted_udid() is None, reason="no booted iOS simulator"),
]


def test_verify_sim_alive_returns_true_after_springboard_kill():
    """Killing SpringBoard inside a booted sim must not cause _verify_sim_alive
    to return (False, ...) within its 15s poll budget — the sim itself stays
    Booted in simctl, and SpringBoard respawns transparently.
    """
    from specterqa.ios.mcp.server import _verify_sim_alive  # noqa: PLC0415

    udid = _booted_udid()
    assert udid is not None, "skip-guard misfired"

    # Kill SpringBoard inside the booted sim. Use a non-zero exit-tolerance:
    # if SpringBoard isn't running this is harmless, and the test still
    # validates the steady-state path.
    subprocess.run(
        ["xcrun", "simctl", "spawn", udid, "killall", "-9", "SpringBoard"],
        capture_output=True, text=True, timeout=10,
    )

    # Call _verify_sim_alive immediately after the kill — within the 15s budget
    # the sim's simctl state remains "Booted" so the function must not declare
    # the session dead. Cap the test runtime to budget + 2s safety margin.
    t0 = time.monotonic()
    alive, state = _verify_sim_alive(udid, poll_budget_s=15.0)
    elapsed = time.monotonic() - t0

    assert alive, (
        f"_verify_sim_alive returned (False, {state!r}) after SpringBoard kill — "
        "sim stayed Booted in simctl, function must not classify as dead."
    )
    assert elapsed < 17.0, f"_verify_sim_alive ran {elapsed:.1f}s — exceeds 15s budget + margin"
