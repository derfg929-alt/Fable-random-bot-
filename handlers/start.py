from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from config import ADMIN_IDS
from keyboards import main_menu
from database import update_user

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    await update_user(user_id, username=message.from_user.username or "")
    text = message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith("ref_"):
        ref_id_str = parts[1][4:]
        if ref_id_str.isdigit():
            ref_id = int(ref_id_str)
            if ref_id != user_id:
                await update_user(user_id, pending_referral=ref_id)
    await message.answer("Добро пожаловать!", reply_markup=main_menu(user_id))
