#!/usr/bin/env bash
# Roften Wave CD target: sync is source-only; release requires explicit dispatch.
set -Eeuo pipefail

mode="$1"
sha="$2"
repo_url="https://github.com/Vlad-Ras/roften-wave.git"
target="/opt/roften-wave"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$mode" == "sync" || "$mode" == "release" ]] || die "Unknown mode"
[[ "$sha" =~ ^[a-f0-9]{40}$ ]] || die "Invalid commit SHA"
command -v git >/dev/null || die "git is not installed"

if [[ ! -d "$target/.git" ]]; then
  if [[ -e "$target" && -n "$(find "$target" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    die "$target already contains files but is not a Git repository; refusing to overwrite them"
  fi
  mkdir -p "$target"
  cd "$target"
  git init -b main
  git remote add origin "$repo_url"
else
  cd "$target"
  origin="$(git remote get-url origin)"
  case "$origin" in
    "https://github.com/Vlad-Ras/roften-wave"|"https://github.com/Vlad-Ras/roften-wave.git"|"git@github.com:Vlad-Ras/roften-wave.git") ;;
    *) die "Unexpected Git origin: $origin" ;;
  esac
  [[ "$(git branch --show-current)" == "main" ]] || die "Expected main branch"
  git diff --quiet || die "Uncommitted tracked changes found"
  git diff --cached --quiet || die "Staged tracked changes found"
fi

git fetch --no-tags origin main
git cat-file -e "$sha^{commit}" || die "Requested commit not found"
git merge-base --is-ancestor "$sha" origin/main || die "Requested SHA is not in origin/main"

if git rev-parse -q --verify HEAD >/dev/null 2>&1; then
  current="$(git rev-parse HEAD)"
  if git merge-base --is-ancestor "$sha" "$current"; then
    printf 'Already on commit %s or newer; no downgrade.\n' "$sha"
  elif git merge-base --is-ancestor "$current" "$sha"; then
    git merge --ff-only "$sha"
  else
    die "Local branch diverged; manual intervention required"
  fi
else
  git checkout -B main "$sha"
fi

installed="$(git rev-parse HEAD)"
printf 'Roften Wave sources: %s (requested %s)\n' "$installed" "$sha"

if [[ "$mode" == "sync" ]]; then
  echo "SYNC ONLY: no Docker commands executed; AzuraCast is untouched."
  exit 0
fi

[[ "$installed" == "$sha" ]] || die "Refusing to release a newer/unverified checkout"
[[ -f .env ]] || die "Missing $target/.env: configure it on the server first"
command -v docker >/dev/null || die "Docker not installed"
docker compose version >/dev/null || die "Docker Compose not installed"

bash deploy/preflight.sh
docker compose -p roften-wave config --quiet
# Touch only this service; do not restart AzuraCast or other Compose projects.
docker compose -p roften-wave up -d --build --no-deps wave
cid="$(docker compose -p roften-wave ps -q wave)"
[[ -n "$cid" ]] || die "Wave container not found"

for attempt in $(seq 1 18); do
  status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid")"
  case "$status" in
    healthy) echo "RELEASE OK: Roften Wave is healthy at $sha"; exit 0 ;;
    unhealthy|exited|dead) die "Wave container status: $status" ;;
  esac
  sleep 5
done
die "Wave did not become healthy within 90 seconds; inspect its logs on the server"
