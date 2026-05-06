# D6 — Empty `logs` on Real Device: Root-Cause Investigation

**Status:** investigation only (no code changes); fix gated to a9 once confirmed
against a real device.

**Symptom (a7 dogfood, Moes Max, iPhone 17 Pro Max):**
`tool_logs` against a real-device session returns
`{"ok": true, "lines": 0, "logs": ""}`. The original a7 dogfood report
attributed this to `simctl`, but `tool_logs` for `target="device"` actually
routes through `device.get_log_tail()`, which uses `idevicesyslog`, not
`simctl`. The symptom is real; the diagnosis was wrong.

## Code path

| File | Lines | Function |
|------|-------|----------|
| `simdrive/src/simdrive/server.py` | 701–710 | `tool_logs` — dispatches to device vs sim by `s.target` |
| `simdrive/src/simdrive/device.py` | 172–203 | `get_log_tail(udid, lines, predicate)` |
| `simdrive/src/simdrive/device.py` | 41–49, 52–56 | `_which`, `libimobiledevice_available` (binary discovery) |

`get_log_tail` boils down to:

```
proc = subprocess.Popen([idevicesyslog_path, "-u", udid],
                        stdout=PIPE, stderr=DEVNULL, text=True)
time.sleep(1.0)
proc.terminate()
out, _ = proc.communicate(timeout=2.0)
out_lines = (out or "").splitlines()
if predicate:
    out_lines = [ln for ln in out_lines if predicate in ln]
return "\n".join(out_lines[-lines:])
```

## Likely root causes — ranked by probability

### 1. (Most likely) 1-second window is too short for first-stream warmup

`idevicesyslog` opens a syslog relay over the lockdownd channel. On real
hardware the first burst of log lines often does NOT arrive within the first
second after spawn — the relay handshake plus the kernel buffer flush takes
~2–4 s on iOS 17/18 devices in our experience. We `time.sleep(1.0)` and
then `terminate()`. If no line has arrived during that window, `proc.communicate()`
returns an empty string and the user sees `lines: 0`.

**Confirmation step (real device):**
1. `idevicesyslog -u <udid>` from shell, time how long until the first line
   appears. Observed >1 s confirms the window is the cause.
2. Patch the timeout up to ~3 s locally (don't merge), call `tool_logs`
   again, expect non-empty output.

### 2. (Plausible) `idevicesyslog` not installed at runtime, but path discovery silently uses an old / wrong copy

`_which` returns the first match across `shutil.which`, `/opt/homebrew/bin/`,
`/usr/local/bin/`. If the user has a stale arm64 `idevicesyslog` (e.g. from a
much older `libimobiledevice` brew formula) it can fail to attach to a modern
iOS 17/18 device's lockdownd and exit immediately with a stderr message —
which we route to `DEVNULL`. The "binary not found" branch raises a clear
error, but the "binary present but broken" branch silently swallows.

**Confirmation step (real device):**
1. `idevicesyslog --version` and `brew list libimobiledevice --versions` —
   confirm the brew formula is current (`>= 1.3.0-39+`).
2. Run `idevicesyslog -u <udid> 2>&1 | head -20` directly: any
   lockdownd-error line indicates a broken / mismatched binary.
3. Check whether `subprocess.Popen` exits with a non-zero returncode
   inside `get_log_tail` and we ignore it.

### 3. (Plausible) Predicate filter is over-aggressive

If the caller passes `predicate="<some-string>"` but the log text emitted
within our window doesn't contain that substring, the in-memory filter
strips the entire output. The user-facing symptom is identical
(`lines: 0`, `logs: ""`) and indistinguishable from cause #1 or #2.

**Confirmation step (real device):**
1. Call `tool_logs` with `predicate=None` (omit it). If non-empty, the
   filter was the issue, not the relay.

### 4. (Less likely) stderr-on-DEVNULL hides startup errors

`stderr=subprocess.DEVNULL` means we never see lockdownd "device not
paired" or "DDI not mounted" complaints from `idevicesyslog`. These don't
prevent the binary from starting, but they prevent it from emitting any
syslog lines. Symptom is again indistinguishable.

**Confirmation step (real device):**
1. Replace `stderr=DEVNULL` with `stderr=PIPE` locally; print stderr after
   `communicate()`. If stderr contains "lockdown" / "could not connect" /
   "Trust this computer", that's the cause.

### 5. (Unlikely) Pairing is fine but Developer Mode is partially off

Some syslog channels require Developer Mode + DDI on iOS 17+. We verify
those during `bootstrap-device` for WDA, but `get_log_tail` is a separate
code path that doesn't re-check. If a user disables Developer Mode after
bootstrapping, logs would silently empty out.

**Confirmation step (real device):**
1. `xcrun devicectl device info details --device <udid> --json-output -`
   and verify `developerModeStatus: enabled` and `ddiServicesAvailable: true`.

## Recommendation: a8 vs a9

**Ship investigation in a8; fix in a9 once confirmed.** Reasoning:

- We have not yet reproduced against a live device with stderr captured —
  the four candidate causes have indistinguishable symptoms, and the
  fix differs per cause (timeout bump vs. tool reinstall vs. predicate
  default vs. stderr surfacing).
- A blind fix that just bumps the sleep window from 1 s → 3 s would mask
  causes #2 and #4 — broken binaries and Developer-Mode regressions
  would still emit empty logs, just slower.
- The right a9 work is a single PR that:
    1. Surfaces stderr from `idevicesyslog` instead of swallowing it.
    2. Adds a `READY:` heartbeat: read until first non-empty line OR
       hard timeout, instead of fixed `sleep(1.0)`.
    3. Returns a richer error envelope when `idevicesyslog` exits
       non-zero so the caller sees the underlying lockdownd message.
- a8 dogfood pass against Moes Max should explicitly capture
  `idevicesyslog -u <udid> 2>&1 | head -50` so we have ground-truth to
  pick the right fix in a9.

## Cross-references

- a7 dogfood report: `simdrive/docs/DOGFOOD_FEEDBACK_2026_05_06_MOES_MAX.md`
- D6 line in the a8 cluster brief: routes through `device.get_log_tail`,
  not `simctl` — original report's blame on simctl was incorrect.

## What this PR does NOT do

- Does NOT modify `simdrive/src/simdrive/device.py`.
- Does NOT modify `simdrive/src/simdrive/server.py`.
- Does NOT add a regression test (we'd be testing the wrong fix).
- Investigation only.
