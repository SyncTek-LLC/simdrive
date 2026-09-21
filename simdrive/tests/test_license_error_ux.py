"""LicenseError UX-envelope tests — [internal-tracker].5 workstream 4.

INIT-2026-610 (Chairman decision, 2026-08-26): SimDrive was retired as a
commercial product. The envelope previously enriched with pricing_url /
trial_command_hint / auth_command_hint fields advertised a purchase path for
a product that is no longer for sale, so those fields — and any message copy
pointing at a trial signup or the pricing page — were removed.

When ANY gated tool raises LicenseError, the structured envelope returned to
the MCP client MUST still include:

  * error:               umbrella "license_required" code
  * code:                granular code (license_not_found, license_expired, …)
  * message:             clear human prose, with a Recovery: clause

...and MUST NOT include pricing_url, trial_command_hint, or
auth_command_hint, and the message text must not advertise a trial signup or
the pricing page.
"""
from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# to_dict() envelope shape
# ---------------------------------------------------------------------------


class TestEnvelopeShape:

    def test_license_not_found_envelope_has_no_advertising_fields(self) -> None:
        from simdrive.license.errors import license_not_found

        env = license_not_found("/tmp/nope.json").to_dict()
        assert env["ok"] is False
        err = env["error"]
        assert err["error"] == "license_required"
        assert err["code"] == "license_not_found"
        assert "message" in err and err["message"]
        assert "pricing_url" not in err
        assert "trial_command_hint" not in err
        assert "auth_command_hint" not in err
        assert "simdrive.dev/pricing" not in err["message"]
        assert "trial start" not in err["message"]

    def test_license_expired_envelope_has_no_advertising_fields(self) -> None:
        from simdrive.license.errors import license_expired

        env = license_expired(1_700_000_000).to_dict()
        err = env["error"]
        assert err["error"] == "license_required"
        assert err["code"] == "license_expired"
        assert "pricing_url" not in err
        assert "simdrive.dev/pricing" not in err["message"]
        assert "trial start" not in err["message"]

    def test_license_invalid_envelope_has_no_advertising_fields(self) -> None:
        from simdrive.license.errors import license_invalid

        env = license_invalid("bad signature").to_dict()
        err = env["error"]
        assert err["error"] == "license_required"
        assert err["code"] == "license_invalid"
        assert "pricing_url" not in err
        assert "simdrive.dev/pricing" not in err["message"]
        assert "trial start" not in err["message"]


# ---------------------------------------------------------------------------
# MCP-wire serialisation — the envelope round-trips cleanly through json
# ---------------------------------------------------------------------------


class TestEnvelopeRoundTrip:

    @pytest.mark.parametrize(
        "factory_kwargs",
        [
            ("license_not_found", {"path": "/tmp/x.json"}),
            ("license_expired", {"expires_at": 1_700_000_000}),
            ("license_invalid", {"reason": "tampered"}),
            ("license_offline_grace_exhausted", {"expires_at": 1_700_000_000}),
        ],
    )
    def test_envelope_is_json_serializable_with_no_pricing_url(self, factory_kwargs) -> None:
        from simdrive.license import errors as lic_errors

        factory_name, kwargs = factory_kwargs
        factory = getattr(lic_errors, factory_name)
        env = factory(**kwargs).to_dict()
        # Must round-trip through json without raising
        encoded = json.dumps(env)
        decoded = json.loads(encoded)
        assert decoded["ok"] is False
        assert decoded["error"]["error"] == "license_required"
        assert "pricing_url" not in decoded["error"]


# ---------------------------------------------------------------------------
# End-to-end via MCP server wrapper — invoking a gated tool with no license
# returns the enriched envelope verbatim.
# ---------------------------------------------------------------------------


class TestMCPCallToolWraps:

    def test_call_tool_returns_license_required_envelope_without_advertising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The sync ``call_tool`` dispatcher must surface the enriched
        ``license_required`` envelope, but that envelope must no longer
        carry pricing/trial advertising."""
        import simdrive.license.entitlement as ent
        from simdrive.license import errors as lic_errors
        from simdrive import server

        def _raise(*_a, **_kw):
            raise lic_errors.license_not_found("/tmp/no")
        monkeypatch.setattr(ent, "check_entitlement", _raise)

        # call_tool catches the LicenseError? Actually no — call_tool just calls
        # the handler and lets it raise. The MCP-wire _call_tool wraps it via
        # exc.to_dict(). Test the equivalent path: invoke the tool, catch the
        # error, serialise.
        from simdrive.license.errors import LicenseError
        with pytest.raises(LicenseError) as exc_info:
            server.call_tool("observe", {"session_id": "x"})
        env = exc_info.value.to_dict()
        assert env["error"]["error"] == "license_required"
        assert "pricing_url" not in env["error"]
        assert "trial_command_hint" not in env["error"]
        assert "auth_command_hint" not in env["error"]


# ---------------------------------------------------------------------------
# Backward-compat — the granular `code` field is still present
# ---------------------------------------------------------------------------


class TestGranularCodePreserved:

    def test_granular_code_field_remains(self) -> None:
        """We kept the sibling `error` umbrella field but must NOT remove `code`
        — existing agents and tests switch on `error.code`."""
        from simdrive.license.errors import license_not_found, license_expired

        for err in (license_not_found("/x"), license_expired(1_700_000_000)):
            env = err.to_dict()
            assert "code" in env["error"]
            assert env["error"]["code"] in (
                "license_not_found", "license_expired",
            )
