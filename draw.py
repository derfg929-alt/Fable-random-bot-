"""Weighted winner selection with commit-reveal integrity (TZ §6).

Algorithm (``fr-weighted-v1``)
-----------------------------
1. Take the *valid* participant list: entries whose ``conditions_met`` is true
   and whose user is not blocked. Duplicates are impossible by construction
   (UNIQUE ``(contest_id, user_id)``) but are filtered defensively.
2. Give every participant a relative weight of ``1 + referral_bonus`` where the
   bonus is ``0.01%`` per invited friend, capped at ``+10%`` (internal only).
3. Draw ``k`` winners **without replacement** using a weighted
   Efraimidis-Spirakis key (``random() ** (1 / weight)``, highest keys win).
   The RNG is seeded from the revealed seed, so the result is reproducible.
4. Persist a draw log containing the participant snapshot, the seed
   commitment (``sha256(seed)``), the revealed seed, the winners and an
   integrity hash over the whole record.

The commitment is written *before* the winners are computed; the seed is only
published afterwards. ``sha256(seed)`` alone proves nothing about fairness, so
we additionally publish the participant snapshot and the algorithm version —
that is what makes the draw independently re-checkable.
"""
from __future__ import annotations

import hashlib
import json
import random
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

import settings
from database import (
    Contest,
    DrawLog,
    DrawStatus,
    Entry,
    User,
    Winner,
)
from referrals import bonus_for_referrals


@dataclass
class Participant:
    user_id: int
    telegram_id: int
    referral_count: int
    weight: float


@dataclass
class DrawResult:
    log: DrawLog
    winners: list[Winner]
    participants: list[Participant]


