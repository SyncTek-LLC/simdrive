"""Structured error model for simdrive.

Every error raised by an MCP tool can be caught as a `SimdriveError` and
inspected via its `.code` (machine-friendly) + `.message` (human-readable).
The MCP server serializes these to a JSON envelope so agents can switch
on the code without parsing prose.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SimdriveError(Exception):
    """Base class for every error simdrive surfaces.

    Subclass via `.code` rather than the Python class — code strings are the
    stable contract; class hierarchy is implementation-internal.
    """
    code: str
    message: str
    details: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": self.message, "details": self.details}}


# --------- Standard error codes (the agent contract) --------- #


def no_session(session_id: str) -> SimdriveError:
    return SimdriveError(
        code="no_session",
        message=(
            f"unknown session_id {session_id!r}. "
            "Recovery: call `start_session` to create a session, then retry with the returned session_id."
        ),
        details={"session_id": session_id},
    )


def no_device(query: dict) -> SimdriveError:
    return SimdriveError(
        code="no_device",
        message=(
            f"no booted simulator matched {query}. "
            "Recovery: run `devices` to list available simulators, then pass a matching `device` filter, "
            "or run `xcrun simctl boot <udid>` to boot one."
        ),
        details={"query": query},
    )


def device_launch_failed(udid: str, bundle_id: str, reason: str) -> SimdriveError:
    return SimdriveError(
        code="device_launch_failed",
        message=(
            f"failed to launch {bundle_id!r} on device {udid}: {reason}. "
            "Recovery: verify the device is paired and unlocked; run "
            "`xcrun devicectl list devices` to confirm visibility, and "
            "`xcrun devicectl device info apps --device <udid>` to confirm the app is installed."
        ),
        details={"udid": udid, "bundle_id": bundle_id, "reason": reason},
    )


def sim_unhealthy(udid: str, reason: str) -> SimdriveError:
    return SimdriveError(
        code="sim_unhealthy",
        message=(
            f"simulator {udid} is in a degraded state ({reason}). "
            "Recovery: quit Simulator.app and `xcrun simctl shutdown all && xcrun simctl boot {udid}`."
        ),
        details={"udid": udid, "reason": reason},
    )


def hid_unavailable(reason: str) -> SimdriveError:
    return SimdriveError(
        code="hid_unavailable",
        message=(
            f"native HID helper unavailable: {reason}. "
            "Recovery: reinstall simdrive (the bundled binary is required) or `cd simdrive/native && make`."
        ),
        details={"reason": reason},
    )


def target_not_found(form: str, query: Any, available: Optional[list] = None) -> SimdriveError:
    return SimdriveError(
        code="target_not_found",
        message=(
            f"no {form} match for {query!r} in last observe. "
            f"Available: {available[:30] if available else '(none)'}. "
            "Recovery: call `observe` to refresh the screen state, then retry with a visible element."
        ),
        details={"form": form, "query": query, "available": available},
    )


def missing_target() -> SimdriveError:
    return SimdriveError(
        code="missing_target",
        message=(
            "tap target required: provide {x, y}, {mark: <id>}, or {text: <query>}. "
            "Recovery: call `observe` to get current marks, then supply one of the above coordinate forms."
        ),
        details={},
    )


def invalid_argument(field: str, value: Any, why: str) -> SimdriveError:
    return SimdriveError(
        code="invalid_argument",
        message=(
            f"invalid {field}={value!r}: {why}. "
            "Recovery: check the tool's parameter schema and supply a valid value."
        ),
        details={"field": field, "value": value, "why": why},
    )


def already_recording(session_id: str, name: str) -> SimdriveError:
    return SimdriveError(
        code="already_recording",
        message=(
            f"session {session_id} already recording {name!r}. "
            "Recovery: call `stop_recording` to finalize the current recording before starting a new one."
        ),
        details={"session_id": session_id, "name": name},
    )


def not_recording(session_id: str) -> SimdriveError:
    return SimdriveError(
        code="not_recording",
        message=(
            f"session {session_id} is not recording. "
            "Recovery: call `start_recording` before attempting to stop or add steps to a recording."
        ),
        details={"session_id": session_id},
    )


def recording_not_found(name: str, path: str) -> SimdriveError:
    return SimdriveError(
        code="recording_not_found",
        message=(
            f"recording {name!r} not found at {path}. "
            "Recovery: run `list_replays` to see available recordings, then retry with a valid name."
        ),
        details={"name": name, "path": path},
    )


def device_input_unavailable(action: str) -> SimdriveError:
    return SimdriveError(
        code="device_input_unavailable",
        message=(
            f"'{action}' on a real device is not yet supported. simdrive v0.1.x "
            "drives observe + logs + app lifecycle on real devices, but synthetic "
            "touch/keyboard input requires WebDriverAgent. Coming in v0.2; track "
            "in docs/REAL_DEVICE_FEASIBILITY.md. "
            "Recovery: switch `target` to `simulator` for now, or run `simdrive bootstrap-device <udid>` "
            "once WDA bootstrap is available in v0.2."
        ),
        details={"action": action},
    )


def replay_drift_halt(step_id: int, similarity: float, threshold: float) -> SimdriveError:
    return SimdriveError(
        code="replay_drift_halt",
        message=(
            f"replay halted at step {step_id}: similarity {similarity:.3f} below threshold {threshold:.3f}. "
            "Recovery: re-record the journey from the current UI state, or lower `drift_threshold` "
            "if the UI change is cosmetic (e.g. `--drift-threshold 0.75`)."
        ),
        details={"step_id": step_id, "similarity": similarity, "threshold": threshold},
    )


# --------- Cycle 1 — Extended error codes (Journey + License + Cloud) -------- #
#
# Atlas integration decision: the 25 new codes from Cycle 1 live in their
# per-package modules as the source-of-truth (journey/errors.py,
# license/errors.py, cloud/errors.py). This avoids circular imports since
# those modules already import SimdriveError / LicenseError from here.
#
# Callers who want the extended codes import directly from the sub-packages:
#   from simdrive.journey.errors import journey_schema_invalid, ...
#   from simdrive.license.errors import LicenseError, license_expired, ...
#   from simdrive.cloud.errors import cloud_error
#
# Cloud codes (5) are inline below because cloud/errors.py does NOT import
# from this module, so no circular import risk.
#
# License codes (7) and journey codes (13) are in their own packages to avoid
# the circular-import problem (those modules import SimdriveError from here).
# They are NOT re-imported here at module level.
#
# New error-code inventory for discoverability (25 codes total):
#   Journey (13): journey_schema_invalid, journey_persona_not_found,
#     journey_schema_version_unsupported, journey_device_selector_missing,
#     persona_schema_invalid, persona_schema_version_unsupported,
#     journey_budget_exceeded, claude_call_failed, claude_cost_cap_hit,
#     act_tool_failed, success_criterion_unevaluable,
#     ci_no_journeys_matched, ci_invalid_journey
#   License (7): license_invalid, license_expired,
#     license_offline_grace_exhausted, license_tier_insufficient,
#     trial_already_used, license_not_found, trial_rate_limited
#   Cloud (5): cloud_auth_missing, cloud_auth_invalid,
#     cloud_storage_quota_exceeded, cloud_recording_not_found,
#     cloud_rate_limited


# ── Cloud error constructors (inline — no circular import) ───────────────────


def _cloud_error(code: str, message: str, details: dict | None = None) -> dict:
    """Internal helper — mirrors cloud/errors.py:cloud_error."""
    return {"ok": False, "error": {"code": code, "message": message, "details": details or {}}}


def cloud_auth_missing() -> dict:
    return _cloud_error(
        "cloud_auth_missing",
        "Authorization header is missing. "
        "Recovery: include 'Authorization: Bearer <token>' in your request.",
    )


def cloud_auth_invalid(reason: str) -> dict:
    return _cloud_error(
        "cloud_auth_invalid",
        f"Authorization token is invalid: {reason}. "
        "Recovery: re-authenticate via POST /auth/token or check your license key.",
        {"reason": reason},
    )


def cloud_storage_quota_exceeded(used_gb: float, limit_gb: float) -> dict:
    return _cloud_error(
        "cloud_storage_quota_exceeded",
        f"Storage quota exceeded ({used_gb:.1f} GB used of {limit_gb:.1f} GB limit). "
        "Recovery: delete old recordings via DELETE /recordings/<id>, or upgrade your plan.",
        {"used_gb": used_gb, "limit_gb": limit_gb},
    )


def cloud_recording_not_found(recording_id: str) -> dict:
    return _cloud_error(
        "cloud_recording_not_found",
        f"Recording {recording_id!r} not found or has been deleted. "
        "Recovery: list available recordings via GET /recordings.",
        {"recording_id": recording_id},
    )


def cloud_rate_limited(retry_after_seconds: int) -> dict:
    return _cloud_error(
        "cloud_rate_limited",
        f"Rate limit exceeded. Retry after {retry_after_seconds}s. "
        "Recovery: reduce request frequency or upgrade to a higher-tier plan.",
        {"retry_after_seconds": retry_after_seconds},
    )


# ── HID / keyboard / focus / wait subclasses ─────────────────────────────────
#
# These four subclasses pair with the polling helpers in ``simdrive._wait`` and
# the native HID injection path. They follow the existing ``code`` + ``message``
# convention (each message ends with a ``Recovery: ...`` clause) but are exposed
# as classes so callers can ``except WaitTimeoutError`` / ``except
# HIDUnavailableError`` instead of switching on ``.code`` strings.


class WaitTimeoutError(SimdriveError):
    """Raised when a polled condition does not become truthy before its deadline.

    Carries the original ``description`` and ``elapsed`` seconds so the MCP
    envelope identifies *what* we were waiting on (e.g. "keyboard visible").
    """

    def __init__(self, description: str, elapsed: float) -> None:
        super().__init__(
            code="wait_timeout",
            message=(
                f"timed out waiting for {description} after {elapsed:.2f}s. "
                "Recovery: increase the timeout, call `observe` to confirm the expected "
                "state is reachable, or check the simulator for an unexpected dialog."
            ),
            details={"description": description, "elapsed": elapsed},
        )


class HIDUnavailableError(SimdriveError):
    """Raised when the native HID helper cannot be invoked.

    Mirrors the existing :func:`hid_unavailable` constructor in class form so
    callers can write ``except HIDUnavailableError``.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(
            code="hid_unavailable",
            message=(
                f"native HID helper unavailable: {reason}. "
                "Recovery: run `simdrive doctor` to diagnose, reinstall simdrive "
                "(the bundled binary is required), or `cd simdrive/native && make`."
            ),
            details={"reason": reason},
        )


class KeyboardNotReadyError(SimdriveError):
    """Raised when type_text is attempted but the on-screen keyboard is not ready."""

    def __init__(self, reason: str = "keyboard not visible") -> None:
        super().__init__(
            code="keyboard_not_ready",
            message=(
                f"keyboard not ready for input: {reason}. "
                "Recovery: wait longer for the keyboard to animate in, or ensure the "
                "focused field is a text input (call `observe` to confirm focus)."
            ),
            details={"reason": reason},
        )


class FocusNotReadyError(SimdriveError):
    """Raised when an action requires focus on a specific element but focus is absent."""

    def __init__(self, reason: str = "no focused element") -> None:
        super().__init__(
            code="focus_not_ready",
            message=(
                f"focus not ready: {reason}. "
                "Recovery: check that a prior tap landed on the intended element; "
                "call `observe` to inspect current focus, then retry the tap."
            ),
            details={"reason": reason},
        )
