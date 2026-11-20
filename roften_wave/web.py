from __future__ import annotations

import asyncio
from collections import defaultdict, deque
import threading
import time
from contextlib import asynccontextmanager
import hmac
import logging
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field

from .config import load_config
from .db import Store
from .diagnostics import check_local, check_yandex
from .player import Player
from .yandex import TRACK_ID, YandexSource

log = logging.getLogger(__name__)
config = load_config()
store = Store(config.data_dir / "wave.sqlite3")
source = YandexSource(config.yandex_token)
player = Player(store, source, config)


def serializer() -> URLSafeTimedSerializer:
    if len(config.session_secret) < 32:
        raise HTTPException(503, "APP_SESSION_SECRET must contain at least 32 characters")
    return URLSafeTimedSerializer(config.session_secret, salt="roften-wave-session-v1")


def identity(request: Request) -> str:
    raw = request.cookies.get("wave_session", "")
    if not raw:
        raise HTTPException(401, "Login required")
    try:
        data = serializer().loads(raw, max_age=12 * 3600)
    except (BadSignature, SignatureExpired) as exc:
        raise HTTPException(401, "Session expired") from exc
    if data.get("user") != config.admin_username or not data.get("csrf"):
        raise HTTPException(401, "Invalid session")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        supplied = request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(data["csrf"], supplied):
            raise HTTPException(403, "Invalid CSRF token")
    return data["csrf"]


def ensure_valid_public_url(value: str) -> str:
    if not value or len(value) > 400:
        raise ValueError("URL is required, maximum 400 characters")
    p = urlsplit(value)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password or p.fragment:
        raise ValueError("URL must be http(s), without credentials or fragments")
    return value.rstrip("/")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if config.auto_start:
        player.start()
    bot_task = None
    if config.telegram_token and config.telegram_admin_ids:
        from .telegram_bot import launch_bot
        bot_task = asyncio.create_task(launch_bot(config, source, store, player))
    elif config.telegram_token:
        log.warning("Telegram bot disabled: TELEGRAM_ADMIN_IDS is empty")
    yield
    if bot_task:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
    player.stop()
    store.close()


app = FastAPI(title="Roften Wave", version="0.1.3", lifespan=lifespan, docs_url=None, redoc_url=None)

_login_attempts: dict[str, deque[float]] = defaultdict(deque)
_login_lock = threading.Lock()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https: data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/app.js")
def js():
    return FileResponse(STATIC_DIR / "app.js", media_type="text/javascript", headers={"Cache-Control": "no-store"})


@app.get("/app.css")
def css():
    return FileResponse(STATIC_DIR / "app.css", media_type="text/css", headers={"Cache-Control": "no-store"})


@app.get("/health")
def health():
    # Liveness only; does not expose station secrets or readiness.
    return {"ok": True}


class Login(BaseModel):
    username: str
    password: str


@app.post("/api/login")
def login(data: Login, request: Request, response: Response):
    peer = request.client.host if request.client else "unknown"
    with _login_lock:
        attempts = _login_attempts[peer]
        now = time.monotonic()
        while attempts and attempts[0] < now - 600:
            attempts.popleft()
        if len(attempts) >= 8:
            raise HTTPException(429, "Too many login attempts; try later")
    if len(config.admin_password) < 12:
        raise HTTPException(503, "APP_ADMIN_PASSWORD must contain at least 12 characters")
    match_user = hmac.compare_digest(data.username, config.admin_username)
    match_pass = hmac.compare_digest(data.password, config.admin_password)
    if not (match_user and match_pass):
        with _login_lock:
            _login_attempts[peer].append(time.monotonic())
        raise HTTPException(401, "Incorrect login")
    with _login_lock:
        _login_attempts.pop(peer, None)
    csrf = secrets.token_urlsafe(32)
    session = serializer().dumps({"user": config.admin_username, "csrf": csrf})
    response.set_cookie("wave_session", session, httponly=True, secure=config.secure_cookies, samesite="strict", max_age=12 * 3600, path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"csrf": csrf}


@app.post("/api/logout")
def logout(response: Response, _: str = Depends(identity)):
    response.delete_cookie("wave_session", path="/")
    return {"ok": True}


@app.get("/api/me")
def me(csrf: str = Depends(identity)):
    return {"username": config.admin_username, "csrf": csrf}


@app.get("/api/state")
def state(_: str = Depends(identity)):
    return player.snapshot()


@app.get("/api/search")
async def search(q: str = "", _: str = Depends(identity)):
    q = q.strip()
    if len(q) < 2 or len(q) > 150:
        raise HTTPException(422, "Search query must contain 2-150 characters")
    try:
        return {"tracks": await asyncio.to_thread(source.search, q)}
    except Exception as exc:
        raise HTTPException(502, f"Yandex Music search error: {type(exc).__name__}") from exc


