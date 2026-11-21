from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from config import ADMIN_IDS


def main_menu(user_id: int, is_admin: bool = False):
    """Reply keyboard for main menu."""
    builder = ReplyKeyboardBuilder()
    buttons = [
        KeyboardButton(text="🎁 Активные"),
        KeyboardButton(text="🎟 Мои билеты"),
    ]
    if is_admin:
        buttons.append(KeyboardButton(text="👑 Создать конкурс"))
    builder.row(*buttons, width=len(buttons))
    return builder.as_markup(resize_keyboard=True)


def join_kb(contest_id: int):
    """Inline keyboard with 'Участвовать' button."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="Участвовать", callback_data=f"join_{contest_id}"))
    return builder.as_markup()


def settings_kb():
    """Inline keyboard for settings."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔧 Настройки кодов", callback_data="toggle_codes"),
        InlineKeyboardButton(text="🏁 Завершить конкурс", callback_data="finish_contest"),
    )
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"),
    )
    return builder.as_markup()
