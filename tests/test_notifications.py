"""Notifications: typed toggles, de-duplication, failure tolerance (TZ §7)."""
from __future__ import annotations

from sqlalchemy import func, select

import admin
import notifications as notify
from database import Notification
from referrals import get_user_by_telegram, register_user
from tests.helpers import FakeBot


async def _user(session, tg=1):
    await register_user(session, telegram_id=tg, first_name="U")
    return await get_user_by_telegram(session, tg)


async def test_notification_sent_and_deduped(session):
    user = await _user(session)
    bot = FakeBot()
    first = await notify.notify_user(
        session, bot, user=user, notification_type="join_success", dedup_key="k1", text="hi"
    )
    second = await notify.notify_user(
        session, bot, user=user, notification_type="join_success", dedup_key="k1", text="hi"
    )
    assert first is True
    assert second is True  # already delivered, not resent
    assert len(bot.sent) == 1  # only one actual message
    count = (await session.execute(select(func.count()).select_from(Notification))).scalar_one()
    assert count == 1


async def test_notification_toggle_disables_type(session):
    user = await _user(session)
    await admin.set_setting(session, notify.setting_key("join_success"), "0")
    bot = FakeBot()
    result = await notify.notify_user(
        session, bot, user=user, notification_type="join_success", dedup_key="k2", text="hi"
    )
    assert result is False
    assert bot.sent == []


async def test_notification_failure_does_not_raise(session):
    user = await _user(session)
    bot = FakeBot(fail=True)
    result = await notify.notify_user(
        session, bot, user=user, notification_type="join_success", dedup_key="k3", text="hi"
    )
    assert result is False
    row = (await session.execute(select(Notification))).scalars().one()
    assert row.status == "failed"
    assert row.error


async def test_broadcast_dedups_per_user(session):
    user1 = await _user(session, 1)
    user2 = await _user(session, 2)
    bot = FakeBot()

    class C:
        id = 7
        title = "Contest"

    result = await notify.broadcast_contest(
        session,
        bot,
        contest=C(),
        notification_type="contest_winners",
        text="winners!",
        dedup_suffix="winners:1",
        recipients=[user1, user2],
    )
    assert result.sent == 2
    # running the same broadcast again must not resend
    again = await notify.broadcast_contest(
        session,
        bot,
        contest=C(),
        notification_type="contest_winners",
        text="winners!",
        dedup_suffix="winners:1",
        recipients=[user1, user2],
    )
    assert again.sent == 0
    assert len(bot.sent) == 2
