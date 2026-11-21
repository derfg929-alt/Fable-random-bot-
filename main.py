import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

# Import DefaultBotProperties directly from the client module
# This fixes the ModuleNotFoundError: No module named 'aiogram.utils.default' error
from aiogram.client.default import DefaultBotProperties

from config import BOT_TOKEN

# Add handlers directory to path
sys.path.insert(0, str(Path(__file__).parent / "handlers"))

from handlers import start, admin, user, draw

# Router registration order matters
ROUTERS = [start.router, admin.router, user.router, draw.router]

async def main() -> None:
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Include all routers
    for router in ROUTERS:
        dp.include_router(router)

    # Start polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped")
