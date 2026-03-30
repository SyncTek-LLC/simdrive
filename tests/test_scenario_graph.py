"""Tests for M12: ScenarioGraph — prefix-trie-based execution planner.

TDD Phase 3 — INIT-2026-492, SpecterQA iOS Simulator Driver.
These tests are written BEFORE the implementation exists and must be
importable even when the implementation module is absent.

Module under test (to be created by CodeAtlas):
    specterqa/ios/engine/scenario_graph.py  — ScenarioGraph, ScenarioNode, ExecutionPlan
"""

from __future__ import annotations

import dataclasses

import pytest

# ---------------------------------------------------------------------------
# Conditional import guard
# ---------------------------------------------------------------------------

try:
    from specterqa.ios.engine.scenario_graph import (  # type: ignore[import]
        ExecutionPlan,
        ScenarioGraph,
        ScenarioNode,
    )
    _SCENARIO_GRAPH_AVAILABLE = True
except ImportError:
    _SCENARIO_GRAPH_AVAILABLE = False
    ScenarioGraph = None  # type: ignore[assignment,misc]
    ScenarioNode = None  # type: ignore[assignment,misc]
    ExecutionPlan = None  # type: ignore[assignment,misc]

needs_scenario_graph = pytest.mark.skipif(
    not _SCENARIO_GRAPH_AVAILABLE,
    reason="specterqa.ios.engine.scenario_graph not yet implemented",
)

# ---------------------------------------------------------------------------
# Helpers — build scenario dicts
# ---------------------------------------------------------------------------


def _make_scenario(scenario_id: str, step_ids: list[str], goals: list[str] | None = None) -> dict:
    """Build a scenario dict with the given step IDs."""
    if goals is None:
        goals = [f"Complete step {sid}" for sid in step_ids]
    steps = [
        {"id": sid, "goal": goal}
        for sid, goal in zip(step_ids, goals)
    ]
    return {"id": scenario_id, "steps": steps}


# ===========================================================================
# Test 1: ScenarioNode dataclass has all fields
# ===========================================================================


@needs_scenario_graph
class TestScenarioNodeDataclass:
    """ScenarioNode must be a dataclass with all required fields."""

    def test_scenario_node_is_dataclass(self):
        """ScenarioNode must be decorated with @dataclass."""
        assert dataclasses.is_dataclass(ScenarioNode)

    def test_scenario_node_has_required_fields(self):
        """ScenarioNode must have: node_id, step, scenarios_requiring,
        children, is_fork_point."""
        fields = {f.name for f in dataclasses.fields(ScenarioNode)}
        required = {"node_id", "step", "scenarios_requiring", "children", "is_fork_point"}
        assert required.issubset(fields), (
            f"ScenarioNode missing fields: {required - fields}"
        )

    def test_is_fork_point_defaults_false(self):
        """is_fork_point must default to False."""
        node = ScenarioNode(
            node_id="step-1",
            step={"id": "step-1", "goal": "Do something"},
            scenarios_requiring={"scen-1"},
            children=[],
        )
        assert node.is_fork_point is False


# ===========================================================================
# Test 2: ExecutionPlan dataclass has all fields
# ===========================================================================


@needs_scenario_graph
class TestExecutionPlanDataclass:
    """ExecutionPlan must be a dataclass with all required fields."""

    def test_execution_plan_is_dataclass(self):
        """ExecutionPlan must be decorated with @dataclass."""
        assert dataclasses.is_dataclass(ExecutionPlan)

    def test_execution_plan_has_required_fields(self):
        """ExecutionPlan must have: nodes, fork_points, shared_prefix_depth,
        scenario_branches."""
        fields = {f.name for f in dataclasses.fields(ExecutionPlan)}
        required = {"nodes", "fork_points", "shared_prefix_depth", "scenario_branches"}
        assert required.issubset(fields), (
            f"ExecutionPlan missing fields: {required - fields}"
        )


# ===========================================================================
# Test 3: build_trie with single scenario returns linear trie
# ===========================================================================


