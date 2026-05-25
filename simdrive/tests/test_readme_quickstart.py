"""
Regression test: the simdrive README must contain the 30-second quickstart
commands in the first 100 lines, and must not contain stale/misleading strings.

If this test fails, the README front-door has regressed — the quickstart was
moved or removed, or a stale string was re-introduced. Fix the README, not the
test.

Source: [internal-tracker]. Added in 1.0.0a6 to pin the discoverability polish.
"""
from __future__ import annotations

from pathlib import Path

README_PATH = Path(__file__).parent.parent / "README.md"


def _readme_lines() -> list[str]:
    return README_PATH.read_text(encoding="utf-8").splitlines()


def _readme_first_n_lines(n: int) -> str:
    return "\n".join(_readme_lines()[:n])


def _readme_full() -> str:
    return README_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Quickstart presence tests — must be in the first 100 lines
# ---------------------------------------------------------------------------

def test_quickstart_pip_install_in_first_100_lines() -> None:
    """`pip install simdrive` must appear in the first 100 lines of the README.

    [internal-tracker].5: the package is now a paywalled trial+paid product
    rather than a pre-release alpha, so the install command no longer carries
    the ``--pre`` flag. The presence of the install command itself is the
    invariant under test.
    """
    head = _readme_first_n_lines(100)
    assert "pip install simdrive" in head, (
        "README quickstart regression: 'pip install simdrive' not found in "
        "the first 100 lines. The install command must be at the top of the README "
        "so new users and MCP client registries see it immediately."
    )


def test_quickstart_trial_start_in_first_100_lines() -> None:
    """`simdrive trial start --email` must appear in the first 100 lines.

    [internal-tracker].5: trial issuance is now the canonical first step after
    install — every gated tool returns ``license_required`` until a trial or
    paid key is on disk.
    """
    head = _readme_first_n_lines(100)
    assert "simdrive trial start --email" in head, (
        "README quickstart regression: 'simdrive trial start --email' not "
        "found in the first 100 lines. The trial-bootstrap command must be "
        "documented up front so new users can clear the paywall in 30 seconds."
    )


def test_quickstart_mcp_servers_in_first_100_lines() -> None:
    """The mcpServers config snippet must appear in the first 100 lines."""
    head = _readme_first_n_lines(100)
    assert "mcpServers" in head, (
        "README quickstart regression: 'mcpServers' MCP config snippet not found in "
        "the first 100 lines. The MCP wiring example must be at the top so agent "
        "users can connect in under 60 seconds."
    )


# ---------------------------------------------------------------------------
# Stale-string regression tests — must NOT appear (outside migration context)
# ---------------------------------------------------------------------------

def test_no_stale_specterqa_ios_package_reference() -> None:
    """
    README must not instruct users to install specterqa-ios.

    Historical context: specterqa-ios was the PyPI name for the 16.x development
    cycle. The 1.0 public name is simdrive. Any 'pip install specterqa-ios' in
    the README is a stale rename artifact that would send users to the wrong package.

    Migration references (e.g. 'pip uninstall specterqa-ios') are OK only in
    clearly-marked migration sections. This test checks for the install direction,
    not any mention of the old name.
    """
    full = _readme_full()
    # The README must not tell users to *install* the old package name
    assert "pip install specterqa-ios" not in full, (
        "README stale-string regression: 'pip install specterqa-ios' found. "
        "The public package name is 'simdrive'. Remove or move to the migration doc."
    )


def test_no_codename_framing() -> None:
    """README must not use the '(codename:' framing — simdrive is the product name."""
    full = _readme_full()
    assert "(codename:" not in full, (
        "README stale-string regression: '(codename:' framing found. "
        "simdrive is the product name, not a codename. Remove this framing."
    )


def test_no_api_key_required_for_mcp_framing() -> None:
    """
    README must not claim ANTHROPIC_API_KEY is required for the MCP flow.

    Since 1.0.0a4 (MCPSamplingLLMClient), run_journey delegates to the connected
    MCP client via MCP sampling. No API key is needed when driving via Claude Code
    or any sampling-capable MCP client. If this string appears without appropriate
    'standalone CLI only' context, it misleads agent users.
    """
    full = _readme_full()
    # The API key env var must not appear in a "you need this" framing
    # (it may appear in CHANGELOG entries or historical notes — check for the
    #  pattern that would mislead a new user)
    misleading_patterns = [
        "ANTHROPIC_API_KEY is required",
        "requires ANTHROPIC_API_KEY",
        "set ANTHROPIC_API_KEY",
        "export ANTHROPIC_API_KEY",
    ]
    for pattern in misleading_patterns:
        assert pattern not in full, (
            f"README stale-string regression: '{pattern}' found. "
            f"As of 1.0.0a4, ANTHROPIC_API_KEY is NOT required for the MCP flow "
            f"(run_journey uses MCP sampling). Only the standalone 'simdrive run' CLI "
            f"needs it. Update the framing."
        )
