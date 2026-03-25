#!/bin/bash
# Full yaucca setup: deploy backend + install client.
# Backs up existing config before making changes.
#
# Usage:
#   ./scripts/setup.sh                        # deploy + install as "yaucca"
#   ./scripts/setup.sh --app-name yaucca-test # use a different app name
#
# To undo: ./scripts/teardown.sh [--app-name <name>]

set -euo pipefail

# Parse --app-name
APP_NAME="yaucca"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --app-name) APP_NAME="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

RULES_FILE="$HOME/.claude/rules/yaucca-memory.md"

echo "=== yaucca setup (app: $APP_NAME) ==="
echo ""

# Back up current state (idempotent — won't overwrite existing backups)
echo "--- Backing up current config ---"
[ -f .mcp.json ] && [ ! -f .mcp.json.backup ] && cp .mcp.json .mcp.json.backup && echo "  .mcp.json"
[ -f "$RULES_FILE" ] && [ ! -f "$RULES_FILE.backup" ] && cp "$RULES_FILE" "$RULES_FILE.backup" && echo "  memory rules"
[ -f "$HOME/.claude/settings.json" ] && [ ! -f "$HOME/.claude/settings.json.pre-setup" ] && \
    cp "$HOME/.claude/settings.json" "$HOME/.claude/settings.json.pre-setup" && echo "  settings.json"
echo ""

# Uninstall any existing yaucca hooks (clean slate)
echo "--- Removing existing yaucca config ---"
uv run python -m yaucca.install --uninstall --app-name "$APP_NAME" 2>/dev/null || true
rm -f .mcp.json
echo ""

# Deploy backend (will stop and prompt if .env needs editing)
echo "--- Deploy backend ---"
if ! uv run python -m yaucca.deploy --app-name "$APP_NAME"; then
    echo ""
    echo "Deploy paused — fill in your .env, then re-run:"
    APP_FLAG=""
    [ "$APP_NAME" != "yaucca" ] && APP_FLAG=" --app-name $APP_NAME"
    echo "  ./scripts/setup.sh$APP_FLAG"
    exit 1
fi
echo ""

# Install client (hooks, rules, MCP, user profile)
echo "--- Install client ---"
uv run python -m yaucca.install --app-name "$APP_NAME"
echo ""

echo "=== Setup complete! ==="
echo "  Start Claude Code: claude"
echo "  Type /mcp to authenticate the $APP_NAME MCP server"
echo ""
echo "  To undo: ./scripts/teardown.sh --app-name $APP_NAME"
