from __future__ import annotations

import asyncio
import logging
import contextlib

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .config import Config
from .db import Store
from .diagnostics import check_local, check_yandex
from .player import Player
from .station_status import fetch_air_status, format_air_status
from .air_monitor import AirAlertState
from .yandex import TRACK_ID, YandexSource

log = logging.getLogger(__name__)


async def launch_bot(config: Config, source: YandexSource, store: Store, player: Player) -> None:
    router = Router()
    router.message.filter(F.from_user.id.in_(config.telegram_admin_ids))
    router.callback_query.filter(F.from_user.id.in_(config.telegram_admin_ids))

    def fmt(track: dict | None) -> str:
        if not track:
            return "Ничего не играет"
        return f"{track['artist']} — {track['title']}"

    def history_message() -> str:
        tracks = store.history()[:10]
        if not tracks:
            return "🕘 История воспроизведения пока пуста"
        return "🕘 Недавно играли:\n" + "\n".join(
            f"{i}. {fmt(track)}" for i, track in enumerate(tracks, 1)
        )

    def now_message() -> str:
        state = player.snapshot()
        track = state["current"]
        if track is None:
            return "🎵 Сейчас ничего не воспроизводится в Roften Wave."
        elapsed = max(0, int(state["elapsed"]))
        duration = max(0, int(track.get("duration") or 0))
        def time_text(seconds: int) -> str:
            return f"{seconds // 60}:{seconds % 60:02d}"
        percentage = min(100, int(elapsed * 100 / duration)) if duration else 0
        filled = percentage // 10
        progress = "━" * filled + "─" * (10 - filled)
        return (
            f"🎵 {fmt(track)}\n"
            f"{time_text(elapsed)} / {time_text(duration)} · {percentage}%\n"
            f"{progress}\n"
            f"Статус: {state['status']} · Режим: {state['mode']}"
        )

    def statistics_message() -> str:
        s = store.stats()
        seconds = int(s["audio_seconds"])
        return (
            "📊 Roften Wave — статистика\n"
            f"Передано аудио: {seconds // 3600} ч {seconds % 3600 // 60} мин\n"
            f"Завершённых треков: {s['finished_tracks']}\n"
            f"Ошибок треков: {s['failed_tracks']}\n"
            f"Прерываний: {s['interruptions']}\n"
            "Статистика считает фактически переданный аудиосигнал, не прослушивания."
        )

    async def send_now(message: Message) -> None:
        state = player.snapshot()
        track = state["current"]
        caption = now_message()
        cover = track.get("cover") if track else ""
        if isinstance(cover, str) and cover.startswith("https://") and len(cover) < 400:
            try:
                await message.answer_photo(photo=cover, caption=caption)
                return
            except Exception as exc:
                log.warning("Telegram cover not available: %s", type(exc).__name__)
        await message.answer(caption)

    async def air_message() -> str:
        if not config.azuracast_api_url or not config.azuracast_station_id:
            return "📡 Не настроены AZURACAST_API_URL и AZURACAST_STATION_ID"
        try:
            status = await asyncio.to_thread(fetch_air_status, config)
        except Exception as exc:
            log.warning("Cannot read AzuraCast air status: %s", type(exc).__name__)
            return "📡 Не удалось прочитать текущий эфир AzuraCast. Попробуй позже."
        return format_air_status(status)

    @router.message(CommandStart())
    async def start(message: Message):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏮ Назад", callback_data="control:prev"),
             InlineKeyboardButton(text="⏯ Пауза/пуск", callback_data="control:toggle"),
             InlineKeyboardButton(text="⏭ Далее", callback_data="control:next")],
            [InlineKeyboardButton(text="🌊 Моя волна", callback_data="control:wave"),
             InlineKeyboardButton(text="♥ Избранное", callback_data="control:likes")],
            [InlineKeyboardButton(text="📋 Очередь", callback_data="control:queue"),
             InlineKeyboardButton(text="📻 Статус", callback_data="control:status")],
            [InlineKeyboardButton(text="📡 Эфир", callback_data="control:air"),
             InlineKeyboardButton(text="🕘 История", callback_data="control:history")],
            [InlineKeyboardButton(text="🎵 Сейчас", callback_data="control:now"),
             InlineKeyboardButton(text="📊 Статистика", callback_data="control:stats")],
            [InlineKeyboardButton(text="🔀 Перемешать", callback_data="control:shuffle"),
             InlineKeyboardButton(text="🧹 Убрать повторы", callback_data="control:dedupe")],
        ])
        await message.answer(
            "🎵 Roften Wave — пульт радио\n"
            "/search запрос — поиск и выбор песни\n"
            "/play ID — играть сейчас\n"
            "/manual — ручная очередь\n"
            "/air — реальный эфир и слушатели AzuraCast\n"
            "/history — последние 10 песен\n"
            "/now — обложка, прогресс и длительность\n"
            "/shuffle — перемешать очередь · /dedupe — убрать повторы\n"
            "/stats — статистика аудио\n"
            "/diagnose — диагностика подключений",
            reply_markup=keyboard,
        )

    @router.callback_query(F.data.startswith("control:"))
    async def controls(callback: CallbackQuery):
        action = (callback.data or "").split(":", 1)[1]
        if action == "next":
            player.next()
            msg = "⏭ Переключаю"
        elif action == "prev":
            msg = "⏮ Предыдущий трек" if player.previous() else "История пуста"
        elif action == "toggle":
            if player.snapshot()["paused"]:
                player.resume()
                msg = "▶️ Продолжаю"
            else:
                player.pause()
                msg = "⏸ Пауза (AutoDJ)"
        elif action in {"wave", "likes"}:
            player.set_mode(action)
            msg = "Режим: " + action
        elif action == "queue":
            tracks = store.queue()
            msg = "\n".join(f"{i+1}. {fmt(t)}" for i, t in enumerate(tracks[:15])) or "Очередь пуста"
        elif action == "now":
            await callback.answer("Готово")
            if callback.message:
                await send_now(callback.message)
            return
        elif action == "stats":
            msg = statistics_message()
        elif action == "shuffle":
            count = store.shuffle()
            player.queue_updated()
            msg = f"🔀 Перемешано записей: {count}"
        elif action == "dedupe":
            removed = store.dedupe()
            player.queue_updated()
            msg = f"🧹 Удалено повторов: {removed}"
        elif action == "air":
            msg = await air_message()
        elif action == "history":
            msg = history_message()
        elif action == "status":
            state = player.snapshot()
            recovery = state['recovery']
            msg = f"📻 {fmt(state['current'])}\nСтатус: {state['status']}\nРежим: {state['mode']}"
            if recovery['retry_in_seconds'] > 0:
                msg += f"\n🔄 Повтор через {recovery['retry_in_seconds']:.0f} с"
        else:
            await callback.answer("Неизвестное действие", show_alert=True)
            return
        await callback.answer("Готово")
        if callback.message:
            await callback.message.answer(msg)

    @router.message(Command("diagnose"))
    async def diagnose(message: Message):
        checks = await asyncio.to_thread(check_local, config)
        checks["yandex_auth"] = await asyncio.to_thread(check_yandex, source, config)
        report = "\n".join(("✅" if item["ok"] else "❌") + " " + item["message"] for item in checks.values())
        await message.answer("Диагностика Roften Wave\n" + report)

    @router.message(Command("status"))
    async def status(message: Message):
        snapshot = player.snapshot()
        recovery = snapshot['recovery']
        retry = (f"\n🔄 Ожидание: {recovery['retry_in_seconds']:.0f} с "
                 f"({'защита от частых подключений' if recovery['circuit_open'] else 'повтор трека'})") if recovery['retry_in_seconds'] > 0 else ''
        await message.answer(f"📻 {fmt(snapshot['current'])}\nРежим: {snapshot['mode']}\nСтатус: {snapshot['status']}\nОчередь: {len(snapshot['queue'])}\nОшибка: {snapshot['error'] or 'нет'}{retry}")

    @router.message(Command("now"))
    async def now_cmd(message: Message):
        await send_now(message)

    @router.message(Command("stats"))
    async def stats_cmd(message: Message):
        await message.answer(statistics_message())

    @router.message(Command("shuffle"))
    async def shuffle_cmd(message: Message):
        count = store.shuffle()
        player.queue_updated()
        await message.answer(f"🔀 Перемешано записей: {count}")

    @router.message(Command("dedupe"))
    async def dedupe_cmd(message: Message):
        removed = store.dedupe()
        player.queue_updated()
        await message.answer(f"🧹 Удалено повторов: {removed}")

    @router.message(Command("air"))
    async def air_cmd(message: Message):
        await message.answer(await air_message())

    @router.message(Command("history"))
    async def history_cmd(message: Message):
        await message.answer(history_message())

    @router.message(Command("queue"))
    async def queue(message: Message):
        tracks = store.queue()
        content = "\n".join(f"{i + 1}. {fmt(t)}" for i, t in enumerate(tracks[:20]))
        await message.answer(content or "Очередь пуста")

    @router.message(Command("next"))
    async def next_cmd(message: Message):
        player.next()
        await message.answer("⏭ Переключаю")

    @router.message(Command("prev"))
    async def prev_cmd(message: Message):
        await message.answer("⏮ Предыдущий трек" if player.previous() else "История пуста")

    @router.message(Command("pause"))
    async def pause_cmd(message: Message):
        player.pause()
        await message.answer("⏸ Пауза. AzuraCast возвращается к AutoDJ")

    @router.message(Command("resume"))
    async def resume_cmd(message: Message):
        player.resume()
        await message.answer("▶️ Возобновляю")

    @router.message(Command("wave", "likes", "manual"))
    async def modes(message: Message):
        text = (message.text or "").split()[0].lstrip("/").split("@")[0]
        player.set_mode({"wave": "wave", "likes": "likes", "manual": "manual"}[text])
        await message.answer(f"Режим: {text}")

    @router.message(Command("play"))
    async def play_cmd(message: Message):
        args = (message.text or "").split(maxsplit=1)
        if len(args) != 2 or not TRACK_ID.fullmatch(args[1].strip()):
            await message.answer("Использование: /play 12345678")
            return
        try:
            info = await asyncio.to_thread(source.get_track, args[1].strip())
            player.enqueue(info, now=True)
            await message.answer(f"▶️ {fmt(info)}")
        except Exception as exc:
            await message.answer(f"Не удалось добавить песню: {type(exc).__name__}")

    @router.message(Command("search"))
    async def search_cmd(message: Message):
        args = (message.text or "").split(maxsplit=1)
        if len(args) < 2 or len(args[1]) < 2:
            await message.answer("Использование: /search исполнитель название")
            return
        try:
            tracks = await asyncio.to_thread(source.search, args[1][:150], limit=5)
        except Exception as exc:
            await message.answer(f"Ошибка поиска: {type(exc).__name__}")
            return
        if not tracks:
            await message.answer("Ничего не найдено")
        for track in tracks:
            buttons = [[
                InlineKeyboardButton(text="▶️ Сейчас", callback_data=f"now:{track['id']}"),
                InlineKeyboardButton(text="➕ Очередь", callback_data=f"add:{track['id']}"),
            ]]
            await message.answer(fmt(track), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    @router.callback_query(F.data.startswith(("now:", "add:")))
    async def select_track(callback: CallbackQuery):
        kind, track_id = callback.data.split(":", 1)
        if not TRACK_ID.fullmatch(track_id):
            await callback.answer("Некорректный трек", show_alert=True)
            return
        try:
            track = await asyncio.to_thread(source.get_track, track_id)
            player.enqueue(track, now=(kind == "now"))
            await callback.answer("Трек добавлен")
            if callback.message:
                await callback.message.answer(("▶️ " if kind == "now" else "➕ ") + fmt(track))
        except Exception as exc:
            await callback.answer(f"Ошибка: {type(exc).__name__}", show_alert=True)

    bot = Bot(config.telegram_token)
    dp = Dispatcher()
    dp.include_router(router)

    async def monitor_air() -> None:
        """Poll real station state only when Wave should be broadcasting.

        Three consecutive missed checks trigger one alert; a recovered stream
        triggers one resolution message. Intentional pause/idle resets the latch.
        """
        guard = AirAlertState(threshold=3)
        while True:
            await asyncio.sleep(20)
            state = player.snapshot()
            expected = not state["paused"] and (
                state["current"] is not None or bool(state["queue"]) or state["mode"] != "manual"
            )
            if not expected:
                guard.evaluate(expected=False, healthy=False)
                continue
            try:
                air = await asyncio.to_thread(fetch_air_status, config)
            except Exception as exc:
                log.warning("Live DJ monitor read failed: %s", type(exc).__name__)
                # An API error cannot prove that the stream is down.
                continue
            healthy = bool(air["live"] and state["status"] == "playing")
            event = guard.evaluate(expected=True, healthy=healthy)
            if event == "lost":
                message = (
                    "🚨 Roften Wave: Live DJ отсутствует в AzuraCast. "
                    f"Локальный статус: {state['status']}. Проверь /status и /air."
                )
            elif event == "restored":
                message = "✅ Roften Wave: Live DJ снова в эфире AzuraCast."
            else:
                continue
            for admin_id in config.telegram_admin_ids:
                try:
                    await bot.send_message(admin_id, message)
                except Exception as exc:
                    log.warning("Could not send Live DJ alert: %s", type(exc).__name__)

    monitor_task = None
    if config.azuracast_api_url and config.azuracast_station_id:
        monitor_task = asyncio.create_task(monitor_air(), name="wave-air-monitor")
    try:
        await dp.start_polling(bot, handle_signals=False, allowed_updates=dp.resolve_used_update_types())
    finally:
        if monitor_task is not None:
            monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor_task
        await bot.session.close()
