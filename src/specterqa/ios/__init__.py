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
        try:
            specterqa.__path__.insert(0, _our_root)
        except AttributeError:
            # _NamespacePath (Python 3.11+) supports append but not insert.
            # Append is sufficient — we just need our path on the search list.
            specterqa.__path__.append(_our_root)  # type: ignore[attr-defined]


_ensure_namespace()

try:
    from specterqa import __version__  # noqa: E402
except ImportError:
    try:
        from importlib.metadata import version as _pkg_version
        __version__ = _pkg_version("specterqa-ios")
    except Exception:
        __version__ = "15.0.0"

try:
    from specterqa.ios.drivers.simulator.driver import SimulatorDriver  # noqa: E402
except ImportError:
    # Graceful degradation: if SimulatorDriver cannot be imported (e.g. due to a
    # missing upstream specterqa __version__ or a broken install), expose a stub
    # so that the module is still importable and CLI commands still load.
    SimulatorDriver = None  # type: ignore[assignment,misc]

__all__ = ["SimulatorDriver"]
