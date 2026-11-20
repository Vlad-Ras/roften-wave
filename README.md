# Roften Wave 0.1.3

Standalone radio manager for **Yandex Music → FFmpeg Live DJ → AzuraCast → Minecraft**, with a private web UI and optional Telegram bot. No changes to AzuraCast internals. One broadcast stream feeds all listeners; Minecraft uses the existing station MP3 URL.

## Features

- Search songs and play **now** or **next**; persistent and reorderable queue.
- Modes: **manual**, **My Wave** (Yandex rotor sessions), **Liked songs**.
- Previous, next, pause, resume; pause/idle releases DJ connection so AzuraCast AutoDJ can take over.
- Own Yandex playlists: import up to 250 items into queue.
- Mobile-first web dashboard with password authentication, signed HttpOnly cookies and CSRF tokens.
- Telegram bot (`/search`, `/play`, `/next`, `/prev`, `/pause`, `/resume`, `/wave`, `/likes`, `/manual`, `/status`, `/queue`, `/air`, `/history`, `/now`, `/playlists`, `/shuffle`, `/dedupe`, `/stats`, `/diagnose`) with inline control buttons; only allowlisted Telegram user IDs.
- Editable public web and radio URLs. Telegram uses long polling without a webhook; domain changes do not affect it.
- Docker Compose + external Nginx. Queue/settings/history in SQLite volume; tracks in short-lived temporary files.
- v0.1.1: crash recovery of claimed queue items; cleanup of interrupted downloads; connection diagnostics; SQLite auto-migration.
- v0.1.2: one-next-song predictive prefetch (reuses finished audio without downloading twice); optional per-song metadata synchronization with AzuraCast; queue-change-aware staging; prefetch/metadata status in web panel; cleanup of old crash leftovers.
- v0.1.3: bounded reconnect attempts (initial + two retries), exponential per-track retry delays, a rolling-window circuit breaker, fallback to AutoDJ while recovering, a resilient manual queue, automatic-track skip after repeated failures and recovery diagnostics in web/Telegram.
- New operator controls: `/air` reads AzuraCast's actual station now-playing, Live DJ state and listener counts (rather than the local Wave playback state); `/history` shows the ten latest songs recorded in the local SQLite history. Both have inline keyboard buttons and require the existing Telegram admin allowlist.
- Operator update: Telegram `/now` (cover/progress), `/playlists` (owned playlists and queue import), `/shuffle`, `/dedupe`, `/stats` plus inline controls. Web queue shuffle/dedupe buttons and measured output statistics. No Minecraft song requests or listener controls.
- Playback improvements: proactive asynchronous refill of Wave/liked-track batches and rolling measurements of server-side gap between completed tracks. These are optimizations, not gapless audio guarantees.
- Monitoring: optional admin notifications after three consecutive 20-second checks that detect missing Live DJ while Wave should play, plus a restoration notice. Only runs when the AzuraCast API URL and station ID are configured; intentional pauses and idle manual queue don't alert.
- Security: FFmpeg failures are mapped to fixed diagnostic categories, avoiding raw URLs and DJ passwords in app logs; rotate any DJ password that appeared in previously shared Liquidsoap logs.

## Prerequisites

- Linux host with Docker Compose, AzuraCast already installed and with working Icecast/MP3 output.
- Access to AzuraCast station admin → `Streamers/DJ Accounts` → enable live streaming; create dedicated DJ account and copy **Icecast Clients** host, port, username, password and mount. **Important:** FFmpeg rejects the default DJ mount `/`. In AzuraCast station **Edit Profile → Streamers/DJs → Advanced → Customize DJ/Streamer Mount Point**, set `/live` (or another named mount), save/restart the station as required, and set the exact same name as `AZURACAST_DJ_MOUNT`. This is a station setting, not a modification to AzuraCast source code. The incoming DJ connection is distinct from the **public listener MP3 URL**.
- Yandex Music account with access to tracks and an authorized Yandex Music OAuth token; its private/unofficial API may change or be region/subscription restricted.
- A Telegram bot token from BotFather and your numerical Telegram user ID (optional).
- A public HTTPS domain for the admin dashboard (e.g. `wave.roften.ru`). DNS and SSL must be set separately; the UI cannot edit registrar DNS or Nginx certificates.

## Installation

