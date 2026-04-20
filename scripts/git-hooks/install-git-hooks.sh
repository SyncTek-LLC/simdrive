#!/usr/bin/env bash
# install-git-hooks.sh — Install governance git hooks into .git/hooks/
# Run from the root of any repo that has a copy of scripts/git-hooks/
#
# Part of INIT-2026-535 Governance Hook Hardening

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || git rev-parse --show-toplevel)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"

if [ ! -d "$HOOKS_DIR" ]; then
    echo "ERROR: .git/hooks directory not found at $HOOKS_DIR" >&2
    echo "Make sure you're running this from inside a git repository." >&2
    exit 1
fi

HOOKS=(commit-msg pre-push post-commit)
INSTALLED=0
SKIPPED=0

echo "Installing governance hooks into $HOOKS_DIR ..."

for hook in "${HOOKS[@]}"; do
    SRC="$SCRIPT_DIR/$hook"
    DEST="$HOOKS_DIR/$hook"

    if [ ! -f "$SRC" ]; then
        echo "  WARNING: source hook not found: $SRC — skipping" >&2
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    # Back up existing hook if it's not already ours
    if [ -f "$DEST" ] && ! grep -q "INIT-2026-535" "$DEST" 2>/dev/null; then
        BACKUP="$DEST.bak.$(date +%Y%m%d%H%M%S)"
        echo "  Backing up existing $hook -> $BACKUP"
        cp "$DEST" "$BACKUP"
    fi

    cp "$SRC" "$DEST"
    chmod +x "$DEST"
    echo "  Installed: $hook"
    INSTALLED=$((INSTALLED + 1))
done

echo ""
echo "Done. $INSTALLED hook(s) installed, $SKIPPED skipped."
echo ""
echo "Installed hooks:"
for hook in "${HOOKS[@]}"; do
    DEST="$HOOKS_DIR/$hook"
    if [ -f "$DEST" ]; then
        echo "  $DEST"
    fi
done
