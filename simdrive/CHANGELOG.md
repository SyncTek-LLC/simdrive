# Changelog

## 0.1.0a1 — 2026-04-27

Initial alpha. simdrive is a fresh package, born from the ashes of `specterqa-ios` after a hard pivot away from XCTest.

### What's in
- 12-tool MCP surface: lifecycle (3) + observe (1) + act (4) + record/replay (3) + logs (1)
- **Background operation**: drives Simulator without stealing focus from your foreground app — keep editing while your agent works
- Screenshot capture, log tail, app launch
- YAML+PNG recording format with drift detection on replay
- 15 unit tests (no live sim required)

### Hard breaks from `specterqa-ios`
- Different package name (`pip install simdrive`)
- No Swift runner, no XCTest, no accessibility-tree selectors
- No HTTP daemon — pure subprocess + AppleScript
- Recording format is incompatible with v16
