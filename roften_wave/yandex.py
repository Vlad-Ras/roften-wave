from __future__ import annotations

import random
import re
import threading
from pathlib import Path
from typing import Any

TRACK_ID = re.compile(r"^[0-9]+(?::[0-9]+)?$")


def as_track(track: Any) -> dict[str, Any]:
    """Normalize SDK Track objects into JSON records."""
    tid = str(getattr(track, "id", "") or getattr(track, "track_id", ""))
    artists = getattr(track, "artists", None) or []
    cover = getattr(track, "cover_uri", None) or ""
    if cover:
        cover = str(cover).replace("%%", "200x200")
        if not cover.startswith(("https://", "http://")):
            cover = "https://" + cover.lstrip("/")
    return {
        "id": tid,
        "title": str(getattr(track, "title", "") or "Без названия"),
        "artist": ", ".join(str(getattr(a, "name", "")) for a in artists) or "Неизвестный исполнитель",
        "duration": (getattr(track, "duration_ms", None) or 0) // 1000,
        "cover": cover,
    }


class YandexSource:
    def __init__(self, token: str):
        self.token = token
        self.lock = threading.RLock()
        self._client: Any = None
        self._session: Any = None
        self._wave_ids: list[str] = []
        self._wave_buffer: list[dict[str, Any]] = []
        self._liked_pool: list[str] = []

    def _get_client(self) -> Any:
        if not self.token:
            raise RuntimeError("YANDEX_MUSIC_TOKEN is not configured")
        if self._client is None:
            from yandex_music import Client
            self._client = Client(self.token).init()
        return self._client

    def check_connection(self) -> None:
        """Initialize the client to verify the token without downloading audio."""
        with self.lock:
            self._get_client().account_status()

    def search(self, text: str, *, limit: int = 15) -> list[dict[str, Any]]:
        with self.lock:
            result = self._get_client().search(text, type_="track")
            tracks = getattr(getattr(result, "tracks", None), "results", None) or []
            return [as_track(t) for t in tracks[:limit]]

    def get_track(self, track_id: str) -> dict[str, Any]:
        if not TRACK_ID.fullmatch(track_id):
            raise ValueError("Invalid Yandex track ID")
        with self.lock:
            tracks = self._get_client().tracks(track_id) or []
            if not tracks:
                raise ValueError("Track not found")
            return as_track(tracks[0])

    def download(self, track_id: str, filepath: Path) -> None:
        if not TRACK_ID.fullmatch(track_id):
            raise ValueError("Invalid Yandex track ID")
        with self.lock:
            tracks = self._get_client().tracks(track_id) or []
        if not tracks:
            raise ValueError("Track not found")
        try:
            tracks[0].download(str(filepath), codec="mp3", bitrate_in_kbps=192, timeout=20)
        except Exception:
            filepath.unlink(missing_ok=True)
            tracks[0].download(str(filepath), codec="mp3", bitrate_in_kbps=128, timeout=20)
        if not filepath.exists() or filepath.stat().st_size == 0:
            raise RuntimeError("Yandex returned an empty audio file")

    def playlists(self) -> list[dict[str, Any]]:
        with self.lock:
            playlists = self._get_client().users_playlists_list() or []
            return [{
                "kind": str(getattr(p, "kind", "")),
                "owner": str(getattr(getattr(p, "owner", None), "uid", None) or getattr(getattr(p, "owner", None), "id", "")),
                "title": str(getattr(p, "title", "")),
                "count": getattr(p, "track_count", 0) or 0,
            } for p in playlists]

    def playlist_tracks(self, kind: str, owner: str, limit: int = 250) -> list[dict[str, Any]]:
        if not kind.isdigit() or (owner and not owner.isdigit()):
            raise ValueError("Invalid playlist ID")
        with self.lock:
            playlist = self._get_client().users_playlists(int(kind), user_id=(int(owner) if owner else None))
            if not playlist:
                raise ValueError("Playlist not found")
            shorts = (getattr(playlist, "tracks", None) or [])[:limit]
            ids = [str(getattr(getattr(s, "track", None), "id", None) or getattr(s, "id", "")) for s in shorts]
            ids = [tid for tid in ids if tid.isdigit()]
            if not ids:
                return []
            tracks = self._get_client().tracks(ids) or []
            return [as_track(t) for t in tracks]

    def likes(self, limit: int = 60) -> list[dict[str, Any]]:
        with self.lock:
            if not self._liked_pool:
                liked = self._get_client().users_likes_tracks() or []
                items = getattr(liked, "tracks", liked)
                self._liked_pool = [str(t.id) for t in items if getattr(t, "id", None)]
                random.shuffle(self._liked_pool)
            ids, self._liked_pool = self._liked_pool[:limit], self._liked_pool[limit:]
            if not ids:
                return []
            tracks = self._get_client().tracks(ids) or []
            return [as_track(t) for t in tracks]

    def wave(self, limit: int = 12) -> list[dict[str, Any]]:
        with self.lock:
            client = self._get_client()
            if len(self._wave_buffer) < limit:
                if self._session is None:
                    self._session = client.rotor_session_new(["user:onyourwave"])
                    client.rotor_session_feedback_radio_started(self._session.radio_session_id, self._session.batch_id)
                    sequence = self._session.sequence or []
                else:
                    batch = client.rotor_session_tracks(self._session.radio_session_id, queue=self._wave_ids[-100:])
                    if batch is None:
                        self._session = None
                        raise RuntimeError("Yandex Wave session expired; retry")
                    sequence = batch.sequence or []
                    self._session.batch_id = batch.batch_id
                fresh = [as_track(item.track) for item in sequence if getattr(item, "track", None)]
                self._wave_ids.extend(t["id"] for t in fresh)
                self._wave_buffer.extend(fresh)
            result, self._wave_buffer = self._wave_buffer[:limit], self._wave_buffer[limit:]
            return result
