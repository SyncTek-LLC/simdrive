"""
setup.py — minimal shim.

All project metadata lives in pyproject.toml.
The runner/ directory is shipped directly via packages.find auto-discovery
(runner/__init__.py makes it a proper Python package) — no build_py override needed.
"""

from setuptools import setup

setup()
