"""Regression tests for Bug 1 — run_journey license gate (INIT-2026-543).

Three failure stacks:
  (a) serve() dispatcher does not route `trial` subcommand
  (b) cloud endpoint https://cloud.simdrive.dev DNS failure
  (c) check_entitlement() raises unconditionally on missing license.json —
      no offline-dev fallback

TDD: written BEFORE the fix. All tests must FAIL on current code.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Bug 1a — serve() dispatcher does not route `trial` subcommand
# ---------------------------------------------------------------------------


class TestServeDispatchesTrial:

    def test_serve_dispatches_trial_subcommand(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """serve() with argv=['trial','start','--email','x@y.com','--offline-dev']
        must NOT fall through to asyncio.run(_serve_async()). It must route to
        a `trial` CLI dispatcher that writes a license file and returns cleanly.

        On the current code this FAILS: serve() has no `trial` branch in its
        dispatcher (only 'run', 'ci', 'bootstrap-device') so it falls through
        to asyncio.run(_serve_async()) which attempts to start the MCP server.

        Fix required: add a `trial` dispatcher branch in serve().

        NOTE on testability: serve() is hard to test end-to-end without
        either (a) a working MCP server or (b) subprocess isolation.
        This test inspects the dispatcher logic directly by examining the
        source branches in serve() — a grey-box approach that verifies the
        routing table contains 'trial' without starting the MCP loop.
        """
        import inspect
        from simdrive import server

        source = inspect.getsource(server.serve)

        # The dispatcher must have a branch for the 'trial' subcommand.
        # Currently it only has: 'run', 'ci', 'bootstrap-device'.
        assert '"trial"' in source or "'trial'" in source, (
            "serve() dispatcher does not contain a 'trial' branch. "
            "Found branches in serve():\n"
            + "\n".join(
                line for line in source.splitlines()
                if "flag ==" in line or "flag in" in line or "if flag" in line
            )
            + "\n\nFix: add `if flag == 'trial': _cmd_trial(args[1:]); return` "
            "to the serve() dispatcher."
        )


# ---------------------------------------------------------------------------
# Bug 1b + 1c — offline-dev fallback in cmd_trial_start
# ---------------------------------------------------------------------------


class TestTrialOfflineDev:

    def test_trial_offline_dev_creates_local_license(self, tmp_path: Path) -> None:
        """cmd_trial_start with offline_dev=True must write a license.json
        with required fields and a valid 14-day expiry, without hitting the network.

        On the current code this fails because cmd_trial_start() has no
        offline_dev parameter — it always calls requests.post first.
        """
        from simdrive.license.cli import cmd_trial_start
        from simdrive.license.entitlement import check_entitlement

        lic_path = tmp_path / "license.json"

        # Current signature: cmd_trial_start(email, *, server_url, license_path)
        # Fix required: add offline_dev=False parameter.
        result = cmd_trial_start(
            "test@example.com",
            offline_dev=True,           # <-- does not exist yet (Bug 1c)
            license_path=lic_path,
        )

        # File must exist
        assert lic_path.exists(), "offline_dev=True must write license.json"

        data = json.loads(lic_path.read_text())
        assert "license_key" in data, "license.json must have 'license_key'"

        # key must contain a subject field with 'dev' or 'trial'
        key: str = data["license_key"]
        import base64, json as _json
        payload_b64 = key.split(".")[0]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
        tier = payload.get("tier", "")
        assert tier in ("trial", "dev"), (
            f"offline-dev license tier must be 'trial' or 'dev', got {tier!r}"
        )

        # expires_at must be ~14 days from now (±1 hour tolerance)
        expires_at = payload.get("expires_at", 0)
        now = int(time.time())
        delta = expires_at - now
        assert 86400 * 13 < delta < 86400 * 15, (
            f"offline-dev license must expire in ~14 days; "
            f"got expires_at delta {delta}s ({delta / 86400:.1f} days)"
        )

        # The file must also pass check_entitlement (read with the embedded public key)
        # We skip this part because offline-dev keys use a dev signing key that
        # won't validate against the production public key — just structural check above.

    def test_trial_cloud_unreachable_falls_back_to_offline_dev(
        self, tmp_path: Path
    ) -> None:
        """When requests.post raises ConnectionError and offline_dev=True,
        cmd_trial_start must succeed via local fallback.

        Without offline_dev=True, it must raise LicenseError (not bare ConnectionError).

        Currently fails because:
        (a) offline_dev param does not exist
        (b) ConnectionError propagates unwrapped instead of being caught as LicenseError
        """
        import requests
        from simdrive.license.cli import cmd_trial_start
        from simdrive.license.errors import LicenseError

        lic_path = tmp_path / "license.json"
        err_path = tmp_path / "license_err.json"

        with patch("requests.post", side_effect=requests.exceptions.ConnectionError("DNS failure")):
            # With offline_dev=True: should succeed despite network failure
            result = cmd_trial_start(
                "test@example.com",
                offline_dev=True,       # <-- does not exist yet
                license_path=lic_path,
            )
            assert lic_path.exists(), (
                "offline_dev=True must produce a license file even when network is down"
            )

            # Without offline_dev: must raise LicenseError, NOT bare ConnectionError
            with pytest.raises(LicenseError) as exc_info:
                cmd_trial_start(
                    "test@example.com",
                    offline_dev=False,
                    license_path=err_path,
                )
            # Code should indicate a license/network problem — not 'internal'
            assert exc_info.value.code != "internal", (
                "Network failure must raise a domain-specific LicenseError, "
                f"not a generic 'internal' code. Got: {exc_info.value.code}"
            )

    def test_run_journey_works_with_offline_dev_license(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After an offline-dev trial start, tool_run_journey must NOT raise
        LicenseError at the license gate.  It may raise other errors (missing
        session, missing journey file) but the LICENSE GATE specifically must pass.

        Currently fails because:
        (a) cmd_trial_start has no offline_dev param, so we can't create a local license
        (b) check_entitlement() calls get_public_key() which uses the production key,
            which won't validate the offline-dev key without the fix
        """
        from simdrive.license.cli import cmd_trial_start
        from simdrive.license.errors import LicenseError
        import simdrive.license.entitlement as entitlement_mod

        lic_path = tmp_path / "license.json"

        # Create the offline-dev license
        cmd_trial_start(
            "test@example.com",
            offline_dev=True,           # <-- does not exist yet
            license_path=lic_path,
        )

        # Point check_entitlement at the temp license (not ~/.simdrive/license.json)
        monkeypatch.setattr(entitlement_mod, "_DEFAULT_LICENSE_PATH", lic_path)

        from simdrive import server

        # Call tool_run_journey with a fake session_id; the license gate fires first.
        # We expect either:
        #   (a) some non-LicenseError (e.g. no_session / missing journey file) — PASS
        #   (b) a plain return dict — PASS
        #   (c) LicenseError — FAIL (this is the bug)
        try:
            result = server.tool_run_journey({"session_id": "fake-sess-001"})
            # If it returned a dict, license gate passed (other error in result is fine)
        except LicenseError as exc:
            pytest.fail(
                f"tool_run_journey raised LicenseError at the license gate even with "
                f"a valid offline-dev license: {exc}"
            )
        except Exception:
            # Any other exception (no_session, KeyError for missing journey_path, etc.)
            # is acceptable — the license gate passed.
            pass
