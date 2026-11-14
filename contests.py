"""Contest and channel services.

Covers TZ §3 (contest system), §4 (channels) and §11.2/§11.3 (admin management).
All functions are async and take an ``AsyncSession`` so callers control the
transaction boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional, Protocol, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import settings
from database import (
    Channel,
    Contest,
    ContestChannel,
    ContestStatus,
    Entry,
    User,
)


class TelegramChannelClient(Protocol):
    """Minimal surface we need from the bot to verify subscriptions."""

    async def get_chat_member(self, chat_id: int, user_id: int): ...  # pragma: no cover


@dataclass
class SubscriptionResult:
    ok: bool
    missing: list[Channel]
    error: bool = False  # True when Telegram could not be queried (fail closed)


# --------------------------------------------------------------------------
# Channels
# --------------------------------------------------------------------------
async def upsert_channel(
    session: AsyncSession,
    *,
    chat_id: int,
    title: str,
    username: Optional[str] = None,
    invite_link: Optional[str] = None,
    added_by: Optional[int] = None,
    bot_is_admin: bool = False,
) -> Channel:
    channel = (
        await session.execute(select(Channel).where(Channel.chat_id == chat_id))
    ).scalar_one_or_none()
    if channel is None:
        channel = Channel(chat_id=chat_id, title=title, added_by=added_by)
        session.add(channel)
    channel.title = title
    if username is not None:
        channel.username = username
    if invite_link is not None:
        channel.invite_link = invite_link
    channel.bot_is_admin = bot_is_admin
    channel.is_active = True
    channel.last_checked_at = settings.now_local()
    await session.flush()
    return channel


async def list_channels(session: AsyncSession, include_inactive: bool = False) -> list[Channel]:
    stmt = select(Channel).order_by(Channel.id)
    if not include_inactive:
        stmt = stmt.where(Channel.is_active.is_(True))
    return list((await session.execute(stmt)).scalars().all())


async def delete_channel(session: AsyncSession, channel_id: int) -> bool:
    channel = await session.get(Channel, channel_id)
    if channel is None:
        return False
    # Soft delete: keeps contest history intact.
    channel.is_active = False
    await session.flush()
    return True


async def set_contest_channels(
    session: AsyncSession, contest_id: int, channel_ids: Sequence[int]
) -> None:
    """Replace the required-channel set of a contest (idempotent)."""
    existing = {
        cc.channel_id
        for cc in (
            await session.execute(
                select(ContestChannel).where(ContestChannel.contest_id == contest_id)
            )
        ).scalars()
    }
    wanted = set(channel_ids)
    for cc in list(
        (
            await session.execute(
                select(ContestChannel).where(ContestChannel.contest_id == contest_id)
            )
        ).scalars()
    ):
        if cc.channel_id not in wanted:
            await session.delete(cc)
    for channel_id in wanted - existing:
        session.add(
            ContestChannel(contest_id=contest_id, channel_id=channel_id, is_required=True)
        )
    await session.flush()


async def contest_channels(session: AsyncSession, contest_id: int) -> list[Channel]:
    stmt = (
        select(Channel)
        .join(ContestChannel, ContestChannel.channel_id == Channel.id)
        .where(ContestChannel.contest_id == contest_id, Channel.is_active.is_(True))
        .order_by(Channel.id)
    )
    return list((await session.execute(stmt)).scalars().all())


async def check_subscriptions(
    client: TelegramChannelClient,
    session: AsyncSession,
    *,
    user_telegram_id: int,
    contest_id: int,
) -> SubscriptionResult:
    """Verify the user is subscribed to every required channel.

    Fails closed: if Telegram cannot be queried the condition is treated as
    *not* met and ``error`` is set, so the user gets a clear message instead of
    silently being allowed in.
    """
    required = await contest_channels(session, contest_id)
    missing: list[Channel] = []
    errored = False
    for channel in required:
        try:
            member = await client.get_chat_member(channel.chat_id, user_telegram_id)
        except Exception:
            errored = True
            missing.append(channel)
            continue
        status = getattr(member, "status", None)
        if status in ("left", "kicked"):
            missing.append(channel)
    return SubscriptionResult(ok=not missing, missing=missing, error=errored)


# --------------------------------------------------------------------------
# Contests
# --------------------------------------------------------------------------
async def create_contest(
    session: AsyncSession,
    *,
    title: str,
    description: str = "",
    prize: str = "",
    image_url: Optional[str] = None,
    winners_count: int = 1,
    created_by: Optional[int] = None,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
    channel_ids: Iterable[int] = (),
) -> Contest:
    contest = Contest(
        title=title.strip(),
        description=description.strip(),
        prize=prize.strip(),
        image_url=image_url,
        winners_count=max(1, int(winners_count)),
        created_by=created_by,
        start_at=start_at,
        end_at=end_at,
        status=ContestStatus.PENDING.value,
    )
    session.add(contest)
    await session.flush()
    if channel_ids:
        await set_contest_channels(session, contest.id, list(channel_ids))
    return contest


async def get_contest(session: AsyncSession, contest_id: int, *, include_archived: bool = False) -> Optional[Contest]:
    contest = await session.get(Contest, contest_id)
    if contest is None:
        return None
    if contest.archived and not include_archived:
        return None
    return contest


async def list_contests(
    session: AsyncSession,
    *,
    statuses: Optional[Sequence[str]] = None,
    include_archived: bool = False,
    limit: Optional[int] = None,
) -> list[Contest]:
    stmt = select(Contest).order_by(Contest.id.desc())
    if not include_archived:
        stmt = stmt.where(Contest.archived.is_(False))
    if statuses:
        stmt = stmt.where(Contest.status.in_(list(statuses)))
    if limit:
        stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars().all())


def effective_status(contest: Contest, now: Optional[datetime] = None) -> str:
    """Status including time-based auto transitions (pending -> active)."""
    if contest.status in (ContestStatus.FINISHED.value, ContestStatus.CANCELLED.value):
        return contest.status
    now = now or settings.now_local()
    start = _naive(contest.start_at)
    end = _naive(contest.end_at)
    if end and now >= end:
        return ContestStatus.ACTIVE.value  # awaiting manual finish
    if start and now >= start:
        return ContestStatus.ACTIVE.value
    return contest.status


def _naive(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(tz=None).replace(tzinfo=None)
    return value


async def set_status(session: AsyncSession, contest: Contest, status: str) -> None:
    contest.status = status
    await session.flush()


async def update_contest(
    session: AsyncSession,
    contest: Contest,
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    prize: Optional[str] = None,
    image_url: Optional[str] = None,
    winners_count: Optional[int] = None,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
    channel_ids: Optional[Sequence[int]] = None,
) -> Contest:
    """Apply edits to an existing contest (TZ §11.2). Only given fields change."""
    if title is not None:
        contest.title = title.strip()
    if description is not None:
        contest.description = description.strip()
    if prize is not None:
        contest.prize = prize.strip()
    if image_url is not None:
        contest.image_url = image_url
    if winners_count is not None:
        contest.winners_count = max(1, int(winners_count))
    if start_at is not None:
        contest.start_at = start_at
    if end_at is not None:
        contest.end_at = end_at
    await session.flush()
    if channel_ids is not None:
        await set_contest_channels(session, contest.id, list(channel_ids))
    return contest


async def archive_contest(session: AsyncSession, contest: Contest) -> None:
    """Soft delete: hides the contest but keeps all history (TZ §14)."""
    contest.archived = True
    await session.flush()


async def entry_count(session: AsyncSession, contest_id: int) -> int:
    return int(
        (
            await session.execute(
                select(func.count()).select_from(Entry).where(Entry.contest_id == contest_id)
            )
        ).scalar_one()
    )


async def contest_entries(session: AsyncSession, contest_id: int) -> list[Entry]:
    return list(
        (
            await session.execute(
                select(Entry).where(Entry.contest_id == contest_id).order_by(Entry.id)
            )
        ).scalars()
    )


@dataclass
class JoinResult:
    joined: bool
    already: bool = False
    blocked: bool = False
    contest_missing: bool = False
    subscription: Optional[SubscriptionResult] = None


async def join_contest(
    session: AsyncSession,
    *,
    user: User,
    contest: Contest,
    subscription: SubscriptionResult,
) -> JoinResult:
    """Register a user for a contest exactly once (TZ §3, §14).

    The UNIQUE ``(contest_id, user_id)`` constraint is the source of truth; a
    duplicate press is reported as ``already`` and never creates a second row.
    """
    if user.is_blocked:
        return JoinResult(joined=False, blocked=True)
    if contest.status not in (ContestStatus.PENDING.value, ContestStatus.ACTIVE.value):
        return JoinResult(joined=False, contest_missing=True)
    if not subscription.ok:
        return JoinResult(joined=False, subscription=subscription)

    existing = (
        await session.execute(
            select(Entry).where(Entry.contest_id == contest.id, Entry.user_id == user.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return JoinResult(joined=False, already=True, subscription=subscription)

    session.add(Entry(contest_id=contest.id, user_id=user.id, conditions_met=True))
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return JoinResult(joined=False, already=True, subscription=subscription)
    return JoinResult(joined=True, subscription=subscription)


async def user_entries(session: AsyncSession, user_id: int) -> list[tuple[Entry, Contest]]:
    stmt = (
        select(Entry, Contest)
        .join(Contest, Contest.id == Entry.contest_id)
        .where(Entry.user_id == user_id)
        .order_by(Entry.id.desc())
    )
    return list((await session.execute(stmt)).all())
