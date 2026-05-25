"""Regression tests for Bug 4 — stale rename strings.

Stale strings that must be purged:
  - ios_observe, ios_start_session, ios_devices, ios_stop_recording,
    ios_start_recording, ios_list_replays, and all other ios_* v0.3 tool names
    in error recovery messages
  - "specterqa-ios" in user-facing help / branding strings
  - "(codename:" framing in help/docstrings
  - "SpecterQA for iOS" as the product name

TDD: written BEFORE the fix. All tests must FAIL on current code.
"""
from __future__ import annotations

import sys
from io import StringIO

import pytest


# ---------------------------------------------------------------------------
# Stale ios_* tool names in error recovery messages
# ---------------------------------------------------------------------------

# The current v1.0 tool names (no ios_ prefix)
_IOS_PREFIXED_STALE_NAMES = [
    "ios_observe",
    "ios_start_session",
    "ios_devices",
    "ios_stop_recording",
    "ios_start_recording",
    "ios_list_replays",
    "ios_stop_session",
    "ios_session_status",
    "ios_app_state",
    "ios_apps",
    "ios_crashes",
    "ios_doctor",
    "ios_logs",
    "ios_perf",
    "ios_replay",
    "ios_observe",
]


class TestNoStaleIosPrefixInRecoveryMessages:

    def _build_all_errors(self) -> list[tuple[str, str]]:
        """Return [(name, message_text)] for every error factory in the error modules."""
        import inspect
        import simdrive.errors as core_errors
        import simdrive.journey.errors as journey_errors
        import simdrive.license.errors as license_errors

        results = []

        for module_name, module in [
            ("errors", core_errors),
            ("journey.errors", journey_errors),
            ("license.errors", license_errors),
        ]:
            for fn_name, fn in inspect.getmembers(module, inspect.isfunction):
                if fn_name.startswith("_"):
                    continue
                # Build the error with minimal dummy args by inspecting the signature
                try:
                    sig = inspect.signature(fn)
                    kwargs = {}
                    for param_name, param in sig.parameters.items():
                        if param.default is inspect.Parameter.empty:
                            # Supply a dummy value based on annotation
                            ann = param.annotation
                            if ann in (str, inspect.Parameter.empty):
                                kwargs[param_name] = "dummy"
                            elif ann is int:
                                kwargs[param_name] = 1
                            elif ann is float:
                                kwargs[param_name] = 1.0
                            elif ann is list or str(ann).startswith("list"):
                                kwargs[param_name] = ["smoke"]
                            else:
                                kwargs[param_name] = "dummy"
                    err = fn(**kwargs)
                except Exception:
                    continue

                if isinstance(err, dict):
                    text = err.get("error", {}).get("message", "")
                elif hasattr(err, "message"):
                    text = err.message
                    if hasattr(err, "details") and isinstance(err.details, dict):
                        text += " " + str(err.details)
                else:
                    continue

                results.append((f"{module_name}.{fn_name}", text))

        return results

    def test_no_stale_ios_prefix_in_recovery_messages(self) -> None:
        """No error message or recovery clause may reference ios_* v0.3-era tool names.

        Currently FAILS because multiple error constructors (e.g. target_not_found,
        act_tool_failed) reference ios_observe, ios_start_session, ios_devices etc.
        in their Recovery: clauses.
        """
        stale_found = []

        for name, text in self._build_all_errors():
            for stale in _IOS_PREFIXED_STALE_NAMES:
                if stale in text:
                    stale_found.append((name, stale, text[:120]))

        assert not stale_found, (
            "Stale ios_* tool names found in error recovery messages:\n"
            + "\n".join(
                f"  [{fn}] contains {stale!r}: ...{snippet!r}..."
                for fn, stale, snippet in stale_found
            )
        )


# ---------------------------------------------------------------------------
# Help banner branding
# ---------------------------------------------------------------------------


