"""Install yaucca: hooks, memory rules, MCP server, and user profile.

Usage:
    yaucca-install                          # full interactive setup
    yaucca-install --user-block "Name: ..." # skip interactive, seed user block directly
    yaucca-install --uninstall              # remove everything

Sets up:
  1. Interactive user block seeding (or --user-block to skip)
  2. Hooks in ~/.claude/settings.json (SessionStart, Stop, SessionEnd)
  3. Memory rules template in ~/.claude/rules/yaucca-memory.md
  4. Remote MCP server via `claude mcp add`
"""

import argparse
import importlib.resources
import json
import os
import shutil
import subprocess
from pathlib import Path

import httpx

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
CONFIG_DIR = Path.home() / ".config" / "yaucca"
ENV_FILE = CONFIG_DIR / ".env"
YAUCCA_MARKER = "yaucca.hooks"


def _is_cloud_env() -> bool:
    """Detect if we're running in a Claude Code cloud sandbox.

    Cloud sandboxes run as root on Ubuntu. The entrypoint is "cloudcode"
    (vs "cli" for local). Falls back to explicit YAUCCA_CLOUD=1.
    """
    entrypoint = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "")
    if entrypoint and entrypoint != "cli":
        return True
    return bool(os.environ.get("YAUCCA_CLOUD"))


# --- .env management ---

# Set by install()/uninstall() to the active .env path for this run
_active_env_file: Path = ENV_FILE


def _load_env() -> dict[str, str]:
    """Load key=value pairs from the active .env file.

    First occurrence wins — if a key appears twice, the first value is kept.
    This prevents appended/stale values from overriding intended config.
    """
    if not _active_env_file.exists():
        return {}
    env: dict[str, str] = {}
    for line in _active_env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            if k not in env:  # first occurrence wins
                env[k] = v.strip()
    return env


def _get_env(key: str) -> str | None:
    """Get a config value from the active .env file (not environment).

    We intentionally read from the .env file only, not os.environ,
    because the shell may have stale env vars from a different instance
    (e.g. production vars leaking into a test install).
    """
    return _load_env().get(key)


# --- User block seeding ---


def _seed_user_block_interactive() -> str | None:
    """Interactively collect user info and return block content."""
    print()
    print("Let's set up your memory profile. This helps Claude remember who you are")
    print("across sessions. Press Enter to skip any field.")
    print()

    fields = [
        ("Name", "What's your name?"),
        ("Email", "Email address?"),
        ("Role", "What's your role? (e.g. 'Senior SWE at Acme', 'CS student')"),
        ("Machine", "What machine are you on? (e.g. 'MacBook Pro 16\", 64GB RAM')"),
        ("GitHub", "GitHub username?"),
        ("Surfaces", "Which Claude surfaces do you use? (e.g. 'Claude Code CLI, Claude.ai web, mobile')"),
        ("About", "Anything else Claude should know about you? (interests, preferences, working style)"),
    ]

    parts = []
    for label, prompt in fields:
        try:
            value = input(f"  {prompt} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if value:
            parts.append(f"{label}: {value}")

    if not parts:
        print("  No info provided — user block will start empty.")
        return None

    return "\n".join(parts)


def _check_user_block() -> str | None:
    """Check if the user block already has content. Returns content or None."""
    url = _get_env("YAUCCA_URL")
    token = _get_env("YAUCCA_AUTH_TOKEN")
    if not url:
        return None
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = httpx.get(f"{url}/api/blocks/user", headers=headers, timeout=10.0)
        if resp.status_code == 200:
            value = resp.json().get("value", "")
            if value.strip():
                return value
    except Exception:
        pass
    return None


def _seed_user_block(content: str) -> bool:
    """Write user block content to the cloud API. Returns True on success."""
    url = _get_env("YAUCCA_URL")
    token = _get_env("YAUCCA_AUTH_TOKEN")
    if not url:
        print("  WARNING: YAUCCA_URL not set — skipping user block seeding.")
        print("  You can seed it later with the MCP tools.")
        return False

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = httpx.put(
            f"{url}/api/blocks/user",
            json={"value": content},
            headers=headers,
            timeout=15.0,
        )
        resp.raise_for_status()
        print(f"  Seeded user block ({len(content)} chars)")
        return True
    except Exception as e:
        print(f"  WARNING: Failed to seed user block: {e}")
        print("  You can seed it later with the MCP tools.")
        return False


# --- Hooks ---


def _yaucca_hooks(app_name: str = "yaucca") -> dict:
    """Build the hooks config for yaucca.

    Uses the absolute path to the current Python interpreter so hooks
    run in the same environment where yaucca is installed, regardless
    of which `python` the user's PATH resolves to.
    """
    import sys

    python = sys.executable
    if app_name == "yaucca":
        env_path = ENV_FILE
    else:
        env_path = CONFIG_DIR / app_name / ".env"
    env_prefix = f'YAUCCA_ENV_FILE="{env_path}"'
    cmd_prefix = f'{env_prefix} {python} -m yaucca.hooks'

    return {
        "SessionStart": [
            {
                "matcher": "startup|resume|compact|clear",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{cmd_prefix} session_start",
                        "timeout": 30,
                    }
                ],
            }
        ],
        "Stop": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{cmd_prefix} stop",
                        "timeout": 10,
                    }
                ],
            }
        ],
        "SessionEnd": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{cmd_prefix} session_end",
                        "timeout": 120,
                    }
                ],
            }
        ],
    }


