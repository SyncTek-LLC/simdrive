"""simdrive — MCP-native iOS simulator driver."""

from importlib.metadata import PackageNotFoundError, version as _v

try:
    __version__ = _v("simdrive")
except PackageNotFoundError:
    __version__ = "0.0.0+local"
