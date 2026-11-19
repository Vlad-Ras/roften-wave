from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class Config:
    admin_username: str
    admin_password: str
    session_secret: str
    secure_cookies: bool
    data_dir: Path
    public_app_url: str
    public_radio_url: str
    yandex_token: str
    dj_host: str
    dj_port: int
    dj_mount: str
    dj_username: str
    dj_password: str
    bitrate: str
    azuracast_api_url: str
    azuracast_api_key: str
    azuracast_station_id: str
    telegram_token: str
    telegram_admin_ids: frozenset[int]
    auto_start: bool

    @property
    def radio_configured(self) -> bool:
        return bool(self.dj_host and self.dj_port and self.dj_username and self.dj_password and self.dj_mount not in {"", "/"})

    @property
    def yandex_configured(self) -> bool:
        return bool(self.yandex_token)


def load_config() -> Config:
    env = os.environ
    ids = frozenset(int(v.strip()) for v in env.get("TELEGRAM_ADMIN_IDS", "").split(",") if v.strip())
    return Config(
        admin_username=env.get("APP_ADMIN_USERNAME", "admin"),
        admin_password=env.get("APP_ADMIN_PASSWORD", ""),
        session_secret=env.get("APP_SESSION_SECRET", ""),
        secure_cookies=env.get("APP_SECURE_COOKIES", "true").lower() in {"1", "yes", "true"},
        data_dir=Path(env.get("APP_DATA_DIR", "./data")),
        public_app_url=env.get("PUBLIC_APP_URL", ""),
        public_radio_url=env.get("PUBLIC_RADIO_URL", ""),
        yandex_token=env.get("YANDEX_MUSIC_TOKEN", ""),
        dj_host=env.get("AZURACAST_DJ_HOST", ""),
        dj_port=int(env.get("AZURACAST_DJ_PORT", "0")),
        dj_mount=env.get("AZURACAST_DJ_MOUNT", "/live"),
        dj_username=env.get("AZURACAST_DJ_USERNAME", ""),
        dj_password=env.get("AZURACAST_DJ_PASSWORD", ""),
        bitrate=env.get("AZURACAST_MP3_BITRATE", "160k"),
        azuracast_api_url=env.get("AZURACAST_API_URL", ""),
        azuracast_api_key=env.get("AZURACAST_API_KEY", ""),
        azuracast_station_id=env.get("AZURACAST_STATION_ID", ""),
        telegram_token=env.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_admin_ids=ids,
        auto_start=env.get("AUTO_START", "true").lower() in {"1", "yes", "true"},
    )
