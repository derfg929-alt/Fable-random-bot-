"""Process-wide holder for the running Telegram bot.

The HTTP API needs a Telegram client to verify channel subscriptions and to
send notifications. ``main.py`` creates the bot, registers it here and starts
the web server, so both halves of the application share one instance.
"""
from __future__ import annotations

from typing import Optional

_bot = None


def set_bot(bot) -> None:
    global _bot
    _bot = bot


def get_bot():
    if _bot is None:
        raise RuntimeError("Telegram bot is not started yet")
    return _bot


def try_get_bot() -> Optional[object]:
    return _bot
