"""Fable Randomizer entry point.

Starts three independent parts in one process:

* Telegram bot (long polling, retried forever so a Telegram outage never kills
  the web part);
* FastAPI/uvicorn web server that serves the Mini App and the HTTP API;
* background worker that starts/finishes contests on schedule.

The bot and the web server keep running when the other one fails, and
SIGINT/SIGTERM shut everything down cleanly.

Usage::

    python main.py            # run the bot + Mini App + worker
    python main.py --check    # validate configuration and create tables
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

import uvicorn
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

import api_runtime
import database
import settings
import tasks
from bot import build_dispatcher, setup_bot_menu

logger = logging.getLogger("fable")

POLLING_RETRY_SECONDS = 15
API_RETRY_SECONDS = 15


async def _ensure_bot_username(bot: Bot) -> None:
    """Fill ``BOT_USERNAME`` from getMe when it is not set in the environment.

    Referral links need the username, so this avoids a confusing empty link on
    a fresh deployment.
    """
    if settings.BOT_USERNAME:
        return
    try:
        me = await bot.get_me()
        username = (getattr(me, "username", "") or "").lstrip("@")
        if username:
            settings.BOT_USERNAME = username
            logger.info("BOT_USERNAME detected: @%s", username)
    except Exception as exc:  # noqa: BLE001 - optional nicety
        logger.warning("Could not resolve bot username: %s", exc)


async def run_bot(bot: Bot, stop_event: asyncio.Event) -> None:
    """Poll Telegram until ``stop_event`` is set, retrying on any error."""
    while not stop_event.is_set():
        try:
            await bot.delete_webhook(drop_pending_updates=False)
            dispatcher = build_dispatcher()
            logger.info("Bot polling started")
            await dispatcher.start_polling(bot, handle_signals=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - must not kill the process
            logger.warning(
                "Telegram polling failed: %s (retry in %ss)", exc, POLLING_RETRY_SECONDS
            )
        if stop_event.is_set():
            break
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=POLLING_RETRY_SECONDS)
        except asyncio.TimeoutError:
            pass
    logger.info("Bot polling stopped")


async def run_api(stop_event: asyncio.Event) -> None:
    """Serve the Mini App + HTTP API, retrying if the port is temporarily busy."""
    from api import create_app

    while not stop_event.is_set():
        config = uvicorn.Config(
            create_app(),
            host=settings.HOST,
            port=settings.PORT,
            log_level="info",
        )
        server = uvicorn.Server(config)
        # aiogram owns SIGINT/SIGTERM; uvicorn must not capture them again or
        # the two shutdown handlers fight each other.
        server.capture_signals = contextlib.nullcontext

        async def _watch() -> None:
            await stop_event.wait()
            server.should_exit = True

        watcher = asyncio.create_task(_watch())
        try:
            logger.info("Mini App / API on http://%s:%s", settings.HOST, settings.PORT)
            await server.serve()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - web part must not kill the bot
            logger.exception("API server failed: %s", exc)
        finally:
            watcher.cancel()
        if stop_event.is_set():
            break
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=API_RETRY_SECONDS)
        except asyncio.TimeoutError:
            pass
    logger.info("API server stopped")


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):  # e.g. Windows
            pass


async def _startup() -> Bot:
    if not settings.is_configured():
        raise SystemExit(
            "BOT_TOKEN is not configured.\n"
            "Copy .env.example to .env, set BOT_TOKEN (from @BotFather) and run again."
        )

    await database.init_models()
    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    api_runtime.set_bot(bot)
    await _ensure_bot_username(bot)
    await setup_bot_menu(bot)
    return bot


async def main() -> None:
    bot = await _startup()
    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)
    logger.info("Fable Randomizer started")
    try:
        await asyncio.gather(
            run_bot(bot, stop_event),
            run_api(stop_event),
            tasks.worker(bot, stop_event),
        )
    finally:
        await bot.session.close()
        logger.info("Fable Randomizer stopped")


async def _check() -> int:
    """Validate configuration, create tables and report readiness.

    Only fatal problems (no token, broken database) produce a non-zero exit;
    the remaining items are warnings so the command stays usable in CI.
    """
    fatal = False
    problems = []
    if not settings.is_configured():
        problems.append("BOT_TOKEN is missing or looks like a placeholder")
        fatal = True
    if not settings.BOT_USERNAME:
        problems.append("BOT_USERNAME is empty (referral links will not work)")
    if not settings.WEBAPP_URL.startswith("https://"):
        problems.append("WEBAPP_URL is not an https URL (Mini App button will be hidden)")
    try:
        await database.init_models()
        db_ok = await database.ping()
    except Exception as exc:  # noqa: BLE001 - report instead of crashing
        db_ok = False
        fatal = True
        problems.append(f"database error: {exc}")
    print(f"database: {'ok' if db_ok else 'FAIL'} ({settings.DATABASE_URL})")
    for problem in problems:
        print(f"warning: {problem}")
    return 1 if fatal else 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    if "--check" in sys.argv[1:]:
        raise SystemExit(asyncio.run(_check()))
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted")
