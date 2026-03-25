"""Interactive server deployment for yaucca.

Usage:
    yaucca-deploy                       # guided setup (app name: yaucca)
    yaucca-deploy --app-name yaucca-test  # deploy a test instance
    yaucca-deploy --check               # verify existing deployment

Walks through Modal setup, GitHub OAuth App creation, .env configuration,
and deployment. Secrets are never entered into the CLI — they go in .env.
"""

import subprocess
import sys
from pathlib import Path

DEFAULT_APP_NAME = "yaucca"
CONFIG_BASE = Path.home() / ".config" / "yaucca"


def _config_dir(app_name: str) -> Path:
    if app_name == DEFAULT_APP_NAME:
        return CONFIG_BASE
    return CONFIG_BASE / app_name


def _env_file(app_name: str) -> Path:
    return _config_dir(app_name) / ".env"


def _secret_name(app_name: str) -> str:
    return f"{app_name}-secrets"


def _volume_name(app_name: str) -> str:
    return f"{app_name}-data"


def _server_url(username: str, app_name: str) -> str:
    return f"https://{username}--{app_name}-serve.modal.run"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _get_modal_username() -> str | None:
    result = _run(["modal", "profile", "current"])
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _check_modal() -> str | None:
    result = _run(["modal", "--version"])
    if result.returncode != 0:
        print("  ERROR: modal CLI not found.")
        print("  Install with: uv pip install modal")
        print("  Then run: modal setup")
        return None

    username = _get_modal_username()
    if not username:
        print("  ERROR: Not logged in to Modal.")
        print("  Run: modal setup")
        return None

    print(f"  Modal account: {username}")
    return username


def _check_deployment(url: str) -> bool:
    import httpx

    try:
        resp = httpx.get(f"{url}/health", timeout=15.0)
        if resp.status_code == 200:
            data = resp.json()
            print(f"  Health: OK (vec={data.get('vec_enabled')}, pending={data.get('embed_queue_pending')})")
            return True
    except Exception as e:
        print(f"  Health check failed: {e}")
    return False


def _env_has_key(env_file: Path, key: str) -> bool:
    if not env_file.exists():
        return False
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            value = line.split("=", 1)[1].strip()
            return bool(value) and not value.startswith("<")
    return False


def deploy(app_name: str) -> None:
    env_file = _env_file(app_name)
    config_dir = _config_dir(app_name)
    secret_name = _secret_name(app_name)

    print(f"=== yaucca-deploy: server setup (app: {app_name}) ===")
    print()

    # Step 1: Check Modal
    print("[1/5] Modal account")
    username = _check_modal()
    if not username:
        raise SystemExit(1)
    print()

    # Step 2: Compute URL
    url = _server_url(username, app_name)
    print("[2/5] Your server URL")
    print(f"  {url}")
    print()

    # Step 3: GitHub OAuth App
    callback_url = f"{url}/oauth/github/callback"
    print("[3/5] GitHub OAuth App")
    print("  Create one at: https://github.com/settings/developers → New OAuth App")
    print()
    print("  Fill in:")
    print(f"    Application name:     {app_name}")
    print(f"    Homepage URL:         {url}")
    print(f"    Authorization callback URL: {callback_url}")
    print()
    print("  After creating, note the Client ID and generate a Client Secret.")
    print()

    # Step 4: Create/update .env
    print("[4/5] Configuration")
    config_dir.mkdir(parents=True, exist_ok=True)

    required_keys = ("YAUCCA_URL", "YAUCCA_AUTH_TOKEN", "OPENROUTER_API_KEY",
                     "YAUCCA_ISSUER_URL", "GITHUB_CLIENT_ID",
                     "GITHUB_CLIENT_SECRET", "GITHUB_ALLOWED_USERS")

    if env_file.exists():
        print(f"  Found existing config at {env_file}")
        missing = [k for k in required_keys if not _env_has_key(env_file, k)]
        if missing:
            print(f"  Missing keys: {', '.join(missing)}")
            print(f"  Edit {env_file} and fill them in.")
        else:
            print("  All keys present.")
    else:
        import secrets
        auth_token = secrets.token_urlsafe(32)

        env_file.write_text(
            f"YAUCCA_URL={url}\n"
            f"YAUCCA_AUTH_TOKEN={auth_token}\n"
            f"OPENROUTER_API_KEY=<your-openrouter-key-from-https://openrouter.ai/keys>\n"
            f"YAUCCA_ISSUER_URL={url}\n"
            f"GITHUB_CLIENT_ID=<from-github-oauth-app>\n"
            f"GITHUB_CLIENT_SECRET=<from-github-oauth-app>\n"
            f"GITHUB_ALLOWED_USERS={username}\n"
        )
        print(f"  Created {env_file}")
        print(f"  Auth token generated: {auth_token[:8]}...")
        print()
        print(f"  *** Edit {env_file} and fill in: ***")
        print("    OPENROUTER_API_KEY  — from https://openrouter.ai/keys")
        print("    GITHUB_CLIENT_ID    — from your GitHub OAuth App")
        print("    GITHUB_CLIENT_SECRET — from your GitHub OAuth App")

    print()

    # Check completeness
    all_set = all(_env_has_key(env_file, k) for k in required_keys)
    if not all_set:
        print(f"  Fill in the missing values in {env_file}, then re-run:")
        rerun = f"yaucca-deploy{'' if app_name == DEFAULT_APP_NAME else f' --app-name {app_name}'}"
        print(f"    {rerun}")
        print()
        print("  (The deploy will pick up where it left off.)")
        raise SystemExit(1)

    # Step 5: Deploy
    print("[5/5] Deploying to Modal")

    # Push secrets
    print("  Pushing secrets to Modal...")
    result = _run([
        sys.executable, "-m", "yaucca.deploy_secrets",
        "--app-name", app_name,
        "--env-file", str(env_file),
    ])
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr.strip()}")
        raise SystemExit(1)
    print(f"  Secrets pushed to '{secret_name}' ✓")

    # Deploy — generate a temporary modal app file if non-default name
    if app_name == DEFAULT_APP_NAME:
        modal_app_path = "src/yaucca/cloud/modal_app.py"
    else:
        modal_app_path = _generate_modal_app(app_name)

    print("  Deploying app...")
    result = _run(["modal", "deploy", modal_app_path])
    if result.returncode != 0:
        result = _run([sys.executable, "-m", "modal", "deploy", modal_app_path])
    if result.returncode != 0:
        print(f"  ERROR: Deploy failed: {result.stderr.strip()}")
        raise SystemExit(1)
    print("  Deployed ✓")

    # Clean up temp file
    if app_name != DEFAULT_APP_NAME:
        Path(modal_app_path).unlink(missing_ok=True)

    # Health check
    print("  Checking health...")
    _check_deployment(url)

    print()
    print("=== Server running! ===")
    print()
    print("Next: run `yaucca-install` on every machine where you use Claude Code.")
    if app_name != DEFAULT_APP_NAME:
        print(f"  (Use --app-name {app_name} with yaucca-install for test instances)")


