"""Read-only AzuraCast now-playing status for operators.

This checks the actual public station, not merely the local Wave player's state.
No DJ credentials or AzuraCast API key are sent with this request.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from .config import Config
from .metadata import STATION_ID


def _display(value: object, limit: int = 140) -> str:
    """Bound plain-text fields from a public API before displaying in Telegram."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _listener_count(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (ValueError, TypeError, OverflowError):
        return 0


def fetch_air_status(config: Config) -> dict[str, object]:
    """Fetch a bounded, credential-free snapshot of what AzuraCast is airing."""
    base = config.azuracast_api_url.strip().rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}
            or not STATION_ID.fullmatch(config.azuracast_station_id)):
        raise ValueError("AzuraCast API URL or station ID is not configured")

    endpoint = f"{base}/api/nowplaying/{config.azuracast_station_id}"
    with httpx.Client(timeout=httpx.Timeout(5.0), follow_redirects=False) as client:
        response = client.get(endpoint, headers={"Accept": "application/json"})
        response.raise_for_status()
        if len(response.content) > 128 * 1024:
            raise ValueError("AzuraCast API response too large")
        payload = response.json()

    if not isinstance(payload, dict):
        raise ValueError("Invalid AzuraCast response")
    live = payload.get("live") or {}
    listeners = payload.get("listeners") or {}
    now_playing = payload.get("now_playing") or {}
    if not all(isinstance(x, dict) for x in (live, listeners, now_playing)):
        raise ValueError("Invalid AzuraCast response")
    song = now_playing.get("song") or {}
    if not isinstance(song, dict):
        raise ValueError("Invalid AzuraCast song data")
    text = _display(song.get("text"))
    if not text:
        artist = _display(song.get("artist"))
        title = _display(song.get("title"))
        text = " — ".join(part for part in (artist, title) if part)

    return {
        "online": bool(payload.get("is_online", False)),
        "live": bool(live.get("is_live", False)),
        "dj": _display(live.get("streamer_name"), 80),
        "song": text or "Не указано",
        "listeners": _listener_count(listeners.get("current")),
        "unique": _listener_count(listeners.get("unique")),
    }


def format_air_status(status: dict[str, object]) -> str:
    """Compose an operator-friendly message without exposing URLs or secrets."""
    if not status["online"]:
        source = "🔴 Станция офлайн"
    elif status["live"]:
        dj = status["dj"]
        source = "🎙 Live DJ" + (f": {dj}" if dj else "")
    else:
        source = "🎶 AutoDJ / резервный источник"
    return (
        "📡 AzuraCast — фактический эфир\n"
        f"Источник: {source}\n"
        f"Трек: {status['song']}\n"
        f"Слушатели: {status['listeners']} (уникальных: {status['unique']})"
    )
