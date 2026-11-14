"""Test helpers: a fake Telegram bot and DB seed utilities."""
from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

import contests as contest_service
from database import Channel, Contest, ContestStatus
from referrals import register_user


class FakeMember:
    def __init__(self, status: str):
        self.status = status


class FakeBot:
    """Minimal Telegram client used by services and the API in tests."""

    def __init__(self, membership: Optional[dict[int, str]] = None, fail: bool = False):
        # membership maps user_id -> status ("member", "left", "kicked")
        self.membership = membership or {}
        self.fail = fail
        self.sent: list[tuple[int, str]] = []
        self._id = 999

    async def get_chat_member(self, chat_id: int, user_id: int):
        if self.fail:
            raise RuntimeError("telegram unavailable")
        return FakeMember(self.membership.get(user_id, "left"))

    async def send_message(self, chat_id: int, text: str, **kwargs):
        if self.fail:
            raise RuntimeError("bot blocked")
        self.sent.append((chat_id, text))

        class _Msg:
            message_id = 1

        return _Msg()

    async def get_me(self):
        class _Me:
            id = self._id

        return _Me()


async def make_user(session: AsyncSession, telegram_id: int, name: str = "User", referrer=None):
    result = await register_user(
        session,
        telegram_id=telegram_id,
        username=f"u{telegram_id}",
        first_name=name,
        referrer_telegram_id=referrer,
    )
    return result


async def make_channel(session: AsyncSession, chat_id: int = -100500, title: str = "Channel") -> Channel:
    return await contest_service.upsert_channel(
        session, chat_id=chat_id, title=title, username=f"ch{abs(chat_id)}", bot_is_admin=True
    )


async def make_contest(
    session: AsyncSession,
    *,
    title: str = "Prize",
    winners: int = 1,
    status: str = ContestStatus.ACTIVE.value,
    channels=(),
    prize: str = "Приз",
) -> Contest:
    contest = await contest_service.create_contest(
        session,
        title=title,
        description="desc",
        prize=prize,
        winners_count=winners,
        channel_ids=[c.id for c in channels],
    )
    contest.status = status
    await session.flush()
    return contest
