"""Database durability: data survives an application restart (TZ §18 #22).

This test uses a real on-disk SQLite file and a separate engine to emulate a
process restart.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import admin
import contests as contest_service
import services
from database import Base, Contest
from referrals import get_user_by_telegram, register_user
from tests.helpers import FakeBot, make_channel, make_contest


async def test_data_survives_restart(tmp_path):
    db_file = tmp_path / "persist.db"
    url = f"sqlite+aiosqlite:///{db_file}"

    # --- first "process" ---
    engine1 = create_async_engine(url)
    async with engine1.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory1 = async_sessionmaker(engine1, expire_on_commit=False)
    async with factory1() as session:
        await register_user(session, telegram_id=1, first_name="Alice")
        await register_user(session, telegram_id=2, first_name="Bob", referrer_telegram_id=1)
        ch = await make_channel(session, -1001, "News")
        contest = await make_contest(session, winners=1, channels=[ch])
        contest_id = contest.id
        bot = FakeBot({1: "member"})
        await services.join_contest_by_id(session, bot, telegram_id=1, contest_id=contest_id)
        await session.commit()
    await engine1.dispose()

    # --- second "process" re-opens the same file ---
    engine2 = create_async_engine(url)
    factory2 = async_sessionmaker(engine2, expire_on_commit=False)
    async with factory2() as session:
        alice = await get_user_by_telegram(session, 1)
        assert alice is not None
        assert alice.referral_count == 1
        contest = await session.get(Contest, contest_id)
        assert contest is not None
        assert await contest_service.entry_count(session, contest_id) == 1
        required = await contest_service.contest_channels(session, contest_id)
        assert [c.title for c in required] == ["News"]
        stats = await admin.get_stats(session)
        assert stats.users_total == 2
        assert stats.entries_total == 1
    await engine2.dispose()