def _is_yaucca_hook(hook_group: dict) -> bool:
    """Check if a hook group belongs to yaucca."""
    for hook in hook_group.get("hooks", []):
        if YAUCCA_MARKER in hook.get("command", ""):
            return True
    return False


def _load_settings() -> dict:
    if SETTINGS_PATH.exists():
        return json.loads(SETTINGS_PATH.read_text())
    return {}


def _save_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n")


def _backup_settings() -> Path | None:
    if SETTINGS_PATH.exists():
        backup = SETTINGS_PATH.with_suffix(".json.bak")
        shutil.copy2(SETTINGS_PATH, backup)
        return backup
    return None


# --- Memory rules template ---


def _install_rules_template() -> None:
    """Install the yaucca memory rules template into ~/.claude/rules/."""
    rules_dir = Path.home() / ".claude" / "rules"
    target = rules_dir / "yaucca-memory.md"

    if target.exists():
        print(f"  Memory rules already installed at {target}")
        return

    template_ref = importlib.resources.files("yaucca.templates").joinpath("memory-rules.md")
    template_text = template_ref.read_text(encoding="utf-8")

    rules_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(template_text)
    print(f"  Installed memory rules at {target}")


# --- Repo-level config (for cloud sandboxes) ---


def _install_repo_level_hooks(app_name: str = "yaucca") -> None:
    """Write hooks to .claude/settings.json in the current repo."""
    repo_settings = Path.cwd() / ".claude" / "settings.json"
    repo_settings.parent.mkdir(parents=True, exist_ok=True)

    if repo_settings.exists():
        settings = json.loads(repo_settings.read_text())
    else:
        settings = {}

    hooks = settings.setdefault("hooks", {})
    # Cloud hooks use bare `python -m` (not venv-specific path)
    new_hooks = _cloud_hooks()

    for event in ("SessionStart", "Stop", "SessionEnd"):
        existing = hooks.get(event, [])
        filtered = [h for h in existing if not _is_yaucca_hook(h)]
        filtered.extend(new_hooks[event])
        hooks[event] = filtered

    # Auto-approve MCP tools
    perms = settings.setdefault("permissions", {})
    allow = perms.setdefault("allow", [])
    mcp_perm = f"mcp__{app_name}"
    if mcp_perm not in allow:
        allow.append(mcp_perm)

    repo_settings.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"  Wrote repo-level hooks to {repo_settings}")
    print(f"  Auto-approved MCP tools ({mcp_perm})")


def _cloud_hooks() -> dict:
    """Build hooks for cloud environments (bare python, no venv path)."""
    cmd_prefix = "python -m yaucca.hooks"
    return {
        "SessionStart": [
            {
                "matcher": "startup|resume|compact|clear",
                "hooks": [{"type": "command", "command": f"{cmd_prefix} session_start", "timeout": 30}],
            }
        ],
        "Stop": [
            {"hooks": [{"type": "command", "command": f"{cmd_prefix} stop", "timeout": 10}]}
        ],
        "SessionEnd": [
            {"hooks": [{"type": "command", "command": f"{cmd_prefix} session_end", "timeout": 120}]}
        ],
    }


