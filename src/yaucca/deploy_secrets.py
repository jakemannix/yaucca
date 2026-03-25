"""Push secrets from .env to Modal for yaucca deployment.

Usage:
    yaucca-deploy-secrets                              # default app
    yaucca-deploy-secrets --app-name yaucca-test       # test instance
    yaucca-deploy-secrets --env-file /path/to/.env     # custom .env
"""

import argparse
import subprocess
import sys
from pathlib import Path

DEFAULT_APP_NAME = "yaucca"
CONFIG_BASE = Path.home() / ".config" / "yaucca"

REQUIRED_KEYS = [
    "YAUCCA_AUTH_TOKEN",
    "OPENROUTER_API_KEY",
    "GITHUB_CLIENT_ID",
    "GITHUB_CLIENT_SECRET",
]
OPTIONAL_KEYS = [
    "YAUCCA_ISSUER_URL",
    "GITHUB_ALLOWED_USERS",
]


def _load_env(env_file: Path) -> dict[str, str]:
    """Load key=value pairs from an .env file."""
    env = {}
    if not env_file.exists():
        return env
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def _secret_name(app_name: str) -> str:
    return f"{app_name}-secrets"


def _default_env_file(app_name: str) -> Path:
    if app_name == DEFAULT_APP_NAME:
        return CONFIG_BASE / ".env"
    return CONFIG_BASE / app_name / ".env"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="yaucca-deploy-secrets",
        description="Push secrets from .env to Modal",
    )
    parser.add_argument("--app-name", default=DEFAULT_APP_NAME,
                        help=f"Modal app name (default: {DEFAULT_APP_NAME})")
    parser.add_argument("--env-file", type=Path, default=None,
                        help="Path to .env file (default: ~/.config/yaucca/.env)")
    args = parser.parse_args()

    env_file = args.env_file or _default_env_file(args.app_name)
    secret_name = _secret_name(args.app_name)

    if not env_file.exists():
        # Fall back to project-local .env for backwards compatibility
        local_env = Path(".env")
        if local_env.exists():
            env_file = local_env
            print(f"Using local {env_file}")
        else:
            print(f"ERROR: No .env found at {env_file}")
            print("Run `yaucca-deploy` first to create one.")
            sys.exit(1)
    else:
        print(f"Loaded {env_file}")

    env = _load_env(env_file)

    # Collect secrets
    secrets: dict[str, str] = {}
    missing = []
    for key in REQUIRED_KEYS:
        val = env.get(key, "")
        if not val or val.startswith("<"):
            missing.append(key)
        else:
            secrets[key] = val

    for key in OPTIONAL_KEYS:
        val = env.get(key, "")
        if val and not val.startswith("<"):
            secrets[key] = val

    if missing:
        print(f"ERROR: Missing required keys: {', '.join(missing)}")
        print(f"Edit {env_file} and fill them in.")
        sys.exit(1)

    # Delete + recreate
    subprocess.run(["modal", "secret", "delete", secret_name, "--yes"], capture_output=True)

    cmd = ["modal", "secret", "create", secret_name]
    for k, v in secrets.items():
        cmd.append(f"{k}={v}")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: {result.stderr}")
        sys.exit(1)

    print(f"Created Modal secret '{secret_name}' with keys: {', '.join(secrets.keys())}")


if __name__ == "__main__":
    main()
