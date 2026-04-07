"""Per-driver credential isolation for the SpecterQA iOS driver.

:class:`CredentialContext` provides a single driver instance with its own
credential store.  Credentials are resolved once at construction time and
never leaked through ``__repr__`` or ``__str__``.

This module has **zero external dependencies** and is intentionally small —
it is a security primitive, not a general-purpose config system.

Usage::

    ctx = CredentialContext(
        api_key="sk-ant-...",
        product_credentials={
            "APP_USERNAME": "tester@example.com",
            "APP_PASSWORD": "hunter2",
        },
    )

    ctx.get("APP_USERNAME")          # "tester@example.com"
    ctx.resolve_template("${APP_USERNAME}")  # "tester@example.com"
    repr(ctx)  # "CredentialContext(keys=2, api_key=set)"

    # From environment variables:
    # SPECTERQA_API_KEY=sk-ant-...
    # SPECTERQA_APP_USERNAME=tester@example.com
    ctx2 = CredentialContext.from_env("SPECTERQA_")
"""

from __future__ import annotations

import os
import re


# Matches ${KEY} template variables.  Key names must be non-empty.
_TEMPLATE_RE = re.compile(r"\$\{([^}]+)\}")


class CredentialContext:
    """Isolated credential store for a single SimulatorDriver instance.

    Each driver instance owns exactly one :class:`CredentialContext`.  There
    is no class-level shared state — two instances are completely independent.

    Resolves ``${KEY}`` template variables (used in persona YAML files) at
    call time via :meth:`resolve_template`.

    Credential values are **never** exposed through :meth:`__repr__` or
    :meth:`__str__` to prevent accidental leakage into logs or error messages.

    Args:
        api_key: Anthropic API key.  Required; may not be empty.
        product_credentials: Optional mapping of credential name → value.
            These are the per-product credentials referenced in persona YAML
            files (e.g. ``APP_USERNAME``, ``APP_PASSWORD``).
    """

    def __init__(
        self,
        api_key: str,
        product_credentials: dict[str, str] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must not be empty")
        self._api_key: str = api_key
        # Shallow copy to ensure per-instance isolation.
        self._credentials: dict[str, str] = dict(product_credentials or {})

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def api_key(self) -> str:
        """The raw Anthropic API key for this driver instance.

        Returns:
            The API key string as provided at construction time.
        """
        return self._api_key

    # ------------------------------------------------------------------
    # Credential access
    # ------------------------------------------------------------------

    def get(self, key: str) -> str:
        """Return the credential value for *key*.

        Checks ``_credentials`` first.  If *key* is ``"api_key"`` and not
        found in ``_credentials``, falls back to returning ``_api_key``.
        This allows callers to retrieve the API key via the uniform
        ``get("api_key")`` interface.

        Args:
            key: Credential name to look up (e.g. ``"APP_USERNAME"`` or
                ``"api_key"``).

        Returns:
            The credential value string.

        Raises:
            KeyError: If *key* is not present and is not ``"api_key"``.
                The exception message lists the available keys (not their
                values) to aid debugging without leaking secrets.
            KeyError: If *key* is in ``_credentials`` but its value is
                ``None`` — ``get()`` never returns ``None``.
        """
        if key in self._credentials:
            value = self._credentials[key]
            if value is None:
                raise KeyError(f"Credential {key!r} has a None value; get() never returns None.")
            return value  # type: ignore[return-value]
        if key == "api_key":
            return self._api_key
        available = ", ".join(sorted(self._credentials.keys())) or "(none)"
        raise KeyError(f"Credential {key!r} not found. Available keys: {available}")

    def resolve_template(self, text: str) -> str:
        """Resolve ``${KEY}`` placeholders in *text* using stored credentials.

        All occurrences of ``${KEY}`` are replaced with the corresponding
        credential value from :attr:`_credentials`.  Placeholders are resolved
        greedily — a single pass replaces all occurrences.

        Args:
            text: Template string containing zero or more ``${KEY}``
                placeholders (e.g. ``"https://api.example.com?token=${TOKEN}"``).

        Returns:
            The fully resolved string.

        Raises:
            KeyError: If any placeholder key is not present in the credential
                store.  The exception message identifies the missing key.
        """

        def _replace(match: re.Match[str]) -> str:
            key = match.group(1)
            # Skip keys that start with '{' — these are escaped-brace edge
            # cases like ${{NOT_A_PLACEHOLDER}} where the outer ${ matched
            # but the inner content is not a real placeholder.
            if key.startswith("{"):
                return match.group(0)
            return self.get(key)

        return _TEMPLATE_RE.sub(_replace, text)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls, env_prefix: str = "SPECTERQA_") -> "CredentialContext":
        """Create a :class:`CredentialContext` from environment variables.

        Reads all environment variables whose names start with *env_prefix*.

        The API key is read from ``{prefix}API_KEY`` (e.g.
        ``SPECTERQA_API_KEY``).  All other matching variables become product
        credentials with the prefix stripped from their names.

        For example, with ``env_prefix="SPECTERQA_"``:

        .. code-block:: bash

            SPECTERQA_API_KEY=sk-ant-...
            SPECTERQA_APP_USERNAME=tester@example.com
            SPECTERQA_APP_PASSWORD=hunter2

        produces::

            CredentialContext(
                api_key="sk-ant-...",
                product_credentials={
                    "APP_USERNAME": "tester@example.com",
                    "APP_PASSWORD": "hunter2",
                },
            )

        Args:
            env_prefix: The prefix to filter environment variables.  Defaults
                to ``"SPECTERQA_"``.

        Returns:
            A new :class:`CredentialContext` populated from the environment.

        Raises:
            KeyError: If ``{prefix}API_KEY`` is not set in the environment.
            ValueError: If the resolved API key is an empty string.
        """
        api_key_var = f"{env_prefix}API_KEY"
        api_key = os.environ.get(api_key_var)
        if api_key is None:
            raise KeyError(
                f"Required environment variable {api_key_var!r} is not set. "
                "Set it to your Anthropic API key before creating a CredentialContext."
            )

        product_credentials: dict[str, str] = {}
        for var_name, var_value in os.environ.items():
            if var_name.startswith(env_prefix) and var_name != api_key_var:
                stripped_name = var_name[len(env_prefix) :]
                product_credentials[stripped_name] = var_value

        return cls(api_key=api_key, product_credentials=product_credentials)

    # ------------------------------------------------------------------
    # Safe repr — never expose values
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        """Return a safe representation that never exposes credential values.

        Shows the number of product credentials and whether the API key is
        set, but does **not** include any credential values.

        Returns:
            A string of the form
            ``"CredentialContext(keys=N, api_key=set)"`` or
            ``"CredentialContext(keys=N, api_key=unset)"``.
        """
        api_key_status = "set" if self._api_key else "unset"
        return f"CredentialContext(keys={len(self._credentials)}, api_key={api_key_status})"

    def __str__(self) -> str:
        """Delegates to :meth:`__repr__` — never exposes credential values."""
        return self.__repr__()
