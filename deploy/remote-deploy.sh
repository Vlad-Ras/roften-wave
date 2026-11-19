#!/usr/bin/env bash
# Single production environment; runs only after successful CI on main.
set -Eeuo pipefail
umask 077
[[ "$#" == 2 ]] || { echo "Expected release and SHA" >&2; exit 2; }
mode="$1"
sha="$2"
target="/opt/roften-wave"
repo_url="https://github.com/Vlad-Ras/roften-wave.git"
project="roften-wave"
volume="roften-wave_wave_data"
production_image="roften-wave:production"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "$mode" == release ]] || die "Only production release mode is supported"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "Invalid SHA"
command -v git >/dev/null || die "git is required"
command -v docker >/dev/null || die "Docker is required"
docker compose version >/dev/null || die "Docker Compose plugin is required"
docker info >/dev/null 2>&1 || die "Deployment user cannot access Docker"

previous_source=""
previous_image=""
snapshot_file=""
switched_image=0
completed=0

rollback() {
  set +e
  trap - EXIT
  echo "ROLLBACK: restoring previous Roften Wave service" >&2
  if [[ "$switched_image" == 1 ]]; then
    docker compose -p "$project" stop wave >&2
    if [[ -n "$snapshot_file" && -s "$snapshot_file" && -n "$previous_image" ]]; then
      docker run --rm --network none --security-opt no-new-privileges \
        --mount "type=volume,source=$volume,target=/data" \
        --mount "type=bind,source=$target/.backups,target=/backups,readonly" \
        --mount "type=bind,source=$target/deploy,target=/scripts,readonly" \
        --entrypoint python "$previous_image" /scripts/sqlite_snapshot.py \
        restore "/backups/$(basename "$snapshot_file")" /data/wave.sqlite3 10001 10001 >&2
    fi
    if [[ -n "$previous_image" ]]; then
      docker image tag "$previous_image" "$production_image"
    else
      docker compose -p "$project" rm -sf wave >&2
      docker image rm "$production_image" >/dev/null 2>&1
    fi
  fi
  if [[ -n "$previous_source" ]]; then
    git reset --hard "$previous_source" >&2
  fi
  if [[ "$switched_image" == 1 && -n "$previous_image" ]]; then
    docker compose -p "$project" up -d --no-deps --no-build --force-recreate wave >&2
    echo "ROLLBACK attempted; verify old container health." >&2
  fi
}
on_exit() {
  code=$?
  if [[ "$completed" != 1 && "$code" != 0 ]]; then
    rollback
  fi
}
trap on_exit EXIT

if [[ -d "$target/.git" ]]; then
  cd "$target"
  origin="$(git remote get-url origin)"
  case "$origin" in
    "https://github.com/Vlad-Ras/roften-wave"|"https://github.com/Vlad-Ras/roften-wave.git"|"git@github.com:Vlad-Ras/roften-wave.git") ;;
    *) die "Unrecognized Git origin: $origin" ;;
  esac
  [[ "$(git branch --show-current)" == main ]] || die "Expected main branch"
  git diff --quiet && git diff --cached --quiet || die "Tracked source files have local changes"
else
  if [[ -e "$target" && -n "$(find "$target" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    die "Nonempty deployment path is not a Git repo; refusing overwrite"
  fi
  mkdir -p "$target"
  cd "$target"
  git init -b main
  git remote add origin "$repo_url"
fi

previous_source="$(git rev-parse --verify HEAD 2>/dev/null || true)"
git fetch --no-tags origin main
git cat-file -e "$sha^{commit}" || die "Requested SHA missing from Git"
if [[ "$(git rev-parse origin/main)" != "$sha" ]]; then
  echo "SKIP: this CI result was superseded by a newer main commit"
  completed=1
  exit 0
fi
if [[ -n "$previous_source" ]]; then
  if git merge-base --is-ancestor "$sha" "$previous_source" && [[ "$previous_source" != "$sha" ]]; then
    echo "SKIP: newer source code is already checked out"
    completed=1
    exit 0
  fi
  git merge-base --is-ancestor "$previous_source" "$sha" || die "Git history diverged"
  git merge --ff-only "$sha"
else
  git checkout -B main "$sha"
fi

# Fail closed before touching any live container.
[[ -f .env && -r .env ]] || die "Production .env missing or unreadable on the VDS"
bash deploy/preflight.sh
docker compose -p "$project" config --quiet

if docker container inspect roften-wave >/dev/null 2>&1; then
  label="$(docker container inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' roften-wave)"
  service="$(docker container inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' roften-wave)"
  [[ "$label" == "$project" && "$service" == wave ]] || die "Existing roften-wave container belongs to another Compose project"
  previous_image="$(docker container inspect -f '{{.Image}}' roften-wave)"
fi
if [[ -f .production-sha && -n "$previous_image" ]] && [[ "$(cat .production-sha)" == "$sha" ]]; then
  status="$(docker container inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' roften-wave)"
  if [[ "$status" == healthy ]]; then
    echo "ALREADY DEPLOYED: $sha"
    completed=1
    exit 0
  fi
fi

mkdir -p .backups
chmod 700 .backups
short_sha="$(printf '%s' "$sha" | cut -c1-12)"
if [[ -n "$previous_image" ]]; then
  docker volume inspect "$volume" >/dev/null || die "Existing Wave service lacks expected SQLite volume"
  snapshot_file="$target/.backups/wave_$(date -u +%Y%m%dT%H%M%SZ)_$short_sha.sqlite3"
  echo "Backing up the online SQLite database before restart"
  docker run --rm --network none --security-opt no-new-privileges \
    --mount "type=volume,source=$volume,target=/data,readonly" \
    --mount "type=bind,source=$target/.backups,target=/backups" \
    --mount "type=bind,source=$target/deploy,target=/scripts,readonly" \
    --entrypoint python "$previous_image" /scripts/sqlite_snapshot.py \
    backup /data/wave.sqlite3 "/backups/$(basename "$snapshot_file")" "$(id -u)" "$(id -g)"
  [[ -s "$snapshot_file" ]] || die "SQLite backup failed"
fi

candidate="roften-wave:candidate-$short_sha"
echo "Building candidate Docker image while old service remains untouched"
docker build -t "$candidate" .
switched_image=1
docker image tag "$candidate" "$production_image"
docker compose -p "$project" up -d --no-deps --no-build --force-recreate wave
cid="$(docker compose -p "$project" ps -q wave)"
[[ -n "$cid" ]] || die "New Wave container not found"
for attempt in $(seq 1 30); do
  status="$(docker container inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid")"
  case "$status" in
    healthy)
      printf '%s\n' "$sha" > .production-sha.tmp
      mv -f .production-sha.tmp .production-sha
      completed=1
      echo "PRODUCTION OK: $sha (Docker healthcheck healthy)"
      exit 0
      ;;
    unhealthy|exited|dead) die "Wave container status: $status" ;;
  esac
  sleep 3
done
die "Wave failed to become healthy within 90 seconds"
