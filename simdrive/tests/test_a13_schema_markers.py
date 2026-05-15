"""a13 schema marker tests.

Tests 16-20: verify that record_start, record_stop, replay, validate_replay,
and list_replays are all marked '(sim + device)' in _TOOLS descriptions after a13.

These tests FAIL on feat/v17-claude-native (HEAD) because:
  - record_start: '(sim only) Begin recording...'
  - record_stop: '(sim only) Finalize...'
  - replay: '(sim only) Replay...'
  - list_replays: '(sim only) List saved replay...'
  - validate_replay: '(sim only) Structural validation...'

All tests PASS after merging feat/simdrive-a13-device-record-replay, which
updates the descriptions to '(sim + device)'.

Note: test_a12_schema_targets.py test 7 (test_record_start_still_marked_sim_only)
will regress after the a13 merge — this is expected and intentional. The a13
merge also updates that marker. The test_a12_schema_targets test must be updated
alongside the merge.
"""
from __future__ import annotations

import pytest


def _get_tool_description(name: str) -> str:
    import simdrive.server as server_mod
    tools = {t["name"]: t for t in server_mod._TOOLS}
    if name not in tools:
        pytest.skip(f"Tool '{name}' not in _TOOLS — may not be registered yet in this branch")
    return tools[name].get("description", "")


# ─── Test 16 ───────────────────────────────────────────────────────────────


def test_record_start_marker_is_sim_and_device():
    """record_start description must contain '(sim + device)' in a13."""
    desc = _get_tool_description("record_start")
    assert "(sim + device)" in desc, (
        f"record_start must be marked '(sim + device)' after a13, got: {desc!r}"
    )


# ─── Test 17 ───────────────────────────────────────────────────────────────


def test_record_stop_marker_is_sim_and_device():
    """record_stop description must contain '(sim + device)' in a13."""
    desc = _get_tool_description("record_stop")
    assert "(sim + device)" in desc, (
        f"record_stop must be marked '(sim + device)' after a13, got: {desc!r}"
    )


# ─── Test 18 ───────────────────────────────────────────────────────────────


def test_replay_marker_is_sim_and_device():
    """replay description must contain '(sim + device)' in a13."""
    desc = _get_tool_description("replay")
    assert "(sim + device)" in desc, (
        f"replay must be marked '(sim + device)' after a13, got: {desc!r}"
    )


# ─── Test 19 ───────────────────────────────────────────────────────────────


def test_validate_replay_marker_is_sim_and_device():
    """validate_replay description must contain '(sim + device)' in a13."""
    desc = _get_tool_description("validate_replay")
    assert "(sim + device)" in desc, (
        f"validate_replay must be marked '(sim + device)' after a13, got: {desc!r}"
    )


# ─── Test 20 ───────────────────────────────────────────────────────────────


def test_list_replays_marker_is_sim_and_device():
    """list_replays description must contain '(sim + device)' in a13."""
    desc = _get_tool_description("list_replays")
    assert "(sim + device)" in desc, (
        f"list_replays must be marked '(sim + device)' after a13, got: {desc!r}"
    )
