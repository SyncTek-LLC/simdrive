"""Tests for cloud privacy scrubbing (INIT-2026-549 W-F).

The scrubber must guarantee no sensitive value survives into logs or
error bodies. Sensitive == any field name containing one of the
SENSITIVE_KEY_SUBSTRINGS tokens, or any value matching a Bearer token
or license-key shape (long base64url.base64url).
"""
from __future__ import annotations



class TestScrubBody:
    def test_scrub_redacts_email_in_dict(self) -> None:
        from simdrive.cloud.privacy import scrub_body

        body = {"email": "alice@example.com", "ok": True}
        scrubbed = scrub_body(body)
        assert scrubbed["email"] == "[redacted]"
        assert "alice@example.com" not in repr(scrubbed)
        # Non-sensitive fields untouched.
        assert scrubbed["ok"] is True

    def test_scrub_redacts_license_key_in_dict(self) -> None:
        from simdrive.cloud.privacy import scrub_body

        body = {"license_key": "abc.def", "tier": "pro"}
        scrubbed = scrub_body(body)
        assert scrubbed["license_key"] == "[redacted]"
        assert scrubbed["tier"] == "pro"

    def test_scrub_redacts_token_field(self) -> None:
        from simdrive.cloud.privacy import scrub_body

        body = {"token": "supersecret"}
        scrubbed = scrub_body(body)
        assert scrubbed["token"] == "[redacted]"
        assert "supersecret" not in repr(scrubbed)

    def test_scrub_redacts_signature_field(self) -> None:
        from simdrive.cloud.privacy import scrub_body

        body = {"signature": "deadbeef"}
        scrubbed = scrub_body(body)
        assert scrubbed["signature"] == "[redacted]"

    def test_scrub_is_case_insensitive(self) -> None:
        from simdrive.cloud.privacy import scrub_body

        body = {"Authorization": "Bearer xyz", "License_Key": "abc"}
        scrubbed = scrub_body(body)
        assert scrubbed["Authorization"] == "[redacted]"
        assert scrubbed["License_Key"] == "[redacted]"

    def test_scrub_nested_dict(self) -> None:
        from simdrive.cloud.privacy import scrub_body

        body = {"user": {"email": "x@y", "name": "Alice"}, "ok": True}
        scrubbed = scrub_body(body)
        assert scrubbed["user"]["email"] == "[redacted]"
        assert scrubbed["user"]["name"] == "Alice"

    def test_scrub_list_of_dicts(self) -> None:
        from simdrive.cloud.privacy import scrub_body

        body = [{"email": "a@a"}, {"email": "b@b"}]
        scrubbed = scrub_body(body)
        assert scrubbed[0]["email"] == "[redacted]"
        assert scrubbed[1]["email"] == "[redacted]"

    def test_scrub_bearer_token_in_string(self) -> None:
        from simdrive.cloud.privacy import scrub_body

        body = "upstream said: Authorization: Bearer abc123.def456 was bad"
        scrubbed = scrub_body(body)
        assert "abc123.def456" not in scrubbed
        assert "Bearer" in scrubbed
        assert "[redacted]" in scrubbed

    def test_scrub_license_key_shape_in_string(self) -> None:
        from simdrive.cloud.privacy import scrub_body

        # Construct a long base64url.base64url string (license key shape).
        key = "A" * 60 + "." + "B" * 60
        body = f"got license: {key}"
        scrubbed = scrub_body(body)
        assert key not in scrubbed
        assert "[redacted]" in scrubbed

    def test_scrub_json_string_body(self) -> None:
        """A JSON-shaped string is parsed, scrubbed, and re-serialised."""
        import json as _json

        from simdrive.cloud.privacy import scrub_body

        body = '{"email": "x@y", "ok": true}'
        scrubbed = scrub_body(body)
        # Still JSON, but with the email redacted
        parsed = _json.loads(scrubbed)
        assert parsed["email"] == "[redacted]"
        assert parsed["ok"] is True

    def test_scrub_handles_bytes(self) -> None:
        from simdrive.cloud.privacy import scrub_body

        body = b'{"license_key": "abc.def"}'
        scrubbed = scrub_body(body)
        assert "abc.def" not in scrubbed
        assert "[redacted]" in scrubbed

    def test_scrub_handles_none(self) -> None:
        from simdrive.cloud.privacy import scrub_body

        assert scrub_body(None) is None

    def test_scrub_preserves_non_sensitive(self) -> None:
        from simdrive.cloud.privacy import scrub_body

        body = {"tier": "pro", "seats": 4, "status": "active"}
        scrubbed = scrub_body(body)
        assert scrubbed == body

    def test_scrub_idempotent(self) -> None:
        from simdrive.cloud.privacy import scrub_body

        body = {"email": "x@y", "tier": "pro"}
        once = scrub_body(body)
        twice = scrub_body(once)
        assert once == twice

    def test_no_sensitive_value_in_scrubbed_output(self) -> None:
        """Whole-output assertion: a typical realistic body has zero
        sensitive substrings after scrubbing."""
        from simdrive.cloud.privacy import scrub_body

        body = {
            "email": "alice@example.com",
            "license_key": "xxxxxxxx.yyyyyyyy",
            "signature": "deadbeefcafe",
            "tier": "pro",
            "nested": {
                "bearer": "tok_abc",
                "extra": "fine",
            },
            "log_line": "Authorization: Bearer secret-token-here ; ok",
        }
        scrubbed = scrub_body(body)
        as_text = repr(scrubbed)
        for forbidden in [
            "alice@example.com",
            "xxxxxxxx.yyyyyyyy",
            "deadbeefcafe",
            "tok_abc",
            "secret-token-here",
        ]:
            assert forbidden not in as_text, (
                f"sensitive value {forbidden!r} survived scrubbing: {scrubbed!r}"
            )


class TestAuthLogsAreScrubbed:
    """Integration: cloud/auth.py must use scrub_body before logging."""

    def test_auth_module_imports_scrub_body(self) -> None:
        import simdrive.cloud.auth as auth_mod

        assert hasattr(auth_mod, "scrub_body")
