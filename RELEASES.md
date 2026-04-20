# SpecterQA iOS — Release Sequence (v14.x)

| Version   | PyPI status | Tag            | Notes                                                                                  |
|-----------|-------------|----------------|----------------------------------------------------------------------------------------|
| 14.0.0b1  | **skipped** | `v14.0.0b1`    | Tagged; publish workflow correctly failed. Wheel installed but `specterqa-ios` CLI     |
|           |             |                | was missing from the console-scripts entry point. Fix folded into v14.0.0.             |
| 14.0.0    | published   | `v14.0.0`      | Phase 3 complete: importlib.resources runner bundling, RunnerProcess registry,          |
|           |             |                | `_runner_source_dir()` via `importlib.resources.files('runner')`, `ios_start_session` |
|           |             |                | pre-deploy block, owned_pids() invariant.                                              |
| 14.0.1    | published   | `v14.0.1`      | Fix: deploy-conflict regression — `handle_start_session` no longer double-deploys      |
|           |             |                | when called while a session is already active. `owned_pids()` guard hardened.          |
| 14.0.2    | published   | `v14.0.2`      | Fix: app_relaunch post-capture_state bug — pre-deployed RunnerProcess was being         |
|           |             |                | orphaned when `TestSession` spawned a second xcodebuild on a different port, causing   |
|           |             |                | the simulator to shut down. Now reuses the pre-deployed runner directly (`_mcp_runner_ref`). |
|           |             |                | Also: 4 live-state tests properly gated, `--version` flag added, RELEASES.md.         |

## Why v14.0.0b1 was skipped

The beta was tagged to lock the `importlib.resources` phase-3 work before the v14.0.0
final publish.  The PyPI publish workflow ran but the resulting wheel was missing the
`specterqa-ios` console-scripts entry point — the CLI was unreachable after a fresh
`pip install specterqa-ios==14.0.0b1`.  The workflow's post-publish smoke test caught
this and the release was abandoned.  No user installs were affected.  The CLI entry-point
fix shipped in v14.0.0 as part of the same phase-3 branch.