def _install_repo_level_mcp(app_name: str = "yaucca") -> None:
    """Write .mcp.json with bearer token auth to the current repo."""
    url = _get_env("YAUCCA_URL")
    if not url:
        url = os.environ.get("YAUCCA_URL", "")
    if not url:
        print("  WARNING: YAUCCA_URL not set — skipping repo-level .mcp.json")
        return

    mcp_json = Path.cwd() / ".mcp.json"
    config = {}
    if mcp_json.exists():
        try:
            config = json.loads(mcp_json.read_text())
        except json.JSONDecodeError:
            pass

    servers = config.setdefault("mcpServers", {})
    servers[app_name] = {
        "type": "http",
        "url": f"{url.rstrip('/')}/mcp",
        "headers": {
            "Authorization": "Bearer ${YAUCCA_AUTH_TOKEN}",
        },
    }
    mcp_json.write_text(json.dumps(config, indent=2) + "\n")
    print(f"  Wrote repo-level MCP config to {mcp_json}")


# --- MCP server ---


def _install_mcp_server(app_name: str = "yaucca") -> None:
    """Add the remote MCP server via claude mcp add."""
    url = _get_env("YAUCCA_URL")
    if not url:
        print("  WARNING: YAUCCA_URL not set — skipping MCP server setup.")
        return

    mcp_url = f"{url}/mcp"

    # Check if already configured
    result = subprocess.run(
        ["claude", "mcp", "get", app_name],
        capture_output=True, text=True,
    )
    if result.returncode == 0 and mcp_url in result.stdout:
        print(f"  MCP server already configured: {mcp_url}")
        return

    # Remove old entry if URL changed
    if result.returncode == 0:
        subprocess.run(
            ["claude", "mcp", "remove", "-s", "user", app_name],
            capture_output=True, text=True,
        )

    result = subprocess.run(
        ["claude", "mcp", "add", "--transport", "http", "-s", "user", app_name, mcp_url],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"  Added MCP server '{app_name}': {mcp_url}")
        print("  On first use, type /mcp in Claude Code to authenticate via GitHub.")
    else:
        print(f"  WARNING: Failed to add MCP server: {result.stderr.strip()}")
        print(f"  Run manually: claude mcp add --transport http -s user {app_name} {mcp_url}")


# --- Main install/uninstall ---


def _check_prerequisites() -> None:
    """Check that required tools are available."""
    import sys

    if _is_cloud_env():
        # Cloud sandboxes use pip, not uv, and claude CLI may not be in PATH
        return

    # Check we're running under uv (or at least not system python)
    python = sys.executable
    if "/anaconda" in python or python == "/usr/bin/python3":
        print(f"WARNING: Running under system Python ({python})")
        print("yaucca should be installed with uv:")
        print("  uv pip install yaucca")
        print("  uv run yaucca-install")
        print()

    # Check uv is available
    result = subprocess.run(["uv", "--version"], capture_output=True, text=True)
    if result.returncode != 0:
        print("ERROR: uv not found. Install it from https://docs.astral.sh/uv/")
        raise SystemExit(1)

    # Check claude CLI is available
    result = subprocess.run(["claude", "--version"], capture_output=True, text=True)
    if result.returncode != 0:
        print("WARNING: claude CLI not found — will skip MCP server setup.")
        print("Install from https://docs.anthropic.com/en/docs/claude-code")
        print()


