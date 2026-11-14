"""Background worker: time-based contest transitions and notifications.

Runs every ``TICK_SECONDS`` and:
* moves ``pending`` contests whose ``start_at`` has passed to ``active`` and
  sends the "contest started" notification (deduped);
* sends a one-time "contest is ending" reminder shortly before ``end_at``;
* optionally auto-finishes contests whose ``end_at`` has passed when the
  ``contest_auto_finish`` setting is enabled.

Every notification goes through :mod:`notifications`, so a user never receives
the same event twice and a blocked bot never crashes the worker.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import admin
import contests as contest_service
import notifications as notify
import services
import settings
from database import ContestStatus, session_scope

TICK_SECONDS = 60
ENDING_REMINDER_HOURS = 6


async def run_once(bot) -> None:
    now = settings.now_local()
    async with session_scope() as session:
        contests = await contest_service.list_contests(
            session, statuses=[ContestStatus.PENDING.value, ContestStatus.ACTIVE.value]
        )
        for contest in contests:
            start = contest.start_at
            end = contest.end_at

            if (
                contest.status == ContestStatus.PENDING.value
                and start is not None
                and _naive(start) <= now
            ):
                contest.status = ContestStatus.ACTIVE.value
                await session.flush()
                await notify.broadcast_contest(
                    session,
                    bot,
                    contest=contest,
                    notification_type="contest_start",
                    text=f"🚀 Конкурс «{contest.title}» начался! Успей участвовать.",
                    dedup_suffix="start",
                )

            if end is None:
                continue
            end_naive = _naive(end)

            if (
                contest.status == ContestStatus.ACTIVE.value
                and end_naive - timedelta(hours=ENDING_REMINDER_HOURS) <= now < end_naive
            ):
                await notify.broadcast_contest(
                    session,
                    bot,
                    contest=contest,
                    notification_type="contest_ending",
                    text=f"⏳ Конкурс «{contest.title}» скоро закончится! Успей участвовать.",
                    dedup_suffix="ending",
                )

            if now >= end_naive:
                auto = await admin.get_setting(session, "contest_auto_finish")
                if auto in ("1", "true", "True"):
                    await services.finish_and_draw(
                        session, bot, contest=contest, run_by=None, reason="auto_finish"
                    )


def _naive(value):
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(tz=None).replace(tzinfo=None)
    return value


async def worker(bot, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await run_once(bot)
        except Exception as exc:  # noqa: BLE001 - worker must never die
            print(f"[background] tick failed: {exc}")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=TICK_SECONDS)
        except asyncio.TimeoutError:
            continue
