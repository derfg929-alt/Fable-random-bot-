"""Notification service (TZ §7).

* Typed notifications with per-type on/off toggles stored in ``settings``.
* De-duplication: a UNIQUE ``(user_id, dedup_key)`` row guarantees a user never
  receives the same event twice, even across restarts.
* Delivery is best-effort: a blocked bot / failed send is recorded and never
  crashes the caller.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import settings
from database import Contest, Entry, Notification, User
from referrals import get_user_by_telegram


class TelegramSender(Protocol):
    async def send_message(self, chat_id: int, text: str, **kwargs): ...  # pragma: no cover


# Notification type -> default enabled flag. Keys are also stored in settings.
NOTIFICATION_TYPES: dict[str, str] = {
    "contest_new": "Новый конкурс",
    "contest_start": "Начало конкурса",
    "contest_ending": "Напоминание об окончании",
    "contest_finished": "Завершение конкурса",
    "contest_winners": "Объявление победителей",
    "contest_changed": "Изменение условий конкурса",
    "contest_cancelled": "Отмена конкурса",
    "join_success": "Успешное участие",
    "subscription_ok": "Успешная проверка подписки",
}

SETTING_PREFIX = "notify_"


def setting_key(notification_type: str) -> str:
    return f"{SETTING_PREFIX}{notification_type}"


@dataclass
class DispatchResult:
    queued: int = 0
    sent: int = 0
    failed: int = 0
    skipped: int = 0


async def notification_enabled(session: AsyncSession, notification_type: str) -> bool:
    from admin import get_setting  # local import avoids a cycle at module load

    value = await get_setting(session, setting_key(notification_type))
    if value is None:
        return True
    return value not in ("0", "false", "False", "")


async def enqueue(
    session: AsyncSession,
    *,
    user: User,
    notification_type: str,
    dedup_key: str,
    text: str,
    contest_id: Optional[int] = None,
) -> Optional[Notification]:
    """Create a pending notification unless it (or an identical one) exists."""
    if not await notification_enabled(session, notification_type):
        return None
    existing = (
        await session.execute(
            select(Notification).where(
                Notification.user_id == user.id, Notification.dedup_key == dedup_key
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    notification = Notification(
        user_id=user.id,
        contest_id=contest_id,
        type=notification_type,
        dedup_key=dedup_key,
        text=text,
        status="pending",
    )
    session.add(notification)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return None
    return notification


async def _send_one(sender: TelegramSender, notification: Notification, user: User) -> bool:
    try:
        await sender.send_message(user.telegram_id, notification.text)
    except Exception as exc:  # blocked bot, chat not found, network, ...
        notification.status = "failed"
        notification.error = str(exc)[:255]
        return False
    notification.status = "sent"
    notification.sent_at = settings.now_local()
    notification.error = None
    return True


async def notify_user(
    session: AsyncSession,
    sender: TelegramSender,
    *,
    user: User,
    notification_type: str,
    dedup_key: str,
    text: str,
    contest_id: Optional[int] = None,
) -> bool:
    """Enqueue (if needed) and immediately attempt delivery of one notification."""
    notification = await enqueue(
        session,
        user=user,
        notification_type=notification_type,
        dedup_key=dedup_key,
        text=text,
        contest_id=contest_id,
    )
    if notification is None:
        return False
    if notification.status == "sent":
        return True  # already delivered earlier — do not resend
    return await _send_one(sender, notification, user)


async def broadcast_contest(
    session: AsyncSession,
    sender: TelegramSender,
    *,
    contest: Contest,
    notification_type: str,
    text: str,
    dedup_suffix: str,
    recipients: Optional[Iterable[User]] = None,
) -> DispatchResult:
    """Send a contest notification to participants (or a given recipient list)."""
    result = DispatchResult()
    if recipients is None:
        rows = (
            await session.execute(
                select(User)
                .join(Entry, Entry.user_id == User.id)
                .where(Entry.contest_id == contest.id)
            )
        ).scalars().all()
        recipients = list(rows)

    for user in recipients:
        dedup_key = f"{notification_type}:{contest.id}:{dedup_suffix}"
        notification = await enqueue(
            session,
            user=user,
            notification_type=notification_type,
            dedup_key=dedup_key,
            text=text,
            contest_id=contest.id,
        )
        if notification is None:
            result.skipped += 1
            continue
        if notification.status == "sent":
            result.skipped += 1
            continue
        result.queued += 1
        if await _send_one(sender, notification, user):
            result.sent += 1
        else:
            result.failed += 1
    return result


async def notify_telegram_id(
    session: AsyncSession,
    sender: TelegramSender,
    *,
    telegram_id: int,
    notification_type: str,
    dedup_key: str,
    text: str,
    contest_id: Optional[int] = None,
) -> bool:
    user = await get_user_by_telegram(session, telegram_id)
    if user is None:
        return False
    return await notify_user(
        session,
        sender,
        user=user,
        notification_type=notification_type,
        dedup_key=dedup_key,
        text=text,
        contest_id=contest_id,
    )
