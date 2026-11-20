from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram import Bot

from database import get_contest, add_participant, get_user, update_user
from keyboards import join_kb


router = Router()


@router.callback_query(F.data.startswith("join_"))
async def join_contest(callback: CallbackQuery, bot: Bot):
    contest_id = callback.data.split("_", 1)[1]
    user_id = callback.from_user.id

    contest = await get_contest(contest_id)
    if not contest or contest.get("status") != "active":
        await callback.answer("Конкурс недоступен", show_alert=True)
        return

    participants = contest.get("participants", [])
    if str(user_id) in participants:
        await callback.answer("Вы уже участвуете", show_alert=True)
        return

    added = await add_participant(contest_id, user_id)
    if not added:
        await callback.answer("Вы уже участвуете", show_alert=True)
        return

    user = await get_user(user_id) or {}
    tickets = user.get("tickets", {}) or {}
    tickets[contest_id] = tickets.get(contest_id, 0) + 1
    await update_user(user_id, tickets=tickets)

    pending = user.get("pending_referral")
    if pending:
        ref_user = await get_user(pending) or {}
        ref_tickets = ref_user.get("tickets", {}) or {}
        amount = contest.get("ref_tickets", 1)
        ref_tickets[contest_id] = ref_tickets.get(contest_id, 0) + amount
        await update_user(pending, tickets=ref_tickets)
        await update_user(user_id, pending_referral=None)

    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{user_id}"
    await callback.message.answer(f"Ты участвуешь! Твоя реферальная ссылка: {link}")
    await callback.answer()
