"""M17a: IOSMCPServer — MCP tool server for iOS Simulator testing.

Exposes iOS simulator capabilities as MCP tools: run_test, run_exploratory,
get_results, list_devices, and list_scenarios.

INIT-2026-492 — SpecterQA iOS Simulator Driver, Phase 4.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from typing import Any, Callable


class IOSMCPServer:
    """MCP tool server that wraps iOS simulator testing capabilities.

    Args:
        pool: Optional SimulatorPool for device acquisition/release.
        step_runner_factory: Optional callable that takes a simulator and
            returns a step runner instance.
    """

    def __init__(
        self,
        pool: Any = None,
        step_runner_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._pool = pool
        self._step_runner_factory = step_runner_factory
        self._results: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # MCP tool manifest
    # ------------------------------------------------------------------

    def _register_tools(self) -> list[dict[str, Any]]:
        """Return the MCP tool manifest for this server.

        Returns:
            List of tool definition dicts, each with 'name', 'description',
            and 'parameters' keys.
        """
        return [
            {
                "name": "run_test",
                "description": "Run a structured test scenario on an iOS Simulator.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scenario": {
                            "type": "object",
                            "description": "Scenario dict with id, name, and steps.",
                        },
                        "device_name": {
                            "type": "string",
                            "description": "Optional simulator device name.",
                        },
                    },
                    "required": ["scenario"],
                },
            },
            {
                "name": "run_exploratory",
                "description": "Run persona-driven exploratory testing on an iOS app.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "persona": {
                            "type": "object",
                            "description": "Persona definition (name, role, goals, traits).",
                        },
                        "app_context": {
                            "type": "string",
                            "description": "Description of the app's current state.",
                        },
                        "device_name": {
                            "type": "string",
                            "description": "Optional simulator device name.",
                        },
                    },
                    "required": ["persona"],
                },
            },
            {
                "name": "get_results",
                "description": "Retrieve stored results for a completed run.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "run_id": {
                            "type": "string",
                            "description": "The run ID returned by run_test or run_exploratory.",
                        },
                    },
                    "required": ["run_id"],
                },
            },
            {
                "name": "list_devices",
                "description": "List available iOS Simulator devices via xcrun simctl.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "list_scenarios",
                "description": "List available test scenarios registered with this server.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        ]

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    def run_test(
        self,
        scenario: dict[str, Any],
        device_name: str | None = None,
    ) -> dict[str, Any]:
        """Run a structured test scenario, acquiring a simulator from the pool.

        Args:
            scenario: Scenario dict with at minimum an 'id' and 'steps' key.
            device_name: Optional hint for which device to acquire.

        Returns:
            Dict with 'run_id', 'results', and 'device_id' keys.

        Raises:
            Any exception raised by the step runner (pool is released first).
        """
        if self._pool is None:
            run_id = str(uuid.uuid4())
            result = {"run_id": run_id, "results": [], "device_id": None}
            self._results[run_id] = result
            return result

        sim = self._pool.acquire()
        try:
            steps = scenario.get("steps", [])
            step_results: list[Any] = []

            if self._step_runner_factory is not None:
                runner = self._step_runner_factory(sim)
                for step in steps:
                    step_result = runner.run_step(step)
                    step_results.append(step_result)

            run_id = str(uuid.uuid4())
            result: dict[str, Any] = {
                "run_id": run_id,
                "results": step_results,
                "device_id": getattr(sim, "udid", None),
            }
            self._results[run_id] = result
            return result
        finally:
            self._pool.release(sim)

    def run_exploratory(
        self,
        persona: dict[str, Any],
        app_context: str = "",
        device_name: str | None = None,
    ) -> dict[str, Any]:
        """Run persona-driven exploratory testing using ExploratoryAgent.

        Args:
            persona: Persona dict (name, role, goals, traits).
            app_context: Description of the app's current state.
            device_name: Optional device hint.

        Returns:
            Exploration result dict from ExploratoryAgent.explore().
        """
        # Import here to avoid circular imports at module load time
        from specterqa.ios.exploratory.agent import ExploratoryAgent

        if self._pool is not None:
            sim = self._pool.acquire()
            try:
                runner = (
                    self._step_runner_factory(sim)
                    if self._step_runner_factory is not None
                    else _NullStepRunner()
                )
                agent = ExploratoryAgent(
                    step_runner=runner,
                    persona=persona,
                )
                result = agent.explore(app_context=app_context)
            finally:
                self._pool.release(sim)
        else:
            runner = (
                self._step_runner_factory()
                if self._step_runner_factory is not None
                else _NullStepRunner()
            )
            agent = ExploratoryAgent(
                step_runner=runner,
                persona=persona,
            )
            result = agent.explore(app_context=app_context)

        run_id = str(uuid.uuid4())
        result["run_id"] = run_id
        self._results[run_id] = result
        return result

    def get_results(self, run_id: str) -> Any:
        """Retrieve stored results for a completed run.

        Args:
            run_id: The run ID returned by run_test or run_exploratory.

        Returns:
            The stored result dict.

        Raises:
            KeyError: If run_id is not found in the result store.
        """
        if run_id not in self._results:
            raise KeyError(f"No results found for run_id: {run_id!r}")
        return self._results[run_id]

    def list_devices(self) -> list[dict[str, Any]]:
        """List available iOS Simulator devices using xcrun simctl.

        Returns:
            List of device dicts parsed from simctl JSON output.
        """
        proc = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "--json"],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return []
        try:
            data = json.loads(proc.stdout)
        except (json.JSONDecodeError, ValueError):
            return []

        devices: list[dict[str, Any]] = []
        for runtime_devices in data.get("devices", {}).values():
            for device in runtime_devices:
                devices.append(device)
        return devices

    def list_scenarios(self) -> list[dict[str, Any]]:
        """Return all scenarios registered with this server.

        Base implementation returns an empty list. Subclasses may override to
        expose a scenario registry.

        Returns:
            List of scenario dicts.
        """
        return []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class _NullStepRunner:
    """Minimal no-op step runner used when no factory is configured."""

    def run_step(self, goal: Any = None, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "success": True,
            "finding": None,
            "covered_area": "unknown",
            "cost": 0.0,
            "critical": False,
        }
