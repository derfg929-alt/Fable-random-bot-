"""Shared pytest fixtures.

The tests run against a single shared in-memory SQLite database so that the
whole application (models, services, API) can be exercised without a real
Telegram connection.
"""
from __future__ import annotations

import os

# Must be set before the app modules import settings/database.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ.setdefault("BOT_TOKEN", "123456:TEST-TOKEN")
os.environ.setdefault("BOT_USERNAME", "fable_test_bot")
os.environ.setdefault("OWNER_IDS", "1000")

import pytest  # noqa: E402

from database import Base, SessionFactory, engine  # noqa: E402


@pytest.fixture(autouse=True)
async def _fresh_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def session():
    async with SessionFactory() as s:
        yield s
        await s.rollback()
