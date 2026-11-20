import secrets
from datetime import datetime

from aiogram import Router
from aiogram import Bot

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database import load_data, get_contest, update_contest

router = Router()

scheduler = AsyncIOScheduler()


async def draw_winner(contest_id, bot: Bot):
    contest = await get_contest(contest_id)
    if not contest or contest.get("status") != "active":
        return
    participants = contest.get("participants", []) or []
    data = await load_data()
    pool = []
    for uid in participants:
        user = data["users"].get(str(uid), {}) or {}
        user_tickets = (user.get("tickets") or {}).get(str(contest_id), 1)
        if user_tickets < 1:
            user_tickets = 1
        pool.extend([int(uid)] * user_tickets)
    if not pool:
        await update_contest(contest_id, status="finished")
        return
    winner_id = secrets.choice(pool)
    await update_contest(contest_id, winner=winner_id, status="finished")
    channel = contest.get("channel")
    if channel:
        await bot.send_message(
            channel,
            f"🏆 Победитель розыгрыша «{contest.get('title')}»: {winner_id}",
        )


async def check_contests(bot: Bot):
    data = await load_data()
    now = datetime.now()
    for cid, contest in (data.get("contests") or {}).items():
        if contest.get("status") != "active":
            continue
        ends_at_str = contest.get("ends_at")
        if not ends_at_str:
            continue
        try:
            ends_at = datetime.strptime(ends_at_str, "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if now >= ends_at:
            await draw_winner(cid, bot)


def start_scheduler(bot: Bot):
    scheduler.add_job(check_contests, "interval", minutes=1, args=[bot])
    scheduler.start()