def install(user_block: str | None = None, app_name: str = "yaucca") -> None:
    print(f"=== yaucca-install (app: {app_name}) ===")
    print()

    _check_prerequisites()

    # Resolve .env location based on app name
    # Must match deploy.py: ~/.config/yaucca/.env for default,
    # ~/.config/yaucca/<app-name>/.env for custom names
    global _active_env_file
    if app_name == "yaucca":
        env_file = ENV_FILE
    else:
        env_file = CONFIG_DIR / app_name / ".env"
    _active_env_file = env_file

    # Step 0: Check .env
    if not env_file.exists():
        print(f"No config found at {env_file}")
        print("Run `yaucca-deploy` first, or create it manually:")
        print(f"  mkdir -p {env_file.parent}")
        print(f"  cat > {env_file} << EOF")
        print("  YAUCCA_URL=https://<your-modal-username>--yaucca-serve.modal.run")
        print("  YAUCCA_AUTH_TOKEN=<your-token>")
        print("  EOF")
        print()

    # Step 1: Seed user block (skip if already populated)
    print("[1/4] User profile")
    existing_user = _check_user_block()
    if existing_user:
        preview = existing_user[:100].replace("\n", " ")
        if len(existing_user) > 100:
            preview += "..."
        print(f"  Already seeded ({len(existing_user)} chars) — skipping.")
        print(f"  Current value: {preview}")
    elif user_block:
        _seed_user_block(user_block)
    else:
        content = _seed_user_block_interactive()
        if content:
            _seed_user_block(content)
        else:
            print("  Skipped — user block will start empty.")
    print()

    # Detect environment
    cloud = _is_cloud_env()
    if cloud:
        print(f"  Detected cloud sandbox — using repo-level config")
        print()

    # Step 2: Install hooks + permissions
    print("[2/4] Hooks + permissions")
    mcp_perm = f"mcp__{app_name}"
    if cloud:
        _install_repo_level_hooks(app_name)
    else:
        settings = _load_settings()
        backup = _backup_settings()
        if backup:
            print(f"  Backed up settings to {backup}")

        hooks = settings.setdefault("hooks", {})
        new_hooks = _yaucca_hooks(app_name)

        for event in ("SessionStart", "Stop", "SessionEnd"):
            existing = hooks.get(event, [])
            filtered = [h for h in existing if not _is_yaucca_hook(h)]
            filtered.extend(new_hooks[event])
            hooks[event] = filtered

        # Auto-approve MCP tools
        perms = settings.setdefault("permissions", {})
        allow = perms.setdefault("allow", [])
        if mcp_perm not in allow:
            allow.append(mcp_perm)

        _save_settings(settings)
        print("  Installed hooks (SessionStart, Stop, SessionEnd)")
        print(f"  Auto-approved MCP tools ({mcp_perm})")
    print()

    # Step 3: Memory rules
    print("[3/4] Memory rules")
    _install_rules_template()
    print()

    # Step 4: MCP server
    print("[4/4] MCP server")
    if cloud:
        _install_repo_level_mcp(app_name)
    else:
        _install_mcp_server(app_name)
    print()

    print("=== Done! ===")
    if cloud:
        print("Hooks and MCP configured for cloud environment.")
        print("MCP uses bearer token auth via YAUCCA_AUTH_TOKEN env var.")
    else:
        print("Start Claude Code and type /mcp to authenticate the yaucca MCP server.")


def uninstall(app_name: str = "yaucca") -> None:
    print(f"=== yaucca-uninstall (app: {app_name}) ===")
    print()

    settings = _load_settings()
    backup = _backup_settings()
    if backup:
        print(f"Backed up settings to {backup}")

    hooks = settings.get("hooks", {})
    for event in ("SessionStart", "Stop", "SessionEnd"):
        existing = hooks.get(event, [])
        filtered = [h for h in existing if not _is_yaucca_hook(h)]
        if filtered:
            hooks[event] = filtered
        elif event in hooks:
            del hooks[event]

    if not hooks:
        settings.pop("hooks", None)

    _save_settings(settings)
    print("Removed yaucca hooks from settings")

    # Remove rules template
    rules_file = Path.home() / ".claude" / "rules" / "yaucca-memory.md"
    if rules_file.exists():
        rules_file.unlink()
        print(f"Removed memory rules at {rules_file}")

    # Remove MCP server
    result = subprocess.run(
        ["claude", "mcp", "remove", "-s", "user", app_name],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"Removed {app_name} MCP server")

    print()
    print("To restore: cp ~/.claude/settings.json.bak ~/.claude/settings.json")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="yaucca-install",
        description="Install yaucca: hooks, memory rules, MCP server, and user profile",
    )
    parser.add_argument("--uninstall", action="store_true", help="Remove everything yaucca installed")
    parser.add_argument("--user-block", type=str, default=None, help="Seed the user memory block (skip interactive prompt)")
    parser.add_argument("--app-name", default="yaucca", help="Modal app name (default: yaucca)")
    args = parser.parse_args()

    if args.uninstall:
        uninstall(app_name=args.app_name)
    else:
        install(user_block=args.user_block, app_name=args.app_name)


if __name__ == "__main__":
    main()