```bash
mkdir -p /opt/roften-wave
cd /opt/roften-wave
# Upload/extract roften-wave-0.1.3.zip here, making sure compose.yaml is in this directory.
cp .env.example .env
chmod 600 .env
python3 -c 'import secrets;print(secrets.token_urlsafe(48))'  # paste into APP_SESSION_SECRET
python3 -c 'import secrets;print(secrets.token_urlsafe(32))'  # paste into APP_ADMIN_PASSWORD
nano .env
# Use station's Icecast *DJ* connection info, not its listener port/mount.
bash deploy/preflight.sh   # read-only checks, no changes to AzuraCast
docker compose up -d --build
docker compose logs -f --tail=100 wave
curl -fsS http://127.0.0.1:8787/health
```

The app is accessible *only* on the host's loopback address `127.0.0.1:8787`. Configure TLS in existing Nginx using `deploy/nginx.conf.example`, ensuring you do not overwrite any existing AzuraCast site configurations. Use a real certificate from your ACME/Certbot setup before permitting external logins. To prepare a config for another domain without touching running Nginx, execute `bash deploy/render-domain.sh wave.example.com` and review the generated file before installing it.

**Change the domain later:** edit `app_url` and `radio_url` via **Settings** in the dashboard. The public app domain still needs DNS, TLS and Nginx changes; existing Minecraft links remain valid only when you retain or redirect the old stream URL. To change the **internal** Icecast DJ endpoint or password, edit `.env` and restart just this service: `docker compose up -d --force-recreate wave`.

**Same host Docker networking:** the example uses `host.docker.internal:host-gateway` with AzuraCast's *published DJ port*. If that port is bound only to another Docker network, change the network configuration and use the actual service name and container port. Verify access before changing any firewalls.

## Web API

`/health` is public liveness only. Other `/api/*` endpoints require login and a CSRF header for mutations. `/api/login` returns the `csrf` token and sets a signed, 12-hour, HttpOnly, SameSite=Strict cookie. `/api/state`, `/api/search?q=...`, `/api/queue`, `/api/queue/order`, `/api/queue/shuffle`, `/api/queue/dedupe`, `/api/statistics`, `/api/mode`, `/api/next`, `/api/previous`, `/api/pause`, `/api/resume`, `/api/playlists`, `/api/settings` and `/api/diagnostics` are used by the built-in UI. Diagnostics verifies local dependencies, performs a TCP handshake to the DJ port, and validates Yandex account access; it does not publish a stream or verify the DJ password.

## Safety and recovery

- Run only one web worker (`--workers 1`): one queue consumer and one Telegram long-polling client. Multiple workers would conflict over the live Icecast source.
- Never expose `.env`, OAuth tokens, DJ password, or unprotected admin API publicly. Use a long password and HTTPS.
- The web settings store only **public display URLs**; secrets remain in environment variables.
- Stop/idle/pause disconnects Live DJ; standard AzuraCast AutoDJ automatically becomes primary. Enable and test an AutoDJ playlist beforehand.
- Redis/PostgreSQL are not required in this release; SQLite uses transactional writes and survives app restarts. In v0.1.1 a claimed queue item stays in the DB until played/skipped; on process restart an unfinished track returns to the queue and may play again from the beginning. Old v0.1.0 databases are migrated automatically. Back up your SQLite volume before upgrading.
- The downloaded audio is temporary, deleted after playing (not a permanent archive). A download continuing after a skip now has a cleanup callback. An abrupt container kill can still leave a temporary file; future cleanup is planned.
- If signed Yandex URLs/API change, adaptation may be required. This has **not** been verified with a live Yandex account or an actual AzuraCast DJ endpoint.
- Due to FFmpeg/Icecast buffering, manual skip isn't literally instantaneous for listeners; test with your Minecraft mod.
- **v0.1.2 metadata:** set `AZURACAST_API_URL` (AzuraCast server origin, not the listener stream URL), `AZURACAST_STATION_ID` (numeric ID or shortcode), and `AZURACAST_API_KEY` (with Manage Station Broadcasting permission). The publisher sends `POST /api/station/{id}/nowplaying/update` with song `artist` and `title`. Failures are reported in the UI but never stop audio. Real compatibility must be verified against the installed AzuraCast release: its API may accept updates yet the listener's ICY metadata may behave differently.
- **Predictive prefetch:** while a track plays, only the *next* known queue/automatic track is downloaded to a short-lived file. Manual high-priority queue changes can replace an old candidate. A finished download is reused when playback reaches that track. Downloads already in progress cannot always be aborted by the SDK; they are cleaned as soon as their worker finishes. Prefetch does not cover the first song or an unknown next automatic batch, so a slow download or stream reconnect can still cause pauses.
- **Orphan cleanup:** when the player starts, only old `wave-*.mp3` temporary files (30+ minutes) from a previous crash are deleted. Recent files and other user content are preserved.
- **Audio production caveat:** a restart of the stream, pauses and slow downloads can cause short silences or switches to AutoDJ. The app does not crossfade between songs and cannot guarantee gapless switching to Minecraft clients, whose buffers vary.
- **v0.1.3 recovery:** failed streams/downloads retry the same track up to two times after the initial attempt (1 s, then 2 s; circuit-breaker cooldown may take precedence). The current manual queue item stays claimed until either successful playback or exhaustion; exhausted items are released to the visible queue and playback pauses for manual inspection. Automatic tracks are retried twice, then skipped if still unavailable. After 4 failures within 90 seconds, the circuit opens for 30 seconds (then up to 120 seconds for repeated trips) before probing again. The Live DJ connection is released during backoff so AzuraCast AutoDJ can play. Explicit operator controls (Play Now, Next, Resume, or change mode) reset the breaker and attempt to reconnect immediately. This reduces rapid reconnect loops but does not guarantee a perfectly continuous stream at the listener.
- **Mid-track reconnect:** if audio was already transmitted before the DJ connection failed, the decoder resumes from the approximate last PCM position. Recovery is best-effort; the external stream and Minecraft player may buffer audio, so slight repeats or skips can occur.
- The recovery state is process-local; after a crash the SQLite queue is recovered, but the current playback offset and in-memory backoff counters are reset. Live credentials and subscriptions are not exercised in local tests.
- **Auth limitations:** a cached API client may require reconnecting after account changes; the on-demand diagnostics calls the Yandex account-status API. No live account was available to validate this release.
- **Network limitations:** a successful TCP connection to the DJ port does not validate DJ credentials, mount or codec. Test an actual stream before using the station for Minecraft.
- **Actual air status:** `/air` performs a read-only GET against `{AZURACAST_API_URL}/api/nowplaying/{AZURACAST_STATION_ID}`. It needs those two environment variables, not the AzuraCast API key. It shows source (Live DJ vs AutoDJ), public Now Playing title and listener count, and returns an unavailable message if the AzuraCast endpoint cannot be reached. It never opens a new DJ source or reveals tokens.