@needs_scenario_graph
class TestBuildTrieSingleScenario:
    """With a single scenario, build_trie must return a linear (non-branching) trie."""

    def test_single_scenario_produces_linear_trie(self):
        """A single scenario with 3 steps should produce a trie where every
        node has at most one child."""
        scenarios = [
            _make_scenario("scen-1", ["step-A", "step-B", "step-C"])
        ]
        graph = ScenarioGraph(scenarios)
        root = graph.build_trie()

        # Traverse the trie and check no branching
        def _max_branching(node) -> int:
            if not node.children:
                return 1
            return max(len(node.children), max(_max_branching(c) for c in node.children))

        # Root may have one child (step-A); no node should have more than 1 child
        assert _max_branching(root) == 1

    def test_single_scenario_trie_depth_matches_step_count(self):
        """A linear trie for 3 steps must have depth 3 (excluding virtual root if any)."""
        scenarios = [
            _make_scenario("scen-1", ["step-A", "step-B", "step-C"])
        ]
        graph = ScenarioGraph(scenarios)
        root = graph.build_trie()

        # Count trie depth by following first child at each level
        def _depth(node) -> int:
            if not node.children:
                return 1
            return 1 + _depth(node.children[0])

        # Root depth should accommodate all 3 steps
        assert _depth(root) >= 3


# ===========================================================================
# Test 4: build_trie with two scenarios sharing prefix builds correct trie
# ===========================================================================


@needs_scenario_graph
class TestBuildTrieSharedPrefix:
    """Two scenarios sharing a common prefix must merge at the shared nodes."""

    def test_shared_prefix_produces_fork_at_divergence(self):
        """With scen-1=[A,B,C] and scen-2=[A,B,D], the node for 'step-B' must
        have exactly 2 children (one for C, one for D)."""
        scenarios = [
            _make_scenario("scen-1", ["step-A", "step-B", "step-C"]),
            _make_scenario("scen-2", ["step-A", "step-B", "step-D"]),
        ]
        graph = ScenarioGraph(scenarios)
        root = graph.build_trie()

        # Find node for step-B by traversing A -> B
        def _find_node(node, node_id: str, depth: int = 3):
            if node.node_id == node_id:
                return node
            if depth == 0:
                return None
            for child in node.children:
                found = _find_node(child, node_id, depth - 1)
                if found:
                    return found
            return None

        node_b = _find_node(root, "step-B")
        assert node_b is not None, "step-B node not found in trie"
        assert len(node_b.children) == 2, (
            f"Expected 2 children at fork point, got {len(node_b.children)}"
        )

    def test_shared_prefix_node_scenarios_requiring_contains_both(self):
        """The shared node 'step-A' must have both scenario IDs in scenarios_requiring."""
        scenarios = [
            _make_scenario("scen-1", ["step-A", "step-B"]),
            _make_scenario("scen-2", ["step-A", "step-C"]),
        ]
        graph = ScenarioGraph(scenarios)
        root = graph.build_trie()

        def _find_node(node, node_id: str, depth: int = 5):
            if node.node_id == node_id:
                return node
            for child in node.children:
                found = _find_node(child, node_id, depth - 1)
                if found:
                    return found
            return None

        node_a = _find_node(root, "step-A")
        assert node_a is not None
        assert "scen-1" in node_a.scenarios_requiring
        assert "scen-2" in node_a.scenarios_requiring


# ===========================================================================
# Test 5: build_trie with no shared prefix returns immediate fork
# ===========================================================================


@needs_scenario_graph
class TestBuildTrieNoSharedPrefix:
    """Two scenarios with no shared steps must fork immediately at the root."""

    def test_no_shared_prefix_root_has_two_children(self):
        """With scen-1=[A,B] and scen-2=[C,D] (no overlap), the trie root
        must have 2 immediate children."""
        scenarios = [
            _make_scenario("scen-1", ["step-A", "step-B"]),
            _make_scenario("scen-2", ["step-C", "step-D"]),
        ]
        graph = ScenarioGraph(scenarios)
        root = graph.build_trie()

        # Root children represent the first step of each scenario
        assert len(root.children) == 2


# ===========================================================================
# Test 6: find_fork_points returns empty for single scenario
# ===========================================================================


@needs_scenario_graph
class TestFindForkPointsSingleScenario:
    """With only one scenario, there are no fork points."""

    def test_single_scenario_no_fork_points(self):
        """find_fork_points must return an empty list for a single scenario."""
        scenarios = [_make_scenario("scen-1", ["step-A", "step-B", "step-C"])]
        graph = ScenarioGraph(scenarios)
        graph.build_trie()
        forks = graph.find_fork_points()
        assert forks == []


# ===========================================================================
# Test 7: find_fork_points identifies fork at correct depth
# ===========================================================================


