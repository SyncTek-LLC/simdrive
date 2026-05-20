"""simdrive.wda — WebDriverAgent integration for real-device input.

Public surface:
  WdaClient       — HTTP client for a running WDA instance
  bootstrap_device — full bootstrap CLI entry-point
  registry        — load/save per-UDID WDA registry

Error constructors (re-exported for callers that catch by code/type):
  wda_recovery_exhausted — raised when the auto-recovery loop gives up.
"""
from .client import WdaClient
from .bootstrap import bootstrap_device
from . import registry
from .errors import wda_recovery_exhausted

__all__ = [
    "WdaClient",
    "bootstrap_device",
    "registry",
    "wda_recovery_exhausted",
]
