"""SpecterQA iOS Driver package.

This package extends the ``specterqa`` namespace with iOS simulator support.
At import time we ensure the upstream ``specterqa`` package (a regular package,
not a namespace package) is aware of our sub-tree so that
``specterqa.ios.*`` resolves correctly regardless of installation order.
"""

from __future__ import annotations

import os


def _ensure_namespace() -> None:
    """Extend specterqa.__path__ to include our src tree if needed."""
    try:
        import specterqa  # noqa: PLC0415
    except ImportError:
        return

    _our_root = os.path.dirname(os.path.dirname(__file__))  # .../src/specterqa
    if _our_root not in specterqa.__path__:
        specterqa.__path__.insert(0, _our_root)


_ensure_namespace()

try:
    from specterqa.ios.drivers.simulator.driver import SimulatorDriver  # noqa: E402
except ImportError:
    # Graceful degradation: if SimulatorDriver cannot be imported (e.g. due to a
    # missing upstream specterqa __version__ or a broken install), expose a stub
    # so that the module is still importable and CLI commands still load.
    SimulatorDriver = None  # type: ignore[assignment,misc]

__all__ = ["SimulatorDriver"]
