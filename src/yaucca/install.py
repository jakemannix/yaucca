"""Install yaucca hooks into Claude Code settings.

Usage:
    uv run python -m yaucca.install          # install
    uv run python -m yaucca.install --uninstall  # revert

Reads the yaucca project directory from the location of this file,
and injects SessionStart/Stop/SessionEnd hooks into ~/.claude/settings.json.
Preserves all other settings. Creates a backup before modifying.
"""

import argparse
import importlib.resources
import json
import shutil
from pathlib import Path

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

# Marker so we can identify our hooks vs. user's other hooks
YAUCCA_MARKER = "yaucca.hooks"


def _project_dir() -> str:
    """Return the yaucca project root (parent of src/yaucca/)."""
    return str(Path(__file__).resolve().parent.parent.parent)


def _yaucca_hooks() -> dict:
    """Build the hooks config for yaucca."""
    project_dir = _project_dir()
    return {
        "SessionStart": [
            {
                "matcher": "startup|resume|compact|clear",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"cd {project_dir} && uv run python -m yaucca.hooks session_start",
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
                        "command": f"cd {project_dir} && uv run python -m yaucca.hooks stop",
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
                        "command": f"cd {project_dir} && uv run python -m yaucca.hooks session_end",
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


def install() -> None:
    settings = _load_settings()
    backup = _backup_settings()
    if backup:
        print(f"Backed up settings to {backup}")

    hooks = settings.setdefault("hooks", {})
    new_hooks = _yaucca_hooks()

    for event in ("SessionStart", "Stop", "SessionEnd"):
        existing = hooks.get(event, [])
        # Remove any previous yaucca hooks
        filtered = [h for h in existing if not _is_yaucca_hook(h)]
        # Add ours
        filtered.extend(new_hooks[event])
        hooks[event] = filtered

    _save_settings(settings)

    project_dir = _project_dir()
    print(f"Installed yaucca hooks (project: {project_dir})")
    print("  SessionStart: timeout=30s")
    print("  Stop: timeout=10s (raw turn persistence only)")
    print("  SessionEnd: timeout=120s (summary + context update)")
    print()

    # Check .env
    env_file = Path(project_dir) / ".env"
    if not env_file.exists():
        print("WARNING: No .env file found. Create one with:")
        print(f"  cat > {env_file} << EOF")
        print("  YAUCCA_URL=https://<username>--yaucca-serve.modal.run")
        print("  YAUCCA_AUTH_TOKEN=<token>")
        print("  EOF")
    else:
        print(f".env found at {env_file}")

    # Offer to install memory rules template
    _install_rules_template()


def _install_rules_template() -> None:
    """Install the yaucca memory rules template into ~/.claude/rules/."""
    rules_dir = Path.home() / ".claude" / "rules"
    target = rules_dir / "yaucca-memory.md"

    if target.exists():
        print(f"Memory rules already installed at {target}")
        return

    # Read the template from the package
    template_ref = importlib.resources.files("yaucca.templates").joinpath("memory-rules.md")
    template_text = template_ref.read_text(encoding="utf-8")

    rules_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(template_text)
    print(f"Installed memory rules template at {target}")
    print("  Edit this file to customize how Claude uses your memory blocks.")


def uninstall() -> None:
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

    # Clean up empty hooks dict
    if not hooks:
        settings.pop("hooks", None)

    _save_settings(settings)
    print("Removed yaucca hooks from Claude Code settings")
    print("To restore the backup: cp ~/.claude/settings.json.bak ~/.claude/settings.json")

    # Remove rules template if it exists
    rules_file = Path.home() / ".claude" / "rules" / "yaucca-memory.md"
    if rules_file.exists():
        rules_file.unlink()
        print(f"Removed memory rules at {rules_file}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="yaucca-install",
        description="Install/uninstall yaucca hooks in Claude Code",
    )
    parser.add_argument("--uninstall", action="store_true", help="Remove yaucca hooks")
    args = parser.parse_args()

    if args.uninstall:
        uninstall()
    else:
        install()


if __name__ == "__main__":
    main()
