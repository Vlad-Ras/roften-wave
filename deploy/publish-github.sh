#!/usr/bin/env bash
# Optional ONE-TIME publication command. This script NEVER deploys the app.
# Must be run by a user authenticated with GitHub CLI (gh auth login).
set -euo pipefail
cd "$(dirname "$0")/.."
OWNER="${GITHUB_OWNER:-Vlad-Ras}"
NAME="${GITHUB_REPO:-roften-wave}"
REMOTE="${OWNER}/${NAME}"

if ! command -v gh >/dev/null 2>&1; then
  echo "Install GitHub CLI (gh) and sign in with 'gh auth login'." >&2
  exit 2
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "GitHub CLI not authenticated. Run 'gh auth login' first." >&2
  exit 2
fi
if [ ! -d .git ]; then
  # Supports a clean source ZIP as well as the separately supplied Git bundle.
  git init -b main
  git add .
  if git ls-files | grep -Eq '(^|/)\.env$|(^|/)(data|build|dist)/|\.sqlite[0-9a-z.-]*$|\.mp3$'; then
    echo "Refusing to commit local secrets or temporary audio files." >&2
    exit 3
  fi
  git commit -m "feat: initial Roften Wave 0.1.3 import"
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "Working tree has uncommitted changes; commit the reviewed source first." >&2
  exit 2
fi
if gh repo view "$REMOTE" >/dev/null 2>&1; then
  echo "Remote $REMOTE already exists; pushing the current main branch."
  if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin "https://github.com/${REMOTE}.git"
  else
    git remote add origin "https://github.com/${REMOTE}.git"
  fi
  git push --set-upstream origin main
else
  echo "Creating PRIVATE repository $REMOTE with GitHub CLI, then pushing main."
  gh repo create "$REMOTE" --private --source=. --remote=origin --push
fi
echo "Published: https://github.com/${REMOTE}"
