"""Mini App API: initData signature verification and endpoint behaviour (TZ §12, §15).

Tests use an in-process ASGI client on the same event loop as the database, so
seeding and requests share one connection.
"""
from __future__ import annotations

import time

import httpx
import pytest

import admin
import api
import api_runtime
import services
from database import Contest, session_scope
from referrals import register_user
from tests.helpers import FakeBot, make_channel, make_contest


def _init_data(user_id: int, *, first_name="Test", age: int = 0):
    fields = {
        "auth_date": str(int(time.time()) - age),
        "user": f'{{"id": {user_id}, "first_name": "{first_name}", "username": "u{user_id}"}}',
        "query_id": "AAF",
    }
    return api.sign_init_data(fields)


@pytest.fixture
async def client():
    bot = FakeBot({1: "member", 2: "member", 9: "left"})
    api_runtime.set_bot(bot)
    app = api.create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.app_bot = bot  # type: ignore[attr-defined]
        yield c


async def _seed(*telegram_ids, owner_id=None):
    async with session_scope() as session:
        for tg in telegram_ids:
            await register_user(session, telegram_id=tg, first_name=f"U{tg}")
        if owner_id is not None:
            await admin.add_admin(session, telegram_id=owner_id, level=2, added_by=1000)


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_index_serves_mini_app(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert "Fable Randomizer" in r.text
    assert "telegram-web-app.js" in r.text


async def test_me_requires_init_data(client):
    r = await client.get("/api/me")
    assert r.status_code == 401


async def test_me_rejects_bad_signature(client):
    r = await client.get(
        "/api/me", headers={"X-Telegram-Init-Data": "user=%7B%22id%22%3A1%7D&hash=deadbeef"}
    )
    assert r.status_code == 401


async def test_me_rejects_expired_session(client):
    await _seed(1)
    init = _init_data(1, age=10 ** 7)
    r = await client.get("/api/me", headers={"X-Telegram-Init-Data": init})
    assert r.status_code == 401
    assert r.json()["detail"] == "session_expired"


async def test_me_ok(client):
    await _seed(1)
    r = await client.get("/api/me", headers={"X-Telegram-Init-Data": _init_data(1)})
    assert r.status_code == 200
    body = r.json()
    assert body["telegram_id"] == 1
    assert body["referral_link"].endswith("ref_1")
    assert body["is_admin"] is False


async def test_unregistered_user_forbidden(client):
    r = await client.get("/api/me", headers={"X-Telegram-Init-Data": _init_data(4242)})
    assert r.status_code == 403


async def test_contest_join_flow(client):
    async with session_scope() as session:
        await register_user(session, telegram_id=1, first_name="U1")
        ch = await make_channel(session, -1001, "News")
        contest = await make_contest(session, channels=[ch])
        contest_id = contest.id

    headers = {"X-Telegram-Init-Data": _init_data(1)}
    listing = await client.get("/api/contests", headers=headers)
    assert listing.status_code == 200
    assert listing.json()[0]["participants"] == 0

    joined = await client.post(f"/api/contests/{contest_id}/join", headers=headers)
    assert joined.status_code == 200
    assert joined.json()["status"] == "joined"

    again = await client.post(f"/api/contests/{contest_id}/join", headers=headers)
    assert again.json()["status"] == "already"


async def test_contest_join_subscription_required(client):
    async with session_scope() as session:
        await register_user(session, telegram_id=9, first_name="U9")
        ch = await make_channel(session, -1001, "News")
        contest = await make_contest(session, channels=[ch])
        contest_id = contest.id

    headers = {"X-Telegram-Init-Data": _init_data(9)}
    r = await client.post(f"/api/contests/{contest_id}/join", headers=headers)
    assert r.status_code == 409
    assert r.json()["status"] == "subscription"
    assert r.json()["missing"][0]["title"] == "News"


async def test_admin_endpoints_require_admin(client):
    await _seed(2)
    headers = {"X-Telegram-Init-Data": _init_data(2)}
    assert (await client.get("/api/admin/stats", headers=headers)).status_code == 403


async def test_admin_stats_ok(client):
    await _seed(1000, owner_id=1000)
    headers = {"X-Telegram-Init-Data": _init_data(1000)}
    r = await client.get("/api/admin/stats", headers=headers)
    assert r.status_code == 200
    assert "users_total" in r.json()


async def test_owner_only_setting_protected(client):
    async with session_scope() as session:
        await register_user(session, telegram_id=5000, first_name="Admin")
        await admin.add_admin(session, telegram_id=5000, level=1, added_by=1000)

    headers = {"X-Telegram-Init-Data": _init_data(5000)}
    r = await client.post(
        "/api/admin/settings", headers=headers, json={"values": {"referral_rate": "0.5"}}
    )
    assert r.status_code == 403
    r2 = await client.post(
        "/api/admin/settings", headers=headers, json={"values": {"project_name": "X"}}
    )
    assert r2.status_code == 200


async def test_admin_can_create_contest(client):
    await _seed(1000, owner_id=1000)
    headers = {"X-Telegram-Init-Data": _init_data(1000)}
    r = await client.post(
        "/api/admin/contests",
        headers=headers,
        json={"title": "New Prize", "description": "d", "prize": "P", "winners_count": 2},
    )
    assert r.status_code == 200
    assert r.json()["id"]


async def test_history_and_winners(client):
    async with session_scope() as session:
        await register_user(session, telegram_id=1, first_name="U1")
        contest = await make_contest(session, winners=1, channels=[])
        contest_id = contest.id

    headers = {"X-Telegram-Init-Data": _init_data(1)}
    await client.post(f"/api/contests/{contest_id}/join", headers=headers)

    hist = await client.get("/api/history", headers=headers)
    assert hist.status_code == 200
    assert hist.json()[0]["contest_id"] == contest_id

    async with session_scope() as session:
        contest = await session.get(Contest, contest_id)
        await services.finish_and_draw(session, api_runtime.get_bot(), contest=contest, run_by=1000)

    winners = await client.get("/api/winners", headers=headers)
    assert winners.status_code == 200
    assert winners.json()[0]["contest_id"] == contest_id


async def test_admin_block_user(client):
    async with session_scope() as session:
        await register_user(session, telegram_id=42, first_name="Victim")
    await _seed(1000, owner_id=1000)
    headers = {"X-Telegram-Init-Data": _init_data(1000)}
    r = await client.post(
        "/api/admin/users/42/block", headers=headers, json={"blocked": True, "reason": "spam"}
    )
    assert r.status_code == 200
    detail = await client.get("/api/admin/users/42", headers=headers)
    assert detail.json()["is_blocked"] is True


async def test_admin_can_update_contest(client):
    async with session_scope() as session:
        contest = await make_contest(session, winners=1, channels=[])
        contest_id = contest.id
    await _seed(1000, owner_id=1000)
    headers = {"X-Telegram-Init-Data": _init_data(1000)}
    r = await client.patch(
        f"/api/admin/contests/{contest_id}",
        headers=headers,
        json={"title": "Обновлённый", "prize": "P2", "winners_count": 3},
    )
    assert r.status_code == 200
    detail = await client.get(f"/api/contests/{contest_id}", headers=headers)
    assert detail.json()["title"] == "Обновлённый"
    assert detail.json()["winners_count"] == 3
