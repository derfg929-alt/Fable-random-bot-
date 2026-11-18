"""Telegram bot: user menus, contest flow and admin panel (TZ §1-§13).

The bot shares every business rule with the Mini App API through the service
modules (``services``, ``contests``, ``draw``, ``admin``, ``notifications``).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

import admin
import contests as contest_service
import notifications as notify
import services
import settings
from database import ContestStatus, User, session_scope
from referrals import get_user_by_telegram, referral_link, register_user

router = Router(name="fable")

WELCOME = (
    "Добро пожаловать в Fable Randomizer! 🎉\n\n"
    "Здесь ты можешь участвовать в конкурсах, выигрывать призы и приглашать друзей, "
    "чтобы увеличить свою скорость на победу.\n\n"
    "Участвуй в конкурсах, приглашай друзей и следи за результатом!"
)

JOIN_OK = "Ты успешно зарегистрировался в конкурсе! 🎉\n\nУдачи! Следи за результатом в Fable Randomizer."

STATUS_LABELS = {
    ContestStatus.PENDING.value: "🕓 Ожидает начала",
    ContestStatus.ACTIVE.value: "🟢 Активен",
    ContestStatus.FINISHED.value: "🏁 Завершён",
    ContestStatus.CANCELLED.value: "⛔ Отменён",
}


# --------------------------------------------------------------------------
# Keyboards
# --------------------------------------------------------------------------
def main_menu_keyboard() -> InlineKeyboardMarkup:
    rows = []
    if settings.WEBAPP_URL.startswith("https://"):
        rows.append(
            [InlineKeyboardButton(text="🚀 Открыть Mini App", web_app=WebAppInfo(url=settings.WEBAPP_URL))]
        )
    rows += [
        [InlineKeyboardButton(text="🎁 Конкурсы", callback_data="menu:contests")],
        [InlineKeyboardButton(text="👤 Мой профиль", callback_data="menu:profile")],
        [InlineKeyboardButton(text="👥 Пригласить друзей", callback_data="menu:referral")],
        [InlineKeyboardButton(text="🏆 Победители", callback_data="menu:winners")],
        [InlineKeyboardButton(text="📜 История участия", callback_data="menu:history")],
        [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="menu:help")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def welcome_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🎁 Открыть конкурсы", callback_data="menu:contests")],
        [InlineKeyboardButton(text="👥 Пригласить друзей", callback_data="menu:referral")],
        [InlineKeyboardButton(text="👤 Мой профиль", callback_data="menu:profile")],
        [InlineKeyboardButton(text="🏆 Победители", callback_data="menu:winners")],
        [InlineKeyboardButton(text="📜 История участия", callback_data="menu:history")],
        [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="menu:help")],
    ]
    if settings.WEBAPP_URL.startswith("https://"):
        rows.insert(
            0,
            [InlineKeyboardButton(text="🚀 Открыть Mini App", web_app=WebAppInfo(url=settings.WEBAPP_URL))],
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def contest_keyboard(contest_id: int, joined: bool = False) -> InlineKeyboardMarkup:
    if joined:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="✅ Ты участвуешь", callback_data=f"contest:{contest_id}")]]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🎉 Участвовать", callback_data=f"join:{contest_id}")]]
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats")],
            [InlineKeyboardButton(text="🎁 Конкурсы", callback_data="admin:contests")],
            [InlineKeyboardButton(text="📢 Каналы", callback_data="admin:channels")],
            [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin:users")],
            [InlineKeyboardButton(text="🎲 Розыгрыши", callback_data="admin:draws")],
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin:settings")],
            [InlineKeyboardButton(text="🧾 Журнал", callback_data="admin:audit")],
        ]
    )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _channel_link(channel) -> str:
    if channel.invite_link:
        return channel.invite_link
    if channel.username:
        return f"https://t.me/{channel.username}"
    return ""


async def _require_admin(message: Message) -> Optional[User]:
    async with session_scope() as session:
        if not await admin.is_admin(session, message.from_user.id):
            await message.answer("⛔ Команда доступна только администраторам.")
            return None
        return await get_user_by_telegram(session, message.from_user.id)


async def _ensure_user(message: Message) -> User:
    async with session_scope() as session:
        result = await register_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            language_code=message.from_user.language_code,
        )
        return result.user


# --------------------------------------------------------------------------
# /start
# --------------------------------------------------------------------------
@router.message(CommandStart())
async def handle_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    parts = (message.text or "").split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""

    referrer_id: Optional[int] = None
    contest_id: Optional[int] = None
    if payload.startswith("ref_") and payload[4:].isdigit():
        referrer_id = int(payload[4:])
    elif payload.startswith("contest_") and payload[8:].isdigit():
        contest_id = int(payload[8:])

    async with session_scope() as session:
        result = await register_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            language_code=message.from_user.language_code,
            referrer_telegram_id=referrer_id,
        )
        new_user = result.created
        referrer = result.referrer_id

    if new_user:
        await message.answer(WELCOME, reply_markup=welcome_keyboard())
    else:
        await message.answer(
            "С возвращением в Fable Randomizer! ✨",
            reply_markup=main_menu_keyboard(),
        )

    if referrer is not None:
        async with session_scope() as session:
            await notify.notify_telegram_id(
                session,
                message.bot,
                telegram_id=referrer,
                notification_type="subscription_ok",
                dedup_key=f"referral:{message.from_user.id}",
                text="🎉 По твоей ссылке присоединился новый участник! Шансы растут.",
            )

    if contest_id is not None:
        await _do_join(message, contest_id)


# --------------------------------------------------------------------------
# User menu callbacks
# --------------------------------------------------------------------------
@router.callback_query(F.data.startswith("menu:"))
async def handle_menu(callback: CallbackQuery) -> None:
    action = callback.data.split(":", 1)[1]
    await callback.answer()
    if action == "contests":
        await _send_contests(callback.message)
    elif action == "profile":
        await _send_profile(callback.message)
    elif action == "referral":
        await _send_referral(callback.message)
    elif action == "winners":
        await _send_winners(callback.message)
    elif action == "history":
        await _send_history(callback.message)
    elif action == "help":
        await _send_help(callback.message)
    else:
        await callback.message.answer("Раздел не найден.")


@router.message(Command("contests"))
async def cmd_contests(message: Message) -> None:
    await _send_contests(message)


async def _send_contests(message: Message) -> None:
    async with session_scope() as session:
        rows = await contest_service.list_contests(session)
        joined_ids = set()
        u = await get_user_by_telegram(session, message.from_user.id)
        if u:
            entries = await contest_service.user_entries(session, u.id)
            joined_ids = {c.id for _, c in entries}
        payload = []
        for c in rows:
            count = await contest_service.entry_count(session, c.id)
            payload.append((c, count, c.id in joined_ids))

    if not payload:
        await message.answer("Пока нет активных конкурсов. Заходи позже! 🎁")
        return
    for contest, count, joined in payload:
        status = STATUS_LABELS.get(contest_service.effective_status(contest), contest.status)
        text = (
            f"🎁 <b>{contest.title}</b>\n"
            f"{contest.description or 'Условия участия — внутри.'}\n\n"
            f"{status} · Участников: {count}"
        )
        if contest.prize:
            text += f"\n🏆 Приз: {contest.prize}"
        await message.answer(
            text, parse_mode="HTML", reply_markup=contest_keyboard(contest.id, joined)
        )


@router.callback_query(F.data.startswith("contest:"))
async def handle_contest_open(callback: CallbackQuery) -> None:
    contest_id = int(callback.data.split(":", 1)[1])
    async with session_scope() as session:
        contest = await contest_service.get_contest(session, contest_id)
        if contest is None:
            await callback.answer("Конкурс не найден", show_alert=True)
            return
        count = await contest_service.entry_count(session, contest.id)
        channels = await contest_service.contest_channels(session, contest.id)
        u = await get_user_by_telegram(session, callback.from_user.id)
        joined = False
        if u:
            entries = await contest_service.user_entries(session, u.id)
            joined = any(c.id == contest.id for _, c in entries)
        lines = [f"🎁 <b>{contest.title}</b>", "", contest.description or ""]
        if contest.prize:
            lines.append(f"\n🏆 Приз: {contest.prize}")
        lines.append(f"\n{STATUS_LABELS.get(contest_service.effective_status(contest), contest.status)}")
        lines.append(f"👥 Участников: {count}")
        if channels:
            lines.append("\nОбязательные каналы:")
            for ch in channels:
                link = _channel_link(ch)
                lines.append(f"• {ch.title}" + (f" — {link}" if link else ""))
        text = "\n".join(x for x in lines if x is not None)
    await callback.message.answer(
        text, parse_mode="HTML", reply_markup=contest_keyboard(contest_id, joined)
    )
    await callback.answer()


# --------------------------------------------------------------------------
# Joining
# --------------------------------------------------------------------------
@router.callback_query(F.data.startswith("join:"))
async def handle_join_callback(callback: CallbackQuery) -> None:
    contest_id = int(callback.data.split(":", 1)[1])
    await callback.answer()
    await _do_join(callback.message, contest_id, telegram_id=callback.from_user.id)


@router.callback_query(F.data.startswith("verify:"))
async def handle_verify(callback: CallbackQuery) -> None:
    contest_id = int(callback.data.split(":", 1)[1])
    await callback.answer("Проверяю подписку...")
    await _do_join(callback.message, contest_id, telegram_id=callback.from_user.id)


@router.message(Command("join"))
async def cmd_join(message: Message) -> None:
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /join ID")
        return
    await _do_join(message, int(parts[1]))


async def _do_join(message: Message, contest_id: int, telegram_id: Optional[int] = None) -> None:
    telegram_id = telegram_id or message.from_user.id
    async with session_scope() as session:
        u = await get_user_by_telegram(session, telegram_id)
        if u is None:
            await register_user(
                session,
                telegram_id=telegram_id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
            )
        outcome = await services.join_contest_by_id(
            session, message.bot, telegram_id=telegram_id, contest_id=contest_id
        )
        missing = outcome.missing
        sub_error = outcome.subscription_error

    if outcome.status == "joined":
        await message.answer(JOIN_OK)
        return
    if outcome.status == "already":
        await message.answer("Ты уже участвуешь в этом конкурсе. Желаем удачи! 🍀")
        return
    if outcome.status == "blocked":
        await message.answer("⛔ Участие в конкурсах ограничено администратором.")
        return
    if outcome.status == "not_found":
        await message.answer("Конкурс не найден или уже недоступен.")
        return
    if outcome.status == "not_open":
        await message.answer("Этот конкурс уже завершён или отменён.")
        return

    # subscription problem
    lines = ["Чтобы участвовать, подпишись на каналы:"]
    buttons = []
    for ch in missing or []:
        link = _channel_link(ch)
        lines.append(f"• {ch.title}")
        if link:
            buttons.append([InlineKeyboardButton(text=f"Подписаться: {ch.title}", url=link)])
    if sub_error:
        lines.append("\n⚠️ Не удалось проверить подписку. Попробуй ещё раз позже.")
    lines.append("\nПосле подписки нажми «Проверить подписку».")
    buttons.append([InlineKeyboardButton(text="🔄 Проверить подписку", callback_data=f"verify:{contest_id}")])
    await message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        disable_web_page_preview=True,
    )


# --------------------------------------------------------------------------
# Profile / referral / history / winners / help
# --------------------------------------------------------------------------
@router.message(Command("profile"))
async def cmd_profile(message: Message) -> None:
    await _send_profile(message)


async def _send_profile(message: Message) -> None:
    async with session_scope() as session:
        user = await get_user_by_telegram(session, message.from_user.id)
        if user is None:
            await message.answer("Напиши /start, чтобы зарегистрироваться.")
            return
        profile = await services.build_profile(session, user)
    text = (
        f"👤 <b>Мой профиль</b>\n\n"
        f"Имя: {user.first_name or '—'}\n"
        f"Username: {'@' + user.username if user.username else '—'}\n"
        f"Дата регистрации: {user.registered_at.strftime('%d.%m.%Y') if user.registered_at else '—'}\n\n"
        f"👥 Приглашено друзей: {user.referral_count}\n"
        f"🎯 Активных участий: {profile.active_entries}\n"
        f"✅ Завершённых участий: {profile.finished_entries}\n"
        f"🏆 Побед: {profile.wins}\n\n"
        "Приглашай друзей — это увеличивает твои шансы на победу!"
    )
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="👥 Пригласить друзей", callback_data="menu:referral")]
            ]
        ),
    )


@router.message(Command("myref"))
async def cmd_myref(message: Message) -> None:
    await _send_referral(message)


async def _send_referral(message: Message) -> None:
    async with session_scope() as session:
        user = await get_user_by_telegram(session, message.from_user.id)
        if user is None:
            await register_user(
                session,
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
            )
            user = await get_user_by_telegram(session, message.from_user.id)
        link = referral_link(user)
        count = user.referral_count
    if not link:
        await message.answer("Администратору нужно указать BOT_USERNAME в настройках.")
        return
    share = f"https://t.me/share/url?url={link}&text=Присоединяйся к Fable Randomizer!"
    await message.answer(
        "👥 <b>Приглашай друзей!</b>\n\n"
        "Чем больше друзей присоединится по твоей ссылке, тем выше твои шансы на победу.\n\n"
        f"Твоя ссылка:\n{link}\n\n"
        f"Уже приглашено: {count}",
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="📤 Поделиться", url=share)]]
        ),
    )


@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    await _send_history(message)


async def _send_history(message: Message) -> None:
    async with session_scope() as session:
        user = await get_user_by_telegram(session, message.from_user.id)
        if user is None:
            await message.answer("Напиши /start, чтобы зарегистрироваться.")
            return
        entries = await contest_service.user_entries(session, user.id)
        wins = await admin.user_wins(session, user)
        win_ids = {c.id for _, c in wins}
        rows = [(e, c, c.id in win_ids) for e, c in entries]
    if not rows:
        await message.answer("У тебя пока нет участий в конкурсах. Загляни в /contests 🎁")
        return
    lines = ["📜 <b>История участия</b>", ""]
    for entry, contest, won in rows[:20]:
        status = STATUS_LABELS.get(contest_service.effective_status(contest), contest.status)
        result = "🏆 Победа!" if won else ("—" if contest.status == ContestStatus.FINISHED.value else "в процессе")
        lines.append(f"• {contest.title} — {status} — {result}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("winners"))
async def cmd_winners(message: Message) -> None:
    await _send_winners(message)


async def _send_winners(message: Message) -> None:
    from sqlalchemy import select

    from database import Contest, User, Winner

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Winner, Contest, User)
                .join(Contest, Contest.id == Winner.contest_id)
                .join(User, User.id == Winner.user_id)
                .order_by(Winner.id.desc())
                .limit(50)
            )
        ).all()
    if not rows:
        await message.answer("Победителей пока нет. Участвуй в конкурсах! 🏆")
        return
    lines = ["🏆 <b>Победители</b>", ""]
    for winner, contest, wuser in rows:
        name = wuser.first_name or f"id{wuser.telegram_id}"
        if wuser.username:
            name += f" (@{wuser.username})"
        lines.append(f"• {contest.title} — {name}")
        if contest.prize:
            lines.append(f"   Приз: {contest.prize}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await _send_help(message)


async def _send_help(message: Message) -> None:
    await message.answer(
        "ℹ️ <b>Помощь Fable Randomizer</b>\n\n"
        "🎁 /contests — список конкурсов\n"
        "🎉 /join ID — участвовать в конкурсе\n"
        "👥 /myref — твоя реферальная ссылка\n"
        "👤 /profile — профиль\n"
        "📜 /history — история участия\n"
        "🏆 /winners — победители\n\n"
        "Приглашай друзей по своей ссылке — это увеличивает шансы на победу!",
        parse_mode="HTML",
    )


# --------------------------------------------------------------------------
# Admin panel
# --------------------------------------------------------------------------
@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    user = await _require_admin(message)
    if user is None:
        return
    await message.answer("🛠 <b>Административная панель</b>", parse_mode="HTML", reply_markup=admin_keyboard())


@router.callback_query(F.data.startswith("admin:"))
async def handle_admin_menu(callback: CallbackQuery) -> None:
    async with session_scope() as session:
        if not await admin.is_admin(session, callback.from_user.id):
            await callback.answer("Нет доступа", show_alert=True)
            return
    action = callback.data.split(":", 1)[1]
    await callback.answer()
    if action == "stats":
        await _admin_stats(callback.message)
    elif action == "contests":
        await _admin_contests(callback.message)
    elif action == "channels":
        await _admin_channels(callback.message)
    elif action == "users":
        await _admin_users(callback.message)
    elif action == "draws":
        await _admin_draws(callback.message)
    elif action == "settings":
        await _admin_settings(callback.message)
    elif action == "audit":
        await _admin_audit(callback.message)


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if await _require_admin(message) is None:
        return
    await _admin_stats(message)


async def _admin_stats(message: Message) -> None:
    async with session_scope() as session:
        s = await admin.get_stats(session)
    await message.answer(
        "📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: {s.users_total} (новых за 7 дней: {s.users_new_7d})\n"
        f"🎁 Активных конкурсов: {s.contests_active}\n"
        f"🏁 Завершённых: {s.contests_finished}\n"
        f"🎯 Всего участий: {s.entries_total}\n"
        f"🏆 Победителей: {s.winners_total}\n"
        f"👥 Приглашений: {s.referrals_total}",
        parse_mode="HTML",
    )


@router.message(Command("channels"))
async def cmd_channels(message: Message) -> None:
    if await _require_admin(message) is None:
        return
    await _admin_channels(message)


async def _admin_channels(message: Message) -> None:
    async with session_scope() as session:
        rows = await contest_service.list_channels(session, include_inactive=True)
    if not rows:
        await message.answer("Каналы ещё не добавлены. Используй /addchannel @channel")
        return
    lines = ["📢 <b>Каналы</b>", ""]
    for ch in rows:
        state = "активен" if ch.is_active else "удалён"
        admin_mark = "✅ бот админ" if ch.bot_is_admin else "⚠️ нет прав бота"
        lines.append(f"{ch.id}. {ch.title} (@{ch.username or 'private'}) — {state}, {admin_mark}")
    await message.answer("\n".join(lines), parse_mode="HTML")


async def _admin_users(message: Message) -> None:
    async with session_scope() as session:
        rows = await admin.recent_users(session, limit=15)
        total = (await admin.get_stats(session)).users_total
    lines = [f"👥 <b>Пользователи</b> (всего: {total})", ""]
    for u in rows:
        flag = "⛔" if u.is_blocked else "✅"
        lines.append(
            f"{flag} {u.first_name or '—'} @{u.username or '—'} "
            f"(id{u.telegram_id}) · друзей: {u.referral_count}"
        )
    lines.append("\n🔍 Поиск: /finduser TELEGRAM_ID")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("addchannel"))
async def cmd_addchannel(message: Message) -> None:
    if await _require_admin(message) is None:
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Формат: /addchannel @channel или /addchannel -1001234567890")
        return
    target = parts[1].strip()
    if target.lstrip("-").isdigit():
        target = int(target)
    try:
        chat = await message.bot.get_chat(target)
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"Не удалось получить канал: {exc}")
        return
    bot_is_admin = False
    try:
        me = await message.bot.get_me()
        member = await message.bot.get_chat_member(chat.id, me.id)
        bot_is_admin = getattr(member, "status", None) in ("administrator", "creator")
    except Exception:  # noqa: BLE001
        bot_is_admin = False
    async with session_scope() as session:
        user = await get_user_by_telegram(session, message.from_user.id)
        channel = await contest_service.upsert_channel(
            session,
            chat_id=chat.id,
            title=chat.title or str(chat.id),
            username=chat.username,
            invite_link=getattr(chat, "invite_link", None),
            added_by=user.id if user else None,
            bot_is_admin=bot_is_admin,
        )
        await admin.audit(
            session,
            actor_id=message.from_user.id,
            action="channel_add",
            target_type="channel",
            target_id=channel.id,
            payload={"chat_id": chat.id},
        )
    note = "" if bot_is_admin else "\n⚠️ Добавь бота администратором канала, иначе проверка подписки будет недоступна."
    await message.answer(f"✅ Канал сохранён: {chat.title}{note}")


@router.message(Command("newcontest"))
async def cmd_newcontest(message: Message, state: FSMContext) -> None:
    if await _require_admin(message) is None:
        return
    await state.set_state(NewContest.title)
    await message.answer("Введи название конкурса (или /cancel для отмены).")


class NewContest(StatesGroup):
    title = State()
    description = State()
    prize = State()
    image = State()
    winners = State()
    start = State()
    end = State()
    channels = State()
    preview = State()


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Нечего отменять.")
        return
    await state.clear()
    await message.answer("Создание конкурса отменено.")


@router.message(NewContest.title)
async def nc_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=(message.text or "").strip())
    await state.set_state(NewContest.description)
    await message.answer("Введи описание конкурса.")


@router.message(NewContest.description)
async def nc_description(message: Message, state: FSMContext) -> None:
    await state.update_data(description=(message.text or "").strip())
    await state.set_state(NewContest.prize)
    await message.answer("Введи приз (что получит победитель).")


@router.message(NewContest.prize)
async def nc_prize(message: Message, state: FSMContext) -> None:
    await state.update_data(prize=(message.text or "").strip())
    await state.set_state(NewContest.image)
    await message.answer("Отправь ссылку на изображение приза или напиши «-», чтобы пропустить.")


@router.message(NewContest.image)
async def nc_image(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    await state.update_data(image_url=None if value in ("-", "") else value)
    await state.set_state(NewContest.winners)
    await message.answer("Сколько победителей? (число, например 1)")


@router.message(NewContest.winners)
async def nc_winners(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) < 1:
        await message.answer("Введи целое число больше нуля.")
        return
    await state.update_data(winners_count=int(raw))
    await state.set_state(NewContest.start)
    await message.answer("Дата и время начала (ДД.ММ.ГГГГ ЧЧ:ММ) или «-» (сразу активно).")


@router.message(NewContest.start)
async def nc_start(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if value in ("-", ""):
        await state.update_data(start_at=None)
    else:
        try:
            await state.update_data(start_at=datetime.strptime(value, "%d.%m.%Y %H:%M").isoformat())
        except ValueError:
            await message.answer("Формат: ДД.ММ.ГГГГ ЧЧ:ММ или «-».")
            return
    await state.set_state(NewContest.end)
    await message.answer("Дата и время окончания (ДД.ММ.ГГГГ ЧЧ:ММ) или «-».")


@router.message(NewContest.end)
async def nc_end(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if value in ("-", ""):
        await state.update_data(end_at=None)
    else:
        try:
            await state.update_data(end_at=datetime.strptime(value, "%d.%m.%Y %H:%M").isoformat())
        except ValueError:
            await message.answer("Формат: ДД.ММ.ГГГГ ЧЧ:ММ или «-».")
            return
    async with session_scope() as session:
        channels = await contest_service.list_channels(session)
    if not channels:
        await state.update_data(channel_ids=[])
        await _nc_preview(message, state)
        return
    rows = [
        [InlineKeyboardButton(text=f"☐ {ch.title}", callback_data=f"nc:chan:{ch.id}")]
        for ch in channels
    ]
    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data="nc:channels_done")])
    await state.set_state(NewContest.channels)
    await message.answer(
        "Выбери обязательные каналы (нажимай, чтобы отметить):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(NewContest.channels, F.data.startswith("nc:chan:"))
async def nc_toggle_channel(callback: CallbackQuery, state: FSMContext) -> None:
    channel_id = int(callback.data.split(":")[2])
    data = await state.get_data()
    selected = set(data.get("channel_ids", []))
    selected.symmetric_difference_update({channel_id})
    await state.update_data(channel_ids=list(selected))
    async with session_scope() as session:
        channels = await contest_service.list_channels(session)
    rows = [
        [
            InlineKeyboardButton(
                text=("☑ " if ch.id in selected else "☐ ") + ch.title,
                callback_data=f"nc:chan:{ch.id}",
            )
        ]
        for ch in channels
    ]
    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data="nc:channels_done")])
    await callback.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await callback.answer()


@router.callback_query(NewContest.channels, F.data == "nc:channels_done")
async def nc_channels_done(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await _nc_preview(callback.message, state)


async def _nc_preview(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(NewContest.preview)
    text = (
        "🔎 <b>Предпросмотр конкурса</b>\n\n"
        f"Название: {data.get('title')}\n"
        f"Описание: {data.get('description')}\n"
        f"Приз: {data.get('prize')}\n"
        f"Победителей: {data.get('winners_count')}\n"
        f"Начало: {data.get('start_at') or 'сразу'}\n"
        f"Окончание: {data.get('end_at') or '—'}\n"
        f"Каналов: {len(data.get('channel_ids', []))}"
    )
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Создать", callback_data="nc:create")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="nc:cancel")],
            ]
        ),
    )


@router.callback_query(NewContest.preview, F.data == "nc:cancel")
async def nc_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await callback.message.answer("Создание конкурса отменено.")


@router.callback_query(NewContest.preview, F.data == "nc:create")
async def nc_create(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    async with session_scope() as session:
        user = await get_user_by_telegram(session, callback.from_user.id)
        contest = await contest_service.create_contest(
            session,
            title=data.get("title") or "Без названия",
            description=data.get("description") or "",
            prize=data.get("prize") or "",
            image_url=data.get("image_url"),
            winners_count=data.get("winners_count") or 1,
            created_by=user.id if user else None,
            start_at=_parse_dt(data.get("start_at")),
            end_at=_parse_dt(data.get("end_at")),
            channel_ids=data.get("channel_ids", []),
        )
        await admin.audit(
            session,
            actor_id=callback.from_user.id,
            action="contest_create",
            target_type="contest",
            target_id=contest.id,
            payload={"title": contest.title},
        )
    await callback.answer("Конкурс создан")
    await callback.message.answer(
        f"✅ Конкурс создан! ID: {contest.id}\nОпубликовать: /publish {contest.id}"
    )


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


@router.message(Command("mycontests"))
async def cmd_mycontests(message: Message) -> None:
    if await _require_admin(message) is None:
        return
    await _admin_contests(message)


async def _admin_contests(message: Message) -> None:
    async with session_scope() as session:
        rows = await contest_service.list_contests(session, include_archived=True)
        payload = []
        for c in rows:
            count = await contest_service.entry_count(session, c.id)
            payload.append((c, count))
    if not payload:
        await message.answer("Конкурсов пока нет. Создай: /newcontest")
        return
    lines = ["🎁 <b>Конкурсы</b>", ""]
    for contest, count in payload[:30]:
        status = STATUS_LABELS.get(contest_service.effective_status(contest), contest.status)
        arch = " 🗄" if contest.archived else ""
        lines.append(
            f"#{contest.id} {contest.title} — {status}{arch}\n"
            f"   Участников: {count} · Победителей: {contest.winners_count}\n"
            f"   /publish {contest.id} · /draw {contest.id} · /participants {contest.id}"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("editcontest"))
async def cmd_editcontest(message: Message) -> None:
    if await _require_admin(message) is None:
        return
    parts = (message.text or "").split(maxsplit=3)
    # /editcontest ID field value   (field: title|description|prize|winners)
    if len(parts) < 4 or not parts[1].isdigit():
        await message.answer(
            "Формат: /editcontest ID поле значение\n"
            "поля: title, description, prize, winners"
        )
        return
    contest_id, field, value = int(parts[1]), parts[2].lower(), parts[3]
    async with session_scope() as session:
        contest = await contest_service.get_contest(session, contest_id, include_archived=True)
        if contest is None:
            await message.answer("Конкурс не найден.")
            return
        kwargs = {}
        if field == "title":
            kwargs["title"] = value
        elif field == "description":
            kwargs["description"] = value
        elif field == "prize":
            kwargs["prize"] = value
        elif field == "winners" and value.isdigit():
            kwargs["winners_count"] = int(value)
        else:
            await message.answer("Неизвестное поле. Доступно: title, description, prize, winners")
            return
        await contest_service.update_contest(session, contest, **kwargs)
        await notify.broadcast_contest(
            session,
            message.bot,
            contest=contest,
            notification_type="contest_changed",
            text=f"✏️ Условия конкурса «{contest.title}» изменились. Проверь детали.",
            dedup_suffix="changed",
        )
        await admin.audit(
            session,
            actor_id=message.from_user.id,
            action="contest_update",
            target_type="contest",
            target_id=contest_id,
            payload={field: value},
        )
    await message.answer(f"✅ Конкурс #{contest_id} обновлён ({field}).")


@router.message(Command("publish"))
async def cmd_publish(message: Message) -> None:
    if await _require_admin(message) is None:
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /publish ID")
        return
    async with session_scope() as session:
        contest = await contest_service.get_contest(session, int(parts[1]))
        if contest is None:
            await message.answer("Конкурс не найден.")
            return
        outcome = await services.publish_contest(session, message.bot, contest=contest)
    text = f"📢 Опубликовано в каналах: {outcome.sent} из {outcome.channels}."
    if outcome.failed:
        text += "\nНе удалось:\n" + "\n".join(outcome.failed)
    await message.answer(text)


@router.message(Command("finish"))
async def cmd_finish(message: Message) -> None:
    if await _require_admin(message) is None:
        return
    await _finish_or_draw(message, redraw=False)


@router.message(Command("draw"))
async def cmd_draw(message: Message) -> None:
    if await _require_admin(message) is None:
        return
    await _finish_or_draw(message, redraw=True)


async def _finish_or_draw(message: Message, *, redraw: bool) -> None:
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: /draw ID [кол-во победителей] [причина]")
        return
    contest_id = int(parts[1])
    winners_count = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
    reason = " ".join(parts[3:]) if len(parts) > 3 else ("Повторный розыгрыш" if redraw else None)
    async with session_scope() as session:
        contest = await contest_service.get_contest(session, contest_id)
        if contest is None:
            await message.answer("Конкурс не найден.")
            return
        if not redraw and contest.status == ContestStatus.FINISHED.value:
            # A plain /finish must not silently run a second draw; require /draw.
            await message.answer(
                "Конкурс уже завершён. Повторный розыгрыш: /draw "
                f"{contest_id} <кол-во> <причина>"
            )
            return
        if redraw and contest.status != ContestStatus.FINISHED.value:
            await message.answer("Повторный розыгрыш доступен только для завершённого конкурса.")
            return
        outcome = await services.finish_and_draw(
            session,
            message.bot,
            contest=contest,
            winners_count=winners_count,
            run_by=message.from_user.id,
            redraw=redraw,
            reason=reason,
        )
    if not outcome.ok:
        reasons = {"no_participants": "В конкурсе нет участников.", "archived": "Конкурс в архиве."}
        await message.answer(reasons.get(outcome.reason, "Не удалось провести розыгрыш."))
        return
    names = ", ".join((u.first_name or f"id{u.telegram_id}") for _, u in outcome.winners or [])
    await message.answer(f"🏆 Розыгрыш проведён!\nПобедители: {names}")


@router.message(Command("participants"))
async def cmd_participants(message: Message) -> None:
    if await _require_admin(message) is None:
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /participants ID")
        return
    async with session_scope() as session:
        entries = await contest_service.contest_entries(session, int(parts[1]))
        lines = ["👥 <b>Участники</b>", ""]
        for e in entries:
            u = await session.get(User, e.user_id)
            if u:
                lines.append(f"• {u.first_name or ''} @{u.username or '—'} (id{u.telegram_id})")
    if len(lines) == 2:
        lines.append("Участников нет.")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("draws"))
async def cmd_draws(message: Message) -> None:
    if await _require_admin(message) is None:
        return
    await _admin_draws(message)


@router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    """Send a JSON report of a contest (participants + all draw attempts)."""
    if await _require_admin(message) is None:
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /export ID")
        return
    async with session_scope() as session:
        report = await admin.contest_report(session, int(parts[1]))
    if not report:
        await message.answer("Конкурс не найден.")
        return
    import json

    from aiogram.types import BufferedInputFile

    payload = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
    await message.answer_document(
        BufferedInputFile(payload, filename=f"contest_{parts[1]}_report.json"),
        caption="📄 Отчёт о конкурсе",
    )



@router.message(Command("finduser"))
async def cmd_finduser(message: Message) -> None:
    if await _require_admin(message) is None:
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Формат: /finduser TELEGRAM_ID")
        return
    telegram_id = int(parts[1])
    async with session_scope() as session:
        user = await admin.find_user(session, telegram_id)
        if user is None:
            await message.answer("Пользователь не найден.")
            return
        history = await admin.user_history(session, user)
        wins = await admin.user_wins(session, user)
        text = (
            f"👤 <b>{user.first_name or '—'}</b> "
            f"({('@' + user.username) if user.username else '—'})\n"
            f"ID: {user.telegram_id}\n"
            f"Регистрация: {user.registered_at.strftime('%d.%m.%Y') if user.registered_at else '—'}\n"
            f"Приглашено: {user.referral_count}\n"
            f"Участий: {len(history)}\n"
            f"Побед: {len(wins)}\n"
            f"Статус: {'⛔ заблокирован' if user.is_blocked else '✅ активен'}\n\n"
            f"Блокировка: /block {telegram_id} причина  ·  /unblock {telegram_id}"
        )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("block"))
async def cmd_block(message: Message) -> None:
    if await _require_admin(message) is None:
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Формат: /block TELEGRAM_ID [причина]")
        return
    telegram_id = int(parts[1])
    reason = parts[2] if len(parts) > 2 else None
    async with session_scope() as session:
        user = await admin.find_user(session, telegram_id)
        if user is None:
            await message.answer("Пользователь не найден.")
            return
        await admin.set_blocked(session, user, True, reason)
        await admin.audit(
            session,
            actor_id=message.from_user.id,
            action="user_block",
            target_type="user",
            target_id=telegram_id,
            payload={"reason": reason},
        )
    await message.answer(
        f"⛔ Пользователь {telegram_id} заблокирован (участие запрещено, история сохранена)."
    )


@router.message(Command("unblock"))
async def cmd_unblock(message: Message) -> None:
    if await _require_admin(message) is None:
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Формат: /unblock TELEGRAM_ID")
        return
    telegram_id = int(parts[1])
    async with session_scope() as session:
        user = await admin.find_user(session, telegram_id)
        if user is None:
            await message.answer("Пользователь не найден.")
            return
        await admin.set_blocked(session, user, False)
        await admin.audit(
            session,
            actor_id=message.from_user.id,
            action="user_unblock",
            target_type="user",
            target_id=telegram_id,
        )
    await message.answer(f"✅ Пользователь {telegram_id} разблокирован.")


@router.message(Command("notify"))
async def cmd_notify(message: Message) -> None:
    """Toggle a notification type on/off (admin)."""
    if await _require_admin(message) is None:
        return
    parts = (message.text or "").split()
    if len(parts) != 3 or parts[2] not in ("0", "1"):
        lines = ["🔔 <b>Уведомления</b> (1 — вкл, 0 — выкл)", ""]
        async with session_scope() as session:
            for key, label in notify.NOTIFICATION_TYPES.items():
                value = await admin.get_setting(session, notify.setting_key(key))
                mark = "✅" if value not in ("0", "false", "") else "⛔"
                lines.append(f"{mark} {key} — {label}")
        lines.append("\nИзменить: /notify TYPE 0|1")
        await message.answer("\n".join(lines), parse_mode="HTML")
        return
    key, value = parts[1], parts[2]
    if key not in notify.NOTIFICATION_TYPES:
        await message.answer("Неизвестный тип уведомления.")
        return
    async with session_scope() as session:
        await admin.set_setting(session, notify.setting_key(key), value)
        await admin.audit(
            session,
            actor_id=message.from_user.id,
            action="notification_toggle",
            target_type="settings",
            target_id=notify.setting_key(key),
            payload={"value": value},
        )
    state = "включены" if value == "1" else "выключены"
    await message.answer(f"🔔 Уведомления «{key}» {state}.")


async def _admin_draws(message: Message) -> None:
    async with session_scope() as session:
        logs = await admin.list_draws(session)
    if not logs:
        await message.answer("Розыгрышей пока нет.")
        return
    lines = ["🎲 <b>История розыгрышей</b>", ""]
    for log in logs[:30]:
        lines.append(
            f"#{log.id} конкурс {log.contest_id} — {log.status}\n"
            f"   Победителей: {log.winners_count} из {log.participants_count}\n"
            f"   Код: {log.commitment}"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    if await _require_admin(message) is None:
        return
    await _admin_settings(message)


async def _admin_settings(message: Message) -> None:
    async with session_scope() as session:
        values = await admin.all_settings(session)
    lines = ["⚙️ <b>Настройки</b>", ""]
    for key, value in sorted(values.items()):
        lines.append(f"• {key} = {value}")
    lines.append("\nИзменить: /set KEY VALUE (критические — только владелец)")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("set"))
async def cmd_set(message: Message) -> None:
    if await _require_admin(message) is None:
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3:
        await message.answer("Формат: /set KEY VALUE")
        return
    key, value = parts[1], parts[2]
    async with session_scope() as session:
        if key in admin.OWNER_ONLY_SETTINGS and not await admin.is_owner(session, message.from_user.id):
            await message.answer("⛔ Эту настройку может менять только владелец проекта.")
            return
        await admin.set_setting(session, key, value)
        await admin.audit(
            session,
            actor_id=message.from_user.id,
            action="settings_update",
            target_type="settings",
            target_id=key,
            payload={"value": value},
        )
    await message.answer(f"✅ {key} = {value}")


async def _admin_audit(message: Message) -> None:
    async with session_scope() as session:
        logs = await admin.recent_audit(session)
    if not logs:
        await message.answer("Журнал пуст.")
        return
    lines = ["🧾 <b>Журнал событий</b>", ""]
    for log in logs[:30]:
        lines.append(f"• {log.action} — {log.target_type or ''}:{log.target_id or ''} (actor {log.actor_id})")
    await message.answer("\n".join(lines), parse_mode="HTML")


# --------------------------------------------------------------------------
# Dispatcher factory
# --------------------------------------------------------------------------
def build_dispatcher() -> Dispatcher:
    from aiogram.fsm.storage.memory import MemoryStorage

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    return dp


USER_COMMANDS = [
    ("start", "Запустить бота"),
    ("contests", "Список конкурсов"),
    ("join", "Участвовать в конкурсе"),
    ("myref", "Моя реферальная ссылка"),
    ("profile", "Мой профиль"),
    ("history", "История участия"),
    ("winners", "Победители"),
    ("help", "Помощь"),
]


async def setup_bot_menu(bot: Bot) -> None:
    """Publish the command list and the Mini App menu button to Telegram.

    Best-effort: a Telegram outage must not stop startup, so errors are logged
    and swallowed. When ``WEBAPP_URL`` is set the chat menu button opens the
    Mini App, otherwise the regular command list is shown.
    """
    from aiogram.types import BotCommand, MenuButtonCommands, MenuButtonWebApp, WebAppInfo

    try:
        await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in USER_COMMANDS])
        if settings.WEBAPP_URL.startswith("https://"):
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="Fable", web_app=WebAppInfo(url=settings.WEBAPP_URL))
            )
        else:
            await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as exc:  # noqa: BLE001 - never block startup
        print(f"[bot] не удалось настроить меню: {exc}")


async def send_safe(bot: Bot, chat_id: int, text: str, **kwargs) -> bool:
    """Send a message, swallowing blocked-bot / network errors."""
    try:
        await bot.send_message(chat_id, text, **kwargs)
        return True
    except TelegramForbiddenError:
        return False
    except Exception:  # noqa: BLE001
        return False
