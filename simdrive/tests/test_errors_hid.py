"""Verify the HID/keyboard/focus/wait error subclasses match the SimdriveError contract.

The new classes (added in [internal-tracker]) are class-form companions to the
existing constructor functions in ``simdrive.errors``. Each must:

- Inherit from :class:`simdrive.errors.SimdriveError`.
- Expose a stable ``.code`` string (the agent contract).
- Carry a non-empty ``recovery`` clause in ``.message``.
- Round-trip through :meth:`SimdriveError.to_dict` cleanly.
"""
from __future__ import annotations

import pytest

from simdrive.errors import (
    FocusNotReadyError,
    HIDUnavailableError,
    KeyboardNotReadyError,
    SimdriveError,
    WaitTimeoutError,
)


@pytest.mark.parametrize(
    "factory,expected_code",
    [
        (lambda: WaitTimeoutError(description="keyboard visible", elapsed=1.23), "wait_timeout"),
        (lambda: HIDUnavailableError("binary not found"), "hid_unavailable"),
        (lambda: KeyboardNotReadyError("keyboard hidden"), "keyboard_not_ready"),
        (lambda: FocusNotReadyError("no focused element"), "focus_not_ready"),
    ],
    ids=["wait_timeout", "hid_unavailable", "keyboard_not_ready", "focus_not_ready"],
)
def test_subclass_has_expected_code(factory, expected_code: str) -> None:
    err = factory()
    assert isinstance(err, SimdriveError)
    assert err.code == expected_code


@pytest.mark.parametrize(
    "factory",
    [
        lambda: WaitTimeoutError(description="keyboard visible", elapsed=1.23),
        lambda: HIDUnavailableError("binary not found"),
        lambda: KeyboardNotReadyError("keyboard hidden"),
        lambda: FocusNotReadyError("no focused element"),
    ],
    ids=["wait_timeout", "hid_unavailable", "keyboard_not_ready", "focus_not_ready"],
)
def test_subclass_has_recovery(factory) -> None:
    err = factory()
    assert err.message  # non-empty
    assert "Recovery:" in err.message, f"missing Recovery: clause in {err.message!r}"


def test_wait_timeout_details_carry_description_and_elapsed() -> None:
    err = WaitTimeoutError(description="keyboard visible", elapsed=2.5)
    assert err.details["description"] == "keyboard visible"
    assert err.details["elapsed"] == pytest.approx(2.5)
    assert "keyboard visible" in err.message
    # Format-spec — two decimals of elapsed seconds.
    assert "2.50s" in err.message


def test_hid_unavailable_mentions_doctor() -> None:
    """Recovery should point users at `simdrive doctor` or the helper binary."""
    err = HIDUnavailableError("missing binary")
    assert "doctor" in err.message or "native" in err.message


def test_keyboard_not_ready_recovery_mentions_text_input() -> None:
    err = KeyboardNotReadyError("not visible")
    assert "text input" in err.message or "keyboard" in err.message


def test_focus_not_ready_recovery_mentions_tap() -> None:
    err = FocusNotReadyError()
    assert "tap" in err.message.lower()


def test_to_dict_contract() -> None:
    """SimdriveError.to_dict envelope should serialize the new subclasses."""
    err = WaitTimeoutError(description="x", elapsed=0.1)
    envelope = err.to_dict()
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "wait_timeout"
    assert envelope["error"]["details"]["description"] == "x"


def test_can_be_raised_and_caught_as_base() -> None:
    """The subclasses must be catchable via the base SimdriveError clause."""
    for ctor in (
        lambda: WaitTimeoutError(description="x", elapsed=0.0),
        lambda: HIDUnavailableError("r"),
        lambda: KeyboardNotReadyError(),
        lambda: FocusNotReadyError(),
    ):
        with pytest.raises(SimdriveError):
            raise ctor()
