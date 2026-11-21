from aiogram import types, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

from keyboards import main_menu, join_kb, settings_kb
from database import get_user, update_user, add_participant, load_data, save_data
from config import ADMIN_IDS, DB_PATH

router = Router()


class ContestCreate(StatesGroup):
    title = State()
    prize = State()
    channel = State()
    ends_at = State()
    winners_count = State()
    settings = State()


@router.message(F.text == "👑 Создать конкурс")
async def cmd_create_contest(message: Message, state: FSMContext):
    await message.answer("Введите название конкурса:", reply_markup=main_menu(message.from_user.id, True))
    await state.set_state(ContestCreate.title)


@router.message(ContestCreate.title)
async def process_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await message.answer("Введите приз конкурса:", reply_markup=main_menu(message.from_user.id, True))
    await state.set_state(ContestCreate.prize)


@router.message(ContestCreate.prize)
async def process_prize(message: Message, state: FSMContext):
    await state.update_data(prize=message.text)
    await message.answer("Введите канал (id или username):", reply_markup=main_menu(message.from_user.id, True))
    await state.set_state(ContestCreate.channel)


@router.message(ContestCreate.channel)
async def process_channel(message: Message, state: FSMContext):
    await state.update_data(channel=message.text)
    await message.answer("Дата окончания (ГГГГ-ММ-ДД):", reply_markup=main_menu(message.from_user.id, True))
    await state.set_state(ContestCreate.ends_at)


@router.message(ContestCreate.ends_at)
async def process_ends_at(message: Message, state: FSMContext):
    await state.update_data(ends_at=message.text)
    await message.answer("Количество победителей:", reply_markup=main_menu(message.from_user.id, True))
    await state.set_state(ContestCreate.winners_count)


@router.message(ContestCreate.winners_count)
async def process_winners_count(message: Message, state: FSMContext):
    await state.update_data(winners_count=int(message.text))
    await message.answer("Настройки:", reply_markup=settings_kb())
    await state.set_state(ContestCreate.settings)


@router.callback_query(ContestCreate.settings, F.data == "finish_contest")
async def finish_contest_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    from database import save_data
    
    # Create contest using the service layer
    # Note: In a real implementation, we'd have the channel object from the FSM flow
    # For now, we'll use the simple approach with the JSON database
    data["status"] = "active"
    db = await load_data()
    contest_id = str(len(db["contests"]) + 1)
    db["contests"][contest_id] = data
    await save_data(db)

    await callback.bot.send_message(
        chat_id=data["channel"],
        text=f"🎉 Новый конкурс: {data['title']}\nПриз: {data['prize']}\n🏆 Победителей: {data['winners_count']}",
        reply_markup=join_kb(contest_id)
    )

    await callback.message.answer("Конкурс создан и опубликован!", reply_markup=main_menu(callback.from_user.id, True))
    await state.clear()
    await callback.answer()


@router.callback_query(ContestCreate.settings, F.data == "cancel")
async def cancel_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Отменено", reply_markup=main_menu(callback.from_user.id, True))
    await callback.answer()