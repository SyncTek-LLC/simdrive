"""Regression tests for Bug 3 — tool_run_journey MCP error contract divergence.

_call_tool in server.py catches errors.SimdriveError but LicenseError lives in
license/errors.py as its own class hierarchy — NOT a subclass of SimdriveError.
The catch-all `except Exception` wraps it as code="internal" instead of
preserving the LicenseError envelope.

Fix required: make LicenseError inherit from SimdriveError, or add an explicit
`except LicenseError` clause before the catch-all.

TDD: written BEFORE the fix. All tests must FAIL on current code.
"""
from __future__ import annotations

import json
import pytest


class TestLicenseErrorInheritance:

    def test_license_error_inherits_from_simdrive_error(self) -> None:
        """LicenseError must be a subclass of SimdriveError.

        Currently FAILS because LicenseError is a standalone @dataclass
        class that inherits only from Exception — not from SimdriveError.

        After the fix: the existing `except errors.SimdriveError` clause in
        _call_tool() automatically handles LicenseError without a second
        catch branch.
        """
        from simdrive.license.errors import LicenseError
        from simdrive.errors import SimdriveError

        assert issubclass(LicenseError, SimdriveError), (
            "LicenseError must inherit from SimdriveError so the MCP wrapper's "
            "`except errors.SimdriveError` clause catches it and preserves the "
            "structured error envelope (code, message, details). "
            "Currently LicenseError only inherits from Exception, causing it to "
            "fall through to the catch-all `except Exception` which overwrites "
            "the error code with 'internal'."
        )


class TestMcpWrapperErrorEnvelope:

    def _simulate_call_tool_wrapper(self, tool_fn):
        """Mirror the exact catch logic from server.py:_call_tool.

        This lets us test the error-dispatch behaviour synchronously
        without spinning up the full asyncio MCP server.
        """
        from simdrive import errors
        try:
            result = tool_fn()
        except errors.SimdriveError as exc:
            return json.loads(json.dumps(exc.to_dict()))
        except Exception as exc:
            envelope = {
                "ok": False,
                "error": {
                    "code": "internal",
                    "message": str(exc),
                    "details": {"exception_type": type(exc).__name__},
                },
            }
            return envelope
        return result

    def test_license_error_caught_by_mcp_wrapper_returns_proper_envelope(self) -> None:
        """When a tool raises LicenseError, the MCP wrapper must return a dict
        with ok=False and error.code matching the LicenseError's code string,
        NOT error.code='internal'.

        Currently FAILS because LicenseError is not a subclass of SimdriveError,
        so the `except errors.SimdriveError` branch is skipped and the catch-all
        sets code='internal', discarding the structured LicenseError information.
        """
        from simdrive.license.errors import LicenseError

        def tool_that_raises_license_error():
            raise LicenseError(
                code="license_not_found",
                message="No license file found at '/tmp/license.json'. "
                        "Recovery: run `simdrive trial start --email <you@example.com>`.",
                details={"path": "/tmp/license.json"},
            )

        result = self._simulate_call_tool_wrapper(tool_that_raises_license_error)

        assert result.get("ok") is False, (
            f"MCP wrapper must return ok=False for LicenseError. Got: {result}"
        )
        error = result.get("error", {})
        assert error.get("code") == "license_not_found", (
            f"MCP wrapper must preserve LicenseError.code='license_not_found', "
            f"but got code={error.get('code')!r}. "
            "Fix: make LicenseError inherit from SimdriveError or add an explicit "
            "`except LicenseError` clause in _call_tool."
        )

    def test_license_error_to_dict_is_superset_of_simdrive_error_schema(self) -> None:
        """LicenseError.to_dict() must remain compatible with SimdriveError consumers.

        [internal-tracker].5: LicenseError adds UX-affordance fields
        (``error: "license_required"``, ``pricing_url``, command hints) so
        agent hosts can surface a copy-pasteable upsell. Existing fields
        (``code``, ``message``, ``details``) are preserved — the envelope is a
        SUPERSET of the SimdriveError envelope, not an exact match.
        """
        from simdrive.license.errors import LicenseError
        from simdrive.errors import SimdriveError

        lic_err = LicenseError(
            code="license_expired",
            message="License expired. Recovery: renew at https://simdrive.dev/pricing.",
            details={"expires_at": 1000000},
        )
        sim_err = SimdriveError(
            code="no_session",
            message="No session. Recovery: call start_session.",
            details={"session_id": "fake"},
        )

        lic_dict = lic_err.to_dict()
        sim_dict = sim_err.to_dict()

        # Outer envelope still matches (ok + error).
        assert set(lic_dict.keys()) == set(sim_dict.keys()), (
            f"LicenseError outer keys {set(lic_dict.keys())} != "
            f"SimdriveError outer keys {set(sim_dict.keys())}"
        )

        # LicenseError MUST preserve every SimdriveError inner key.
        missing = set(sim_dict["error"].keys()) - set(lic_dict["error"].keys())
        assert not missing, (
            f"LicenseError envelope dropped SimdriveError keys: {missing}"
        )

        # LicenseError adds the W1.5 UX-affordance fields.
        expected_extras = {"error", "pricing_url", "trial_command_hint", "auth_command_hint"}
        actual_extras = set(lic_dict["error"].keys()) - set(sim_dict["error"].keys())
        assert expected_extras <= actual_extras, (
            f"LicenseError envelope missing W1.5 UX fields: "
            f"{expected_extras - actual_extras}"
        )
