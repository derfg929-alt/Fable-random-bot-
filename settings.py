"""Fable Randomizer — static configuration and secrets.

Everything sensitive (bot token, web app URL) is read from environment
variables. Copy ``.env.example`` to ``.env``, fill in real values and never
commit ``.env`` to a public repository.

Runtime-changeable settings (project name, support link, notification
toggles, contest defaults, ...) live in the ``settings`` database table and
are edited from the admin panel — see ``admin.py``.
"""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

try:  # python-dotenv is optional; env vars alone are enough in production
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:  # pragma: no cover - optional dependency
    pass

BASE_DIR = Path(__file__).resolve().parent


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_ids(name: str) -> set[int]:
    raw = os.getenv(name, "")
    result: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            result.add(int(chunk))
    return result


# --- Telegram ---------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip().lstrip("@")

# Owner ids are trusted unconditionally (cannot be removed from the panel).
OWNER_IDS = _get_ids("OWNER_IDS") or _get_ids("ADMIN_IDS")

# --- Web / Mini App ---------------------------------------------------------
# Public HTTPS URL where the Mini App is served. Hosting platforms usually
# expose it as PUBLIC_URL / RENDER_EXTERNAL_URL, so those are used as fallbacks.
WEBAPP_URL = (
    os.getenv("WEBAPP_URL")
    or os.getenv("MINI_APP_URL")
    or os.getenv("PUBLIC_URL")
    or os.getenv("RENDER_EXTERNAL_URL")
    or ""
).strip()
if WEBAPP_URL and not WEBAPP_URL.startswith(("http://", "https://")):
    WEBAPP_URL = f"https://{WEBAPP_URL}"
WEBAPP_URL = WEBAPP_URL.rstrip("/")
HOST = os.getenv("HOST", "0.0.0.0")
# ``PORT`` is what Heroku/Railway/Render/Amvera inject; API_PORT is a fallback.
PORT = _get_int("PORT", _get_int("API_PORT", 8000))
# How long a Telegram Mini App initData signature stays valid (seconds).
INIT_DATA_MAX_AGE = _get_int("INIT_DATA_MAX_AGE", 86400)

# --- Database ---------------------------------------------------------------
# SQLite for the first version; swap for postgresql+asyncpg://... later.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite+aiosqlite:///{BASE_DIR / 'fable_randomizer.db'}",
)
DB_ECHO = _get_bool("DB_ECHO", False)

# --- Time -------------------------------------------------------------------
TIMEZONE_NAME = os.getenv("TIMEZONE", "Europe/Moscow")
try:
    TIMEZONE = ZoneInfo(TIMEZONE_NAME)
except Exception:  # pragma: no cover - bad tz name in env
    TIMEZONE_NAME = "UTC"
    TIMEZONE = ZoneInfo("UTC")

# --- Draw algorithm ---------------------------------------------------------
# Hidden relative weight added for every invited friend: 0.01% per invite,
# capped at +10% relative weight. This is an internal algorithm parameter and
# must never be shown to end users.
REFERRAL_RATE = _get_float("REFERRAL_RATE", 0.0001)
MAX_REFERRAL_BONUS = _get_float("MAX_REFERRAL_BONUS", 0.10)
DRAW_ALGORITHM_VERSION = "fr-weighted-v1"

# --- Notifications ----------------------------------------------------------
# Minimum seconds between two notifications of the same type to one user.
NOTIFICATION_MIN_GAP_SECONDS = _get_int("NOTIFICATION_MIN_GAP_SECONDS", 0)

# --- Misc -------------------------------------------------------------------
from datetime import datetime  # noqa: E402  (after config parsing on purpose)


def now_local() -> datetime:
    """Naive 'now' in the configured timezone, used consistently in the DB."""
    return datetime.now(TIMEZONE).replace(tzinfo=None)


def is_configured() -> bool:
    """True when a real bot token looks present."""
    return ":" in BOT_TOKEN and not BOT_TOKEN.upper().startswith("PUT_")
