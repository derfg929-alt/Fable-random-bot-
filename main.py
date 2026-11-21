import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN
from handlers import start, user, admin, draw
from handlers.draw import start_scheduler


async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(start.router)
    dp.include_router(user.router)
    dp.include_router(admin.router)
    dp.include_router(draw.router)
    start_scheduler(bot)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
