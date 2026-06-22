"""Repo root is NOT an installable package.

The shipping package `simdrive` lives in the `simdrive/` subdirectory (its own
pyproject.toml). The repo-root pyproject.toml was intentionally removed
("option B", commit a0abf0b) and the legacy `specterqa` tree under `src/` is
retired. Building from the root would flat-layout-autodiscover that dead tree
and fail with a cryptic `Invalid distribution name __init__-0.0.0`.

Install / build from the package directory instead:

    pip install ./simdrive            # or:  pip install --force-reinstall ./simdrive
    cd simdrive && pip install -e .   # editable dev install (matches CI)

This shim exists only to turn `pip install .` at the repo root into the clear
message above rather than a confusing setuptools autodiscovery error.
"""
import sys

sys.exit(
    "ERROR: the repo root is not installable. The `simdrive` package lives in "
    "./simdrive — run `pip install ./simdrive` (or `cd simdrive && pip install -e .`)."
)
