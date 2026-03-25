#!/bin/bash
# Remove yaucca client config and optionally destroy the Modal backend.
#
# Without --destroy-backend: removes local config (hooks, rules, MCP)
#   and restores any backups from setup.sh. Your Modal server and data
#   are untouched — you can re-run setup.sh to reconnect.
#
# With --destroy-backend: also stops the Modal app, deletes the
#   persistent volume (ALL your memory data), and deletes Modal secrets.
#   This is irreversible.
#
# Usage:
#   ./scripts/teardown.sh                                   # local config only
#   ./scripts/teardown.sh --app-name yaucca-test            # specific app
#   ./scripts/teardown.sh --app-name yaucca-test --destroy-backend  # nuke everything

set -euo pipefail

# Parse args
APP_NAME="yaucca"
DESTROY=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --app-name) APP_NAME="$2"; shift 2 ;;
        --destroy-backend) DESTROY=true; shift ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

RULES_FILE="$HOME/.claude/rules/yaucca-memory.md"

echo "=== yaucca teardown (app: $APP_NAME) ==="
echo ""

# Remove client config
echo "--- Removing client config ---"
uv run python -m yaucca.install --uninstall --app-name "$APP_NAME" 2>/dev/null || true
echo ""

# Restore backups from setup.sh
echo "--- Restoring backups ---"
[ -f "$HOME/.claude/settings.json.pre-setup" ] && \
    cp "$HOME/.claude/settings.json.pre-setup" "$HOME/.claude/settings.json" && \
    rm "$HOME/.claude/settings.json.pre-setup" && echo "  settings.json"
[ -f "$RULES_FILE.backup" ] && mv "$RULES_FILE.backup" "$RULES_FILE" && echo "  memory rules"
[ -f .mcp.json.backup ] && mv .mcp.json.backup .mcp.json && echo "  .mcp.json"
echo ""

if $DESTROY; then
    echo "--- Destroy Modal backend ---"
    echo ""
    echo "  This will permanently delete the following Modal resources:"
    echo ""
    echo "    App:     $APP_NAME (the running server)"
    echo "    Volume:  ${APP_NAME}-data (your SQLite database — ALL memory blocks,"
    echo "             archival passages, embeddings, and conversation history)"
    echo "    Secrets: ${APP_NAME}-secrets (auth token, API keys, GitHub OAuth creds)"
    echo ""
    echo "  This is irreversible. Your memory data cannot be recovered."
    echo ""
    read -p "  Type 'destroy $APP_NAME' to confirm: " confirmation
    if [ "$confirmation" != "destroy $APP_NAME" ]; then
        echo "  Aborted."
        exit 0
    fi
    echo ""
    uv run --extra deploy modal app stop "$APP_NAME" 2>/dev/null && echo "  Stopped app" || echo "  App already stopped"
    uv run --extra deploy modal volume delete "${APP_NAME}-data" --yes 2>/dev/null && echo "  Deleted volume" || echo "  Volume already gone"
    uv run --extra deploy modal secret delete "${APP_NAME}-secrets" --yes 2>/dev/null && echo "  Deleted secrets" || echo "  Secrets already gone"
    echo ""
    echo "Backend destroyed. All memory data for '$APP_NAME' is gone."
else
    echo "Modal backend left running. Your data is safe."
    echo "  To reconnect: ./scripts/setup.sh --app-name $APP_NAME"
    echo "  To destroy:   ./scripts/teardown.sh --app-name $APP_NAME --destroy-backend"
fi

echo ""
echo "=== Done ==="