def _commitment(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _snapshot_hash(participants: Sequence[Participant]) -> str:
    payload = json.dumps(
        [[p.user_id, p.referral_count] for p in participants],
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _integrity_hash(
    *,
    contest_id: int,
    seed: str,
    commitment: str,
    snapshot: str,
    winner_ids: Sequence[int],
    winners_count: int,
    participants_count: int,
    algorithm_version: str,
) -> str:
    blob = "|".join(
        [
            str(contest_id),
            seed,
            commitment,
            snapshot,
            ",".join(str(w) for w in winner_ids),
            str(winners_count),
            str(participants_count),
            algorithm_version,
        ]
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def weighted_sample_without_replacement(
    participants: Sequence[Participant], k: int, rng: random.Random
) -> list[Participant]:
    """Deterministic weighted draw of ``k`` distinct participants.

    Uses the Efraimidis-Spirakis A-ES algorithm: for each participant draw
    ``u = rng.random()`` and key ``u ** (1 / weight)``; the ``k`` largest keys
    win. Every participant can be selected at most once.
    """
    if k <= 0 or not participants:
        return []
    k = min(k, len(participants))
    keyed = [(rng.random() ** (1.0 / max(p.weight, 1e-9)), p) for p in participants]
    keyed.sort(key=lambda item: item[0], reverse=True)
    return [p for _, p in keyed[:k]]


async def collect_participants(
    session: AsyncSession, contest_id: int
) -> list[Participant]:
    stmt = (
        select(Entry, User)
        .join(User, User.id == Entry.user_id)
        .where(Entry.contest_id == contest_id, Entry.conditions_met.is_(True))
    )
    rows = (await session.execute(stmt)).all()
    seen: set[int] = set()
    participants: list[Participant] = []
    for entry, user in rows:
        if user.is_blocked or user.id in seen:
            continue
        seen.add(user.id)
        bonus = bonus_for_referrals(user.referral_count or 0)
        participants.append(
            Participant(
                user_id=user.id,
                telegram_id=user.telegram_id,
                referral_count=user.referral_count or 0,
                weight=1.0 + bonus,
            )
        )
    participants.sort(key=lambda p: p.user_id)
    return participants


async def run_draw(
    session: AsyncSession,
    *,
    contest: Contest,
    winners_count: Optional[int] = None,
    run_by: Optional[int] = None,
    reason: Optional[str] = None,
    seed: Optional[str] = None,
    now: Optional[datetime] = None,
) -> DrawResult:
    """Execute a draw for ``contest`` and persist an auditable log.

    A previous completed draw for the same contest is marked ``redrawn`` rather
    than deleted, so the full history of attempts is preserved (TZ §6).
    """
    participants = await collect_participants(session, contest.id)
    if not participants:
        raise ValueError("no valid participants")

    k = winners_count if winners_count is not None else contest.winners_count
    k = max(1, min(int(k), len(participants)))

    seed = seed or secrets.token_hex(32)
    commitment = _commitment(seed)  # committed before selecting winners
    snapshot = json.dumps(
        [[p.user_id, p.referral_count] for p in participants], separators=(",", ":")
    )
    now = now or settings.now_local()

    rng = random.Random(int(seed, 16))
    selected = weighted_sample_without_replacement(participants, k, rng)
    winner_ids = [p.user_id for p in selected]

    log = DrawLog(
        contest_id=contest.id,
        status=DrawStatus.COMPLETED.value,
        algorithm_version=settings.DRAW_ALGORITHM_VERSION,
        participants_snapshot=snapshot,
        commitment=commitment,
        seed=seed,
        integrity_hash="",  # filled in below
        winners_count=k,
        participants_count=len(participants),
        run_by=run_by,
        reason=reason,
    )
    log.created_at = now
    log.updated_at = now
    log.integrity_hash = _integrity_hash(
        contest_id=contest.id,
        seed=seed,
        commitment=commitment,
        snapshot=snapshot,
        winner_ids=winner_ids,
        winners_count=k,
        participants_count=len(participants),
        algorithm_version=settings.DRAW_ALGORITHM_VERSION,
    )

    # Supersede any earlier completed draw for this contest (never delete).
    await session.execute(
        update(DrawLog)
        .where(DrawLog.contest_id == contest.id, DrawLog.status == DrawStatus.COMPLETED.value)
        .values(status=DrawStatus.REDRAWN.value)
    )

    session.add(log)
    await session.flush()

    winners: list[Winner] = []
    for position, participant in enumerate(selected, start=1):
        winner = Winner(
            draw_id=log.id,
            contest_id=contest.id,
            user_id=participant.user_id,
            position=position,
        )
        session.add(winner)
        winners.append(winner)
    await session.flush()
    return DrawResult(log=log, winners=winners, participants=participants)


def verify_draw(log: DrawLog, winner_ids: Optional[Sequence[int]] = None) -> bool:
    """Re-check a stored draw: commitment, integrity hash and reproducibility.

    ``winner_ids`` may be supplied by the caller (e.g. loaded from the winners
    table) to avoid triggering a lazy relationship load outside the async
    context. When omitted, the ``log.winners`` relationship is used.

    Returns ``True`` when the record is internally consistent and the recorded
    winners can be re-derived from the revealed seed and the snapshot.
    """
    if _commitment(log.seed) != log.commitment:
        return False
    if winner_ids is None:
        winner_ids = [w.user_id for w in log.winners]
    winner_ids = list(winner_ids)
    expected = _integrity_hash(
        contest_id=log.contest_id,
        seed=log.seed,
        commitment=log.commitment,
        snapshot=log.participants_snapshot,
        winner_ids=winner_ids,
        winners_count=log.winners_count,
        participants_count=log.participants_count,
        algorithm_version=log.algorithm_version,
    )
    if expected != log.integrity_hash:
        return False

    try:
        raw = json.loads(log.participants_snapshot)
    except json.JSONDecodeError:
        return False
    participants = [
        Participant(
            user_id=uid,
            telegram_id=0,
            referral_count=refs,
            weight=1.0 + bonus_for_referrals(refs),
        )
        for uid, refs in raw
    ]
    rng = random.Random(int(log.seed, 16))
    recomputed = weighted_sample_without_replacement(participants, log.winners_count, rng)
    return [p.user_id for p in recomputed] == winner_ids
