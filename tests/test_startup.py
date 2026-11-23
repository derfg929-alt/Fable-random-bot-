"""Startup robustness: Mini App page loading, bot/API independence, menu setup.

These tests guard the regressions that made the whole app unusable:

* ``api`` used to read ``mini_app.html`` at import time; the file was renamed to
  ``index.html`` and the import (and thus the bot and the Mini App) crashed.
* ``main`` ran the bot and the API in one ``asyncio.gather`` without isolation,
  so a Telegram failure killed the Mini App too.
"""
from __future__ import annotations

from pathlib import Path

import api
import bot as bot_module
import settings


def test_mini_app_html_loads_and_has_content():
    assert "Fable Randomizer" in api.MINI_APP_HTML
    assert "telegram-web-app.js" in api.MINI_APP_HTML


def test_mini_app_html_loader_accepts_index_html(monkeypatch, tmp_path: Path):
    page = tmp_path / "index.html"
    page.write_text("<!doctype html><title>Fable Randomizer</title>", encoding="utf-8")
    monkeypatch.setattr(api, "__file__", str(tmp_path / "api.py"))
    assert "Fable Randomizer" in api._load_mini_app_html()


def test_mini_app_html_loader_raises_when_missing(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api, "__file__", str(tmp_path / "api.py"))
    try:
        api._load_mini_app_html()
    except FileNotFoundError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("expected FileNotFoundError when no HTML is present")


def test_user_commands_cover_public_commands():
    commands = {c for c, _ in bot_module.USER_COMMANDS}
    assert {"start", "contests", "join", "myref", "profile", "history", "winners", "help"} <= commands


async def test_setup_bot_menu_registers_commands(monkeypatch):
    calls = {}

    class FakeBot:
        async def set_my_commands(self, commands):
            calls["commands"] = [c.command for c in commands]

        async def set_chat_menu_button(self, menu_button):
            calls["menu"] = type(menu_button).__name__

    monkeypatch.setattr(settings, "WEBAPP_URL", "https://example.com")
    await bot_module.setup_bot_menu(FakeBot())
    assert calls["commands"] == [c for c, _ in bot_module.USER_COMMANDS]
    assert calls["menu"] == "MenuButtonWebApp"


async def test_setup_bot_menu_swallows_errors():
    class FailingBot:
        async def set_my_commands(self, commands):
            raise RuntimeError("telegram down")

        async def set_chat_menu_button(self, menu_button):
            raise RuntimeError("telegram down")

    # Must not raise even though every Telegram call fails.
    await bot_module.setup_bot_menu(FailingBot())


async def test_run_bot_retries_instead_of_crashing(monkeypatch):
    """A failing Telegram connection must not propagate out of run_bot."""
    import asyncio

    import main as main_module

    monkeypatch.setattr(main_module, "POLLING_RETRY_SECONDS", 0)

    class FlakyBot:
        def __init__(self):
            self.attempts = 0

        async def delete_webhook(self, **kwargs):
            self.attempts += 1
            raise RuntimeError("Telegram Unauthorized")

    class FakeDispatcher:
        async def start_polling(self, *args, **kwargs):  # pragma: no cover - not reached
            raise AssertionError("polling must not be reached while delete_webhook fails")

    monkeypatch.setattr(main_module, "build_dispatcher", lambda: FakeDispatcher())

    bot = FlakyBot()
    stop_event = asyncio.Event()

    async def _stop_soon():
        await asyncio.sleep(0.05)
        stop_event.set()

    await asyncio.gather(main_module.run_bot(bot, stop_event), _stop_soon())
    assert bot.attempts >= 1


async def test_run_bot_stops_polling_when_stop_event_is_set(monkeypatch):
    """SIGTERM only sets ``stop_event``; polling must be stopped explicitly."""
    import asyncio

    import main as main_module

    class FakeBot:
        async def delete_webhook(self, **kwargs):
            return True

    class BlockingDispatcher:
        def __init__(self):
            self._running_lock = asyncio.Lock()
            self.stopped = asyncio.Event()

        async def start_polling(self, *args, **kwargs):
            async with self._running_lock:
                await self.stopped.wait()

        async def stop_polling(self):
            self.stopped.set()

    dispatcher = BlockingDispatcher()
    monkeypatch.setattr(main_module, "build_dispatcher", lambda: dispatcher)

    stop_event = asyncio.Event()
    task = asyncio.create_task(main_module.run_bot(FakeBot(), stop_event))
    await asyncio.sleep(0.05)
    stop_event.set()
    await asyncio.wait_for(task, timeout=1)
    assert dispatcher.stopped.is_set()
