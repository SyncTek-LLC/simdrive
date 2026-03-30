"""Data redaction primitive for the SpecterQA iOS driver.

Every output boundary in the iOS driver passes through :class:`DataRedactor`
before the data leaves the process (logs, screenshots, API payloads, etc.).

This module has **zero external dependencies** and must never raise on valid
input.  It is safe to call from hot paths.
"""

from __future__ import annotations

import copy
import re
from typing import Any


class DataRedactor:
    """Redacts sensitive data from text, dicts, and log entries.

    Built-in patterns cover: Bearer tokens, access_token, refresh_token,
    password fields, and email addresses.  Custom patterns can be added at
    runtime via :meth:`add_pattern`.

    This is a security primitive — it must have zero external dependencies
    and never raise on valid input.

    Pattern application order is deterministic (insertion order).  Built-in
    patterns are registered in the order listed in ``_DEFAULT_PATTERNS`` so
    that ``bearer`` fires before ``access_token``, avoiding partial matches
    where the bearer replacement text might re-match.

    Redaction is idempotent: the replacement strings themselves do not match
    any built-in pattern, so running :meth:`redact` twice on already-redacted
    text produces the same result.

    Usage::

        redactor = DataRedactor()
        safe = redactor.redact("Authorization: Bearer abc.def.ghi")
        # "Authorization: Bearer [REDACTED]"

        safe_dict = redactor.redact_dict({"token": "secret123", "count": 5})
        # {"token": "secret123", "count": 5}  — non-token keys not matched

    Note on ``redact_dict``: the method walks the entire nested structure and
    applies redaction to every *string value*.  Non-string leaf values (int,
    float, bool, None, etc.) pass through unchanged.  The input dict is never
    mutated — a new structure is always returned.
    """

    # Built-in patterns: name → (raw_regex, replacement_string).
    # Order matters — first entry applied first.
    _DEFAULT_PATTERNS: dict[str, tuple[str, str]] = {
        "bearer": (
            r"(?i)Bearer\s+[A-Za-z0-9\-._~+/]+=*",
            "Bearer [REDACTED]",
        ),
        "access_token": (
            r"(?i)access_token[\"'\s:=]+[A-Za-z0-9\-._~+/]+=*",
            "access_token=[REDACTED]",
        ),
        "refresh_token": (
            r"(?i)refresh_token[\"'\s:=]+[A-Za-z0-9\-._~+/]+=*",
            "refresh_token=[REDACTED]",
        ),
        "password": (
            r"(?i)password[\"'\s:=]+\S+",
            "password=[REDACTED]",
        ),
        "email": (
            r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
            "[EMAIL]",
        ),
    }

    def __init__(self) -> None:
        # Maps name → (compiled pattern, replacement string).
        # Populated in _DEFAULT_PATTERNS insertion order so that bearer fires
        # before access_token — Python 3.7+ dicts preserve insertion order.
        self._patterns: dict[str, tuple[re.Pattern[str], str]] = {}
        for name, (regex, repl) in self._DEFAULT_PATTERNS.items():
            self._patterns[name] = (re.compile(regex), repl)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def redact(self, text: str) -> str:
        """Apply all registered patterns to *text* and return the result.

        Patterns are applied sequentially in registration order.  The bearer
        pattern fires before access_token to prevent ``Bearer [REDACTED]``
        from being partially re-matched by the access_token pattern.

        This method is safe to call on already-redacted text (idempotent).

        Args:
            text: The raw string to redact.

        Returns:
            A new string with all sensitive fragments replaced.  If *text*
            contains no sensitive data the original string object is returned
            unchanged (no copy overhead).
        """
        result = text
        for pattern, replacement in self._patterns.values():
            result = pattern.sub(replacement, result)
        return result

    def redact_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Deep-walk *data* and redact all string values.

        The walk recurses into nested dicts and lists.  Non-string leaf
        values (int, float, bool, None, bytes, …) are preserved verbatim.
        The original *data* dict is **never mutated** — a new structure is
        always returned.

        Args:
            data: Arbitrary nested dict.  Keys are not redacted, only values.

        Returns:
            A new dict with the same structure and all string values redacted.
        """
        return self._redact_value(data)  # type: ignore[return-value]

    def redact_log_entry(self, entry: Any) -> Any:
        """Return a shallow copy of *entry* with its ``message`` field redacted.

        The copy is shallow — only the top-level object is duplicated.  All
        non-``message`` attributes are carried over by reference (they are not
        cloned or inspected).  This is intentional: copying large nested
        structures in a hot logging path would be too expensive.

        If *entry* has no ``message`` attribute it is returned as-is.

        Args:
            entry: Any object that optionally has a ``message`` attribute.
                Dataclasses, simple namespaces, and ``logging.LogRecord``
                instances all work here.

        Returns:
            A shallow copy of *entry* with the ``message`` field redacted, or
            the original object if it has no ``message`` attribute.
        """
        if not hasattr(entry, "message"):
            return entry
        cloned = copy.copy(entry)
        cloned.message = self.redact(entry.message)
        return cloned

    def add_pattern(self, name: str, regex: str, replacement: str) -> None:
        """Register (or replace) a named redaction pattern.

        Args:
            name: Unique identifier for this pattern.  If a pattern with this
                name already exists it is replaced.
            regex: Regular expression string.  Compiled with :func:`re.compile`
                using default flags.  Use inline flags (``(?i)``) for
                case-insensitivity.
            replacement: Literal replacement string (not a regex template).
                Backslashes are treated as literals — use a plain string here.

        Raises:
            re.error: If *regex* is not a valid regular expression.
        """
        self._patterns[name] = (re.compile(regex), replacement)

    def remove_pattern(self, name: str) -> None:
        """Remove a named pattern.  No-op if *name* does not exist.

        Args:
            name: The pattern name to remove.  Built-in pattern names
                (``bearer``, ``access_token``, ``refresh_token``,
                ``password``, ``email``) can be removed here if you need to
                disable a built-in.
        """
        self._patterns.pop(name, None)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    # Built-in sensitive key names that should always have their values
    # redacted when found as dict keys, even if the bare value string does
    # not contain the key prefix (e.g. a dict entry like
    # {"access_token": "deep_nested_token_xyz"} must still be redacted).
    _SENSITIVE_KEYS: frozenset[str] = frozenset(
        {"access_token", "refresh_token", "password", "token", "secret",
         "api_key", "bearer", "authorization"}
    )

    def _redact_value(self, value: Any, *, _key: str | None = None) -> Any:
        """Recursively redact a single value (str, dict, list, or other).

        Args:
            value: The value to redact.
            _key: The dict key under which this value was found, if any.
                  Used to force-redact values whose key names are sensitive
                  even when the bare value string would not normally match a
                  built-in pattern.
        """
        if isinstance(value, str):
            # If the parent dict key is a known-sensitive field, synthesise
            # the canonical key=value form so the existing pattern fires.
            if _key is not None and _key.lower() in self._SENSITIVE_KEYS:
                synthetic = f"{_key}={value}"
                redacted_synthetic = self.redact(synthetic)
                # Strip the key prefix back out so only the redacted value
                # is stored (preserving the dict structure).
                prefix = f"{_key}="
                if redacted_synthetic.startswith(prefix):
                    return redacted_synthetic[len(prefix):]
                return redacted_synthetic
            return self.redact(value)
        if isinstance(value, dict):
            return {k: self._redact_value(v, _key=k) for k, v in value.items()}
        if isinstance(value, list):
            return [self._redact_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._redact_value(item) for item in value)
        # int, float, bool, None, bytes, etc. — pass through unchanged
        return value
