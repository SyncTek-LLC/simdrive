try:
    from importlib.metadata import version as _get_version
    __version__ = _get_version("specterqa-ios")
except Exception:
    __version__ = "5.1.0"