def _generate_modal_app(app_name: str) -> str:
    """Generate a temporary modal_app.py for a non-default app name."""
    secret_name = _secret_name(app_name)
    volume_name = _volume_name(app_name)
    content = f'''"""Auto-generated Modal app for {app_name}."""
import modal

app = modal.App("{app_name}")
volume = modal.Volume.from_name("{volume_name}", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "fastapi>=0.115.0", "uvicorn>=0.30.0", "httpx>=0.27.0",
        "pydantic>=2.0.0", "pydantic-settings>=2.0.0",
        "sqlite-vec>=0.1.0", "mcp>=1.25.0", "sse-starlette>=2.0.0",
    )
    .add_local_python_source("yaucca")
)

@app.function(
    image=image,
    volumes={{"/data": volume}},
    scaledown_window=300,
    secrets=[modal.Secret.from_name("{secret_name}")],
)
@modal.concurrent(max_inputs=10)
@modal.asgi_app()
def serve():
    import os
    from yaucca.cloud.server import create_composite_app
    return create_composite_app(
        db_path="/data/{app_name}.db",
        on_write=lambda: None,
        commit_fn=volume.commit,
        issuer_url=os.environ.get("YAUCCA_ISSUER_URL"),
    )
'''
    path = f"src/yaucca/cloud/modal_app_{app_name.replace('-', '_')}.py"
    Path(path).write_text(content)
    return path


def check(app_name: str) -> None:
    env_file = _env_file(app_name)

    print(f"=== yaucca-deploy --check (app: {app_name}) ===")
    print()

    if not env_file.exists():
        print(f"No config found at {env_file}")
        print("Run `yaucca-deploy` first.")
        raise SystemExit(1)

    url = None
    for line in env_file.read_text().splitlines():
        if line.startswith("YAUCCA_URL="):
            url = line.split("=", 1)[1].strip()
            break

    if not url or url.startswith("<"):
        print("YAUCCA_URL not configured in .env")
        raise SystemExit(1)

    print(f"Server: {url}")
    if _check_deployment(url):
        print("Everything looks good.")
    else:
        print("Server unreachable — redeploy with `yaucca-deploy`")
        raise SystemExit(1)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="yaucca-deploy",
        description="Deploy your yaucca server to Modal",
    )
    parser.add_argument("--app-name", default=DEFAULT_APP_NAME,
                        help=f"Modal app name (default: {DEFAULT_APP_NAME})")
    parser.add_argument("--check", action="store_true", help="Verify existing deployment")
    args = parser.parse_args()

    if args.check:
        check(args.app_name)
    else:
        deploy(args.app_name)


if __name__ == "__main__":
    main()
