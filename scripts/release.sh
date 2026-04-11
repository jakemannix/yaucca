#!/bin/bash
# Bump minor version, commit, tag, push, and trigger PyPI release.
#
# Usage:
#   ./scripts/release.sh          # bump minor: 0.3.0 → 0.4.0
#   ./scripts/release.sh patch    # bump patch: 0.3.0 → 0.3.1
#   ./scripts/release.sh major    # bump major: 0.3.0 → 1.0.0
#   ./scripts/release.sh 0.5.0    # set explicit version
#
# After push, GitHub Actions builds and publishes to PyPI via trusted publisher.

set -euo pipefail

PYPROJECT="pyproject.toml"
CURRENT=$(grep '^version' "$PYPROJECT" | head -1 | sed 's/version = "\(.*\)"/\1/')
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"

BUMP="${1:-minor}"

case "$BUMP" in
  patch)  NEW="$MAJOR.$MINOR.$((PATCH + 1))" ;;
  minor)  NEW="$MAJOR.$((MINOR + 1)).0" ;;
  major)  NEW="$((MAJOR + 1)).0.0" ;;
  *.*.*)  NEW="$BUMP" ;;
  *)      echo "Usage: $0 [patch|minor|major|X.Y.Z]"; exit 1 ;;
esac

echo "Bumping $CURRENT → $NEW"

# Update pyproject.toml
sed -i '' "s/^version = \"$CURRENT\"/version = \"$NEW\"/" "$PYPROJECT"

# Commit, tag, push
git add "$PYPROJECT"
git commit -m "Release v$NEW"
git tag "v$NEW"
git push && git push --tags

# Create GitHub release (triggers PyPI publish via Actions)
echo ""
echo "Creating GitHub release v$NEW..."
GITHUB_TOKEN= gh release create "v$NEW" --title "v$NEW" --generate-notes

echo ""
echo "Released v$NEW → PyPI publish triggered."
echo "https://github.com/jakemannix/yaucca/releases/tag/v$NEW"
