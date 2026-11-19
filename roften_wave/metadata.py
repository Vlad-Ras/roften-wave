"""Optional and isolated AzuraCast now-playing metadata publisher."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx

from .config import Config

STATION_ID = re.compile(r'^[A-Za-z0-9_-]{1,80}$')


class MetadataPublisher:
    def __init__(self, config: Config):
        self.config = config

    @property
    def enabled(self) -> bool:
        return bool(self.config.azuracast_api_url and self.config.azuracast_api_key and self.config.azuracast_station_id)

    def endpoint(self) -> str:
        origin = self.config.azuracast_api_url.rstrip('/')
        url = urlsplit(origin)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.fragment or url.query or url.path not in ('', '/'):
            raise ValueError('AZURACAST_API_URL must be an http(s) origin without a path or credentials')
        if not STATION_ID.fullmatch(self.config.azuracast_station_id):
            raise ValueError('Invalid AZURACAST_STATION_ID')
        return f'{origin}/api/station/{self.config.azuracast_station_id}/nowplaying/update'

    def publish(self, track: dict[str, object]) -> None:
        if not self.enabled:
            return
        url = self.endpoint()
        payload = {'artist': str(track.get('artist') or 'Неизвестный исполнитель')[:255],
                   'title': str(track.get('title') or 'Без названия')[:255]}
        with httpx.Client(timeout=httpx.Timeout(5.0), follow_redirects=False) as client:
            response = client.post(url, headers={
                'Authorization': f'Bearer {self.config.azuracast_api_key}',
                'Content-Type': 'application/json',
            }, json=payload)
            response.raise_for_status()
