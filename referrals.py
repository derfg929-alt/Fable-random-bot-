"""Referral system.

Rules enforced here (see TZ §5):
* a referral bonus is granted only for the *first* launch of a brand new user;
* repeated ``/start`` never re-registers and never re-grants a bonus;
* self-referral is rejected;
* the referrer can never be changed after registration;
* one Telegram id maps to exactly one registration.

The bonus is an internal draw-weight parameter (0.01% relative weight per
invited friend, capped at 10%). It is stored on the user row and must never be
shown to end users.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import settings
from database import Referral, User


@dataclass
class RegistrationResult:
    user: User
    created: bool
    bonus_granted: bool = False
    referrer_id: Optional[int] = None
    invalid_referral: bool = False


def bonus_for_referrals(referral_count: int) -> float:
    """Relative draw-weight bonus for a given invite count (internal only)."""
    return min(max(referral_count, 0) * settings.REFERRAL_RATE, settings.MAX_REFERRAL_BONUS)


def referral_link(user: User) -> str:
    """Personal referral link, ``https://t.me/<bot>?start=ref_<id>``."""
    username = settings.BOT_USERNAME
    if not username:
        return ""
    return f"https://t.me/{username}?start=ref_{user.telegram_id}"


async def get_user_by_telegram(session: AsyncSession, telegram_id: int) -> Optional[User]:
    return (
        await session.execute(select(User).where(User.telegram_id == telegram_id))
    ).scalar_one_or_none()


async def register_user(
    session: AsyncSession,
    *,
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    language_code: Optional[str] = None,
    referrer_telegram_id: Optional[int] = None,
) -> RegistrationResult:
    """Idempotently register a Telegram user and attribute a referral once.

    Safe to call on every ``/start``: an existing user is returned untouched and
    no bonus is granted a second time.
    """
    existing = await get_user_by_telegram(session, telegram_id)
    if existing is not None:
        _refresh_profile(existing, username, first_name, last_name, language_code)
        return RegistrationResult(user=existing, created=False)

    user = User(
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        language_code=language_code,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:
        # A concurrent request already created this user — treat as existing.
        await session.rollback()
        again = await get_user_by_telegram(session, telegram_id)
        if again is None:  # pragma: no cover - extremely unlikely
            raise
        return RegistrationResult(user=again, created=False)

    invalid_referral = False
    referrer: Optional[User] = None
    if referrer_telegram_id is not None:
        if referrer_telegram_id == telegram_id:
            invalid_referral = True  # self-referral
        else:
            referrer = await get_user_by_telegram(session, referrer_telegram_id)
            if referrer is None:
                invalid_referral = True

    if referrer is None:
        return RegistrationResult(user=user, created=True, invalid_referral=invalid_referral)

    user.referred_by = referrer.id
    bonus = settings.REFERRAL_RATE
    session.add(
        Referral(
            referrer_id=referrer.id,
            referred_id=user.id,
            bonus_granted=bonus,
            source="link",
        )
    )
    referrer.referral_count = (referrer.referral_count or 0) + 1
    referrer.bonus = bonus_for_referrals(referrer.referral_count)
    try:
        await session.flush()
    except IntegrityError:
        # referral already recorded for this user (unique on referred_id)
        await session.rollback()
        return RegistrationResult(user=user, created=True, invalid_referral=False)

    return RegistrationResult(
        user=user,
        created=True,
        bonus_granted=True,
        referrer_id=referrer.id,
    )


def _refresh_profile(user: User, username, first_name, last_name, language_code) -> None:
    """Keep the display data fresh without touching referral state."""
    if username is not None:
        user.username = username
    if first_name is not None:
        user.first_name = first_name
    if last_name is not None:
        user.last_name = last_name
    if language_code is not None:
        user.language_code = language_code