class TrackPayload(BaseModel):
    track_id: str = Field(min_length=1, max_length=50)
    now: bool = False


@app.post("/api/queue")
async def enqueue(body: TrackPayload, _: str = Depends(identity)):
    if not TRACK_ID.fullmatch(body.track_id):
        raise HTTPException(422, "Invalid track ID")
    try:
        info = await asyncio.to_thread(source.get_track, body.track_id)
    except Exception as exc:
        raise HTTPException(502, f"Cannot load track: {type(exc).__name__}") from exc
    item_id = player.enqueue(info, now=body.now)
    return {"queue_id": item_id, "track": info}


@app.post("/api/queue/shuffle")
def shuffle_queue(_: str = Depends(identity)):
    count = store.shuffle()
    player.queue_updated()
    return {"ok": True, "shuffled": count}


@app.post("/api/queue/dedupe")
def dedupe_queue(_: str = Depends(identity)):
    removed = store.dedupe()
    player.queue_updated()
    return {"ok": True, "removed": removed}


@app.get("/api/statistics")
def statistics(_: str = Depends(identity)):
    return store.stats()


@app.delete("/api/queue/{item_id}")
def remove(item_id: int, _: str = Depends(identity)):
    if not store.remove(item_id):
        raise HTTPException(404, "Item not found")
    player.queue_updated()
    return {"ok": True}


class QueueOrder(BaseModel):
    queue_ids: list[int] = Field(max_length=1000)


@app.post("/api/queue/order")
def order(body: QueueOrder, _: str = Depends(identity)):
    try:
        store.reorder(body.queue_ids)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    player.queue_updated()
    return {"ok": True}


class Mode(BaseModel):
    mode: str


@app.post("/api/mode")
def mode(body: Mode, _: str = Depends(identity)):
    try:
        player.set_mode(body.mode)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True}


@app.post("/api/next")
def next_track(_: str = Depends(identity)):
    player.next()
    return {"ok": True}


@app.post("/api/previous")
def previous(_: str = Depends(identity)):
    return {"ok": player.previous()}


@app.post("/api/pause")
def pause(_: str = Depends(identity)):
    player.pause()
    return {"ok": True}


@app.post("/api/resume")
def resume(_: str = Depends(identity)):
    player.resume()
    return {"ok": True}


@app.get("/api/playlists")
async def playlists(_: str = Depends(identity)):
    try:
        return {"playlists": await asyncio.to_thread(source.playlists)}
    except Exception as exc:
        raise HTTPException(502, f"Cannot load playlists: {type(exc).__name__}") from exc


class PlaylistAdd(BaseModel):
    kind: str
    owner: str = ""


@app.post("/api/playlists/enqueue")
async def enqueue_playlist(body: PlaylistAdd, _: str = Depends(identity)):
    try:
        tracks = await asyncio.to_thread(source.playlist_tracks, body.kind, body.owner)
    except Exception as exc:
        raise HTTPException(502, f"Cannot load playlist: {type(exc).__name__}") from exc
    count = store.add_many(tracks)
    player.queue_updated()
    return {"added": count}


class DomainSettings(BaseModel):
    app_url: str
    radio_url: str


@app.get("/api/settings")
def get_settings(_: str = Depends(identity)):
    return {
        "app_url": store.get("public_app_url", config.public_app_url),
        "radio_url": store.get("public_radio_url", config.public_radio_url),
        "dj_host": config.dj_host,
        "dj_port": config.dj_port,
        "dj_mount": config.dj_mount,
        "telegram_enabled": bool(config.telegram_token and config.telegram_admin_ids),
        "azuracast_metadata_enabled": player.metadata.enabled,
    }


@app.put("/api/settings")
def update_settings(data: DomainSettings, _: str = Depends(identity)):
    try:
        app_url = ensure_valid_public_url(data.app_url)
        radio_url = ensure_valid_public_url(data.radio_url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    store.set("public_app_url", app_url)
    store.set("public_radio_url", radio_url)
    return {"ok": True, "app_url": app_url, "radio_url": radio_url}


@app.get("/api/diagnostics")
async def diagnostics(_: str = Depends(identity)):
    """Read-only preflight: no broadcast attempt and no AzuraCast restart."""
    checks = await asyncio.to_thread(check_local, config)
    checks["yandex_auth"] = await asyncio.to_thread(check_yandex, source, config)
    return {"ok": all(bool(item["ok"]) for item in checks.values()), "checks": checks,
            "note": "TCP-доступ не проверяет пароль DJ. Трансляцию нужно проверять отдельно."}
