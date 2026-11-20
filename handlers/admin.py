import uuid
from datetime import datetime

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram import Bot

from config import ADMIN_IDS
from keyboards import settings_kb, join_kb
from database import update_contest


router = Router()


class CreateContest(StatesGroup):
    title = State()
    prize = State()
    channel = State()
    ends_at = State()
    winners_count = State()
    settings = State()


@router.message(Command("create"))
async def cmd_create(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(CreateContest.title)
    await message.answer("Введите название конкурса:")


@router.message(F.text == "👑 Создать конкурс")
async def btn_create(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(CreateContest.title)
    await message.answer("Введите название конкурса:")


@router.message(CreateContest.title)
async def process_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(CreateContest.prize)
    await message.answer("Введите приз:")


@router.message(CreateContest.prize)
async def process_prize(message: Message, state: FSMContext):
    await state.update_data(prize=message.text)
    await state.set_state(CreateContest.channel)
    await message.answer("Введите username канала (например @channel):")


@router.message(CreateContest.channel)
async def process_channel(message: Message, state: FSMContext):
    await state.update_data(channel=message.text)
    await state.set_state(CreateContest.ends_at)
    await message.answer("Введите дату окончания (ГГГГ-ММ-ДД ЧЧ:ММ):")


@router.message(CreateContest.ends_at)
async def process_ends_at(message: Message, state: FSMContext):
    try:
        dt = datetime.strptime(message.text, "%Y-%m-%d %H:%M")
    except ValueError:
        await message.answer("Неверный формат. Используйте ГГГГ-ММ-ДД ЧЧ:ММ")
        return
    await state.update_data(ends_at=dt.strftime("%Y-%m-%d %H:%M"))
    await state.set_state(CreateContest.winners_count)
    await message.answer("Введите количество победителей:")


@router.message(CreateContest.winners_count)
async def process_winners(message: Message, state: FSMContext):
    if not (message.text or "").isdigit() or int(message.text) < 1:
        await message.answer("Введите положительное число")
        return
    await state.update_data(winners_count=int(message.text))
    await state.set_state(CreateContest.settings)
    await state.update_data(ref_tickets=1, codes_enabled=False)
    await message.answer("Настройки конкурса:", reply_markup=settings_kb())


@router.callback_query(CreateContest.settings, F.data == "set_ref_tickets")
async def set_ref_tickets_prompt(callback: CallbackQuery):
    await callback.message.answer("Введите количество билетов за реферала:")
    await callback.answer()


@router.message(CreateContest.settings)
async def settings_text(message: Message, state: FSMContext):
    if (message.text or "").isdigit():
        await state.update_data(ref_tickets=int(message.text))
        await message.answer(f"Билеты за реферала: {message.text}", reply_markup=settings_kb())
    else:
        await message.answer("Введите число", reply_markup=settings_kb())


@router.callback_query(CreateContest.settings, F.data == "toggle_codes")
async def toggle_codes(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    new_value = not data.get("codes_enabled", False)
    await state.update_data(codes_enabled=new_value)
    await callback.message.edit_reply_markup(reply_markup=settings_kb())
    await callback.answer("Коды включены" if new_value else "Коды выключены")


@router.callback_query(CreateContest.settings, F.data == "cancel")
async def cancel_create(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Создание отменено")
    await callback.answer()


@router.callback_query(CreateContest.settings, F.data == "finish_contest")
async def finish_contest(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    contest_id = uuid.uuid4().hex[:8]
    contest_payload = {
        "title": data["title"],
        "prize": data["prize"],
        "channel": data["channel"],
        "ends_at": data["ends_at"],
        "winners_count": data["winners_count"],
        "ref_tickets": data.get("ref_tickets", 1),
        "codes_enabled": data.get("codes_enabled", False),
        "status": "active",
        "participants": [],
        "tickets": {},
    }
    await update_contest(contest_id, **contest_payload)
    channel = data["channel"]
    text = (
        f"🎉 Розыгрыш: {data['title']}\n"
        f"Приз: {data['prize']}\n"
        f"Завершение: {data['ends_at']}\n"
        f"Победителей: {data['winners_count']}"
    )
    await bot.send_message(channel, text, reply_markup=join_kb(contest_id))
    await state.clear()
    await callback.message.answer(f"Конкурс создан! ID: {contest_id}")
    await callback.answer()
