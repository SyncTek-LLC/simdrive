# Changelog

## 0.1.0a1 — 2026-04-27

Initial alpha. simdrive is a fresh package, born from the ashes of `specterqa-ios` after a hard pivot away from XCTest.

### What's in
- 12-tool MCP surface: lifecycle (3) + observe (1) + act (4) + record/replay (3) + logs (1)
- **Set-of-Mark observe**: every observe returns the screenshot plus an annotated copy with numbered red boxes drawn over each detected text region. The agent never has to compute pixels.
- **Hybrid tap targets**: `tap` (and `swipe` endpoints, `type_text` `tap_first`) accept `{x, y}` coords, `{mark: <id>}` from the latest observe, or `{text: "..."}` matched against detected text.
- Screenshot capture, log tail, app launch
- YAML+PNG recording format with drift detection on replay
- 22 unit tests (no live sim required)

### Known limitations
- Action dispatch currently brings the Simulator window to the foreground; we have a non-disruptive backend (opt-in via `SIMDRIVE_INPUT_BACKEND=pid`) but it's not yet reliable on macOS Sequoia + Xcode 26. Default-on for v0.1.0a2.
- SoM detection is OCR-only — purely-iconic targets without visible text will need pixel coords for now.
- Simulator only; real devices are post-v0.1.

### Hard breaks from `specterqa-ios`
- Different package name (`pip install simdrive`)
- No Swift runner, no XCTest, no accessibility-tree selectors
- No HTTP daemon — pure subprocess + AppleScript
- Recording format is incompatible with v16
