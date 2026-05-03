"""simdrive.wda — WebDriverAgent integration for real-device input.

Public surface:
  WdaClient       — HTTP client for a running WDA instance
  bootstrap_device — full bootstrap CLI entry-point
  registry        — load/save per-UDID WDA registry
"""
from .client import WdaClient
from .bootstrap import bootstrap_device
from . import registry

__all__ = ["WdaClient", "bootstrap_device", "registry"]
