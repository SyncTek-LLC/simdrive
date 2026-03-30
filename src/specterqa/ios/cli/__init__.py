"""SpecterQA iOS CLI extension — adds iOS simulator commands.

This module is discovered by the main ``specterqa`` CLI via the
``specterqa.cli_extensions`` entry-point declared in pyproject.toml.
The ``ios_command_group`` click.Group is mounted as ``specterqa ios``.
"""

from __future__ import annotations

from specterqa.ios.cli.commands import ios_command_group

__all__ = ["ios_command_group"]
