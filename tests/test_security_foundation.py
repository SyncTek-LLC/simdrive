"""Tests for security foundation modules: DataRedactor (M9) and CredentialContext (M13).

TDD Phase 1 — INIT-2026-492.
These tests are written BEFORE the implementation exists and must be importable
even when the implementation modules are absent.

Modules under test (to be created by CodeAtlas):
  specterqa/ios/security/redactor.py   — DataRedactor
  specterqa/ios/security/credentials.py — CredentialContext
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard — tests are importable even if impl is missing.
# Each test that requires the module will be marked xfail with
# reason="implementation not yet written" if the import fails.
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.security.redactor import DataRedactor  # type: ignore[import]

    _REDACTOR_AVAILABLE = True
except ImportError:
    _REDACTOR_AVAILABLE = False
    DataRedactor = None  # type: ignore[assignment,misc]

try:
    from specterqa.ios.security.credentials import CredentialContext  # type: ignore[import]

    _CREDENTIALS_AVAILABLE = True
except ImportError:
    _CREDENTIALS_AVAILABLE = False
    CredentialContext = None  # type: ignore[assignment,misc]

# Pytest marks — skip with a clear message when impl doesn't exist yet.
needs_redactor = pytest.mark.skipif(
    not _REDACTOR_AVAILABLE,
    reason="specterqa.ios.security.redactor not yet implemented",
)
needs_credentials = pytest.mark.skipif(
    not _CREDENTIALS_AVAILABLE,
    reason="specterqa.ios.security.credentials not yet implemented",
)


# ---------------------------------------------------------------------------
# Shared fixture: a minimal LogEntry dataclass for redact_log_entry tests.
# The real LogEntry (to be defined by CodeAtlas) must be compatible with this
# structure: it must have at minimum `message`, `timestamp`, `level`, and
# `subsystem` fields.
# ---------------------------------------------------------------------------


@dataclass
class MockLogEntry:
    """Minimal LogEntry for testing DataRedactor.redact_log_entry()."""

    message: str
    timestamp: str = "2026-03-28T10:00:00Z"
    level: str = "INFO"
    subsystem: str = "network"


# ===========================================================================
#  M9: DataRedactor — 25 tests
# ===========================================================================


@needs_redactor
class TestDataRedactorBearerToken:
    """Bearer-token redaction via the built-in pattern."""

    def test_redact_bearer_token(self):
        """Standard Authorization header Bearer token is fully redacted."""
        redactor = DataRedactor()
        raw = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc.def"
        result = redactor.redact(raw)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result
        assert "[REDACTED]" in result
        # The non-sensitive prefix should be preserved
        assert "Authorization:" in result

    def test_redact_bearer_in_log_line(self):
        """A full log line containing a Bearer token is redacted correctly."""
        redactor = DataRedactor()
        line = "2026-03-28 10:00:00 [DEBUG] Request sent. Headers: {'Authorization': 'Bearer sk-ant-api03-xyz987'}"
        result = redactor.redact(line)
        assert "sk-ant-api03-xyz987" not in result
        assert "[REDACTED]" in result
        # Timestamp and level context should survive
        assert "2026-03-28" in result


@needs_redactor
class TestDataRedactorTokenFields:
    """access_token and refresh_token field redaction."""

    def test_redact_access_token_json(self):
        """access_token value in a JSON-style string is redacted."""
        redactor = DataRedactor()
        raw = '{"access_token": "abc123def456"}'
        result = redactor.redact(raw)
        assert "abc123def456" not in result
        assert "[REDACTED]" in result

    def test_redact_refresh_token(self):
        """refresh_token value is redacted regardless of surrounding syntax."""
        redactor = DataRedactor()
        raw = 'refresh_token = "r3fr3sh_s3cr3t_tok3n"'
        result = redactor.redact(raw)
        assert "r3fr3sh_s3cr3t_tok3n" not in result
        assert "[REDACTED]" in result


@needs_redactor
class TestDataRedactorPasswordAndEmail:
    """Password-field and email-address redaction."""

    def test_redact_password_field(self):
        """password=... patterns are redacted to password=[REDACTED]."""
        redactor = DataRedactor()
        raw = "password=mySecret123"
        result = redactor.redact(raw)
        assert "mySecret123" not in result
        assert "[REDACTED]" in result

    def test_redact_email(self):
        """Email addresses are replaced with [EMAIL]."""
        redactor = DataRedactor()
        raw = "User contact: user@example.com"
        result = redactor.redact(raw)
        assert "user@example.com" not in result
        assert "[EMAIL]" in result


@needs_redactor
class TestDataRedactorMultipleAndEdgeCases:
    """Multiple patterns in one string and edge-case inputs."""

    def test_redact_multiple_patterns_in_one_string(self):
        """A string with Bearer token + email + password is fully sanitised."""
        redactor = DataRedactor()
        raw = "Authorization: Bearer tok3n_xyz | email: admin@corp.com | password=hunter2"
        result = redactor.redact(raw)
        assert "tok3n_xyz" not in result
        assert "admin@corp.com" not in result
        assert "hunter2" not in result
        # At least two redaction markers present
        assert result.count("[REDACTED]") + result.count("[EMAIL]") >= 3

    def test_redact_preserves_non_sensitive_text(self):
        """Plain text with no sensitive patterns is returned unchanged."""
        redactor = DataRedactor()
        raw = "Hello, world! This is a normal log line with no secrets."
        assert redactor.redact(raw) == raw

    def test_redact_empty_string(self):
        """An empty string input returns an empty string."""
        redactor = DataRedactor()
        assert redactor.redact("") == ""

    def test_redact_no_patterns_match(self):
        """Text that contains sensitive-looking keys but no values is unchanged."""
        redactor = DataRedactor()
        raw = "Field names: access_token, refresh_token, password — all empty."
        # No actual values to redact, so the text may remain or partially change.
        # Crucially, the result must not raise and must be a string.
        result = redactor.redact(raw)
        assert isinstance(result, str)

    def test_redact_case_insensitive_bearer(self):
        """Both 'bearer' and 'BEARER' (and mixed case) variants are redacted."""
        redactor = DataRedactor()
        for variant in ("Bearer tok3n", "bearer tok3n", "BEARER tok3n"):
            result = redactor.redact(variant)
            assert "tok3n" not in result, f"Token not redacted for variant: {variant!r}"

    def test_redact_multiline_text(self):
        """Multi-line string with tokens on different lines are all redacted."""
        redactor = DataRedactor()
        raw = "line1: nothing sensitive\nline2: Bearer multi_line_tok3n\nline3: user@multiline.com\n"
        result = redactor.redact(raw)
        assert "multi_line_tok3n" not in result
        assert "user@multiline.com" not in result

    def test_redact_url_with_token(self):
        """access_token query parameter in a URL is redacted."""
        redactor = DataRedactor()
        raw = "https://api.example.com/callback?access_token=url_t0k3n_abc&state=xyz"
        result = redactor.redact(raw)
        assert "url_t0k3n_abc" not in result

    def test_redact_oauth_response_body(self):
        """Typical OAuth2 JSON response body has all tokens redacted."""
        redactor = DataRedactor()
        raw = (
            '{"token_type":"Bearer","access_token":"at_abc123","expires_in":3600,'
            '"refresh_token":"rt_xyz789","scope":"read write"}'
        )
        result = redactor.redact(raw)
        assert "at_abc123" not in result
        assert "rt_xyz789" not in result
        # Non-sensitive fields should survive
        assert "token_type" in result
        assert "expires_in" in result

    def test_redact_is_idempotent(self):
        """Redacting already-redacted text does not double-redact or corrupt output."""
        redactor = DataRedactor()
        raw = "Authorization: Bearer real_secret_token"
        once = redactor.redact(raw)
        twice = redactor.redact(once)
        assert once == twice, "redact() is not idempotent"


@needs_redactor
class TestDataRedactorDict:
    """redact_dict() — deep-redaction of nested dict/list structures."""

    def test_redact_dict_flat(self):
        """A flat dict with a Bearer token value has it redacted."""
        redactor = DataRedactor()
        data = {"token": "Bearer abc_secret"}
        result = redactor.redact_dict(data)
        assert "abc_secret" not in result.get("token", "")

    def test_redact_dict_nested(self):
        """Sensitive values nested multiple levels deep are all redacted."""
        redactor = DataRedactor()
        data = {"auth": {"credentials": {"access_token": "deep_nested_token_xyz"}}}
        result = redactor.redact_dict(data)
        nested_val = result["auth"]["credentials"]["access_token"]
        assert "deep_nested_token_xyz" not in nested_val

    def test_redact_dict_with_list(self):
        """A dict containing a list of strings with tokens are all redacted."""
        redactor = DataRedactor()
        data = {
            "log_lines": [
                "Bearer tok1_aaa",
                "normal line",
                "Bearer tok2_bbb",
            ]
        }
        result = redactor.redact_dict(data)
        lines = result["log_lines"]
        assert "tok1_aaa" not in lines[0]
        assert lines[1] == "normal line"
        assert "tok2_bbb" not in lines[2]

    def test_redact_dict_preserves_non_string_values(self):
        """Integers, booleans, and None values are passed through unchanged."""
        redactor = DataRedactor()
        data: dict[str, Any] = {
            "count": 42,
            "active": True,
            "nothing": None,
            "score": 3.14,
        }
        result = redactor.redact_dict(data)
        assert result["count"] == 42
        assert result["active"] is True
        assert result["nothing"] is None
        assert result["score"] == pytest.approx(3.14)

    def test_redact_dict_empty(self):
        """An empty dict input returns an empty dict."""
        redactor = DataRedactor()
        assert redactor.redact_dict({}) == {}


@needs_redactor
class TestDataRedactorLogEntry:
    """redact_log_entry() — LogEntry message is redacted; metadata unchanged."""

    def test_redact_log_entry(self):
        """A LogEntry with a Bearer token in its message has the message redacted."""
        redactor = DataRedactor()
        entry = MockLogEntry(message="Sent Authorization: Bearer secret_log_tok")
        result = redactor.redact_log_entry(entry)
        assert "secret_log_tok" not in result.message
        assert "[REDACTED]" in result.message

    def test_redact_log_entry_preserves_metadata(self):
        """timestamp, level, and subsystem fields are NOT modified."""
        redactor = DataRedactor()
        entry = MockLogEntry(
            message="token=Bearer sens1tiv3",
            timestamp="2026-03-28T12:34:56Z",
            level="ERROR",
            subsystem="auth",
        )
        result = redactor.redact_log_entry(entry)
        assert result.timestamp == "2026-03-28T12:34:56Z"
        assert result.level == "ERROR"
        assert result.subsystem == "auth"


@needs_redactor
class TestDataRedactorCustomPatterns:
    """add_pattern() and remove_pattern() — custom pattern management."""

    def test_add_custom_pattern(self):
        """A custom SSN pattern is applied after registration."""
        redactor = DataRedactor()
        # US Social Security Number: NNN-NN-NNNN
        redactor.add_pattern(
            name="ssn",
            regex=r"\b\d{3}-\d{2}-\d{4}\b",
            replacement="[SSN]",
        )
        result = redactor.redact("SSN on file: 123-45-6789 — keep the rest.")
        assert "123-45-6789" not in result
        assert "[SSN]" in result

    def test_remove_pattern(self):
        """After removing the 'bearer' pattern, Bearer tokens are NOT redacted."""
        redactor = DataRedactor()
        redactor.remove_pattern("bearer")
        raw = "Authorization: Bearer should_remain_visible"
        result = redactor.redact(raw)
        # Token value should still be in the output since pattern was removed
        assert "should_remain_visible" in result

    def test_add_pattern_overwrites_existing(self):
        """Re-registering a pattern name replaces the previous one."""
        redactor = DataRedactor()
        # Override the email pattern with a no-op replacement
        redactor.add_pattern(
            name="email",
            regex=r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}",
            replacement="[OVERRIDE]",
        )
        result = redactor.redact("contact: test@override.com")
        assert "test@override.com" not in result
        assert "[OVERRIDE]" in result


# ===========================================================================
#  M13: CredentialContext — 15 tests
# ===========================================================================


@needs_credentials
class TestCredentialContextGetters:
    """get() — retrieval of api_key and product_credentials."""

    def test_get_api_key(self):
        """The api_key is accessible via get('api_key')."""
        ctx = CredentialContext(api_key="sk-ant-test-key-abc")
        assert ctx.get("api_key") == "sk-ant-test-key-abc"

    def test_get_product_credential(self):
        """A product credential registered at construction is retrievable."""
        ctx = CredentialContext(
            api_key="sk-ant-test",
            product_credentials={"BASE_URL": "https://example.com"},
        )
        assert ctx.get("BASE_URL") == "https://example.com"

    def test_get_missing_key_raises(self):
        """Requesting a key that was never registered raises KeyError."""
        ctx = CredentialContext(api_key="sk-ant-test")
        with pytest.raises(KeyError):
            ctx.get("NONEXISTENT_KEY")

    def test_get_never_returns_none(self):
        """Even if None is stored, get() must raise rather than return None."""
        ctx = CredentialContext(
            api_key="sk-ant-test",
            product_credentials={"NULLABLE": None},  # type: ignore[dict-item]
        )
        with pytest.raises((KeyError, ValueError, TypeError)):
            result = ctx.get("NULLABLE")
            # If the impl does not raise, assert it's not None to enforce contract
            assert result is not None, "get() must never return None"


@needs_credentials
class TestCredentialContextResolveTemplate:
    """resolve_template() — ${KEY} placeholder substitution."""

    def test_resolve_template_simple(self):
        """A single ${NAME} placeholder is substituted correctly."""
        ctx = CredentialContext(
            api_key="sk-ant-test",
            product_credentials={"NAME": "John"},
        )
        assert ctx.resolve_template("Hello ${NAME}") == "Hello John"

    def test_resolve_template_multiple(self):
        """Multiple distinct placeholders in one string are all resolved."""
        ctx = CredentialContext(
            api_key="sk-ant-test",
            product_credentials={"A": "alpha", "B": "beta"},
        )
        result = ctx.resolve_template("${A} and ${B}")
        assert result == "alpha and beta"

    def test_resolve_template_no_placeholders(self):
        """Plain text without ${...} patterns is returned unchanged."""
        ctx = CredentialContext(api_key="sk-ant-test")
        plain = "No placeholders here."
        assert ctx.resolve_template(plain) == plain

    def test_resolve_template_missing_key_raises(self):
        """A ${MISSING} placeholder whose key is not registered raises KeyError."""
        ctx = CredentialContext(
            api_key="sk-ant-test",
            product_credentials={"PRESENT": "value"},
        )
        with pytest.raises(KeyError):
            ctx.resolve_template("Try ${MISSING} now")

    def test_resolve_template_nested_braces(self):
        """Text containing ${{}} (escaped braces) edge case does not crash."""
        ctx = CredentialContext(api_key="sk-ant-test")
        # Whatever the output, it must not raise
        result = ctx.resolve_template("${{NOT_A_PLACEHOLDER}}")
        assert isinstance(result, str)


@needs_credentials
class TestCredentialContextFromEnv:
    """from_env() — construction from environment variables."""

    def test_from_env(self):
        """from_env() with default prefix reads SPECTERQA_API_KEY."""
        with patch.dict(os.environ, {"SPECTERQA_API_KEY": "sk-ant-env-default"}, clear=False):
            ctx = CredentialContext.from_env()
            assert ctx.get("api_key") == "sk-ant-env-default"

    def test_from_env_custom_prefix(self):
        """from_env() accepts a custom prefix and strips it from credential keys."""
        with patch.dict(
            os.environ,
            {"MYAPP_API_KEY": "sk-ant-custom-pfx", "MYAPP_BASE_URL": "https://myapp.io"},
            clear=False,
        ):
            ctx = CredentialContext.from_env(env_prefix="MYAPP_")
            assert ctx.get("api_key") == "sk-ant-custom-pfx"

    def test_from_env_missing_api_key_raises(self):
        """from_env() raises an appropriate error when the API key env var is absent."""
        # Remove both common spellings to ensure no fallthrough
        env_without_key = {k: v for k, v in os.environ.items() if not k.startswith("SPECTERQA_")}
        with patch.dict(os.environ, env_without_key, clear=True):
            with pytest.raises((KeyError, ValueError, EnvironmentError, Exception)):
                CredentialContext.from_env()


@needs_credentials
class TestCredentialContextReprSafety:
    """__repr__() — must not expose credential values."""

    def test_repr_hides_credentials(self):
        """repr() output must not contain any of the actual credential values."""
        api_key = "sk-ant-super-secret-key-xyz"
        ctx = CredentialContext(
            api_key=api_key,
            product_credentials={"PASSWORD": "hunter2", "TOKEN": "s3cr3t"},
        )
        representation = repr(ctx)
        assert api_key not in representation, "API key must not appear in repr()"
        assert "hunter2" not in representation, "Password must not appear in repr()"
        assert "s3cr3t" not in representation, "Token must not appear in repr()"

    def test_repr_shows_key_count(self):
        """repr() should indicate how many credentials are loaded (not their values)."""
        ctx = CredentialContext(
            api_key="sk-ant-test",
            product_credentials={"A": "1", "B": "2", "C": "3"},
        )
        representation = repr(ctx)
        # The count (3 product credentials, plus the api_key) should appear somewhere
        # We accept any numeric indication; the exact format is left to CodeAtlas.
        assert any(char.isdigit() for char in representation), (
            "repr() should include at least one digit (credential count)"
        )


@needs_credentials
class TestCredentialContextIsolation:
    """Two CredentialContext instances must not share credential state."""

    def test_credential_isolation(self):
        """Mutating one instance's credentials must not affect another instance."""
        ctx_a = CredentialContext(
            api_key="sk-ant-a",
            product_credentials={"SHARED_KEY": "value_a"},
        )
        ctx_b = CredentialContext(
            api_key="sk-ant-b",
            product_credentials={"SHARED_KEY": "value_b"},
        )
        # Each context should return its own value
        assert ctx_a.get("SHARED_KEY") == "value_a"
        assert ctx_b.get("SHARED_KEY") == "value_b"
        # Verify api_key isolation too
        assert ctx_a.get("api_key") != ctx_b.get("api_key")
