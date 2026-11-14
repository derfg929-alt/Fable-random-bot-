"""Bot handler tests: user commands, admin panel, FSM contest creation.

Handlers are invoked directly with real ``aiogram`` Message/CallbackQuery
objects bound to a fake bot, so the actual handler logic runs (no mocks of the
code under test).
"""
from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, User
from sqlalchemy import func, select

import admin
import bot as bot_module
import contests as contest_service
from database import Contest, ContestStatus, Entry
from referrals import get_user_by_telegram, register_user
from tests.helpers import make_channel, make_contest


class HandlerBot(Bot):
    """A real ``aiogram.Bot`` subclass that answers API calls locally.

    Handlers call ``message.answer(...)`` etc., which go through ``Bot.__call__``.
    We intercept the method objects here so the real handler code runs without a
    network connection.
    """

    def __init__(self, membership=None, fail=False):
        super().__init__(token="123456:TEST-TOKEN")
        self.membership = membership or {}
        self.fail = fail
        self.answers: list[str] = []
        self.markups = []
        self._sent_chats: list[int] = []

    async def __call__(self, method, request_timeout=None):
        name = type(method).__name__
        if name == "SendMessage":
            if self.fail:
                raise TelegramForbiddenError(method=method, message="bot blocked")
            self.answers.append(method.text)
            self._sent_chats.append(method.chat_id)
            self.markups.append(method.reply_markup)
            return Message(
                message_id=len(self.answers),
                date=datetime.now(timezone.utc),
                chat=Chat(id=method.chat_id, type="private"),
                text=method.text,
            )
        if name == "GetChatMember":
            status = self.membership.get(method.user_id, "left")
            if self.fail:
                raise TelegramBadRequest(method=method, message="unavailable")
            return _FakeChatMember(status)
        if name == "GetChat":
            target = method.chat_id
            return Chat(
                id=target if isinstance(target, int) else -100999,
                type="channel",
                title="Fake Channel",
                username="fakechannel",
            )
        if name == "GetMe":
            return User(id=999, is_bot=True, first_name="FableBot", username="fable_test_bot")
        if name in ("EditMessageReplyMarkup", "EditMessageText"):
            return Message(
                message_id=1,
                date=datetime.now(timezone.utc),
                chat=Chat(id=1, type="private"),
                text="edited",
            )
        if name in ("AnswerCallbackQuery", "DeleteMessage", "DeleteWebhook"):
            return True
        raise AssertionError(f"Unexpected bot call: {name}")


class _FakeChatMember:
    def __init__(self, status: str):
        self.status = status


def make_message(bot, text, user_id=1, first_name="User"):
    msg = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name=first_name, username=f"u{user_id}"),
        text=text,
    )
    return msg.as_(bot)


def make_callback(bot, data, user_id=1, first_name="User"):
    msg = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name=first_name),
        text="x",
    ).as_(bot)
    return CallbackQuery(
        id="cb1",
        from_user=User(id=user_id, is_bot=False, first_name=first_name),
        chat_instance="ci",
        data=data,
        message=msg,
    ).as_(bot)


def make_state(bot, user_id=1) -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id),
    )


# --------------------------------------------------------------------------
# /start
# --------------------------------------------------------------------------
async def test_start_registers_and_welcomes():
    bot = HandlerBot()
    msg = make_message(bot, "/start")
    state = make_state(bot)
    await bot_module.handle_start(msg, state)
    assert any("Добро пожаловать в Fable Randomizer" in a for a in bot.answers)
    async with __import__("database").session_scope() as session:
        user = await get_user_by_telegram(session, 1)
        assert user is not None
    # the welcome message carries the required buttons
    markup = bot.markups[-1]
    labels = [b.text for row in markup.inline_keyboard for b in row]
    for expected in ["Открыть конкурсы", "Пригласить друзей", "Мой профиль", "Победители", "История участия", "Помощь"]:
        assert any(expected in l for l in labels)


async def test_start_twice_does_not_duplicate():
    bot = HandlerBot()
    state = make_state(bot)
    await bot_module.handle_start(make_message(bot, "/start"), state)
    await bot_module.handle_start(make_message(bot, "/start"), state)
    from database import session_scope

    async with session_scope() as session:
        count = (await session.execute(select(func.count()).select_from(__import__("database").User))).scalar_one()
    assert count == 1


async def test_start_with_referral_payload():
    bot = HandlerBot()
    state = make_state(bot)
    await bot_module.handle_start(make_message(bot, "/start", user_id=1), state)
    await bot_module.handle_start(make_message(bot, "/start ref_1", user_id=2), state)
    from database import session_scope

    async with session_scope() as session:
        alice = await get_user_by_telegram(session, 1)
        bob = await get_user_by_telegram(session, 2)
    assert alice.referral_count == 1
    assert bob.referred_by == alice.id


