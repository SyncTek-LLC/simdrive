"""M12: ScenarioGraph — prefix-trie-based execution planner.

Builds a prefix trie over scenario step sequences to identify shared
execution prefixes, fork points, and per-scenario branches.

INIT-2026-492 — SpecterQA iOS Simulator Driver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ScenarioNode:
    """A single node in the scenario prefix trie.

    Attributes:
        node_id: The step ID this node represents.
        step: The full step dict ({"id": ..., "goal": ...}).
        scenarios_requiring: Set of scenario IDs that pass through this node.
        children: Child nodes (next steps in any scenario's path).
        is_fork_point: True when this node has more than one child.
    """

    node_id: str
    step: dict
    scenarios_requiring: set
    children: List["ScenarioNode"] = field(default_factory=list)
    is_fork_point: bool = False


@dataclass
class ExecutionPlan:
    """The result of planning scenario execution via the trie.

    Attributes:
        nodes: All nodes in the trie (breadth-first order).
        fork_points: Nodes where the execution path branches.
        shared_prefix_depth: Number of steps shared by ALL scenarios before
            the first fork.
        scenario_branches: Maps each scenario ID to its branch-specific nodes
            (nodes after the shared prefix).
    """

    nodes: List[ScenarioNode]
    fork_points: List[ScenarioNode]
    shared_prefix_depth: int
    scenario_branches: Dict[str, List[ScenarioNode]]


class ScenarioGraph:
    """Builds and analyses a prefix trie over a set of test scenarios.

    Each scenario is a dict with:
        - "id": unique scenario identifier
        - "steps": list of step dicts, each with "id" and "goal"

    Steps are compared by their "id" field.  Two scenarios share a node
    whenever they both pass through a step with the same ID at the same
    trie depth.
    """

    def __init__(self, scenarios: list[dict]) -> None:
        self._scenarios: list[dict] = scenarios
        self._root: Optional[ScenarioNode] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_trie(self) -> ScenarioNode:
        """Build a prefix trie from all scenario step sequences.

        Returns:
            A virtual root ScenarioNode whose children are the first steps
            of each (unique) scenario entry point.
        """
        root = ScenarioNode(
            node_id="__root__",
            step={},
            scenarios_requiring=set(),
        )

        for scenario in self._scenarios:
            scen_id: str = scenario["id"]
            steps: list[dict] = scenario.get("steps", [])
            current = root
            current.scenarios_requiring.add(scen_id)

            for step in steps:
                step_id: str = step["id"]
                # Find an existing child with this step ID
                existing: Optional[ScenarioNode] = next((c for c in current.children if c.node_id == step_id), None)
                if existing is not None:
                    existing.scenarios_requiring.add(scen_id)
                    current = existing
                else:
                    new_node = ScenarioNode(
                        node_id=step_id,
                        step=step,
                        scenarios_requiring={scen_id},
                    )
                    current.children.append(new_node)
                    current = new_node

        # Mark fork points
        self._mark_fork_points(root)
        self._root = root
        return root

    def find_fork_points(self) -> list[ScenarioNode]:
        """Return all nodes where execution branches (child count > 1).

        build_trie() must be called before this method; if not, it is
        called implicitly.
        """
        if self._root is None:
            self.build_trie()
        return self._collect_fork_points(self._root)

    def shared_prefix_depth(self) -> int:
        """Return the number of steps shared by ALL scenarios before the
        first fork.

        A depth of 0 means scenarios diverge immediately (no common prefix).
        """
        if self._root is None:
            self.build_trie()
        return self._compute_shared_prefix_depth(self._root)

    def plan(self) -> ExecutionPlan:
        """Build and return a complete ExecutionPlan.

        Calls build_trie() internally; safe to call multiple times.
        """
        root = self.build_trie()
        fork_points = self.find_fork_points()
        depth = self._compute_shared_prefix_depth(root)
        all_nodes = self._collect_all_nodes(root)
        branches = self._build_scenario_branches(depth)

        return ExecutionPlan(
            nodes=all_nodes,
            fork_points=fork_points,
            shared_prefix_depth=depth,
            scenario_branches=branches,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _mark_fork_points(node: ScenarioNode) -> None:
        """Recursively mark every node whose child count > 1 as a fork point."""
        if len(node.children) > 1:
            node.is_fork_point = True
        for child in node.children:
            ScenarioGraph._mark_fork_points(child)

    def _collect_fork_points(self, root: ScenarioNode) -> list[ScenarioNode]:
        """BFS collection of all fork-point nodes (excluding virtual root)."""
        result: list[ScenarioNode] = []
        queue: list[ScenarioNode] = list(root.children)
        while queue:
            node = queue.pop(0)
            if node.is_fork_point:
                result.append(node)
            queue.extend(node.children)
        return result

    def _compute_shared_prefix_depth(self, root: ScenarioNode) -> int:
        """Walk the trie from the root, counting steps as long as there is
        exactly one child (i.e., all scenarios agree on the next step)."""
        depth = 0
        current = root
        while len(current.children) == 1:
            current = current.children[0]
            depth += 1
        return depth

    @staticmethod
    def _collect_all_nodes(root: ScenarioNode) -> list[ScenarioNode]:
        """BFS traversal returning all nodes except the virtual root."""
        result: list[ScenarioNode] = []
        queue: list[ScenarioNode] = list(root.children)
        while queue:
            node = queue.pop(0)
            result.append(node)
            queue.extend(node.children)
        return result

    def _build_scenario_branches(self, prefix_depth: int) -> dict[str, list[ScenarioNode]]:
        """For each scenario, collect the nodes AFTER the shared prefix.

        If a scenario has fewer steps than the shared prefix, its branch is
        an empty list.
        """
        branches: dict[str, list[ScenarioNode]] = {}
        for scenario in self._scenarios:
            scen_id = scenario["id"]
            steps = scenario.get("steps", [])
            branch_steps = steps[prefix_depth:]
            branch_nodes: list[ScenarioNode] = []
            # Walk the trie to collect the actual node objects for branch steps
            if self._root is not None and branch_steps:
                node = self._find_branch_start(self._root, prefix_depth)
                for step in branch_steps:
                    if node is None:
                        break
                    match = next((c for c in node.children if c.node_id == step["id"]), None)
                    if match is not None:
                        branch_nodes.append(match)
                        node = match
                    else:
                        break
            branches[scen_id] = branch_nodes
        return branches

    def _find_branch_start(self, root: ScenarioNode, depth: int) -> Optional[ScenarioNode]:
        """Descend *depth* levels along the single-child spine to reach the
        node just before the branches split.  Returns that node so callers
        can then look into children."""
        current = root
        for _ in range(depth):
            if not current.children:
                return None
            # On the shared prefix the trie has a single child per level
            current = current.children[0]
        return current
