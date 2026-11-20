# Roften Wave changelog

## 2026-11-20 — radio operations update (main)
- Admin-only Telegram `/now` includes artwork, elapsed/duration, progress and playback mode; `/air` displays AzuraCast's actual Live DJ status and listener counts.
- Telegram `/playlists` lists owned Yandex playlists with queue-import buttons; `/shuffle`, `/dedupe`, `/stats` and corresponding inline controls added.
- Queue shuffle and duplicate removal operate transactionally on waiting items only; the currently claimed track is protected.
- SQLite daily counters record delivered PCM duration, completed songs, failed attempts and intentional interruptions using at most one DB write per track attempt.
- Web dashboard shows historical output totals, shuffle/dedupe buttons and recent track handoff delays.
- Automatic Wave/likes batches are fetched asynchronously before the final known song ends, without opening an additional Icecast source. Track handoff measurements are best-effort metrics, not a guarantee of uninterrupted listener playback.
- Telegram alerts after three consecutive failed 20-second station checks when a track should be on air; one recovery notice is sent when Live DJ returns. Pauses and an empty manual queue do not trigger alerts.
- FFmpeg diagnostic categories are extracted from a private temporary stderr file and exposed as fixed labels; DJ passwords and raw FFmpeg URLs remain suppressed in app logs.
- Unit and simulated integration tests added for queue/statistics, monitoring, auto-refill and credential-safe FFmpeg diagnosis.
- Existing AzuraCast configuration, external stream URL, and Minecraft client controls are unchanged. No listener or Minecraft song-request feature was added.


## 0.1.3 — local development build (2026-11-19)
- Per-track bounded retry with 1s/2s exponential delays and protected SQLite claims for manual tracks.
- Automatic-mode failed songs are skipped after three total attempts; manual requests are restored to the queue for explicit inspection.
- Rolling 90-second failure threshold opens a 30–120 second circuit to avoid hammering unstable sources and the DJ endpoint; Live DJ disconnects during cooldown.
- Detect prematurely exhausted/invalid decoder output instead of marking zero-audio tracks played.
- Recover approximately the last PCM position when a stream disconnects mid-song.
- Expose retry countdown, attempts and circuit status in web and Telegram; explicit operator commands bypass circuit cooldown.
- GitHub Actions tests expanded across Python 3.11, 3.12 and 3.13; private GitHub publication helper added (no deployment).
- 34 synthetic and unit tests, no live account/server access.


## 0.1.2 — local development build (2026-11-19)
- Bounded one-track predictive download staging. Queued manual tracks take priority over automatic candidates.
- Completed prefetched downloads are reused instead of downloaded a second time.
- Cancelling/replacing a prefetch cleans its file even if the SDK download finishes later.
- Optional AzuraCast API now-playing metadata synchronization with separate private token and station ID; network failures never interrupt playback.
- Web dashboard shows prefetch and metadata delivery state.
- Startup cleanup of temporary audio files from previous crashes (only older than 30 minutes).
- 25 automated tests, including a synthetic two-song live stream to a fake Icecast server.

## 0.1.1
- Recover incomplete queue items after a crash, clean temporary downloads, web and Telegram connection diagnostics and Telegram control keyboard.

## 0.1.0
- First local prototype: Yandex Music search/playlist access, manual queue and radio automation, FFmpeg Live DJ output, FastAPI dashboard and Telegram bot.

**Deployment status:** Neither this release nor its predecessors have been installed or tested on the user's existing AzuraCast server.