# --------------------------------------------------------------------------
# profile / referral / help
# --------------------------------------------------------------------------
async def test_profile_shows_no_bonus_percentage():
    bot = HandlerBot()
    state = make_state(bot)
    await bot_module.handle_start(make_message(bot, "/start"), state)
    await bot_module.cmd_profile(make_message(bot, "/profile"))
    text = "\n".join(bot.answers)
    assert "Приглашено друзей" in text
    assert "%" not in text  # internal percentages must never be shown


async def test_myref_returns_personal_link():
    bot = HandlerBot()
    state = make_state(bot)
    await bot_module.handle_start(make_message(bot, "/start", user_id=7), state)
    await bot_module.cmd_myref(make_message(bot, "/myref", user_id=7))
    assert any("ref_7" in a for a in bot.answers)


async def test_help_lists_commands():
    bot = HandlerBot()
    await bot_module.cmd_help(make_message(bot, "/help"))
    text = "\n".join(bot.answers)
    for cmd in ["/contests", "/join", "/myref", "/profile", "/history", "/winners"]:
        assert cmd in text


async def test_history_empty_message():
    bot = HandlerBot()
    state = make_state(bot)
    await bot_module.handle_start(make_message(bot, "/start"), state)
    await bot_module.cmd_history(make_message(bot, "/history"))
    assert any("нет участий" in a for a in bot.answers)


# --------------------------------------------------------------------------
# joining via bot
# --------------------------------------------------------------------------
async def test_join_command_success():
    bot = HandlerBot({1: "member"})
    state = make_state(bot)
    await bot_module.handle_start(make_message(bot, "/start"), state)
    from database import session_scope

    async with session_scope() as session:
        contest = await make_contest(session, channels=[])
        contest_id = contest.id
    await bot_module.cmd_join(make_message(bot, f"/join {contest_id}"))
    assert any("успешно зарегистрировался" in a for a in bot.answers)


async def test_join_command_requires_subscription():
    bot = HandlerBot({})  # not a member
    state = make_state(bot)
    await bot_module.handle_start(make_message(bot, "/start"), state)
    from database import session_scope

    async with session_scope() as session:
        ch = await make_channel(session, -1001, "News")
        contest = await make_contest(session, channels=[ch])
        contest_id = contest.id
    await bot_module.cmd_join(make_message(bot, f"/join {contest_id}"))
    assert any("подпишись" in a.lower() for a in bot.answers)


# --------------------------------------------------------------------------
# admin access control
# --------------------------------------------------------------------------
async def test_admin_panel_denied_for_regular_user():
    bot = HandlerBot()
    state = make_state(bot)
    await bot_module.handle_start(make_message(bot, "/start", user_id=1), state)
    await bot_module.cmd_admin(make_message(bot, "/admin", user_id=1))
    assert any("только администраторам" in a for a in bot.answers)


async def test_admin_panel_opens_for_owner():
    bot = HandlerBot()
    state = make_state(bot)
    await bot_module.handle_start(make_message(bot, "/start", user_id=1000), state)
    await bot_module.cmd_admin(make_message(bot, "/admin", user_id=1000))
    assert any("Административная панель" in a for a in bot.answers)


async def test_admin_stats_command():
    bot = HandlerBot()
    state = make_state(bot)
    await bot_module.handle_start(make_message(bot, "/start", user_id=1000), state)
    await bot_module.cmd_stats(make_message(bot, "/stats", user_id=1000))
    assert any("Статистика" in a for a in bot.answers)


async def test_addchannel_registers_channel():
    bot = HandlerBot()
    state = make_state(bot)
    await bot_module.handle_start(make_message(bot, "/start", user_id=1000), state)
    await bot_module.cmd_addchannel(make_message(bot, "/addchannel @fakechannel", user_id=1000))
    from database import session_scope

    async with session_scope() as session:
        channels = await contest_service.list_channels(session)
    assert len(channels) == 1
    assert channels[0].title == "Fake Channel"


async def test_addchannel_denied_for_user():
    bot = HandlerBot()
    state = make_state(bot)
    await bot_module.handle_start(make_message(bot, "/start", user_id=1), state)
    await bot_module.cmd_addchannel(make_message(bot, "/addchannel @x", user_id=1))
    assert any("только администраторам" in a for a in bot.answers)


