from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from config import ADMIN_IDS


def main_menu(user_id):
    keyboard = [
        [KeyboardButton(text="🎁 Активные розыгрыши"), KeyboardButton(text="🎟 Мои билеты")],
    ]
    if user_id in ADMIN_IDS:
        keyboard.append([KeyboardButton(text="👑 Создать конкурс")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def join_kb(contest_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Участвовать", callback_data=f"join_{contest_id}")]
        ]
    )


def settings_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Установить билеты за реферала", callback_data="set_ref_tickets")],
            [InlineKeyboardButton(text="Вкл/выкл коды", callback_data="toggle_codes")],
            [InlineKeyboardButton(text="Завершить конкурс", callback_data="finish_contest")],
            [InlineKeyboardButton(text="Отмена", callback_data="cancel")],
        ]
    )
