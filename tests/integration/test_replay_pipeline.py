"""Integration tests for the replay pipeline and Maestro YAML alias normalization.

Sources:
  - ReplayRecorder/ReplayPlayer real file-I/O tests: extracted from test_integration_smoke.py
    (TestFullRecordReplayCycle) — these use tmp_path for real file operations, no mocks.
  - Maestro YAML alias normalization: extracted from test_v11_features.py
    (TestMaestroYAMLAliases) — pure logic tests, no mocks.

Run:
    pytest tests/integration/test_replay_pipeline.py -v --tb=short
"""

from __future__ import annotations

import pytest


# ===========================================================================
# TestFullRecordReplayCycle — real file I/O, no mocks
# ===========================================================================


class TestFullRecordReplayCycle:
    """Verify recording an MCP session and replaying it works via real YAML I/O."""

    def test_record_save_load_replay(self, tmp_path):
        """Record steps via recorder API, save to YAML, load with player, verify."""
        from specterqa.ios.replay import ReplayPlayer, ReplayRecorder

        # Record
        r = ReplayRecorder(bundle_id="com.test.app")
        r.record_tap(1, "Settings", 100.0, 200.0)
        r.record_swipe("down")
        r.record_swipe_back()
        r.record_type("hello")
        r.record_press_key("return")
        r.add_checkpoint(["General", "About"])

        # Save
        replay_path = tmp_path / "test.yaml"
        r.save(str(replay_path), name="integration-test")
        assert replay_path.exists()

        # Load
        player = ReplayPlayer(str(replay_path))
        assert player.bundle_id == "com.test.app"
        assert player.name == "integration-test"
        # 5 action steps (add_checkpoint attaches to last step, not a separate step)
        assert len(player.steps) == 5
        assert player.steps[0]["action"] == "tap"
        assert player.steps[0]["element_label"] == "Settings"

    def test_record_save_load_replay_step_count_matches_actions(self, tmp_path):
        """add_checkpoint does not add a step; step count == action calls."""
        from specterqa.ios.replay import ReplayPlayer, ReplayRecorder

        r = ReplayRecorder(bundle_id="com.bundle.app")
        r.record_tap(0, "OK", 10, 20)
        r.record_type("world")
        r.add_checkpoint(["Success"])

        out = tmp_path / "cp.yaml"
        r.save(str(out))

        player = ReplayPlayer(str(out))
        assert len(player.steps) == 2  # tap + type; checkpoint is metadata

    def test_replay_yaml_roundtrip_preserves_all_fields(self, tmp_path):
        """All ReplayStep fields should survive YAML round-trip."""
        from specterqa.ios.replay import ReplayPlayer, ReplayRecorder

        r = ReplayRecorder(bundle_id="com.test")
        r.record_long_press(1, "Cell", 50.0, 50.0, 1.5)
        r.add_checkpoint(["Action Sheet", "Cancel"])

        out = tmp_path / "rt.yaml"
        r.save(str(out))

        player = ReplayPlayer(str(out))
        step = player.steps[0]
        assert step["action"] == "long_press"
        assert step["duration"] == 1.5
        assert step["element_label"] == "Cell"
        # Checkpoint should be attached as expect_elements on the step
        assert "Action Sheet" in step.get("expect_elements", [])
        assert "Cancel" in step.get("expect_elements", [])

    def test_bundle_id_preserved_across_save_load(self, tmp_path):
        """Bundle ID written to YAML and read back correctly."""
        from specterqa.ios.replay import ReplayPlayer, ReplayRecorder

        r = ReplayRecorder(bundle_id="com.example.MyApp")
        r.record_swipe("up")

        out = tmp_path / "bid.yaml"
        r.save(str(out))

        player = ReplayPlayer(str(out))
        assert player.bundle_id == "com.example.MyApp"

    def test_all_recorded_action_types_survive_roundtrip(self, tmp_path):
        """Every action recorder method produces a loadable step."""
        from specterqa.ios.replay import ReplayPlayer, ReplayRecorder

        r = ReplayRecorder(bundle_id="com.all.actions")
        r.record_tap(1, "Btn", 10, 20)
        r.record_swipe("left")
        r.record_swipe_back()
        r.record_type("abc")
        r.record_press_key("delete")
        r.record_long_press(2, "Cell", 30, 40, 2.0)

        out = tmp_path / "all_actions.yaml"
        r.save(str(out))

        player = ReplayPlayer(str(out))
        actions = [s["action"] for s in player.steps]
        assert "tap" in actions
        assert "swipe" in actions
        assert "swipe_back" in actions
        assert "type" in actions
        assert "press_key" in actions
        assert "long_press" in actions

    def test_save_creates_parent_directories(self, tmp_path):
        """ReplayRecorder.save() creates nested directories as needed."""
        from specterqa.ios.replay import ReplayRecorder

        r = ReplayRecorder(bundle_id="com.test")
        r.record_tap(0, "X", 0, 0)

        deep = tmp_path / "a" / "b" / "c" / "replay.yaml"
        r.save(str(deep))
        assert deep.exists()

    def test_empty_session_saves_and_loads(self, tmp_path):
        """A recorder with no steps should still produce a valid YAML file."""
        from specterqa.ios.replay import ReplayPlayer, ReplayRecorder

        r = ReplayRecorder(bundle_id="com.empty")
        out = tmp_path / "empty.yaml"
        r.save(str(out))

        player = ReplayPlayer(str(out))
        assert player.bundle_id == "com.empty"
        assert player.steps == []


