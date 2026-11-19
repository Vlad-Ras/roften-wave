"""Credential-safe diagnostics. Never publish passwords, tokens or exception messages."""
from __future__ import annotations

import shutil
import socket
import tempfile
from pathlib import Path

from .config import Config
from .yandex import YandexSource


def check_local(config: Config) -> dict[str, dict[str, object]]:
    items: dict[str, dict[str, object]] = {}
    items['ffmpeg'] = {'ok': bool(shutil.which('ffmpeg')), 'message': 'FFmpeg найден' if shutil.which('ffmpeg') else 'FFmpeg не установлен'}
    try:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=config.data_dir) as handle:
            handle.write(b'wave-test')
        items['storage'] = {'ok': True, 'message': 'Хранилище доступно для записи'}
    except OSError:
        items['storage'] = {'ok': False, 'message': 'Ошибка доступа к каталогу данных'}
    items['yandex_token'] = {'ok': config.yandex_configured, 'message': 'Токен задан' if config.yandex_configured else 'Укажи YANDEX_MUSIC_TOKEN'}
    items['dj_settings'] = {'ok': config.radio_configured, 'message': 'Параметры DJ заданы' if config.radio_configured else 'Заполни DJ host/port/login/password и mount'}
    if config.radio_configured:
        try:
            with socket.create_connection((config.dj_host, config.dj_port), timeout=3):
                items['dj_port'] = {'ok': True, 'message': 'DJ-порт принимает TCP-соединения (не проверяет пароль)'}
        except (OSError, OverflowError):
            items['dj_port'] = {'ok': False, 'message': 'Нет TCP-соединения с DJ-портом'}
    else:
        items['dj_port'] = {'ok': False, 'message': 'Порт не проверен: не хватает настроек'}
    return items


def check_yandex(source: YandexSource, config: Config) -> dict[str, object]:
    if not config.yandex_configured:
        return {'ok': False, 'message': 'Не настроен Yandex OAuth-токен'}
    try:
        source.check_connection()
        return {'ok': True, 'message': 'Авторизация Яндекс Музыки прошла'}
    except Exception as exc:
        # No sensitive SDK error / HTTP headers are exposed to web/Telegram.
        return {'ok': False, 'message': f'Проверка Яндекса не прошла ({type(exc).__name__})'}
