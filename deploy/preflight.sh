#!/usr/bin/env bash
# Read-only checks. Does not install, configure or restart AzuraCast.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
failed=0
pass() { printf 'OK   %s\n' "$1"; }
warn() { printf 'WARN %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; failed=1; }
if command -v docker >/dev/null 2>&1; then
  pass "Docker command available"
  if docker compose version >/dev/null 2>&1; then pass "Docker Compose available"; else fail "Docker Compose plugin unavailable"; fi
  if docker info >/dev/null 2>&1; then pass "Docker daemon running"; else warn "Docker daemon not reachable (or insufficient permissions)"; fi
else
  fail "Docker not installed"
fi
if [[ -f .env ]]; then
  pass ".env exists"
  # Avoid executing the user-controlled .env as shell code; we only inspect expected keys.
  for key in APP_ADMIN_PASSWORD APP_SESSION_SECRET YANDEX_MUSIC_TOKEN AZURACAST_DJ_HOST AZURACAST_DJ_PORT AZURACAST_DJ_MOUNT AZURACAST_DJ_USERNAME AZURACAST_DJ_PASSWORD; do
    value="$(sed -n "s/^${key}=//p" .env | tail -n 1)"
    if [[ -z "$value" || "$value" == REPLACE_WITH* ]]; then fail "$key is unset or a placeholder"; else pass "$key is set"; fi
  done
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    if docker compose config --quiet >/dev/null 2>&1; then pass "Compose configuration parses"; else fail "Compose configuration invalid"; fi
  fi
else
  fail "Missing .env: create it from .env.example"
fi
if [[ "$failed" -ne 0 ]]; then
  printf '\nFix FAIL lines before running docker compose up -d --build.\n' >&2
  exit 1
fi
printf '\nLocal preflight OK. After launch, use web Settings -> Check connections.\n'