# ===========================================================================
# TestMaestroYAMLAliases — pure logic, no mocks
# ===========================================================================


class TestMaestroYAMLAliases:
    """Verify Maestro-compatible YAML syntax normalizes correctly.

    Extracted from test_v11_features.py — these test pure normalization logic
    in ReplayPlayer._normalize_maestro_step(), no I/O or mocks required.
    """

    def test_tapOn_normalizes_to_tap(self):
        from specterqa.ios.replay import ReplayPlayer

        step = {"tapOn": "Save"}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())
        assert normalized["action"] == "tap"
        assert normalized["element_label"] == "Save"

    def test_assertVisible_single_string_normalizes(self):
        from specterqa.ios.replay import ReplayPlayer

        step = {"assertVisible": "Welcome"}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())
        assert normalized["action"] == "assert"
        assert "Welcome" in normalized["expect_elements"]

    def test_assertVisible_list_normalizes(self):
        from specterqa.ios.replay import ReplayPlayer

        step = {"assertVisible": ["Welcome", "Continue"]}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())
        assert normalized["action"] == "assert"
        assert "Welcome" in normalized["expect_elements"]
        assert "Continue" in normalized["expect_elements"]

    def test_assertNotVisible_single_string_normalizes(self):
        from specterqa.ios.replay import ReplayPlayer

        step = {"assertNotVisible": "Error"}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())
        assert normalized["action"] == "assert"
        assert "Error" in normalized["expect_not_elements"]

    def test_assertNotVisible_list_normalizes(self):
        from specterqa.ios.replay import ReplayPlayer

        step = {"assertNotVisible": ["Error", "Spinner"]}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())
        assert normalized["action"] == "assert"
        assert "Error" in normalized["expect_not_elements"]
        assert "Spinner" in normalized["expect_not_elements"]

    def test_inputText_normalizes(self):
        from specterqa.ios.replay import ReplayPlayer

        step = {"inputText": "hello world"}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())
        assert normalized["action"] == "type"
        assert normalized["text"] == "hello world"

    def test_waitFor_normalizes(self):
        from specterqa.ios.replay import ReplayPlayer

        step = {"waitFor": "Loading"}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())
        assert normalized["action"] == "wait_for_element"
        assert normalized["label"] == "Loading"

    def test_normalize_does_not_mutate_original(self):
        """Normalization must work on a copy; the caller's original dict is untouched."""
        from specterqa.ios.replay import ReplayPlayer

        original = {"tapOn": "Save"}
        copy_for_normalization = original.copy()
        ReplayPlayer._normalize_maestro_step(copy_for_normalization)
        # original should be unchanged
        assert "tapOn" in original
        assert "action" not in original

    def test_native_action_not_overridden_by_tapOn(self):
        """If a step already has an action, tapOn must not clobber it."""
        from specterqa.ios.replay import ReplayPlayer

        step = {"action": "swipe", "tapOn": "Ignored"}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())
        # setdefault means existing 'action' wins
        assert normalized["action"] == "swipe"

    def test_empty_assertVisible_list(self):
        from specterqa.ios.replay import ReplayPlayer

        step = {"assertVisible": []}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())
        assert normalized["action"] == "assert"
        assert normalized["expect_elements"] == []

    def test_step_with_unknown_extra_keys_passes_through(self):
        from specterqa.ios.replay import ReplayPlayer

        step = {"tapOn": "OK", "custom_key": "ignored"}
        normalized = ReplayPlayer._normalize_maestro_step(step.copy())
        assert normalized["action"] == "tap"
        assert "custom_key" in normalized  # unknown keys preserved, not stripped
