"""Database layer: SQLAlchemy 2.0 async models and session helpers.

The whole application (bot, Mini App API, background worker) shares one
database. SQLite is the default so the bot runs with a single ``python main.py``;
set ``DATABASE_URL`` to a PostgreSQL DSN for production.

All datetimes are stored as naive values in the configured timezone
(``settings.now_local``) so comparisons never mix aware/naive values.
"""
from __future__ import annotations

import enum
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.pool import StaticPool

import settings


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------
# Enumerations (stored as plain strings for portability)
# --------------------------------------------------------------------------
class ContestStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    FINISHED = "finished"
    CANCELLED = "cancelled"


class DrawStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    REDRAWN = "redrawn"
    FAILED = "failed"


# --------------------------------------------------------------------------
# Users and referrals
# --------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    language_code: Mapped[str | None] = mapped_column(String(16), nullable=True)

    referred_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    referral_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Internal draw-weight bonus; never shown to end users.
    bonus: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    blocked_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    registered_at: Mapped[datetime] = mapped_column(
        DateTime, default=settings.now_local, nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Referral(Base):
    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(primary_key=True)
    referrer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # One invitee can be attributed to exactly one referrer.
    referred_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    bonus_granted: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=settings.now_local, nullable=False
    )


# --------------------------------------------------------------------------
# Channels and contests
# --------------------------------------------------------------------------
class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    invite_link: Mapped[str | None] = mapped_column(String(255), nullable=True)
    added_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    bot_is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=settings.now_local, nullable=False
    )


class Contest(Base):
    __tablename__ = "contests"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    prize: Mapped[str] = mapped_column(Text, default="", nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    winners_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    contest_type: Mapped[str] = mapped_column(String(32), default="simple", nullable=False)

    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), default=ContestStatus.PENDING.value, index=True, nullable=False
    )
    start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=settings.now_local, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=settings.now_local, onupdate=settings.now_local, nullable=False
    )


class ContestChannel(Base):
    __tablename__ = "contest_channels"
    __table_args__ = (UniqueConstraint("contest_id", "channel_id", name="uq_contest_channel"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    contest_id: Mapped[int] = mapped_column(
        ForeignKey("contests.id", ondelete="CASCADE"), index=True, nullable=False
    )
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True, nullable=False
    )
    is_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Entry(Base):
    __tablename__ = "entries"
    __table_args__ = (UniqueConstraint("contest_id", "user_id", name="uq_contest_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    contest_id: Mapped[int] = mapped_column(
        ForeignKey("contests.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    conditions_met: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=settings.now_local, nullable=False
    )


# --------------------------------------------------------------------------
# Draws and winners
# --------------------------------------------------------------------------
class DrawLog(Base):
    __tablename__ = "draw_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    contest_id: Mapped[int] = mapped_column(
        ForeignKey("contests.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default=DrawStatus.PENDING.value, index=True, nullable=False
    )
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)
    participants_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    commitment: Mapped[str] = mapped_column(String(64), nullable=False)
    seed: Mapped[str] = mapped_column(String(64), nullable=False)
    integrity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    winners_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    participants_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    run_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=settings.now_local, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=settings.now_local, onupdate=settings.now_local, nullable=False
    )

    winners: Mapped[list["Winner"]] = relationship(
        back_populates="draw",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="Winner.position",
    )


class Winner(Base):
    __tablename__ = "winners"
    __table_args__ = (UniqueConstraint("draw_id", "user_id", name="uq_draw_winner"),)

    id: Mapped[int] = mapped_column(primary_key=True)
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
        DateTime, default=settings.now_local, nullable=False
    )

    draw: Mapped[DrawLog] = relationship(back_populates="winners")


# --------------------------------------------------------------------------
# Notifications, admins, settings, audit
# --------------------------------------------------------------------------
class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("user_id", "dedup_key", name="uq_user_notification"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    contest_id: Mapped[int | None] = mapped_column(
        ForeignKey("contests.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(128), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=settings.now_local, nullable=False
    )


class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    added_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=settings.now_local, nullable=False
    )


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=settings.now_local, onupdate=settings.now_local, nullable=False
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=settings.now_local, nullable=False
    )


# --------------------------------------------------------------------------
# Engine and sessions
# --------------------------------------------------------------------------
def _engine_options(url: str) -> dict:
    options: dict = {"echo": settings.DB_ECHO, "future": True}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
        # A shared in-memory DB only works when every session uses the same
        # connection, hence StaticPool.
        if ":memory:" in url:
            options["poolclass"] = StaticPool
    return options


engine = create_async_engine(settings.DATABASE_URL, **_engine_options(settings.DATABASE_URL))

SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional session: commit on success, roll back on any error."""
    session = SessionFactory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def init_models() -> None:
    """Create all tables if they do not exist yet (no migrations needed)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def ping() -> bool:
    """Cheap connectivity check used by startup/health tooling."""
    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:  # pragma: no cover - only on a broken DB
        return False
