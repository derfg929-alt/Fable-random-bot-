"""Fable Randomizer — entrypoint.

Starts the Telegram bot (long polling) and the FastAPI Mini App server in the
same event loop, sharing one database and one Telegram client.
"""
from __future__ import annotations

import asyncio

import uvicorn
from aiogram import Bot

import api
import api_runtime
import settings
import tasks
from bot import build_dispatcher
from database import init_models


async def run_bot(bot: Bot) -> None:
    dp = build_dispatcher()
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot)


async def run_api() -> None:
    app = api.create_app()
    config = uvicorn.Config(app, host=settings.HOST, port=settings.PORT, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def run_background(bot: Bot, stop_event: asyncio.Event) -> None:
    await tasks.worker(bot, stop_event)


async def main() -> None:
    if not settings.is_configured():
        raise RuntimeError(
            "BOT_TOKEN не настроен. Скопируй .env.example в .env и укажи настоящий токен."
        )
    if not settings.OWNER_IDS:
        print("⚠️  OWNER_IDS не задан — административная панель будет недоступна.")
    if not settings.WEBAPP_URL.startswith("https://"):
        print("⚠️  WEBAPP_URL не задан — кнопка Mini App будет скрыта.")

    await init_models()

    bot = Bot(settings.BOT_TOKEN)
    api_runtime.set_bot(bot)

    stop_event = asyncio.Event()
    print("Fable Randomizer: bot + Mini App API запускаются...")
    try:
        await asyncio.gather(
            run_bot(bot),
            run_api(),
            run_background(bot, stop_event),
        )
    finally:
        stop_event.set()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Fable Randomizer остановлен.")
