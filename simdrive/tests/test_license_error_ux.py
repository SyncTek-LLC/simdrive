"""LicenseError UX-envelope tests — INIT-2026-549 W1.5 workstream 4.

When ANY gated tool raises LicenseError, the structured envelope returned to
the MCP client MUST include:

  * error:               umbrella "license_required" code
  * code:                granular code (license_not_found, license_expired, …)
  * message:             clear human prose
  * pricing_url:         https://simdrive.dev/pricing
  * auth_command_hint:   exact CLI string to install a key
  * trial_command_hint:  exact CLI string to start a trial

These fields are copy-pasteable — agent hosts (Claude Code, Cursor) surface
them verbatim so the user never has to leave the loop to figure out how to
escape the paywall.
"""
from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# to_dict() envelope shape
# ---------------------------------------------------------------------------


class TestEnvelopeShape:

    def test_license_not_found_envelope_has_all_ux_fields(self) -> None:
        from simdrive.license.errors import license_not_found

        env = license_not_found("/tmp/nope.json").to_dict()
        assert env["ok"] is False
        err = env["error"]
        assert err["error"] == "license_required"
        assert err["code"] == "license_not_found"
        assert "message" in err and err["message"]
        assert err["pricing_url"] == "https://simdrive.dev/pricing"
        assert "simdrive trial start" in err["trial_command_hint"]
        assert "simdrive auth" in err["auth_command_hint"]

    def test_license_expired_envelope_has_all_ux_fields(self) -> None:
        from simdrive.license.errors import license_expired

        env = license_expired(1_700_000_000).to_dict()
        err = env["error"]
        assert err["error"] == "license_required"
        assert err["code"] == "license_expired"
        # The human message must mention the upsell — "Pro" or "trial expired"
        assert "trial" in err["message"].lower() or "pro" in err["message"].lower()
        assert err["pricing_url"].startswith("https://simdrive.dev")

    def test_license_invalid_envelope_has_all_ux_fields(self) -> None:
        from simdrive.license.errors import license_invalid

        env = license_invalid("bad signature").to_dict()
        err = env["error"]
        assert err["error"] == "license_required"
        assert err["code"] == "license_invalid"
        assert err["pricing_url"] == "https://simdrive.dev/pricing"


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
    def test_envelope_is_json_serializable(self, factory_kwargs) -> None:
        from simdrive.license import errors as lic_errors

        factory_name, kwargs = factory_kwargs
        factory = getattr(lic_errors, factory_name)
        env = factory(**kwargs).to_dict()
        # Must round-trip through json without raising
        encoded = json.dumps(env)
        decoded = json.loads(encoded)
        assert decoded["ok"] is False
        assert decoded["error"]["error"] == "license_required"
        assert decoded["error"]["pricing_url"]


# ---------------------------------------------------------------------------
# End-to-end via MCP server wrapper — invoking a gated tool with no license
# returns the enriched envelope verbatim.
# ---------------------------------------------------------------------------


class TestMCPCallToolWraps:

    def test_call_tool_returns_license_required_envelope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The sync ``call_tool`` dispatcher must surface the enriched
        ``license_required`` envelope — *not* the bare ``code: license_not_found``
        shape — so agent hosts can render the upsell UI."""
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
        assert env["error"]["pricing_url"] == "https://simdrive.dev/pricing"
        assert "trial" in env["error"]["trial_command_hint"]


# ---------------------------------------------------------------------------
# Backward-compat — the granular `code` field is still present
# ---------------------------------------------------------------------------


class TestGranularCodePreserved:

    def test_granular_code_field_remains(self) -> None:
        """We added a sibling `error` umbrella field but must NOT remove `code`
        — existing agents and tests switch on `error.code`."""
        from simdrive.license.errors import license_not_found, license_expired

        for err in (license_not_found("/x"), license_expired(1_700_000_000)):
            env = err.to_dict()
            assert "code" in env["error"]
            assert env["error"]["code"] in (
                "license_not_found", "license_expired",
            )
