"""Contest lifecycle, subscriptions and duplicate-entry protection (TZ §3, §4, §14)."""
from __future__ import annotations

from sqlalchemy import func, select

import contests as contest_service
import services
from database import ContestStatus, Entry
from tests.helpers import FakeBot, make_channel, make_contest, make_user


async def test_create_contest_with_channels(session):
    ch = await make_channel(session, -1001, "News")
    contest = await make_contest(session, title="iPhone", winners=2, channels=[ch])
    assert contest.id is not None
    assert contest.status == ContestStatus.ACTIVE.value
    required = await contest_service.contest_channels(session, contest.id)
    assert [c.title for c in required] == ["News"]


async def test_set_contest_channels_replaces(session):
    ch1 = await make_channel(session, -1001, "A")
    ch2 = await make_channel(session, -1002, "B")
    contest = await make_contest(session, channels=[ch1])
    await contest_service.set_contest_channels(session, contest.id, [ch2.id])
    required = await contest_service.contest_channels(session, contest.id)
    assert [c.title for c in required] == ["B"]


async def test_join_requires_subscription(session):
    ch = await make_channel(session, -1001, "News")
    await make_user(session, 1, "Alice")
    contest = await make_contest(session, channels=[ch])
    bot = FakeBot({})  # nobody is a member
    outcome = await services.join_contest_by_id(session, bot, telegram_id=1, contest_id=contest.id)
    assert outcome.status == "subscription"
    assert [c.title for c in outcome.missing] == ["News"]
    assert await contest_service.entry_count(session, contest.id) == 0


async def test_subscription_check_fails_closed_on_telegram_error(session):
    ch = await make_channel(session, -1001, "News")
    await make_user(session, 1, "Alice")
    contest = await make_contest(session, channels=[ch])
    bot = FakeBot(fail=True)
    outcome = await services.join_contest_by_id(session, bot, telegram_id=1, contest_id=contest.id)
    assert outcome.status == "subscription"
    assert outcome.subscription_error is True
    assert await contest_service.entry_count(session, contest.id) == 0


async def test_join_success_and_duplicate_blocked(session):
    ch = await make_channel(session, -1001, "News")
    await make_user(session, 1, "Alice")
    contest = await make_contest(session, channels=[ch])
    bot = FakeBot({1: "member"})
    first = await services.join_contest_by_id(session, bot, telegram_id=1, contest_id=contest.id)
    assert first.status == "joined"
    second = await services.join_contest_by_id(session, bot, telegram_id=1, contest_id=contest.id)
    assert second.status == "already"
    count = (await session.execute(select(func.count()).select_from(Entry))).scalar_one()
    assert count == 1


async def test_join_without_channels(session):
    await make_user(session, 1, "Alice")
    contest = await make_contest(session, channels=[])
    bot = FakeBot({})
    outcome = await services.join_contest_by_id(session, bot, telegram_id=1, contest_id=contest.id)
    assert outcome.status == "joined"


async def test_blocked_user_cannot_join(session):
    await make_user(session, 1, "Alice")
    contest = await make_contest(session, channels=[])
    from referrals import get_user_by_telegram
    user = await get_user_by_telegram(session, 1)
    user.is_blocked = True
    await session.flush()
    bot = FakeBot({})
    outcome = await services.join_contest_by_id(session, bot, telegram_id=1, contest_id=contest.id)
    assert outcome.status == "blocked"
    assert await contest_service.entry_count(session, contest.id) == 0


async def test_cannot_join_finished_contest(session):
    await make_user(session, 1, "Alice")
    contest = await make_contest(session, status=ContestStatus.FINISHED.value, channels=[])
    bot = FakeBot({})
    outcome = await services.join_contest_by_id(session, bot, telegram_id=1, contest_id=contest.id)
    assert outcome.status == "not_open"


async def test_archive_keeps_history(session):
    await make_user(session, 1, "Alice")
    contest = await make_contest(session, channels=[])
    bot = FakeBot({})
    await services.join_contest_by_id(session, bot, telegram_id=1, contest_id=contest.id)
    await contest_service.archive_contest(session, contest)
    # hidden from public listing but entries still present
    visible = await contest_service.list_contests(session)
    assert contest.id not in [c.id for c in visible]
    assert await contest_service.entry_count(session, contest.id) == 1
    all_contests = await contest_service.list_contests(session, include_archived=True)
    assert contest.id in [c.id for c in all_contests]


async def test_delete_channel_keeps_contest(session):
    ch = await make_channel(session, -1001, "News")
    contest = await make_contest(session, channels=[ch])
    await contest_service.delete_channel(session, ch.id)
    # contest survives, but the channel is no longer a required one
    required = await contest_service.contest_channels(session, contest.id)
    assert required == []
