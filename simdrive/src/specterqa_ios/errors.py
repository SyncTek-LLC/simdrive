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
        message=f"unknown session_id {session_id!r}. Call session_start first.",
        details={"session_id": session_id},
    )


def no_device(query: dict) -> SimdriveError:
    return SimdriveError(
        code="no_device",
        message=f"no booted simulator matched {query}. Pass `device` to boot one.",
        details={"query": query},
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
            "Reinstall simdrive (the bundled binary is required) or `cd simdrive/native && make`."
        ),
        details={"reason": reason},
    )


def target_not_found(form: str, query: Any, available: Optional[list] = None) -> SimdriveError:
    return SimdriveError(
        code="target_not_found",
        message=(
            f"no {form} match for {query!r} in last observe. "
            f"Available: {available[:30] if available else '(none)'}"
        ),
        details={"form": form, "query": query, "available": available},
    )


def missing_target() -> SimdriveError:
    return SimdriveError(
        code="missing_target",
        message="tap target required: provide {x, y}, {mark: <id>}, or {text: <query>}",
        details={},
    )


def invalid_argument(field: str, value: Any, why: str) -> SimdriveError:
    return SimdriveError(
        code="invalid_argument",
        message=f"invalid {field}={value!r}: {why}",
        details={"field": field, "value": value, "why": why},
    )


def already_recording(session_id: str, name: str) -> SimdriveError:
    return SimdriveError(
        code="already_recording",
        message=f"session {session_id} already recording {name!r}; call record_stop first.",
        details={"session_id": session_id, "name": name},
    )


def not_recording(session_id: str) -> SimdriveError:
    return SimdriveError(
        code="not_recording",
        message=f"session {session_id} is not recording.",
        details={"session_id": session_id},
    )


def recording_not_found(name: str, path: str) -> SimdriveError:
    return SimdriveError(
        code="recording_not_found",
        message=f"recording {name!r} not found at {path}",
        details={"name": name, "path": path},
    )


def device_input_unavailable(action: str) -> SimdriveError:
    return SimdriveError(
        code="device_input_unavailable",
        message=(
            f"'{action}' on a real device is not yet supported. simdrive v0.1.x "
            "drives observe + logs + app lifecycle on real devices, but synthetic "
            "touch/keyboard input requires WebDriverAgent. Coming in v0.2; track "
            "in docs/REAL_DEVICE_FEASIBILITY.md."
        ),
        details={"action": action},
    )


def replay_drift_halt(step_id: int, similarity: float, threshold: float) -> SimdriveError:
    return SimdriveError(
        code="replay_drift_halt",
        message=f"replay halted at step {step_id}: similarity {similarity:.3f} below threshold {threshold:.3f}",
        details={"step_id": step_id, "similarity": similarity, "threshold": threshold},
    )
