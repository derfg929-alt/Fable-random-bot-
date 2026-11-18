"""Fable Randomizer — entrypoint.

Starts the Telegram bot (long polling) and the FastAPI Mini App server in the
same event loop, sharing one database and one Telegram client.

The two halves are independent: if Telegram is unreachable (bad token, network
blip, platform restart) the bot polling loop retries on its own and the Mini
App / API keeps serving. A crash in one half must never take down the other.
"""
from __future__ import annotations

import asyncio
import contextlib
import signal

import uvicorn
from aiogram import Bot

import api
import api_runtime
import settings
import tasks
from bot import build_dispatcher, setup_bot_menu
from database import init_models

POLLING_RETRY_SECONDS = 15


async def run_bot(bot: Bot, stop_event: asyncio.Event) -> None:
    """Poll Telegram, retrying forever instead of killing the process.

    ``delete_webhook`` needs the network, so it is retried together with
    polling: a temporary Telegram outage must not stop the Mini App.
    """
    dp = build_dispatcher()
    while not stop_event.is_set():
        try:
            await bot.delete_webhook(drop_pending_updates=False)
            await dp.start_polling(bot, handle_signals=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep the app alive
            print(f"[bot] polling failed ({exc}); retrying in {POLLING_RETRY_SECONDS}s")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=POLLING_RETRY_SECONDS)
        except asyncio.TimeoutError:
            continue


async def run_api(stop_event: asyncio.Event) -> None:
    app = api.create_app()
    config = uvicorn.Config(app, host=settings.HOST, port=settings.PORT, log_level="info")
    server = uvicorn.Server(config)
    # We own the signal handling; letting uvicorn install its own handlers
    # would make it raise on SIGTERM and fight our graceful shutdown.
    server.capture_signals = contextlib.nullcontext

    async def _watch_stop() -> None:
        await stop_event.wait()
        server.should_exit = True

    watcher = asyncio.create_task(_watch_stop())
    try:
        await server.serve()
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher


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
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)

    print("Fable Randomizer: bot + Mini App API запускаются...")
    await setup_bot_menu(bot)
    tasks_running = [
        asyncio.create_task(run_bot(bot, stop_event), name="bot"),
        asyncio.create_task(run_api(stop_event), name="api"),
        asyncio.create_task(run_background(bot, stop_event), name="background"),
    ]
    try:
        await asyncio.gather(*tasks_running)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        stop_event.set()
        for task in tasks_running:
            task.cancel()
        await asyncio.gather(*tasks_running, return_exceptions=True)
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Fable Randomizer остановлен.")