class TestHelpBannerBranding:

    def test_help_banner_uses_simdrive_branding(self) -> None:
        """_HELP_TEXT must NOT contain 'specterqa-ios', '(codename:', or
        'SpecterQA for iOS'.  It MUST contain 'simdrive' as the product name.

        Currently FAILS: _HELP_TEXT begins with
        'specterqa-ios — SpecterQA for iOS MCP server. (codename: simdrive)'
        """
        from simdrive.server import _HELP_TEXT

        for forbidden in ("specterqa-ios", "(codename:", "SpecterQA for iOS"):
            assert forbidden not in _HELP_TEXT, (
                f"_HELP_TEXT must not contain stale string {forbidden!r}. "
                f"Current first line: {_HELP_TEXT.splitlines()[0]!r}"
            )

        assert "simdrive" in _HELP_TEXT, (
            "_HELP_TEXT must mention 'simdrive' as the product name on the first line."
        )

    def test_version_output_says_simdrive(self, capsys: pytest.CaptureFixture) -> None:
        """serve() --version must print a string starting with 'simdrive ' (not 'specterqa-ios ').

        Currently FAILS: the version branch prints
        f"specterqa-ios {__version__}"
        """
        import simdrive
        from simdrive import server

        with pytest.raises(SystemExit) as exc_info:
            # Simulate serve() being called with --version
            import sys
            orig_argv = sys.argv[:]
            try:
                sys.argv = ["simdrive", "--version"]
                server.serve()
            finally:
                sys.argv = orig_argv

        assert exc_info.value.code == 0, (
            f"serve() --version must exit with code 0, got {exc_info.value.code}"
        )

        captured = capsys.readouterr()
        output = captured.out.strip()
        assert output.startswith("simdrive "), (
            f"Version output must start with 'simdrive ', "
            f"but got: {output!r}. "
            "Fix: change the --version branch in serve() from "
            "'specterqa-ios {__version__}' to 'simdrive {__version__}'."
        )


# ---------------------------------------------------------------------------
# Module docstring / __init__.py branding
# ---------------------------------------------------------------------------


class TestInitDocstringBranding:

    def test_init_docstring_no_codename_framing(self) -> None:
        """simdrive.__doc__ must NOT contain 'codename' or 'SpecterQA for iOS'.

        The product name is now simply 'simdrive'.

        Currently FAILS: simdrive/__init__.py docstring reads
        'SpecterQA for iOS — MCP-native iOS simulator driver. (Internal codename: simdrive.)'
        """
        import simdrive

        doc = simdrive.__doc__ or ""

        assert "codename" not in doc, (
            f"simdrive.__doc__ must not contain 'codename' framing. "
            f"Current docstring: {doc!r}"
        )

        assert "SpecterQA for iOS" not in doc, (
            f"simdrive.__doc__ must not contain 'SpecterQA for iOS'. "
            f"Current docstring: {doc!r}"
        )

    def test_server_module_docstring_no_codename_framing(self) -> None:
        """server.py module docstring must NOT reference 'specterqa-ios' as the
        primary product name or use '(codename:' framing.

        Currently FAILS: server.py docstring starts with
        'SpecterQA for iOS MCP server. (Internal codename: simdrive.)'
        and references 'specterqa-ios' in the Run: section.
        """
        import simdrive.server as _server

        doc = _server.__doc__ or ""

        # The module docstring should not open with the old product identity
        assert "(codename:" not in doc, (
            f"server.py docstring must not contain '(codename:' framing. "
            f"Current first line: {doc.splitlines()[0] if doc else '(empty)'!r}"
        )

        # 'specterqa-ios' may appear in historical context / alias notes but
        # must NOT appear as the primary product name (first line)
        first_line = doc.splitlines()[0] if doc else ""
        assert not first_line.startswith("SpecterQA for iOS"), (
            f"server.py docstring first line must not start with 'SpecterQA for iOS'. "
            f"Current first line: {first_line!r}"
        )
