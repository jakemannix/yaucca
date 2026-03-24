"""Recreate Modal secrets from .env for yaucca deployment.

Usage:
    uv run --extra deploy python -m yaucca.deploy_secrets
"""

import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

REQUIRED = ["YAUCCA_AUTH_TOKEN", "OPENROUTER_API_KEY", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"]
OPTIONAL = {
    "YAUCCA_ISSUER_URL": "https://jakemannix--yaucca-serve.modal.run",
    "GITHUB_ALLOWED_USERS": "jakemannix",
}
SECRET_NAME = "yaucca-secrets"


def main() -> None:
    # Load .env from the project root
    env_file = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_file.exists():
        load_dotenv(env_file)
        print(f"Loaded {env_file}")
    else:
        print(f"WARNING: {env_file} not found, using environment only")

    # Collect values
    secrets: dict[str, str] = {}
    missing = []
    for key in REQUIRED:
        val = os.environ.get(key, "")
        if not val:
            missing.append(key)
        secrets[key] = val
    for key, default in OPTIONAL.items():
        secrets[key] = os.environ.get(key, default)

    if missing:
        print(f"ERROR: Missing required vars: {', '.join(missing)}")
        print("Add them to .env and retry.")
        sys.exit(1)

    # Delete + recreate
    subprocess.run(["modal", "secret", "delete", SECRET_NAME, "--yes"], capture_output=True)
    args = ["modal", "secret", "create", SECRET_NAME]
    for k, v in secrets.items():
        args.append(f"{k}={v}")

    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: {result.stderr}")
        sys.exit(1)

    print(f"Created Modal secret '{SECRET_NAME}' with keys: {', '.join(secrets.keys())}")


if __name__ == "__main__":
    main()
