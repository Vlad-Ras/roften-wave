# Roften Wave changelog

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