@needs_scenario_graph
class TestFindForkPointsCorrectDepth:
    """find_fork_points must identify nodes where branching occurs."""

    def test_fork_at_depth_two(self):
        """With scen-1=[A,B,C] and scen-2=[A,B,D], the fork is at step-B (depth 2)."""
        scenarios = [
            _make_scenario("scen-1", ["step-A", "step-B", "step-C"]),
            _make_scenario("scen-2", ["step-A", "step-B", "step-D"]),
        ]
        graph = ScenarioGraph(scenarios)
        graph.build_trie()
        forks = graph.find_fork_points()

        assert len(forks) >= 1
        fork_ids = {n.node_id for n in forks}
        assert "step-B" in fork_ids, (
            f"Expected step-B as fork point, got: {fork_ids}"
        )

    def test_multiple_scenarios_fork_at_first_divergence(self):
        """With 3 scenarios sharing first 2 steps, the fork point is at step 2."""
        scenarios = [
            _make_scenario("scen-1", ["common-1", "common-2", "branch-X"]),
            _make_scenario("scen-2", ["common-1", "common-2", "branch-Y"]),
            _make_scenario("scen-3", ["common-1", "common-2", "branch-Z"]),
        ]
        graph = ScenarioGraph(scenarios)
        graph.build_trie()
        forks = graph.find_fork_points()

        fork_ids = {n.node_id for n in forks}
        assert "common-2" in fork_ids


# ===========================================================================
# Test 8: plan returns ExecutionPlan with correct shared_prefix_depth
# ===========================================================================


@needs_scenario_graph
class TestPlanSharedPrefixDepth:
    """plan() must return an ExecutionPlan with the correct shared_prefix_depth."""

    def test_plan_shared_prefix_depth_two(self):
        """With 2 shared steps, shared_prefix_depth must be 2."""
        scenarios = [
            _make_scenario("scen-1", ["step-A", "step-B", "step-C"]),
            _make_scenario("scen-2", ["step-A", "step-B", "step-D"]),
        ]
        graph = ScenarioGraph(scenarios)
        plan = graph.plan()

        assert isinstance(plan, ExecutionPlan)
        assert plan.shared_prefix_depth == 2


# ===========================================================================
# Test 9: plan.shared_prefix_depth is 0 when no shared steps
# ===========================================================================


@needs_scenario_graph
class TestPlanSharedPrefixDepthZero:
    """When no steps are shared, shared_prefix_depth must be 0."""

    def test_no_shared_steps_prefix_depth_zero(self):
        """Scenarios with no common steps must yield shared_prefix_depth == 0."""
        scenarios = [
            _make_scenario("scen-1", ["step-A", "step-B"]),
            _make_scenario("scen-2", ["step-C", "step-D"]),
        ]
        graph = ScenarioGraph(scenarios)
        plan = graph.plan()

        assert plan.shared_prefix_depth == 0


# ===========================================================================
# Test 10: plan.scenario_branches contains all scenario IDs
# ===========================================================================


@needs_scenario_graph
class TestPlanScenarioBranches:
    """plan.scenario_branches must have an entry for every scenario ID."""

    def test_all_scenario_ids_in_branches(self):
        """Every scenario ID must appear as a key in ExecutionPlan.scenario_branches."""
        scenarios = [
            _make_scenario("scen-alpha", ["step-A", "step-B", "step-C"]),
            _make_scenario("scen-beta", ["step-A", "step-B", "step-D"]),
            _make_scenario("scen-gamma", ["step-E", "step-F"]),
        ]
        graph = ScenarioGraph(scenarios)
        plan = graph.plan()

        assert "scen-alpha" in plan.scenario_branches
        assert "scen-beta" in plan.scenario_branches
        assert "scen-gamma" in plan.scenario_branches


# ===========================================================================
# Test 11: plan.fork_points matches find_fork_points
# ===========================================================================


@needs_scenario_graph
class TestPlanForkPointsConsistency:
    """plan.fork_points must match the output of find_fork_points()."""

    def test_plan_fork_points_matches_find_fork_points(self):
        """ExecutionPlan.fork_points must be equal to graph.find_fork_points()."""
        scenarios = [
            _make_scenario("scen-1", ["step-A", "step-B", "step-C"]),
            _make_scenario("scen-2", ["step-A", "step-B", "step-D"]),
        ]
        graph = ScenarioGraph(scenarios)
        plan = graph.plan()
        forks = graph.find_fork_points()

        plan_fork_ids = {n.node_id for n in plan.fork_points}
        find_fork_ids = {n.node_id for n in forks}
        assert plan_fork_ids == find_fork_ids


# ===========================================================================
# Test 12: shared_prefix_depth returns correct count for 3-step shared prefix
# ===========================================================================


