"""Admin services: permissions, settings, statistics, user management, audit.

Access control (TZ §11, §15)
----------------------------
* ``OWNER_IDS`` from the environment are always owners.
* Other administrators live in the ``admins`` table with ``level`` 1 (admin)
  or 2 (owner). Only owners may touch critical settings and the admin list.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import settings
from database import (
    Admin,
    AuditLog,
    Contest,
    ContestStatus,
    DrawLog,
    Entry,
    Referral,
    Setting,
    User,
    Winner,
)

# Settings that only the project owner may change.
OWNER_ONLY_SETTINGS = {
    "admin_ids",
    "referral_enabled",
    "referral_rate",
    "max_referral_bonus",
    "max_winners",
    "contest_auto_finish",
}

DEFAULT_SETTINGS: dict[str, str] = {
    "project_name": "Fable Randomizer",
    "support_link": "https://t.me/",
    "rules_link": "",
    "admin_ids": "",
    "default_winners_count": "1",
    "max_winners": "50",
    "contest_auto_finish": "0",
    "referral_enabled": "1",
    "referral_rate": str(settings.REFERRAL_RATE),
    "max_referral_bonus": str(settings.MAX_REFERRAL_BONUS),
    "max_active_contests": "0",
    "min_contest_duration_hours": "0",
    # Notification toggles (see notifications.NOTIFICATION_TYPES)
    "notify_contest_new": "1",
    "notify_contest_start": "1",
    "notify_contest_ending": "1",
    "notify_contest_finished": "1",
    "notify_contest_winners": "1",
    "notify_contest_changed": "1",
    "notify_contest_cancelled": "1",
    "notify_join_success": "1",
    "notify_subscription_ok": "1",
}


# --------------------------------------------------------------------------
# Audit log
# --------------------------------------------------------------------------
async def audit(
    session: AsyncSession,
    *,
    actor_id: Optional[int],
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[Any] = None,
    payload: Optional[dict] = None,
) -> None:
    session.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            payload=json.dumps(payload, ensure_ascii=False) if payload else None,
        )
    )
    await session.flush()


async def recent_audit(session: AsyncSession, limit: int = 50) -> list[AuditLog]:
    return list(
        (
            await session.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))
        ).scalars()
    )


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------
async def is_admin(session: AsyncSession, telegram_id: int) -> bool:
    if telegram_id in settings.OWNER_IDS:
        return True
    row = (
        await session.execute(
            select(Admin).where(Admin.telegram_id == telegram_id, Admin.is_active.is_(True))
        )
    ).scalar_one_or_none()
    return row is not None


async def is_owner(session: AsyncSession, telegram_id: int) -> bool:
    if telegram_id in settings.OWNER_IDS:
        return True
    row = (
        await session.execute(
            select(Admin).where(
                Admin.telegram_id == telegram_id,
                Admin.is_active.is_(True),
                Admin.level >= 2,
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def admin_level(session: AsyncSession, telegram_id: int) -> int:
    if telegram_id in settings.OWNER_IDS:
        return 2
    row = (
        await session.execute(
            select(Admin).where(Admin.telegram_id == telegram_id, Admin.is_active.is_(True))
        )
    ).scalar_one_or_none()
    return row.level if row else 0


async def add_admin(
    session: AsyncSession, *, telegram_id: int, level: int = 1, added_by: Optional[int] = None
) -> Admin:
    row = (
        await session.execute(select(Admin).where(Admin.telegram_id == telegram_id))
    ).scalar_one_or_none()
    if row is None:
        row = Admin(telegram_id=telegram_id, level=level, added_by=added_by)
        session.add(row)
    row.level = level
    row.is_active = True
    await session.flush()
    return row


async def remove_admin(session: AsyncSession, telegram_id: int) -> bool:
    if telegram_id in settings.OWNER_IDS:
        return False  # owner ids cannot be removed via the panel
    row = (
        await session.execute(select(Admin).where(Admin.telegram_id == telegram_id))
    ).scalar_one_or_none()
    if row is None:
        return False
    row.is_active = False
    await session.flush()
    return True


async def list_admins(session: AsyncSession) -> list[Admin]:
    return list(
        (await session.execute(select(Admin).where(Admin.is_active.is_(True)))).scalars()
    )


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
async def get_setting(session: AsyncSession, key: str, default: Optional[str] = None) -> Optional[str]:
    row = await session.get(Setting, key)
    if row is not None:
        return row.value
    if key in DEFAULT_SETTINGS:
        return DEFAULT_SETTINGS[key]
    return default


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=str(value)))
    else:
        row.value = str(value)
    await session.flush()


async def all_settings(session: AsyncSession) -> dict[str, str]:
    rows = (await session.execute(select(Setting))).scalars().all()
    merged = dict(DEFAULT_SETTINGS)
    merged.update({row.key: row.value for row in rows})
    return merged


async def get_int_setting(session: AsyncSession, key: str, default: int) -> int:
    raw = await get_setting(session, key)
    try:
        return int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------
@dataclass
class Stats:
    users_total: int
    users_new_7d: int
    contests_active: int
    contests_finished: int
    contests_total: int
    entries_total: int
    winners_total: int
    referrals_total: int


async def _count(session: AsyncSession, model, *conditions) -> int:
    stmt = select(func.count()).select_from(model)
    for condition in conditions:
        stmt = stmt.where(condition)
    return int((await session.execute(stmt)).scalar_one())


async def get_stats(session: AsyncSession) -> Stats:
    week_ago = settings.now_local().timestamp() - 7 * 86400
    week_dt = datetime.fromtimestamp(week_ago)
    return Stats(
        users_total=await _count(session, User),
        users_new_7d=await _count(session, User, User.registered_at >= week_dt),
        contests_active=await _count(
            session, Contest, Contest.status == ContestStatus.ACTIVE.value, Contest.archived.is_(False)
        ),
        contests_finished=await _count(
            session, Contest, Contest.status == ContestStatus.FINISHED.value
        ),
        contests_total=await _count(session, Contest),
        entries_total=await _count(session, Entry),
        winners_total=await _count(session, Winner),
        referrals_total=await _count(session, Referral),
    )


# --------------------------------------------------------------------------
# User management
# --------------------------------------------------------------------------
async def find_user(session: AsyncSession, telegram_id: int) -> Optional[User]:
    return (
        await session.execute(select(User).where(User.telegram_id == telegram_id))
    ).scalar_one_or_none()


async def set_blocked(
    session: AsyncSession, user: User, blocked: bool, reason: Optional[str] = None
) -> None:
    """Project-level block: forbids new contest entries, keeps all history."""
    user.is_blocked = blocked
    user.blocked_reason = reason if blocked else None
    await session.flush()


async def user_history(session: AsyncSession, user: User) -> list[tuple[Entry, Contest]]:
    stmt = (
        select(Entry, Contest)
        .join(Contest, Contest.id == Entry.contest_id)
        .where(Entry.user_id == user.id)
        .order_by(Entry.id.desc())
    )
    return list((await session.execute(stmt)).all())


async def user_wins(session: AsyncSession, user: User) -> list[tuple[Winner, Contest]]:
    stmt = (
        select(Winner, Contest)
        .join(Contest, Contest.id == Winner.contest_id)
        .where(Winner.user_id == user.id)
        .order_by(Winner.id.desc())
    )
    return list((await session.execute(stmt)).all())


async def recent_users(session: AsyncSession, limit: int = 20) -> list[User]:
    return list(
        (await session.execute(select(User).order_by(User.id.desc()).limit(limit))).scalars()
    )


# --------------------------------------------------------------------------
# Draw / report helpers
# --------------------------------------------------------------------------
async def list_draws(session: AsyncSession, limit: int = 50) -> list[DrawLog]:
    return list(
        (await session.execute(select(DrawLog).order_by(DrawLog.id.desc()).limit(limit))).scalars()
    )


async def contest_report(session: AsyncSession, contest_id: int) -> dict:
    """Serialisable report of a contest and all of its draw attempts."""
    contest = await session.get(Contest, contest_id)
    if contest is None:
        return {}
    draws = list(
        (
            await session.execute(
                select(DrawLog).where(DrawLog.contest_id == contest_id).order_by(DrawLog.id)
            )
        ).scalars()
    )
    participants = list(
        (
            await session.execute(
                select(User)
                .join(Entry, Entry.user_id == User.id)
                .where(Entry.contest_id == contest_id)
            )
        ).scalars()
    )
    return {
        "contest": {
            "id": contest.id,
            "title": contest.title,
            "status": contest.status,
            "winners_count": contest.winners_count,
            "archived": contest.archived,
        },
        "participants": [
            {"telegram_id": u.telegram_id, "name": u.first_name, "username": u.username}
            for u in participants
        ],
        "draws": [
            {
                "id": d.id,
                "status": d.status,
                "algorithm_version": d.algorithm_version,
                "commitment": d.commitment,
                "seed": d.seed,
                "integrity_hash": d.integrity_hash,
                "participants_count": d.participants_count,
                "winners_count": d.winners_count,
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "reason": d.reason,
            }
            for d in draws
        ],
    }
