"""MCP-tool entitlement gate — single entry point for paywall enforcement.

[internal-tracker].5: every MCP tool handler calls ``gate()`` at its entry. The
function delegates to ``simdrive.license.entitlement.check_entitlement`` and
re-raises any ``LicenseError`` unchanged so the MCP envelope wrapper sees the
structured ``license_required`` / ``license_expired`` payload.

Why a thin wrapper rather than calling ``check_entitlement`` directly:
  * Single point of override for tests (one ``monkeypatch.setattr`` per test).
  * Single seam to add telemetry (call count, tier breakdown) later without
    touching every tool handler.
  * Documents intent: a call to ``gate()`` is a paywall checkpoint.

Bootstrap commands (``simdrive trial start``, ``simdrive license …``,
``simdrive auth …``) MUST NOT call ``gate()`` — users run them to escape the
paywall, so gating them creates a chicken-and-egg lockout.
"""
from __future__ import annotations

from typing import Optional

from simdrive.license.entitlement import Entitlement
from simdrive.license.errors import LicenseError


def gate() -> Optional[Entitlement]:
    """Raise LicenseError when no valid license is on disk; else return Entitlement.

    Returns
    -------
    Entitlement
        The resolved entitlement for the active license — handlers may inspect
        ``tier`` to drive tier-conditional behaviour. Callers that only need
        gating may discard the return value.

    Raises
    ------
    LicenseError
        Propagated unchanged from ``check_entitlement``. Subclassed codes are
        documented in ``simdrive.license.errors``.
    """
    # Late attribute lookup (not a top-of-module ``from … import``) so tests
    # can ``monkeypatch.setattr(entitlement, "check_entitlement", …)`` and have
    # the override take effect for every gate() call.
    from simdrive.license import entitlement as _ent
    try:
        return _ent.check_entitlement()
    except LicenseError:
        raise
