# Governance Git Hooks

Part of **INIT-2026-535: Governance Hook Hardening**.

Three hooks enforce initiative linkage and gate compliance on every commit and push.

## Hooks

| Hook | Gate | Blocking? |
|------|------|-----------|
| `commit-msg` | Rejects commit unless message has `INIT-YYYY-NNN` or `work_context.json` has active initiative | Yes |
| `pre-push` | Runs `ba branch-check`; blocks push if non-zero exit or gate keywords found | Yes |
| `post-commit` | Logs commit SHA/msg/ts to `CompanyState/business-graph/initiatives/<INIT>/commit-log.jsonl` | No (warning only) |

## Installation

```bash
bash scripts/git-hooks/install-git-hooks.sh
```

This copies all three hooks into `.git/hooks/` and sets `+x`. Any pre-existing hooks (not already from this suite) are backed up with a timestamp suffix.

## How Each Hook Works

### commit-msg
Reads the commit message file passed by git. Checks for:
1. `INIT-YYYY-NNN` pattern in the message body (any position), OR
2. Most recent `active_work` entry with `initiative_id` in `work_context.json`

Note: uses `reversed()` scan to find the latest entry — `[0]` is stale by design.

To fix a rejection:
- Add `INIT-2026-NNN` anywhere in your commit message, OR
- Run `ba classify --title "..." --type STANDARD --initiative-id INIT-XXXX-NNN` first

### pre-push
Skips `main`/`master` (never pushed directly). For all other branches, runs:
```
ba branch-check
```
Blocks if exit code is non-zero OR output contains `blocked`, `unresolved`, or `gate`.

### post-commit
Appends a JSON line to the active initiative's `commit-log.jsonl`:
```json
{"sha": "abc1234", "msg": "feat: thing", "ts": "2026-04-19T00:00:00Z", "repo": "BusinessAtlas", "initiative_id": "INIT-2026-535"}
```
If no active initiative is set, logs to `CompanyState/business-graph/commit-log-unclassified.jsonl` with a stderr warning. Never fails the commit.

## Notes for specterqa-ios

The specterqa-ios repo contains **independent copies** of these hooks (not symlinks). Reason: symlinks would break if the repo is cloned to a different machine or path, whereas copies are self-contained. The hooks in both repos point to `work_context.json` and `ba` in the BusinessAtlas tree — this is intentional, as those are single-machine shared state.
