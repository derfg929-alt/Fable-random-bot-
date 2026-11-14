"""Draw algorithm: weights, no duplicates, integrity, re-draw history (TZ §6, §18)."""
from __future__ import annotations

import random

from sqlalchemy import select

import draw as draw_service
import services
from database import DrawLog, DrawStatus, Winner
from tests.helpers import FakeBot, make_contest, make_user


def _participants(n, refs=None):
    refs = refs or {}
    return [
        draw_service.Participant(
            user_id=i,
            telegram_id=1000 + i,
            referral_count=refs.get(i, 0),
            weight=1.0 + draw_service.bonus_for_referrals(refs.get(i, 0)),
        )
        for i in range(n)
    ]


def test_weighted_sample_no_duplicates():
    participants = _participants(5)
    rng = random.Random(42)
    chosen = draw_service.weighted_sample_without_replacement(participants, 3, rng)
    ids = [p.user_id for p in chosen]
    assert len(ids) == 3
    assert len(set(ids)) == 3


def test_weighted_sample_respects_k_larger_than_population():
    participants = _participants(2)
    rng = random.Random(1)
    chosen = draw_service.weighted_sample_without_replacement(participants, 10, rng)
    assert len(chosen) == 2


def test_deterministic_with_same_seed():
    participants = _participants(10, {3: 50})
    a = [p.user_id for p in draw_service.weighted_sample_without_replacement(participants, 3, random.Random(7))]
    b = [p.user_id for p in draw_service.weighted_sample_without_replacement(participants, 3, random.Random(7))]
    assert a == b


def test_referral_weight_increases_win_share():
    """A heavily-referred participant should win noticeably more often."""
    rng = random.Random(1234)
    big_refs = {0: 1000}  # 0.01% * 1000 = 10% cap
    participants = _participants(20, big_refs)
    wins = 0
    rounds = 60000
    for _ in range(rounds):
        winner = draw_service.weighted_sample_without_replacement(participants, 1, rng)[0]
        if winner.user_id == 0:
            wins += 1
    share = wins / rounds
    # 1.10 weight out of ~20.10 -> ~5.5%; plain 1/20 = 5%. Must be clearly above.
    assert share > 0.053


async def test_run_draw_creates_log_and_winners(session):
    await make_user(session, 1, "Alice")
    await make_user(session, 2, "Bob")
    contest = await make_contest(session, winners=1, channels=[])
    bot = FakeBot({})
    await services.join_contest_by_id(session, bot, telegram_id=1, contest_id=contest.id)
    await services.join_contest_by_id(session, bot, telegram_id=2, contest_id=contest.id)

    result = await draw_service.run_draw(session, contest=contest, winners_count=1, seed="ab" * 32)
    assert result.log.id is not None
    assert len(result.winners) == 1
    assert result.log.participants_count == 2
    log = (await session.execute(select(DrawLog))).scalars().one()
    assert log.commitment  # commit before reveal
    winner_ids = [w.user_id for w in result.winners]
    assert draw_service.verify_draw(log, winner_ids) is True


async def test_redraw_supersedes_not_deletes(session):
    await make_user(session, 1, "Alice")
    await make_user(session, 2, "Bob")
    contest = await make_contest(session, winners=1, channels=[])
    bot = FakeBot({})
    await services.join_contest_by_id(session, bot, telegram_id=1, contest_id=contest.id)
    await services.join_contest_by_id(session, bot, telegram_id=2, contest_id=contest.id)

    first = await draw_service.run_draw(session, contest=contest, seed="11" * 32)
    second = await draw_service.run_draw(session, contest=contest, seed="22" * 32)
    logs = list((await session.execute(select(DrawLog).order_by(DrawLog.id))).scalars())
    assert len(logs) == 2
    assert logs[0].status == DrawStatus.REDRAWN.value
    assert logs[1].status == DrawStatus.COMPLETED.value
    assert first.log.id != second.log.id


async def test_verify_detects_tampering(session):
    await make_user(session, 1, "Alice")
    contest = await make_contest(session, winners=1, channels=[])
    bot = FakeBot({})
    await services.join_contest_by_id(session, bot, telegram_id=1, contest_id=contest.id)
    result = await draw_service.run_draw(session, contest=contest, seed="33" * 32)
    log = result.log
    winner_ids = [w.user_id for w in result.winners]
    assert draw_service.verify_draw(log, winner_ids) is True
    log.seed = "44" * 32  # tamper with the revealed seed
    assert draw_service.verify_draw(log, winner_ids) is False


async def test_draw_excludes_blocked_and_dedups(session):
    await make_user(session, 1, "Alice")
    await make_user(session, 2, "Bob")
    contest = await make_contest(session, winners=1, channels=[])
    bot = FakeBot({})
    await services.join_contest_by_id(session, bot, telegram_id=1, contest_id=contest.id)
    await services.join_contest_by_id(session, bot, telegram_id=2, contest_id=contest.id)

    from referrals import get_user_by_telegram
    bob = await get_user_by_telegram(session, 2)
    bob.is_blocked = True
    await session.flush()

    participants = await draw_service.collect_participants(session, contest.id)
    assert [p.telegram_id for p in participants] == [1]


async def test_finish_and_draw_marks_contest_finished(session):
    await make_user(session, 1, "Alice")
    contest = await make_contest(session, winners=1, channels=[])
    bot = FakeBot({})
    await services.join_contest_by_id(session, bot, telegram_id=1, contest_id=contest.id)
    outcome = await services.finish_and_draw(session, bot, contest=contest, run_by=1)
    assert outcome.ok is True
    assert contest.status == "finished"
    winners = list((await session.execute(select(Winner))).scalars())
    assert len(winners) == 1


async def test_finish_without_participants_fails(session):
    contest = await make_contest(session, winners=1, channels=[])
    bot = FakeBot({})
    outcome = await services.finish_and_draw(session, bot, contest=contest, run_by=1)
    assert outcome.ok is False
    assert outcome.reason == "no_participants"
