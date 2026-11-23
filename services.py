"""High-level orchestration shared by the bot and the Mini App API.

Keeping the flows here means the Telegram bot and the HTTP API perform the
*exact same* logic (subscription checks, joining, publishing, finishing,
notifications) instead of duplicating it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import admin
import contests as contest_service
import draw as draw_service
import notifications as notify
import settings
from database import Channel, Contest, ContestStatus, Entry, User, Winner
from referrals import bonus_for_referrals, get_user_by_telegram


class BotClient(Protocol):
    async def get_chat_member(self, chat_id: int, user_id: int): ...  # pragma: no cover
    async def send_message(self, chat_id: int, text: str, **kwargs): ...  # pragma: no cover


# --------------------------------------------------------------------------
# Joining
# --------------------------------------------------------------------------
@dataclass
class JoinOutcome:
    status: str  # joined | already | blocked | not_found | not_open | subscription
    contest: Optional[Contest] = None
    missing: Optional[list[Channel]] = None
    subscription_error: bool = False


async def join_contest_by_id(
    session: AsyncSession,
    bot: BotClient,
    *,
    telegram_id: int,
    contest_id: int,
) -> JoinOutcome:
    """Full join flow: validate contest, check subscriptions, register once."""
    user = await get_user_by_telegram(session, telegram_id)
    if user is None:
        return JoinOutcome(status="not_found")
    if user.is_blocked:
        return JoinOutcome(status="blocked")

    contest = await contest_service.get_contest(session, contest_id)
    if contest is None:
        return JoinOutcome(status="not_found")

    status = contest_service.effective_status(contest)
    if status not in (ContestStatus.PENDING.value, ContestStatus.ACTIVE.value):
        return JoinOutcome(status="not_open", contest=contest)

    subscription = await contest_service.check_subscriptions(
        bot, session, user_telegram_id=telegram_id, contest_id=contest_id
    )
    if not subscription.ok:
        return JoinOutcome(
            status="subscription",
            contest=contest,
            missing=subscription.missing,
            subscription_error=subscription.error,
        )

    result = await contest_service.join_contest(
        session, user=user, contest=contest, subscription=subscription
    )
    if result.already:
        return JoinOutcome(status="already", contest=contest)
    if result.blocked:
        return JoinOutcome(status="blocked", contest=contest)

    await notify.notify_user(
        session,
        bot,
        user=user,
        notification_type="join_success",
        dedup_key=f"join_success:{contest.id}",
        text=(
            "Ты успешно зарегистрировался в конкурсе! 🎉\n\n"
            "Удачи! Следи за результатом в Fable Randomizer."
        ),
        contest_id=contest.id,
    )
    await notify.notify_user(
        session,
        bot,
        user=user,
        notification_type="subscription_ok",
        dedup_key=f"subscription_ok:{contest.id}",
        text=f"✅ Подписка проверена. Ты в конкурсе «{contest.title}».",
        contest_id=contest.id,
    )
    return JoinOutcome(status="joined", contest=contest)


# --------------------------------------------------------------------------
# Publishing
# --------------------------------------------------------------------------
@dataclass
class PublishOutcome:
    sent: int = 0
    failed: list[str] = None  # type: ignore[assignment]
    channels: int = 0

    def __post_init__(self):
        if self.failed is None:
            self.failed = []


def contest_announcement(contest: Contest) -> str:
    lines = [f"🎁 <b>{contest.title}</b>", ""]
    if contest.description:
        lines.append(contest.description)
        lines.append("")
    if contest.prize:
        lines.append(f"🏆 Приз: {contest.prize}")
    if contest.end_at:
        lines.append(f"⏳ До: {contest.end_at.strftime('%d.%m.%Y %H:%M')}")
    lines.append("")
    lines.append("Нажми «Участвовать», чтобы принять участие!")
    return "\n".join(lines)


async def publish_contest(
    session: AsyncSession, bot: BotClient, *, contest: Contest
) -> PublishOutcome:
    outcome = PublishOutcome()
    channels = await contest_service.contest_channels(session, contest.id)
    outcome.channels = len(channels)
    text = contest_announcement(contest)
    link = _contest_link(contest.id)
    markup = None
    if link:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🎉 Участвовать", url=link)]]
        )
    for channel in channels:
        try:
            await bot.send_message(
                channel.chat_id,
                text,
                parse_mode="HTML",
                disable_web_page_preview=False,
                reply_markup=markup,
            )
            outcome.sent += 1
        except Exception as exc:  # noqa: BLE001 - report per channel, keep going
            outcome.failed.append(f"{channel.title}: {str(exc)[:120]}")
    contest.published_at = settings.now_local()
    if contest.status == ContestStatus.PENDING.value:
        contest.status = ContestStatus.ACTIVE.value
    await session.flush()

    # Notify all registered users about the new contest (deduped).
    users = list((await session.execute(select(User))).scalars())
    await notify.broadcast_contest(
        session,
        bot,
        contest=contest,
        notification_type="contest_new",
        text=f"🎉 Новый конкурс: «{contest.title}»!\n\nЗаходи и участвуй.",
        dedup_suffix="new",
        recipients=users,
    )
    await admin.audit(
        session,
        actor_id=contest.created_by,
        action="contest_publish",
        target_type="contest",
        target_id=contest.id,
        payload={"sent": outcome.sent, "channels": outcome.channels},
    )
    return outcome


def _contest_link(contest_id: int) -> str:
    from settings import BOT_USERNAME

    if not BOT_USERNAME:
        return ""
    return f"https://t.me/{BOT_USERNAME}?start=contest_{contest_id}"


# --------------------------------------------------------------------------
# Finishing + drawing
# --------------------------------------------------------------------------
@dataclass
class FinishOutcome:
    ok: bool
    reason: str = ""
    winners: Optional[list[tuple[Winner, User]]] = None


async def finish_and_draw(
    session: AsyncSession,
    bot: BotClient,
    *,
    contest: Contest,
    winners_count: Optional[int] = None,
    run_by: Optional[int] = None,
    redraw: bool = False,
    reason: Optional[str] = None,
) -> FinishOutcome:
    """Close a contest and run the weighted draw, then announce the winners."""
    if contest.archived:
        return FinishOutcome(ok=False, reason="archived")
    try:
        result = await draw_service.run_draw(
            session,
            contest=contest,
            winners_count=winners_count,
            run_by=run_by,
            reason=reason,
        )
    except ValueError:
        return FinishOutcome(ok=False, reason="no_participants")

    contest.status = ContestStatus.FINISHED.value
    await session.flush()

    winners: list[tuple[Winner, User]] = []
    for winner in result.winners:
        user = await session.get(User, winner.user_id)
        winners.append((winner, user))

    names = ", ".join((u.first_name or f"id{u.telegram_id}") for _, u in winners)
    text = (
        f"🏆 Конкурс «{contest.title}» завершён!\n\n"
        f"Победители ({len(winners)}): {names}\n\n"
        f"Проверить честность розыгрыша можно по коду: {result.log.commitment}"
    )
    await notify.broadcast_contest(
        session,
        bot,
        contest=contest,
        notification_type="contest_finished",
        text=f"🏁 Конкурс «{contest.title}» завершён. Итоги подведены.",
        dedup_suffix="finished",
    )
    await notify.broadcast_contest(
        session,
        bot,
        contest=contest,
        notification_type="contest_winners",
        text=text,
        dedup_suffix=f"winners:{result.log.id}",
    )
    await admin.audit(
        session,
        actor_id=run_by,
        action="contest_redraw" if redraw else "contest_finish",
        target_type="contest",
        target_id=contest.id,
        payload={
            "draw_id": result.log.id,
            "winners": [w.user_id for w in result.winners],
            "algorithm_version": result.log.algorithm_version,
        },
    )
    return FinishOutcome(ok=True, winners=winners)


async def cancel_contest(
    session: AsyncSession, bot: BotClient, *, contest: Contest, actor_id: Optional[int] = None
) -> None:
    contest.status = ContestStatus.CANCELLED.value
    await session.flush()
    await notify.broadcast_contest(
        session,
        bot,
        contest=contest,
        notification_type="contest_cancelled",
        text=f"⚠️ Конкурс «{contest.title}» отменён организатором.",
        dedup_suffix="cancelled",
    )
    await admin.audit(
        session,
        actor_id=actor_id,
        action="contest_cancel",
        target_type="contest",
        target_id=contest.id,
    )


# --------------------------------------------------------------------------
# Profile helpers
# --------------------------------------------------------------------------
@dataclass
class Profile:
    user: User
    active_entries: int
    finished_entries: int
    wins: int
    recent: list[tuple[Entry, Contest]]
    bonus: float


async def build_profile(session: AsyncSession, user: User) -> Profile:
    history = await contest_service.user_entries(session, user.id)
    active = sum(
        1 for _, c in history if c.status in (ContestStatus.PENDING.value, ContestStatus.ACTIVE.value)
    )
    finished = sum(1 for _, c in history if c.status == ContestStatus.FINISHED.value)
    wins = int(
        (
            await session.execute(
                select(Winner).where(Winner.user_id == user.id)
            )
        ).scalars().all().__len__()
    )
    return Profile(
        user=user,
        active_entries=active,
        finished_entries=finished,
        wins=wins,
        recent=history[:5],
        bonus=bonus_for_referrals(user.referral_count or 0),
    )
