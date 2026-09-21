# SimDrive MCP Tool Surface (Canonical)

> **Source of truth:** `simdrive/src/simdrive/server.py::_TOOLS`.
> If you change the tool surface there, you MUST update this doc and `llms.txt` in the
> same commit, and the assertion in `simdrive/tests/test_unit.py::test_tool_count_is_thirty_six`
> will fail until the count matches.

**Canonical count: 36 tools.**

Verified by:

```python
from simdrive import server
assert len(server.list_tools()) == 36
```

## Categories

### 1. Session lifecycle (3)
- `session_start` — Start a SimDrive session on a booted iOS Simulator (or opted-in physical device).
- `session_end` — Stop the runner and clean up the session.
- `session_status` — Report active sessions, modes, and version.

### 2. Observation (1)
- `observe` — Capture an annotated screenshot + element list. Vision-first primitive — agents call this before every act.

### 3. Action (5)
- `tap` — Tap by mark id, label, identifier, or raw coordinates.
- `swipe` — Swipe in a direction or between two points.
- `type_text` — Type into a focused text field.
- `press_key` — Press a named keyboard key (return, escape, etc.).
- `tap_and_wait_keyboard` — Atomic tap + settle + observe composite.

### 4. Record / Replay (3)
- `record_start` — Begin a new recording (clears the step buffer).
- `record_stop` — Save the current recording and clear the buffer.
- `replay` — Run a saved recording end-to-end against a booted target.

### 5. Devices + Logs (2)
- `list_devices` — List booted iOS simulators (UDID, name, runtime, state).
- `logs` — Recent app console logs from the iOS Simulator.

### 6. Performance + Memory (4)
- `perf` — Real-time CPU / RSS / thread count for the app under test.
- `perf_baseline` — Capture a metrics baseline.
- `perf_compare` — Compare current metrics against the baseline.
- `memory` — Detailed memory breakdown via the macOS `footprint` tool.

### 7. Diagnostics + App state (4)
- `doctor` — Environment readiness: Xcode, simulator runtimes, booted devices, runner build status.
- `app_state` — App lifecycle state (foreground, background, suspended).
- `apps` — List apps installed on a booted simulator.
- `crashes` — App crashes since the session started.

### 8. Alerts + Permissions + Appearance (4)
- `dismiss_first_launch_alerts` — Dismiss system permission alerts at first launch.
- `pre_grant_permissions` — Pre-grant iOS app permissions before launch.
- `set_appearance` — Toggle dark or light mode on the iOS Simulator.
- `dismiss_sheet` — Dismiss a presented sheet by swiping down.

### 9. Replay management (2)
- `list_replays` — List saved recordings with their names, step counts, and timestamps.
- `validate_replay` — Parse and validate a saved recording without executing it.

### 10. Utility (2)
- `version` — Return the installed SimDrive version.
- `clear_field` — Clear a text field.

### 11. Journeys + Recording maintenance (3)
- `load_journey` — Load a recorded journey for replay.
- `lint_recordings` — Lint saved recordings for state-contract drift.
- `migrate_recording` — Migrate a recording from an older schema to the current one.

### 12. Host-AX accessibility (3)
- `perform_accessibility_action` — Invoke a custom accessibility action / VoiceOver announcement.
- `get_announcements` — Read recent VoiceOver announcements.
- `set_text` — Set text on fields HID input can't reach (e.g. `UIAlertController`).

## Total

3 + 1 + 5 + 3 + 2 + 4 + 4 + 4 + 2 + 2 + 3 + 3 = **36**

## Drift history

| Date       | Change                                                                  | New total |
|------------|-------------------------------------------------------------------------|-----------|
| pre-2026-04 | Initial 29-tool surface                                                 | 29        |
| 2026-04 (1.0.0a7) | + `load_journey`                                                  | 30        |
| 2026-04 (1.0.0a9.1) | + `lint_recordings`, `migrate_recording`                       | 32        |
| 2026-05-17 | Documented canonical count in MCP_TOOL_SURFACE.md       | 32        |
| 1.0.0b3+ | + `tap_and_wait_keyboard`, `perform_accessibility_action`, `get_announcements`, `set_text` | 36 |