## Upgrade from 0.1.0 / 0.1.1

1. Back up the Docker volume containing `/data/wave.sqlite3` (including `-wal` and `-shm` if present) or stop the service and back up the whole volume.
2. Replace the project files, preserving `.env` and the named `wave_data` volume. Do not remove existing AzuraCast containers.
3. Run `bash deploy/preflight.sh`, then `docker compose up -d --build` and inspect logs.
4. Open **Settings → Проверить подключения**; use Telegram `/diagnose` if the bot is configured.

## Production deployment (GitHub Actions CI/CD)

This project uses **one production environment** on VPS `199.189.254.39` — no staging.
After a successful `Tests` workflow on a push to `main`, GitHub Actions attempts
to build and release the exact tested commit over SSH. The server-side script
checks the production `.env` and Docker access before modifying containers,
takes a consistent SQLite snapshot for upgrades, and attempts an automatic
rollback of the Wave container if its Docker healthcheck fails.
AzuraCast is managed separately; the pipeline updates **only** the Wave service.

**First-time VPS setup, SSH permissions, production secrets, health checks,
and rollback details:** [deploy/CI-CD.md](deploy/CI-CD.md).

A green CI or source sync does not prove the radio is running:
check the CD workflow and production container. Real Yandex / AzuraCast DJ
authentication and the Minecraft stream require a separate live check.

## Future work

- Real-stream timing/jitter measurement, authentication failure classification (versus transient network errors), rate limits for community song requests, and live DJ integration testing with the real station and Minecraft client. Automatic retry control and the circuit breaker were added in 0.1.3 and still require real-world verification.


## Automated checks

```bash
python3 -m pip install '.[test]'
pytest -q
python3 -m compileall -q roften_wave
```

An actual end-to-end check needs real credentials and a reachable DJ port: find a test track, add it, start playback, and verify on the public AzuraCast MP3 listener URL and in Minecraft. No credentials are embedded in this project.

## Development status (0.1.3)

This source release is published to GitHub and is managed by the production CD workflow. Deployment succeeds only after VPS credentials and Docker permissions are configured; verify actual status in Actions and on the VDS. AzuraCast is not changed by CD. Run synthetic tests locally with `python -m pytest -q`; the suite tests cache reuse, rapid candidate replacement and deletion of stale files, recovery, auth/CSRF, FFmpeg audio delivery to a fake Icecast endpoint, and HTTP metadata formatting. Real Yandex OAuth/account behavior and AzuraCast's installed API have not been tested.
