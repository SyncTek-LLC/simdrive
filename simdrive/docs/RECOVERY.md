# SimDrive Error Recovery Reference

One-stop reference for every error code and its recovery step. When SimDrive raises an error, the `Recovery:` field in the message tells you exactly what to do next.

## Core errors (`simdrive.errors`)

### `no_session`
**Cause:** The `session_id` passed to a tool is not known.
**Recovery:** Call `ios_start_session` to create a session, then retry with the returned `session_id`.

---

### `no_device`
**Cause:** No booted simulator matched the query filter.
**Recovery:** Run `ios_devices` to list available simulators, then pass a matching `device` filter, or run `xcrun simctl boot <udid>` to boot one.

---

### `sim_unhealthy`
**Cause:** Simulator is in a degraded/shutdown-loop state.
**Recovery:** Quit Simulator.app and run `xcrun simctl shutdown all && xcrun simctl boot <udid>`.

---

### `hid_unavailable`
**Cause:** The native HID helper binary is missing or not executable.
**Recovery:** Reinstall simdrive (the bundled binary is required) or run `cd simdrive/native && make`.

---

### `target_not_found`
**Cause:** The requested mark, text, or coordinate was not found in the last observe.
**Recovery:** Call `ios_observe` to refresh the screen state, then retry with a visible element.

---

### `missing_target`
**Cause:** A tap/swipe call was made without specifying `{x, y}`, `{mark: <id>}`, or `{text: <query>}`.
**Recovery:** Call `ios_observe` to get current marks, then supply one of the coordinate forms.

---

### `invalid_argument`
**Cause:** A tool parameter was out of range or the wrong type.
**Recovery:** Check the tool's parameter schema and supply a valid value.

---

### `already_recording`
**Cause:** `ios_start_recording` called while a recording is in progress.
**Recovery:** Call `ios_stop_recording` to finalize the current recording before starting a new one.

---

### `not_recording`
**Cause:** `ios_stop_recording` called when no recording is active.
**Recovery:** Call `ios_start_recording` before attempting to stop or add steps to a recording.

---

### `recording_not_found`
**Cause:** The requested recording name does not exist at the expected path.
**Recovery:** Run `ios_list_replays` to see available recordings, then retry with a valid name.

---

### `device_input_unavailable`
**Cause:** Synthetic touch/keyboard input on a real device requires WebDriverAgent (not yet available).
**Recovery:** Switch `target` to `simulator` for now, or run `simdrive bootstrap-device <udid>` once WDA bootstrap is available in v0.2.

---

### `replay_drift_halt`
**Cause:** The live screen differed from the recorded pre-screenshot by more than the drift threshold.
**Recovery:** Re-record the journey from the current UI state, or lower `drift_threshold` if the UI change is cosmetic (e.g. `--drift-threshold 0.75`).

---

## Journey errors (`simdrive.journey.errors`)

### `journey_schema_invalid`
**Recovery:** Run `simdrive validate --journeys-dir <dir>` to see all schema errors.

### `journey_persona_not_found`
**Recovery:** Create `.simdrive/personas/{persona_slug}.yaml` or update the `persona:` field in your journey file.

### `journey_schema_version_unsupported`
**Recovery:** Update `schema_version:` to `1` in your journey YAML.

### `journey_device_selector_missing`
**Recovery:** Add a `device_selector:` block with at least `udid` or `name` to your journey YAML.

### `persona_schema_invalid`
**Recovery:** Run `simdrive validate --personas-dir <dir>` to see all schema errors.

### `persona_schema_version_unsupported`
**Recovery:** Update `schema_version:` to `1` in your persona YAML.

### `journey_budget_exceeded`
**Recovery:** Increase `budget.max_steps` / `max_seconds` / `max_llm_calls` in the journey YAML, or simplify the journey goals.

### `claude_call_failed`
**Recovery:** Check network connectivity and the `ANTHROPIC_API_KEY` environment variable.

### `claude_cost_cap_hit`
**Recovery:** Set the `SIMDRIVE_COST_CAP_USD` env var to a higher value, or reduce `budget.max_llm_calls` in your journey.

### `act_tool_failed`
**Recovery:** Check the journey step's target exists on screen (use `ios_observe` to verify).

### `success_criterion_unevaluable`
**Recovery:** Ensure the required data (observe output, perf snapshot, etc.) is available before evaluating this criterion type.

### `ci_no_journeys_matched`
**Recovery:** Run `simdrive validate` to list discovered journeys, or adjust `--tag` / `--journeys` filter.

### `ci_invalid_journey`
**Recovery:** Fix the journey file or remove it from the journeys directory.

---

## License errors (`simdrive.license.errors`)

### `license_invalid`
**Recovery:** Run `simdrive license status` to check your key, or `simdrive trial start` to begin a new trial.

### `license_expired`
**Recovery:** Run `simdrive license activate <key>` to install a renewed key, or visit https://simdrive.dev/pricing to renew.

### `license_offline_grace_exhausted`
**Recovery:** Connect to the internet and run `simdrive license status` to refresh, or visit https://simdrive.dev/pricing to renew.

### `license_tier_insufficient`
**Recovery:** Visit https://simdrive.dev/pricing to upgrade your plan.

### `trial_already_used`
**Recovery:** Visit https://simdrive.dev/pricing to purchase a license.

### `license_not_found`
**Recovery:** Run `simdrive trial start --email <you@example.com>` to begin a 14-day free trial.

### `trial_rate_limited`
**Recovery:** Try again tomorrow or contact support@synctek.io.

---

## Cloud API errors (`simdrive.errors` cloud section)

### `cloud_auth_missing`
**Recovery:** Include `Authorization: Bearer <token>` in your request.

### `cloud_auth_invalid`
**Recovery:** Re-authenticate via `POST /auth/token` or check your license key.

### `cloud_storage_quota_exceeded`
**Recovery:** Delete old recordings via `DELETE /recordings/<id>`, or upgrade your plan.

### `cloud_recording_not_found`
**Recovery:** List available recordings via `GET /recordings`.

### `cloud_rate_limited`
**Recovery:** Reduce request frequency or upgrade to a higher-tier plan.
