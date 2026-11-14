"""Database layer: async SQLAlchemy engine, session factory and models.

The first version ships with SQLite (``sqlite+aiosqlite``) and can be moved to
PostgreSQL by pointing ``DATABASE_URL`` at ``postgresql+asyncpg://...``.

Design notes
------------
* ``users`` is keyed by the Telegram id so one Telegram account maps to exactly
  one row (protects against duplicate registrations).
* Referral attribution is stored on ``users.referred_by`` and mirrored into the
  ``referrals`` table; the invite counter is kept in ``users.referral_count``.
* Contest history must survive contest deletion, so contests are *archived*
  (soft delete via ``archived``/``status``) and never hard-deleted while they
  have entries or draw logs.
* ``entries`` has a UNIQUE ``(contest_id, user_id)`` constraint: one person can
  take part in a given contest only once.
* Draws are recorded in ``draw_logs`` with the participant snapshot, the seed
  commitment and the revealed seed, so results are auditable and re-draws are
  never lost.
"""
from __future__ import annotations

import enum
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

import settings


class Base(DeclarativeBase):
    pass


class ContestStatus(str, enum.Enum):
    """Lifecycle of a contest."""

    PENDING = "pending"      # Ожидает начала
    ACTIVE = "active"        # Активен
    FINISHED = "finished"    # Завершён
    CANCELLED = "cancelled"  # Отменён


class DrawStatus(str, enum.Enum):
    DRAFT = "draft"        # not yet executed
    COMPLETED = "completed"
    REDRAWN = "redrawn"    # superseded by a later draw


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(64))
    first_name: Mapped[Optional[str]] = mapped_column(String(255))
    last_name: Mapped[Optional[str]] = mapped_column(String(255))
    language_code: Mapped[Optional[str]] = mapped_column(String(16))

    # Registration / referral data
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    referred_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    referral_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bonus: Mapped[float] = mapped_column(default=0.0, nullable=False)

    # Activity / participation aggregates (recomputed on the fly where cheap)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    activity_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Project-level block (blocks new contest participation, keeps history)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    blocked_reason: Mapped[Optional[str]] = mapped_column(String(255))

    referrals_made: Mapped[list["Referral"]] = relationship(
        back_populates="referrer", foreign_keys="Referral.referrer_id"
    )


class Channel(Base, TimestampMixin):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    invite_link: Mapped[Optional[str]] = mapped_column(String(255))
    added_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    bot_is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Contest(Base, TimestampMixin):
    __tablename__ = "contests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    prize: Mapped[str] = mapped_column(Text, default="", nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(String(512))
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    status: Mapped[str] = mapped_column(String(16), default=ContestStatus.PENDING.value, nullable=False)
    winners_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    start_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    conditions_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    channels: Mapped[list["ContestChannel"]] = relationship(
        back_populates="contest", cascade="all, delete-orphan"
    )


class ContestChannel(Base):
    __tablename__ = "contest_channels"
    __table_args__ = (UniqueConstraint("contest_id", "channel_id", name="uq_contest_channel"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contest_id: Mapped[int] = mapped_column(
        ForeignKey("contests.id", ondelete="CASCADE"), index=True, nullable=False
    )
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True, nullable=False
    )
    is_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    contest: Mapped["Contest"] = relationship(back_populates="channels")
    channel: Mapped["Channel"] = relationship()


class Entry(Base, TimestampMixin):
    __tablename__ = "entries"
    __table_args__ = (
        UniqueConstraint("contest_id", "user_id", name="uq_entry_contest_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contest_id: Mapped[int] = mapped_column(
        ForeignKey("contests.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    conditions_met: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Referral(Base, TimestampMixin):
    __tablename__ = "referrals"
    __table_args__ = (
        UniqueConstraint("referred_id", name="uq_referral_referred"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    referrer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    referred_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    bonus_granted: Mapped[float] = mapped_column(default=0.0, nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String(64))

    referrer: Mapped["User"] = relationship(
        back_populates="referrals_made", foreign_keys=[referrer_id]
    )


class DrawLog(Base, TimestampMixin):
    __tablename__ = "draw_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contest_id: Mapped[int] = mapped_column(
        ForeignKey("contests.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default=DrawStatus.COMPLETED.value, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)

    # commit-reveal integrity
    participants_snapshot: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list
    commitment: Mapped[str] = mapped_column(String(64), nullable=False)       # sha256(seed)
    seed: Mapped[str] = mapped_column(String(64), nullable=False)             # revealed seed
    integrity_hash: Mapped[str] = mapped_column(String(64), nullable=False)   # sha256(all fields)

    winners_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    participants_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    run_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reason: Mapped[Optional[str]] = mapped_column(String(255))

    winners: Mapped[list["Winner"]] = relationship(
        back_populates="draw", cascade="all, delete-orphan"
    )


class Winner(Base):
    __tablename__ = "winners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draw_id: Mapped[int] = mapped_column(
        ForeignKey("draw_logs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    contest_id: Mapped[int] = mapped_column(
        ForeignKey("contests.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    draw: Mapped["DrawLog"] = relationship(back_populates="winners")
    user: Mapped["User"] = relationship()


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    contest_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("contests.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(160), nullable=False)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error: Mapped[Optional[str]] = mapped_column(String(255))

    __table_args__ = (
        UniqueConstraint("user_id", "dedup_key", name="uq_notification_dedup"),
        Index("ix_notification_user_type_contest", "user_id", "type", "contest_id"),
    )


class Admin(Base, TimestampMixin):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 1 admin, 2 owner
    added_by: Mapped[Optional[int]] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(255))


class Setting(Base, TimestampMixin):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="", nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    target_type: Mapped[Optional[str]] = mapped_column(String(32))
    target_id: Mapped[Optional[str]] = mapped_column(String(64))
    payload: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------------
# Engine / session plumbing
# --------------------------------------------------------------------------
def _build_engine():
    url = make_url(settings.DATABASE_URL)
    kwargs: dict = {"echo": settings.DB_ECHO, "future": True}
    if url.get_backend_name() == "sqlite":
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in settings.DATABASE_URL or url.database in (None, "", ":memory:"):
            # A shared in-memory database needs a single connection pool.
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
    return create_async_engine(url, **kwargs)


engine = _build_engine()
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_models() -> None:
    """Create tables if they do not exist (safe to call on every startup)."""
    async with engine.begin() as conn:
        if engine.url.get_backend_name() == "sqlite":
            from sqlalchemy import text

            await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional scope: commit on success, rollback on error."""
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
