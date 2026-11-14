"""Registration + referral rules (TZ §1, §5)."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from database import Referral, User
from referrals import (
    bonus_for_referrals,
    get_user_by_telegram,
    referral_link,
    register_user,
)


async def test_new_user_registers_once(session):
    first = await register_user(session, telegram_id=1, username="a", first_name="Alice")
    assert first.created is True
    assert first.user.referral_count == 0

    again = await register_user(session, telegram_id=1, username="a2", first_name="Alice2")
    assert again.created is False  # repeated /start never re-registers
    total = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    assert total == 1
    # profile data is refreshed, referral state untouched
    assert again.user.first_name == "Alice2"


async def test_referral_attribution(session):
    await register_user(session, telegram_id=1, first_name="Alice")
    result = await register_user(session, telegram_id=2, first_name="Bob", referrer_telegram_id=1)
    assert result.bonus_granted is True
    alice = await get_user_by_telegram(session, 1)
    assert alice.referral_count == 1
    assert alice.bonus == pytest.approx(0.0001)
    refs = (await session.execute(select(Referral))).scalars().all()
    assert len(refs) == 1
    assert refs[0].referrer_id == alice.id


async def test_self_referral_rejected(session):
    result = await register_user(session, telegram_id=5, first_name="Solo", referrer_telegram_id=5)
    assert result.created is True
    assert result.invalid_referral is True
    assert result.referrer_id is None
    user = await get_user_by_telegram(session, 5)
    assert user.referral_count == 0
    assert user.referred_by is None


async def test_unknown_referrer_rejected(session):
    result = await register_user(session, telegram_id=6, referrer_telegram_id=99999)
    assert result.invalid_referral is True
    assert result.referrer_id is None


async def test_no_double_bonus_for_same_referred(session):
    await register_user(session, telegram_id=1, first_name="Alice")
    await register_user(session, telegram_id=2, first_name="Bob", referrer_telegram_id=1)
    # a second start for the same user (already registered) must not add a bonus
    await register_user(session, telegram_id=2, first_name="Bob", referrer_telegram_id=1)
    alice = await get_user_by_telegram(session, 1)
    assert alice.referral_count == 1
    refs = (await session.execute(select(Referral))).scalars().all()
    assert len(refs) == 1


async def test_referrer_cannot_be_changed(session):
    await register_user(session, telegram_id=1, first_name="Alice")
    await register_user(session, telegram_id=2, first_name="Bob")
    await register_user(session, telegram_id=3, first_name="Carl", referrer_telegram_id=1)
    # Carl starting again with a different referrer must not re-attribute
    await register_user(session, telegram_id=3, first_name="Carl", referrer_telegram_id=2)
    carl = await get_user_by_telegram(session, 3)
    alice = await get_user_by_telegram(session, 1)
    bob = await get_user_by_telegram(session, 2)
    assert carl.referred_by == alice.id
    assert alice.referral_count == 1
    assert bob.referral_count == 0


def test_bonus_is_capped():
    assert bonus_for_referrals(0) == 0
    assert bonus_for_referrals(1) == pytest.approx(0.0001)
    # 0.01% * 1000 invites would be 10%, cap is 10%
    assert bonus_for_referrals(1000) == pytest.approx(0.10)
    assert bonus_for_referrals(99999) == pytest.approx(0.10)


def test_referral_link_format():
    class U:
        telegram_id = 4242

    assert referral_link(U()) == "https://t.me/fable_test_bot?start=ref_4242"
