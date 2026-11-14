"""Background worker: time-based status transitions and notifications (TZ §3, §7)."""
from __future__ import annotations

from datetime import timedelta

import admin
import settings
import tasks
from database import Contest, ContestStatus, Entry, session_scope
from referrals import get_user_by_telegram, register_user
from tests.helpers import FakeBot, make_contest


async def _seed_contest(*, start=None, end=None, status=ContestStatus.PENDING.value, join=True):
    async with session_scope() as session:
        await register_user(session, telegram_id=1, first_name="Alice")
        user = await get_user_by_telegram(session, 1)
        contest = await make_contest(session, channels=[], status=status)
        contest.start_at = start
        contest.end_at = end
        await session.flush()
        if join:
            session.add(Entry(contest_id=contest.id, user_id=user.id, conditions_met=True))
            await session.flush()
        return contest.id


async def _get(contest_id):
    async with session_scope() as session:
        return await session.get(Contest, contest_id)


async def test_pending_contest_becomes_active_and_notifies():
    past = settings.now_local() - timedelta(minutes=5)
    contest_id = await _seed_contest(start=past)
    bot = FakeBot({})
    await tasks.run_once(bot)
    contest = await _get(contest_id)
    assert contest.status == ContestStatus.ACTIVE.value
    assert any("начался" in text for _, text in bot.sent)


async def test_ending_reminder_sent_once():
    now = settings.now_local()
    await _seed_contest(
        start=now - timedelta(hours=1), end=now + timedelta(hours=1),
        status=ContestStatus.ACTIVE.value,
    )
    bot = FakeBot({})
    await tasks.run_once(bot)
    await tasks.run_once(bot)  # second tick must not resend
    reminders = [t for _, t in bot.sent if "скоро закончится" in t]
    assert len(reminders) == 1


async def test_auto_finish_when_enabled():
    now = settings.now_local()
    contest_id = await _seed_contest(
        start=now - timedelta(hours=2), end=now - timedelta(minutes=1),
        status=ContestStatus.ACTIVE.value,
    )
    async with session_scope() as session:
        await admin.set_setting(session, "contest_auto_finish", "1")
    bot = FakeBot({})
    await tasks.run_once(bot)
    contest = await _get(contest_id)
    assert contest.status == ContestStatus.FINISHED.value


async def test_no_auto_finish_by_default():
    now = settings.now_local()
    contest_id = await _seed_contest(
        start=now - timedelta(hours=2), end=now - timedelta(minutes=1),
        status=ContestStatus.ACTIVE.value,
    )
    bot = FakeBot({})
    await tasks.run_once(bot)
    contest = await _get(contest_id)
    assert contest.status == ContestStatus.ACTIVE.value
