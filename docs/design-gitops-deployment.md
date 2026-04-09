# Design: GitOps / Infrastructure-as-Code Deployment

**Priority**: Medium
**Status**: Proposed

## Problem

yaucca is open source, but deploying your own instance requires manual steps:
1. `yaucca-deploy` (interactive CLI wizard)
2. `modal deploy src/yaucca/cloud/modal_app.py`
3. Manual secret management via `yaucca-deploy-secrets`

There's no continuous deployment — merging to main doesn't deploy anything.
Jake's personal instance requires manual `modal deploy` from his laptop.

For an open source project, every user who forks yaucca should be able to
set up their own auto-deploying instance without copying CI/CD config by hand.

## Goals

1. Any user can run a single script to scaffold a private GitOps repo
2. That repo auto-deploys their yaucca instance on push to main
3. Secrets stay in the private repo (GitHub Secrets / Modal Secrets)
4. Upstream yaucca updates can be pulled in cleanly
5. No CI/CD config lives in the public yaucca repo

## Architecture

```
┌─────────────────────────────┐     ┌─────────────────────────────┐
│  jakemannix/yaucca (public) │     │  user/yaucca-infra (private)│
│                             │     │                             │
│  src/yaucca/                │     │  .github/workflows/         │
│  docs/                      │────▶│    deploy.yml               │
│  scripts/                   │     │  config/                    │
│    scaffold-infra.sh        │     │    modal-app.py             │
│  templates/                 │     │    .env.template            │
│    infra/                   │     │  README.md                  │
│      deploy.yml.j2          │     │                             │
│      modal-app.py.j2        │     │  (GitHub Secrets:           │
│      README.md.j2           │     │   MODAL_TOKEN_ID,           │
│                             │     │   MODAL_TOKEN_SECRET,       │
│                             │     │   YAUCCA_AUTH_TOKEN, ...)   │
└─────────────────────────────┘     └─────────────────────────────┘
                                              │
                                              │ push to main
                                              ▼
                                    ┌─────────────────────┐
                                    │  GitHub Actions      │
                                    │  modal deploy ...    │
                                    └─────────┬───────────┘
                                              │
                                              ▼
                                    ┌─────────────────────┐
                                    │  Modal.com           │
                                    │  user's yaucca       │
                                    │  instance             │
                                    └─────────────────────┘
```

## The Scaffold Script

`scripts/scaffold-infra.sh` — run once to create your private infra repo.

```bash
#!/bin/bash
# Usage: ./scripts/scaffold-infra.sh [--repo-name yaucca-infra] [--app-name yaucca]

REPO_NAME="${1:-yaucca-infra}"
APP_NAME="${2:-yaucca}"
MODAL_USERNAME=$(modal profile current 2>/dev/null)

echo "=== yaucca infrastructure scaffolding ==="
echo "  Repo: $REPO_NAME"
echo "  App:  $APP_NAME"
echo "  Modal user: $MODAL_USERNAME"

# Create the repo directory
mkdir -p "$REPO_NAME"/{.github/workflows,config}

# Generate deploy workflow
cat > "$REPO_NAME/.github/workflows/deploy.yml" << 'WORKFLOW'
... (templated content)
WORKFLOW

# Generate Modal app wrapper
cat > "$REPO_NAME/config/modal-app.py" << 'MODAL'
... (templated content)
MODAL

echo "=== Done! ==="
echo "Next steps:"
echo "  cd $REPO_NAME"
echo "  gh repo create --private $REPO_NAME"
echo "  # Add GitHub Secrets (see README)"
echo "  git push -u origin main"
```

## Generated Files

### `.github/workflows/deploy.yml`

```yaml
name: Deploy yaucca

on:
  push:
    branches: [main]
  workflow_dispatch:  # manual trigger

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v6

      - name: Install yaucca[deploy]
        run: uv pip install --system "yaucca[deploy]"

      - name: Install Modal
        run: uv pip install --system modal

      - name: Push secrets to Modal
        env:
          MODAL_TOKEN_ID: ${{ secrets.MODAL_TOKEN_ID }}
          MODAL_TOKEN_SECRET: ${{ secrets.MODAL_TOKEN_SECRET }}
          YAUCCA_AUTH_TOKEN: ${{ secrets.YAUCCA_AUTH_TOKEN }}
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
          GITHUB_CLIENT_ID: ${{ secrets.GH_OAUTH_CLIENT_ID }}
          GITHUB_CLIENT_SECRET: ${{ secrets.GH_OAUTH_CLIENT_SECRET }}
          GITHUB_ALLOWED_USERS: ${{ secrets.GH_ALLOWED_USERS }}
          YAUCCA_ISSUER_URL: ${{ secrets.YAUCCA_ISSUER_URL }}
        run: |
          # Write temp .env from GitHub Secrets
          cat > /tmp/yaucca.env << EOF
          YAUCCA_AUTH_TOKEN=$YAUCCA_AUTH_TOKEN
          OPENROUTER_API_KEY=$OPENROUTER_API_KEY
          GITHUB_CLIENT_ID=$GITHUB_CLIENT_ID
          GITHUB_CLIENT_SECRET=$GITHUB_CLIENT_SECRET
          GITHUB_ALLOWED_USERS=$GITHUB_ALLOWED_USERS
          YAUCCA_ISSUER_URL=$YAUCCA_ISSUER_URL
          EOF
          yaucca-deploy-secrets --env-file /tmp/yaucca.env

      - name: Deploy to Modal
        env:
          MODAL_TOKEN_ID: ${{ secrets.MODAL_TOKEN_ID }}
          MODAL_TOKEN_SECRET: ${{ secrets.MODAL_TOKEN_SECRET }}
        run: |
          modal deploy src/yaucca/cloud/modal_app.py
```