@needs_scenario_graph
class TestSharedPrefixDepthMethod:
    """shared_prefix_depth() method must return the correct integer."""

    def test_three_step_shared_prefix(self):
        """With 3 common steps before divergence, shared_prefix_depth() == 3."""
        scenarios = [
            _make_scenario("scen-1", ["s1", "s2", "s3", "branch-A"]),
            _make_scenario("scen-2", ["s1", "s2", "s3", "branch-B"]),
        ]
        graph = ScenarioGraph(scenarios)
        depth = graph.shared_prefix_depth()
        assert depth == 3

    def test_one_step_shared_prefix(self):
        """With only 1 common step, shared_prefix_depth() == 1."""
        scenarios = [
            _make_scenario("scen-1", ["common", "branch-A", "branch-B"]),
            _make_scenario("scen-2", ["common", "branch-C", "branch-D"]),
        ]
        graph = ScenarioGraph(scenarios)
        depth = graph.shared_prefix_depth()
        assert depth == 1


# ===========================================================================
# Test 13: Scenarios with identical steps have full shared prefix
# ===========================================================================


@needs_scenario_graph
class TestIdenticalScenariosFullSharedPrefix:
    """When two scenarios have identical step IDs, all steps are shared."""

    def test_identical_scenarios_full_shared_prefix(self):
        """Two scenarios with the same steps must have shared_prefix_depth equal
        to the number of steps."""
        scenarios = [
            _make_scenario("scen-1", ["step-A", "step-B", "step-C"]),
            _make_scenario("scen-2", ["step-A", "step-B", "step-C"]),
        ]
        graph = ScenarioGraph(scenarios)
        depth = graph.shared_prefix_depth()
        assert depth == 3

    def test_identical_scenarios_no_fork_points(self):
        """Two identical scenarios must produce no fork points."""
        scenarios = [
            _make_scenario("scen-1", ["step-A", "step-B"]),
            _make_scenario("scen-2", ["step-A", "step-B"]),
        ]
        graph = ScenarioGraph(scenarios)
        graph.build_trie()
        forks = graph.find_fork_points()
        assert forks == []


# ===========================================================================
# Test 14: Empty scenarios handled gracefully
# ===========================================================================


@needs_scenario_graph
class TestEmptyScenariosHandled:
    """ScenarioGraph must not raise when given empty or near-empty scenarios."""

    def test_empty_scenario_list(self):
        """ScenarioGraph([]) must not raise and plan() must return a valid plan."""
        graph = ScenarioGraph([])
        plan = graph.plan()
        assert isinstance(plan, ExecutionPlan)
        assert plan.shared_prefix_depth == 0
        assert plan.scenario_branches == {}
        assert plan.fork_points == []

    def test_scenario_with_no_steps(self):
        """A scenario with an empty steps list must not cause an error."""
        scenarios = [{"id": "empty-scen", "steps": []}]
        graph = ScenarioGraph(scenarios)
        plan = graph.plan()
        assert isinstance(plan, ExecutionPlan)


# ===========================================================================
# Test 15: Single-step scenarios handled correctly
# ===========================================================================


@needs_scenario_graph
class TestSingleStepScenarios:
    """ScenarioGraph must handle single-step scenarios correctly."""

    def test_two_single_step_scenarios_different_steps(self):
        """Two single-step scenarios with different step IDs must fork immediately:
        shared_prefix_depth == 0, 2 fork entries (or depth 0)."""
        scenarios = [
            _make_scenario("scen-1", ["only-A"]),
            _make_scenario("scen-2", ["only-B"]),
        ]
        graph = ScenarioGraph(scenarios)
        depth = graph.shared_prefix_depth()
        assert depth == 0

    def test_two_single_step_scenarios_same_step(self):
        """Two single-step scenarios with the same step ID must share fully:
        shared_prefix_depth == 1."""
        scenarios = [
            _make_scenario("scen-1", ["shared-step"]),
            _make_scenario("scen-2", ["shared-step"]),
        ]
        graph = ScenarioGraph(scenarios)
        depth = graph.shared_prefix_depth()
        assert depth == 1

    def test_single_scenario_single_step_plan(self):
        """A plan from a single one-step scenario must have shared_prefix_depth == 1
        and one branch entry."""
        scenarios = [_make_scenario("scen-solo", ["only-step"])]
        graph = ScenarioGraph(scenarios)
        plan = graph.plan()

        assert isinstance(plan, ExecutionPlan)
        assert "scen-solo" in plan.scenario_branches
        # Single scenario — no forks
        assert plan.fork_points == []