# --------------------------------------------------------------------------
# FSM contest creation
# --------------------------------------------------------------------------
async def test_new_contest_flow_creates_contest():
    bot = HandlerBot()
    state = make_state(bot, user_id=1000)
    await bot_module.handle_start(make_message(bot, "/start", user_id=1000), state)
    await bot_module.cmd_newcontest(make_message(bot, "/newcontest", user_id=1000), state)
    await bot_module.nc_title(make_message(bot, "Super Prize", user_id=1000), state)
    await bot_module.nc_description(make_message(bot, "Описание", user_id=1000), state)
    await bot_module.nc_prize(make_message(bot, "Телефон", user_id=1000), state)
    await bot_module.nc_image(make_message(bot, "-", user_id=1000), state)
    await bot_module.nc_winners(make_message(bot, "2", user_id=1000), state)
    await bot_module.nc_start(make_message(bot, "-", user_id=1000), state)
    await bot_module.nc_end(make_message(bot, "-", user_id=1000), state)
    # no channels configured -> straight to preview
    assert any("Предпросмотр" in a for a in bot.answers)
    await bot_module.nc_create(make_callback(bot, "nc:create", user_id=1000), state)

    from database import session_scope

    async with session_scope() as session:
        contests = await contest_service.list_contests(session, include_archived=True)
    assert len(contests) == 1
    assert contests[0].title == "Super Prize"
    assert contests[0].winners_count == 2


async def test_publish_and_draw_via_commands():
    bot = HandlerBot()
    state = make_state(bot, user_id=1000)
    await bot_module.handle_start(make_message(bot, "/start", user_id=1000), state)
    from database import session_scope

    async with session_scope() as session:
        ch = await make_channel(session, -1001, "News")
        contest = await make_contest(session, winners=1, channels=[ch])
        contest_id = contest.id
        await register_user(session, telegram_id=1, first_name="U1")
        await register_user(session, telegram_id=2, first_name="U2")

    # seed entries directly (subscription not under test here)
    async with session_scope() as session:
        for tg in (1, 2):
            u = await get_user_by_telegram(session, tg)
            session.add(Entry(contest_id=contest_id, user_id=u.id, conditions_met=True))

    await bot_module.cmd_publish(make_message(bot, f"/publish {contest_id}", user_id=1000))
    assert any("Опубликовано" in a for a in bot.answers)

    await bot_module.cmd_finish(make_message(bot, f"/finish {contest_id}", user_id=1000))
    assert any("Розыгрыш проведён" in a for a in bot.answers)

    async with session_scope() as session:
        contest = await session.get(Contest, contest_id)
    assert contest.status == ContestStatus.FINISHED.value


async def test_settings_command_lists_values():
    bot = HandlerBot()
    state = make_state(bot, user_id=1000)
    await bot_module.handle_start(make_message(bot, "/start", user_id=1000), state)
    await bot_module.cmd_settings(make_message(bot, "/settings", user_id=1000))
    assert any("project_name" in a for a in bot.answers)


async def test_set_owner_only_denied_for_plain_admin():
    bot = HandlerBot()
    state = make_state(bot, user_id=2000)
    await bot_module.handle_start(make_message(bot, "/start", user_id=2000), state)
    from database import session_scope

    async with session_scope() as session:
        await admin.add_admin(session, telegram_id=2000, level=1, added_by=1000)
    await bot_module.cmd_set(make_message(bot, "/set referral_rate 0.5", user_id=2000))
    assert any("только владелец" in a for a in bot.answers)


async def test_admin_block_and_unblock_user():
    bot = HandlerBot()
    state = make_state(bot, user_id=1000)
    await bot_module.handle_start(make_message(bot, "/start", user_id=1000), state)
    await bot_module.handle_start(make_message(bot, "/start", user_id=55), state)

    await bot_module.cmd_finduser(make_message(bot, "/finduser 55", user_id=1000))
    assert any("ID: 55" in a for a in bot.answers)

    await bot_module.cmd_block(make_message(bot, "/block 55 спам", user_id=1000))
    from database import session_scope

    async with session_scope() as session:
        user = await admin.find_user(session, 55)
    assert user.is_blocked is True

    await bot_module.cmd_unblock(make_message(bot, "/unblock 55", user_id=1000))
    async with session_scope() as session:
        user = await admin.find_user(session, 55)
    assert user.is_blocked is False


async def test_notify_toggle_command():
    bot = HandlerBot()
    state = make_state(bot, user_id=1000)
    await bot_module.handle_start(make_message(bot, "/start", user_id=1000), state)
    await bot_module.cmd_notify(make_message(bot, "/notify contest_new 0", user_id=1000))
    assert any("выключены" in a for a in bot.answers)
    from database import session_scope

    async with session_scope() as session:
        value = await admin.get_setting(session, "notify_contest_new")
    assert value == "0"