### `config/modal-app.py` (optional override)

For users who want to customize their Modal deployment (custom app name,
different scale-down window, additional secrets, etc.):

```python
"""Custom Modal app for my yaucca instance.

Override the default modal_app.py with instance-specific config.
Deploy with: modal deploy config/modal-app.py
"""
import modal

APP_NAME = "yaucca"  # Change for multiple instances
VOLUME_NAME = f"{APP_NAME}-data"
SECRET_NAME = f"{APP_NAME}-secrets"

app = modal.App(APP_NAME)
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("yaucca[deploy]")
)

@app.function(
    image=image,
    volumes={"/data": volume},
    scaledown_window=300,
    secrets=[modal.Secret.from_name(SECRET_NAME)],
)
@modal.concurrent(max_inputs=10)
@modal.asgi_app()
def serve():
    import os
    from yaucca.cloud.server import create_composite_app
    return create_composite_app(
        db_path="/data/yaucca.db",
        on_write=lambda: None,
        commit_fn=volume.commit,
        issuer_url=os.environ.get("YAUCCA_ISSUER_URL"),
    )
```

### `README.md`

Generated with the user's app name, Modal username, and endpoint URL.
Includes:
- Quick start (what secrets to set)
- How to trigger a deploy
- How to pull upstream yaucca updates
- How to customize (app name, scale-down, etc.)

## Required GitHub Secrets

| Secret | Source | Required |
|--------|--------|----------|
| `MODAL_TOKEN_ID` | `modal token new` | Yes |
| `MODAL_TOKEN_SECRET` | `modal token new` | Yes |
| `YAUCCA_AUTH_TOKEN` | Generate with `openssl rand -hex 32` | Yes |
| `OPENROUTER_API_KEY` | openrouter.ai dashboard | Yes |
| `GH_OAUTH_CLIENT_ID` | GitHub OAuth App settings | Yes |
| `GH_OAUTH_CLIENT_SECRET` | GitHub OAuth App settings | Yes |
| `GH_ALLOWED_USERS` | Comma-separated GitHub usernames | Yes |
| `YAUCCA_ISSUER_URL` | `https://<modal-user>--<app>-serve.modal.run` | Yes |

## Pulling Upstream Updates

The infra repo doesn't fork yaucca — it installs it from PyPI. To get
new yaucca features:

1. Upstream merges a feature to `jakemannix/yaucca` main
2. Upstream cuts a new PyPI release (via GitHub Release + trusted publisher)
3. User's infra repo workflow runs `uv pip install yaucca[deploy]` — gets latest
4. `modal deploy` picks up the new code

For pinned versions, the infra repo can specify `yaucca[deploy]==0.4.0` in
a `requirements.txt` or the workflow file.

## Version Pinning (Optional)

Add a `config/requirements.txt` to the infra repo:

```
yaucca[deploy]>=0.3.0,<0.4.0
```

Then in the workflow:
```yaml
- run: uv pip install --system -r config/requirements.txt
```

## Future: Multi-Instance Support

The scaffold script already accepts `--app-name`. A user could run it
twice with different names to get separate dev/prod instances:

```bash
./scripts/scaffold-infra.sh yaucca-infra yaucca
./scripts/scaffold-infra.sh yaucca-dev-infra yaucca-dev
```

Each gets its own Modal app, volume, secrets, and GitHub Actions workflow.

## Implementation Plan

1. Create `templates/infra/` directory with Jinja2 or heredoc templates
2. Write `scripts/scaffold-infra.sh` that generates a complete infra repo
3. Test: scaffold, create private repo, set secrets, push, verify deploy
4. Document in main README under "Self-hosting"
5. Consider: `yaucca-scaffold` CLI entry point (friendlier than running a script from the repo)
