"""Verify every error constructor's message contains 'Recovery:'.

This is the Component 9 §4.1 test: any new or existing error code whose
message does not end with a 'Recovery: ...' clause fails this test.
"""
from __future__ import annotations

import inspect

import pytest

import simdrive.errors as _errors
import simdrive.journey.errors as _journey_errors
import simdrive.license.errors as _license_errors


# ── Helper ────────────────────────────────────────────────────────────────────


def _collect_constructors(module) -> list[tuple[str, callable]]:
    """Return (name, fn) for every public function in a module that returns an error."""
    results = []
    for name, fn in inspect.getmembers(module, inspect.isfunction):
        if name.startswith("_"):
            continue
        results.append((name, fn))
    return results


# ── Core errors.py ────────────────────────────────────────────────────────────

_CORE_CONSTRUCTORS = [
    ("no_session", lambda: _errors.no_session("sess-001")),
    ("no_device", lambda: _errors.no_device({"name": "iPhone 16"})),
    ("sim_unhealthy", lambda: _errors.sim_unhealthy("udid-001", "shutdown loop")),
    ("hid_unavailable", lambda: _errors.hid_unavailable("binary not found")),
    ("target_not_found", lambda: _errors.target_not_found("mark", "login_button")),
    ("missing_target", lambda: _errors.missing_target()),
    ("invalid_argument", lambda: _errors.invalid_argument("x", -1, "must be positive")),
    ("already_recording", lambda: _errors.already_recording("sess-001", "my-recording")),
    ("not_recording", lambda: _errors.not_recording("sess-001")),
    ("recording_not_found", lambda: _errors.recording_not_found("my-rec", "/path/to/rec")),
    ("device_input_unavailable", lambda: _errors.device_input_unavailable("tap")),
    ("replay_drift_halt", lambda: _errors.replay_drift_halt(3, 0.72, 0.85)),
    ("cloud_auth_missing", lambda: _errors.cloud_auth_missing()),
    ("cloud_auth_invalid", lambda: _errors.cloud_auth_invalid("token expired")),
    ("cloud_storage_quota_exceeded", lambda: _errors.cloud_storage_quota_exceeded(0.9, 1.0)),
    ("cloud_recording_not_found", lambda: _errors.cloud_recording_not_found("rec-001")),
    ("cloud_rate_limited", lambda: _errors.cloud_rate_limited(30)),
]


@pytest.mark.parametrize("name,factory", _CORE_CONSTRUCTORS, ids=[c[0] for c in _CORE_CONSTRUCTORS])
def test_core_error_has_recovery(name: str, factory) -> None:
    """Every core error constructor must include 'Recovery:' in its message."""
    err = factory()
    # cloud errors return dicts; SimdriveError instances have .message
    if isinstance(err, dict):
        message = err.get("error", {}).get("message", "")
    else:
        message = err.message
    assert "Recovery:" in message, (
        f"error {name!r} is missing 'Recovery:' in its message.\n"
        f"Current message: {message!r}"
    )


# ── Journey errors ────────────────────────────────────────────────────────────

_JOURNEY_CONSTRUCTORS = [
    ("journey_schema_invalid", lambda: _journey_errors.journey_schema_invalid("/p/j.yaml", "bad field")),
    ("journey_persona_not_found", lambda: _journey_errors.journey_persona_not_found("my-user", "/p/")),
    ("journey_schema_version_unsupported", lambda: _journey_errors.journey_schema_version_unsupported(99)),
    ("journey_device_selector_missing", lambda: _journey_errors.journey_device_selector_missing("login")),
    ("persona_schema_invalid", lambda: _journey_errors.persona_schema_invalid("/p/p.yaml", "bad field")),
    ("persona_schema_version_unsupported", lambda: _journey_errors.persona_schema_version_unsupported(99)),
    ("journey_budget_exceeded", lambda: _journey_errors.journey_budget_exceeded("login", 30, 180.0, 40)),
    ("claude_call_failed", lambda: _journey_errors.claude_call_failed("timeout", 2)),
    ("claude_cost_cap_hit", lambda: _journey_errors.claude_cost_cap_hit(5.01, 5.0)),
    ("act_tool_failed", lambda: _journey_errors.act_tool_failed("tap", "hid_unavailable", "hid missing")),
    ("success_criterion_unevaluable", lambda: _journey_errors.success_criterion_unevaluable("perf_under", "no snapshot")),
    ("ci_no_journeys_matched", lambda: _journey_errors.ci_no_journeys_matched("/journeys/", ["smoke"])),
    ("ci_invalid_journey", lambda: _journey_errors.ci_invalid_journey("/j/bad.yaml", "schema error")),
]


@pytest.mark.parametrize(
    "name,factory", _JOURNEY_CONSTRUCTORS, ids=[c[0] for c in _JOURNEY_CONSTRUCTORS]
)
def test_journey_error_has_recovery(name: str, factory) -> None:
    err = factory()
    assert "Recovery:" in err.message, (
        f"journey error {name!r} is missing 'Recovery:'.\nCurrent: {err.message!r}"
    )


# ── License errors ────────────────────────────────────────────────────────────

_LICENSE_CONSTRUCTORS = [
    ("license_invalid", lambda: _license_errors.license_invalid("bad signature")),
    ("license_expired", lambda: _license_errors.license_expired(1000000)),
    ("license_offline_grace_exhausted", lambda: _license_errors.license_offline_grace_exhausted(1000000)),
    ("license_tier_insufficient", lambda: _license_errors.license_tier_insufficient("pro", "solo")),
    ("trial_already_used", lambda: _license_errors.trial_already_used("test@example.com")),
    ("license_not_found", lambda: _license_errors.license_not_found("/home/.simdrive/license.json")),
    ("trial_rate_limited", lambda: _license_errors.trial_rate_limited("1.2.3.4")),
]


@pytest.mark.parametrize(
    "name,factory", _LICENSE_CONSTRUCTORS, ids=[c[0] for c in _LICENSE_CONSTRUCTORS]
)
def test_license_error_has_recovery(name: str, factory) -> None:
    err = factory()
    assert "Recovery:" in err.message, (
        f"license error {name!r} is missing 'Recovery:'.\nCurrent: {err.message!r}"
    )
