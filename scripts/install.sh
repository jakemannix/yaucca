#!/usr/bin/env bash
# Install yaucca hooks and MCP server globally for Claude Code.
#
# This script configures:
# 1. MCP server in ~/.claude.json (global Claude Code config)
# 2. Hooks in ~/.claude/settings.json (global user settings)
#
# Prerequisites:
# - uv installed
# - yaucca installed: cd <this-repo> && uv sync
# - YAUCCA_AGENT_ID set in .env or environment
#
# Usage:
#   ./scripts/install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

echo "Installing yaucca for Claude Code..."
echo "Repo: $REPO_DIR"

# Ensure uv project is synced
echo "Syncing dependencies..."
cd "$REPO_DIR" && uv sync

# --- Configure MCP server in ~/.claude.json ---
CLAUDE_CONFIG="$HOME/.claude.json"

if [ ! -f "$CLAUDE_CONFIG" ]; then
    echo '{}' > "$CLAUDE_CONFIG"
fi

# Use python to safely merge MCP config
python3 -c "
import json, sys

config_path = '$CLAUDE_CONFIG'
repo_dir = '$REPO_DIR'

with open(config_path) as f:
    config = json.load(f)

config.setdefault('mcpServers', {})
config['mcpServers']['yaucca'] = {
    'command': 'uv',
    'args': ['--directory', repo_dir, 'run', 'python', '-m', 'yaucca.mcp_server'],
    'env': {}
}

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
    f.write('\n')

print(f'  MCP server configured in {config_path}')
"

# --- Configure hooks in ~/.claude/settings.json ---
SETTINGS_DIR="$HOME/.claude"
SETTINGS_FILE="$SETTINGS_DIR/settings.json"

mkdir -p "$SETTINGS_DIR"

if [ ! -f "$SETTINGS_FILE" ]; then
    echo '{}' > "$SETTINGS_FILE"
fi

python3 -c "
import json

settings_path = '$SETTINGS_FILE'
repo_dir = '$REPO_DIR'

with open(settings_path) as f:
    settings = json.load(f)

settings.setdefault('hooks', {})

# SessionStart hook
settings['hooks']['SessionStart'] = settings['hooks'].get('SessionStart', [])
# Remove any existing yaucca hooks
settings['hooks']['SessionStart'] = [
    h for h in settings['hooks']['SessionStart']
    if not any('yaucca' in hook.get('command', '') for hook in h.get('hooks', []))
]
settings['hooks']['SessionStart'].append({
    'matcher': 'startup|resume|compact|clear',
    'hooks': [{
        'type': 'command',
        'command': f'cd {repo_dir} && uv run python -m yaucca.hooks session_start',
        'timeout': 10
    }]
})

# Stop hook
settings['hooks']['Stop'] = settings['hooks'].get('Stop', [])
settings['hooks']['Stop'] = [
    h for h in settings['hooks']['Stop']
    if not any('yaucca' in hook.get('command', '') for hook in h.get('hooks', []))
]
settings['hooks']['Stop'].append({
    'hooks': [{
        'type': 'command',
        'command': f'cd {repo_dir} && uv run python -m yaucca.hooks stop',
        'timeout': 120
    }]
})

with open(settings_path, 'w') as f:
    json.dump(settings, f, indent=2)
    f.write('\n')

print(f'  Hooks configured in {settings_path}')
"

echo ""
echo "Installation complete!"
echo ""
echo "Next steps:"
echo "  1. Ensure Letta server is running: docker compose up -d"
echo "  2. Create agent: uv run python -m yaucca.setup_agent"
echo "  3. Set YAUCCA_AGENT_ID in $REPO_DIR/.env"
echo "  4. Open a new Claude Code session to test"
