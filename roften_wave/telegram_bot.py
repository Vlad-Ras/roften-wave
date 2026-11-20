from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .config import Config
from .db import Store
from .diagnostics import check_local, check_yandex
from .player import Player
from .station_status import fetch_air_status, format_air_status
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
        ])
        await message.answer(
            "🎵 Roften Wave — пульт радио\n"
            "/search запрос — поиск и выбор песни\n"
            "/play ID — играть сейчас\n"
            "/manual — ручная очередь\n"
            "/air — реальный эфир и слушатели AzuraCast\n"
            "/history — последние 10 песен\n"
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
    try:
        await dp.start_polling(bot, handle_signals=False, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
