from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram import Bot

from database import get_contest, add_participant, get_user, update_user
from keyboards import join_kb, main_menu
from captcha import generate_captcha
from config import ADMIN_IDS, DB_PATH

router = Router()


@router.callback_query(F.data.startswith("join_"))
async def join_contest(callback: CallbackQuery, bot: Bot):
    contest_id = callback.data.split("_", 1)[1]
    user_id = callback.from_user.id

    contest = await get_contest(contest_id)
    if not contest or contest.get("status") != "active":
        await callback.answer("Конкурс недоступен", show_alert=True)
        return

    user = await get_user(user_id)
    captcha_passed = user.get("captcha_passed", False) if user else False

    if not captcha_passed:
        if user and user.get("captcha_answer") is not None:
            # User already started captcha but didn't finish - show the image again
            await callback.message.answer_photo(
                photo=user["captcha_answer"][0] if isinstance(user["captcha_answer"], tuple) else user["captcha_answer"],
                caption="Введите текст с картинки:"
            )
            await callback.answer()
            return

        # Generate new captcha
        captcha_image, captcha_answer = generate_captcha()
        user_db = await get_user(user_id)
        user_db["captcha_answer"] = captcha_image
        await update_user(user_id, **user_db)

        await callback.message.answer_photo(
            photo=captcha_image,
            caption="Введите текст с картинки:"
        )
        await callback.answer()
        return

    # Captcha already passed - proceed with participation
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
        ref_user = await get_user(int(pending)) or {}
        ref_tickets = ref_user.get("tickets", {}) or {}
        amount = contest.get("ref_tickets", 1)
        ref_tickets[contest_id] = ref_tickets.get(contest_id, 0) + amount
        await update_user(int(pending), tickets=ref_tickets)
        await update_user(user_id, pending_referral=None)

    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{user_id}"
    await callback.message.answer(f"Ты участвуешь! Твоя реферальная ссылка: {link}")
    await callback.answer()


@router.message(F.text)
async def process_captcha_text(message: Message):
    user_id = message.from_user.id
    user = await get_user(user_id)

    if not user or not user.get("captcha_answer"):
        # No captcha in progress, ignore or handle normally
        return

    captcha_answer = user.get("captcha_answer")
    if isinstance(captcha_answer, tuple):
        captcha_answer = captcha_answer[1] if len(captcha_answer) > 1 else str(captcha_answer)

    if message.text == captcha_answer:
        # Captcha correct - mark as passed and clean up
        await update_user(user_id, captcha_passed=True, captcha_answer=None)
        await message.answer("Капча пройдена!")

        # Now proceed with contest participation
        # Check if user was trying to join (check last message context)
        # For now, just inform user
    else:
        await message.answer("Неверный ответ. Попробуйте еще раз:")
        # Keep captcha_answer in database for retry