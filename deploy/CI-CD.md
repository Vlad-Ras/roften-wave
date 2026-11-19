# Roften Wave — single-environment production CI/CD

Repository: https://github.com/Vlad-Ras/roften-wave
Production VPS: **199.189.254.39**
Source checkout: **/opt/roften-wave**
Compose project/service: **roften-wave / wave**

There is **no staging**. Each **successful push to main** (not a PR) deploys
the exact tested commit to the production server over pinned-key SSH.
A manual **release** can also be started in GitHub Actions.
No workflow uses `docker compose down` on the AzuraCast project.

## Before enabling the first production release

The existing GitHub Actions SSH connection already uses:

- `DEPLOY_SSH_KEY_B64` (preferred) or legacy `DEPLOY_SSH_KEY` secret
- `DEPLOY_SSH_KNOWN_HOSTS` secret, pinned and verified
- `DEPLOY_SSH_USER` variable (`wave-deploy`)
- Optional `DEPLOY_SSH_PORT` variable (default `22`)

On the VPS, the `wave-deploy` user needs access to Docker. One straightforward
option is adding the user to the Docker group. **Docker group membership is
effectively root-equivalent**; use only a dedicated trusted account/key:

```bash
# Run as root on the VPS. New SSH sessions are required for group membership.
usermod -aG docker wave-deploy
su - wave-deploy -c 'docker info >/dev/null && docker compose version'
```

Set the application's production secrets **on the VPS only**. Do not add
them to the public GitHub repository:

```bash
su - wave-deploy
cd /opt/roften-wave
test -f .env || cp .env.example .env
chmod 600 .env
nano .env
bash deploy/preflight.sh
exit
```

Required: strong `APP_ADMIN_PASSWORD`, `APP_SESSION_SECRET` (at least
32 random characters), `YANDEX_MUSIC_TOKEN`, DJ host/port/mount/username/password
from the **Icecast DJ connection** in AzuraCast. Keep `AUTO_START=true`.
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_ADMIN_IDS` are optional but needed for
Telegram control. An optional AzuraCast API key enables now-playing metadata.

Verify the existing AzuraCast DJ port and mount before starting the app.
The dashboard binds to **127.0.0.1:8787** only; configure existing nginx,
DNS and HTTPS separately for the public domain. The CD job does not modify
nginx, AzuraCast, its containers, or DNS.

## Automatic production release

1. Push to `main`.
2. Existing `Tests` workflow runs on Python 3.11, 3.12 and 3.13
   (pytest, static checks, and Docker build). A failed CI **blocks deployment**.
3. On green CI, `CD - Roften Wave` checks the event really originated from a
   push to this repository's `main`. It checks out exactly that commit.
4. The CD job uses strict host key verification and runs
   `bash deploy/remote-deploy.sh release <tested SHA>` on the VPS.
5. Server checks Docker access, clean Git history, the exact latest `main`
   SHA, production `.env` values and Compose configuration.
6. If an existing Roften Wave container is present, the script creates a
   **consistent SQLite online snapshot** from the named volume using SQLite's
   backup API (includes committed WAL state).
7. Docker builds the candidate **without stopping the existing stream**,
   tags it `roften-wave:production`, then recreates **only** the
   `wave` service with `--no-deps --no-build --force-recreate`.
8. Docker health must become `healthy` within 90 seconds. The successfully
   released SHA is stored in the server-only `.production-sha` file.
9. If changing the service fails, the script attempts to stop the new Wave,
   restore the old SQLite snapshot, tag the old image, and restart the
   previous Wave image. Failed rollback must be investigated manually.

A production release does **not** guarantee external Yandex OAuth, Icecast DJ
authentication or Minecraft listener behavior; the Docker healthcheck tests
HTTP liveness only. Test those links on the real infrastructure after the
first release. Brief stream interruptions during container replacement are
possible; AzuraCast's own AutoDJ should be configured as backup.

The release refuses to start if SSH, Docker permissions, `.env` or the
backup are missing/invalid; **it does not install dependencies or fabricate
credentials automatically**. A failed attempt does not mean the app is live.

## Manual production release

GitHub → Actions → **CD - Roften Wave** → **Run workflow** → `main` → `release`.

Manual releases run the same production script and checks. A CI failure does
not permit an automatic production release. Do not downgrade from a newer
checked-out commit; deploy the latest main commit instead.

## Verification on the VPS

```bash
cd /opt/roften-wave
git rev-parse --short HEAD
cat .production-sha                # present only after successful release
docker compose -p roften-wave ps
docker compose -p roften-wave logs --tail=100 wave
curl -fsS http://127.0.0.1:8787/health
```

Backups of the Wave SQLite state are stored in
`/opt/roften-wave/.backups/`. They are untracked and excluded from Docker
build context. Keep adequate disk space and rotate them manually according
to your retention policy. Do not use `docker compose down -v` on Wave.
