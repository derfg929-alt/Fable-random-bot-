"""Server-side HTTP API for the Telegram Mini App.

Security (TZ §12, §15)
----------------------
* Every authenticated call carries Telegram ``initData``; the HMAC signature is
  verified server-side against the bot token. A client-supplied Telegram id is
  never trusted.
* Admin endpoints additionally require the verified user to be an admin/owner.
* ``initData`` has an ``auth_date`` freshness check.

All mutations go through the shared services so the bot and the API behave
identically.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

import admin
import contests as contest_service
import notifications as notify
import services
import settings
from database import Channel, Contest, Entry, User, Winner, session_scope
from referrals import get_user_by_telegram, referral_link

def _load_mini_app_html() -> str:
    """Load the Mini App page from disk.

    The file has been renamed a few times (``mini_app.html`` -> ``index.html``),
    so accept any known name and fail loudly if none is present instead of
    crashing on import.
    """
    for name in ("index.html", "mini_app.html"):
        candidate = Path(__file__).with_name(name)
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError(
        "Mini App HTML not found next to api.py (expected index.html or mini_app.html)"
    )


MINI_APP_HTML = _load_mini_app_html()


# --------------------------------------------------------------------------
# initData verification
# --------------------------------------------------------------------------
def verify_init_data(init_data: str, *, bot_token: Optional[str] = None) -> dict:
    """Validate a Telegram Mini App ``initData`` string and return its fields.

    Raises ``HTTPException(401)`` when the signature is missing/invalid or the
    session is too old.
    """
    token = bot_token or settings.BOT_TOKEN
    if not init_data:
        raise HTTPException(status_code=401, detail="initData_required")
    if not token or ":" not in token:
        raise HTTPException(status_code=500, detail="bot_token_not_configured")

    data = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = data.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="hash_missing")

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise HTTPException(status_code=401, detail="invalid_signature")

    try:
        auth_date = int(data.get("auth_date", "0"))
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid_auth_date")
    if settings.INIT_DATA_MAX_AGE and time.time() - auth_date > settings.INIT_DATA_MAX_AGE:
        raise HTTPException(status_code=401, detail="session_expired")

    user_raw = data.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="user_missing")
    try:
        user = json.loads(user_raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=401, detail="invalid_user")
    data["user_parsed"] = user
    return data


def sign_init_data(fields: dict, bot_token: Optional[str] = None) -> str:
    """Build a valid ``initData`` string (used by tests and local tooling)."""
    token = bot_token or settings.BOT_TOKEN
    fields = {k: str(v) for k, v in fields.items()}
    check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    from urllib.parse import urlencode

    return urlencode({**fields, "hash": signature})


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------
async def _bot_client():
    from api_runtime import get_bot

    return get_bot()


async def current_user(
    x_telegram_init_data: str = Header(default=""),
) -> User:
    payload = verify_init_data(x_telegram_init_data)
    tg = payload["user_parsed"]
    async with session_scope() as session:
        user = await get_user_by_telegram(session, tg["id"])
        if user is None:
            raise HTTPException(status_code=403, detail="not_registered")
        if user.is_blocked:
            raise HTTPException(status_code=403, detail="blocked")
        session.expunge(user)
    return user


async def require_admin(user: User = Depends(current_user)) -> User:
    async with session_scope() as session:
        if not await admin.is_admin(session, user.telegram_id):
            raise HTTPException(status_code=403, detail="admin_only")
    return user


async def require_owner(user: User = Depends(current_user)) -> User:
    async with session_scope() as session:
        if not await admin.is_owner(session, user.telegram_id):
            raise HTTPException(status_code=403, detail="owner_only")
    return user


# --------------------------------------------------------------------------
# Request models (module level: with ``from __future__ import annotations``
# FastAPI cannot resolve models defined inside a function scope)
# --------------------------------------------------------------------------
class ContestIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    prize: str = ""
    image_url: Optional[str] = None
    winners_count: int = 1
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    channel_ids: list[int] = []


class ContestPatchIn(BaseModel):
    """Partial contest update: only provided fields are applied (TZ §11.2)."""

    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    prize: Optional[str] = None
    image_url: Optional[str] = None
    winners_count: Optional[int] = None
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    channel_ids: Optional[list[int]] = None


class ActionIn(BaseModel):
    winners_count: Optional[int] = None
    reason: Optional[str] = None


class BlockIn(BaseModel):
    blocked: bool
    reason: Optional[str] = None


class SettingsIn(BaseModel):
    values: dict[str, str]


# --------------------------------------------------------------------------
# Serialisers
# --------------------------------------------------------------------------
def serialize_contest(contest: Contest, participants: int, joined: bool = False) -> dict:
    return {
        "id": contest.id,
        "title": contest.title,
        "description": contest.description,
        "prize": contest.prize,
        "image_url": contest.image_url,
        "status": contest_service.effective_status(contest),
        "winners_count": contest.winners_count,
        "participants": participants,
        "start_at": contest.start_at.isoformat() if contest.start_at else None,
        "end_at": contest.end_at.isoformat() if contest.end_at else None,
        "joined": joined,
    }


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
def create_app() -> FastAPI:
    app = FastAPI(title="Fable Randomizer API", version="1.0")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTMLResponse(MINI_APP_HTML)

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "configured": settings.is_configured()}

    # ---- user ----
    @app.get("/api/me")
    async def me(user: User = Depends(current_user)):
        async with session_scope() as session:
            fresh = await session.get(User, user.id)
            profile = await services.build_profile(session, fresh)
            return {
                "telegram_id": fresh.telegram_id,
                "first_name": fresh.first_name,
                "username": fresh.username,
                "registered_at": fresh.registered_at.isoformat() if fresh.registered_at else None,
                "referral_count": fresh.referral_count,
                "referral_link": referral_link(fresh),
                "active_entries": profile.active_entries,
                "finished_entries": profile.finished_entries,
                "wins": profile.wins,
                "is_admin": await admin.is_admin(session, fresh.telegram_id),
                "is_owner": await admin.is_owner(session, fresh.telegram_id),
            }

    @app.get("/api/contests")
    async def list_contests(user: User = Depends(current_user)):
        async with session_scope() as session:
            rows = await contest_service.list_contests(session)
            joined_ids = {
                e.contest_id
                for e in (
                    await session.execute(select(Entry).where(Entry.user_id == user.id))
                ).scalars()
            }
            out = []
            for c in rows:
                count = await contest_service.entry_count(session, c.id)
                out.append(serialize_contest(c, count, joined=c.id in joined_ids))
            return out

    @app.get("/api/contests/{contest_id}")
    async def contest_detail(contest_id: int, user: User = Depends(current_user)):
        async with session_scope() as session:
            contest = await contest_service.get_contest(session, contest_id)
            if contest is None:
                raise HTTPException(status_code=404, detail="not_found")
            count = await contest_service.entry_count(session, contest.id)
            channels = await contest_service.contest_channels(session, contest.id)
            joined = (
                await session.execute(
                    select(Entry).where(
                        Entry.contest_id == contest.id, Entry.user_id == user.id
                    )
                )
            ).scalar_one_or_none() is not None
            data = serialize_contest(contest, count, joined=joined)
            data["channels"] = [
                {"title": ch.title, "username": ch.username, "link": _channel_link(ch)}
                for ch in channels
            ]
            return data

    @app.post("/api/contests/{contest_id}/join")
    async def join(contest_id: int, user: User = Depends(current_user)):
        bot = await _bot_client()
        async with session_scope() as session:
            outcome = await services.join_contest_by_id(
                session, bot, telegram_id=user.telegram_id, contest_id=contest_id
            )
        if outcome.status == "joined":
            return {"ok": True, "status": "joined"}
        if outcome.status == "already":
            return {"ok": True, "status": "already"}
        if outcome.status == "subscription":
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "status": "subscription",
                    "error": "subscription_required",
                    "missing": [
                        {"title": ch.title, "link": _channel_link(ch)}
                        for ch in (outcome.missing or [])
                    ],
                    "subscription_error": outcome.subscription_error,
                },
            )
        raise HTTPException(status_code=400, detail=outcome.status)

    @app.get("/api/history")
    async def history(user: User = Depends(current_user)):
        async with session_scope() as session:
            fresh = await session.get(User, user.id)
            entries = await contest_service.user_entries(session, fresh.id)
            win_ids = {
                w.contest_id
                for w in (
                    await session.execute(select(Winner).where(Winner.user_id == fresh.id))
                ).scalars()
            }
            return [
                {
                    "contest_id": c.id,
                    "title": c.title,
                    "joined_at": e.created_at.isoformat() if e.created_at else None,
                    "status": contest_service.effective_status(c),
                    "won": c.id in win_ids,
                }
                for e, c in entries
            ]

    @app.get("/api/winners")
    async def winners(user: User = Depends(current_user)):
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(Winner, Contest, User)
                    .join(Contest, Contest.id == Winner.contest_id)
                    .join(User, User.id == Winner.user_id)
                    .order_by(Winner.id.desc())
                    .limit(100)
                )
            ).all()
            result = []
            for winner, contest, wuser in rows:
                result.append(
                    {
                        "contest_id": contest.id,
                        "contest_title": contest.title,
                        "prize": contest.prize,
                        "finished_at": contest.updated_at.isoformat()
                        if contest.updated_at
                        else None,
                        "position": winner.position,
                        "name": wuser.first_name or f"id{wuser.telegram_id}",
                        "username": wuser.username,
                    }
                )
            return result

    # ---- admin ----
    @app.get("/api/admin/stats")
    async def admin_stats(user: User = Depends(require_admin)):
        async with session_scope() as session:
            s = await admin.get_stats(session)
            return s.__dict__

    @app.get("/api/admin/contests")
    async def admin_contests(user: User = Depends(require_admin)):
        async with session_scope() as session:
            rows = await contest_service.list_contests(session, include_archived=True)
            out = []
            for c in rows:
                count = await contest_service.entry_count(session, c.id)
                data = serialize_contest(c, count)
                data["archived"] = c.archived
                out.append(data)
            return out


    @app.post("/api/admin/contests")
    async def admin_create_contest(payload: ContestIn, user: User = Depends(require_admin)):
        from datetime import datetime

        def parse(value: Optional[str]):
            if not value:
                return None
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                raise HTTPException(status_code=400, detail="bad_datetime")

        async with session_scope() as session:
            contest = await contest_service.create_contest(
                session,
                title=payload.title,
                description=payload.description,
                prize=payload.prize,
                image_url=payload.image_url,
                winners_count=payload.winners_count,
                created_by=user.id,
                start_at=parse(payload.start_at),
                end_at=parse(payload.end_at),
                channel_ids=payload.channel_ids,
            )
            await admin.audit(
                session,
                actor_id=user.id,
                action="contest_create",
                target_type="contest",
                target_id=contest.id,
                payload={"title": contest.title, "via": "api"},
            )
            return {"ok": True, "id": contest.id}


    @app.patch("/api/admin/contests/{contest_id}")
    async def admin_update_contest(
        contest_id: int, payload: ContestPatchIn, user: User = Depends(require_admin)
    ):
        from datetime import datetime

        def parse(value: Optional[str]):
            if not value:
                return None
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                raise HTTPException(status_code=400, detail="bad_datetime")

        bot = await _bot_client()
        async with session_scope() as session:
            contest = await contest_service.get_contest(
                session, contest_id, include_archived=True
            )
            if contest is None:
                raise HTTPException(status_code=404, detail="not_found")
            await contest_service.update_contest(
                session,
                contest,
                title=payload.title,
                description=payload.description,
                prize=payload.prize,
                image_url=payload.image_url,
                winners_count=payload.winners_count,
                start_at=parse(payload.start_at),
                end_at=parse(payload.end_at),
                channel_ids=payload.channel_ids,
            )
            await notify.broadcast_contest(
                session,
                bot,
                contest=contest,
                notification_type="contest_changed",
                text=f"✏️ Условия конкурса «{contest.title}» изменились. Проверь детали.",
                dedup_suffix="changed",
            )
            await admin.audit(
                session,
                actor_id=user.id,
                action="contest_update",
                target_type="contest",
                target_id=contest_id,
                payload={"title": contest.title, "via": "api"},
            )
            return {"ok": True}

    @app.post("/api/admin/contests/{contest_id}/publish")
    async def admin_publish(contest_id: int, user: User = Depends(require_admin)):
        bot = await _bot_client()
        async with session_scope() as session:
            contest = await contest_service.get_contest(session, contest_id)
            if contest is None:
                raise HTTPException(status_code=404, detail="not_found")
            outcome = await services.publish_contest(session, bot, contest=contest)
            return {"ok": True, "sent": outcome.sent, "failed": outcome.failed}

    @app.post("/api/admin/contests/{contest_id}/finish")
    async def admin_finish(
        contest_id: int, payload: ActionIn, user: User = Depends(require_admin)
    ):
        bot = await _bot_client()
        async with session_scope() as session:
            contest = await contest_service.get_contest(session, contest_id)
            if contest is None:
                raise HTTPException(status_code=404, detail="not_found")
            outcome = await services.finish_and_draw(
                session,
                bot,
                contest=contest,
                winners_count=payload.winners_count,
                run_by=user.id,
                reason=payload.reason,
            )
            if not outcome.ok:
                raise HTTPException(status_code=400, detail=outcome.reason)
            return {
                "ok": True,
                "winners": [
                    {"telegram_id": u.telegram_id, "name": u.first_name} for _, u in outcome.winners
                ],
            }

    @app.post("/api/admin/contests/{contest_id}/cancel")
    async def admin_cancel(contest_id: int, user: User = Depends(require_admin)):
        bot = await _bot_client()
        async with session_scope() as session:
            contest = await contest_service.get_contest(session, contest_id)
            if contest is None:
                raise HTTPException(status_code=404, detail="not_found")
            await services.cancel_contest(session, bot, contest=contest, actor_id=user.id)
            return {"ok": True}

    @app.post("/api/admin/contests/{contest_id}/archive")
    async def admin_archive(contest_id: int, user: User = Depends(require_admin)):
        async with session_scope() as session:
            contest = await contest_service.get_contest(
                session, contest_id, include_archived=True
            )
            if contest is None:
                raise HTTPException(status_code=404, detail="not_found")
            await contest_service.archive_contest(session, contest)
            await admin.audit(
                session,
                actor_id=user.id,
                action="contest_archive",
                target_type="contest",
                target_id=contest_id,
            )
            return {"ok": True}

    @app.get("/api/admin/contests/{contest_id}/participants")
    async def admin_participants(contest_id: int, user: User = Depends(require_admin)):
        async with session_scope() as session:
            entries = await contest_service.contest_entries(session, contest_id)
            out = []
            for e in entries:
                u = await session.get(User, e.user_id)
                out.append(
                    {
                        "telegram_id": u.telegram_id,
                        "name": u.first_name,
                        "username": u.username,
                        "referral_count": u.referral_count,
                        "conditions_met": e.conditions_met,
                    }
                )
            return out

    @app.get("/api/admin/draws")
    async def admin_draws(user: User = Depends(require_admin)):
        async with session_scope() as session:
            logs = await admin.list_draws(session)
            return [
                {
                    "id": d.id,
                    "contest_id": d.contest_id,
                    "status": d.status,
                    "algorithm_version": d.algorithm_version,
                    "commitment": d.commitment,
                    "seed": d.seed,
                    "integrity_hash": d.integrity_hash,
                    "participants_count": d.participants_count,
                    "winners_count": d.winners_count,
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                }
                for d in logs
            ]

    @app.get("/api/admin/contests/{contest_id}/report")
    async def admin_report(contest_id: int, user: User = Depends(require_admin)):
        async with session_scope() as session:
            return await admin.contest_report(session, contest_id)

    @app.get("/api/admin/channels")
    async def admin_channels(user: User = Depends(require_admin)):
        async with session_scope() as session:
            rows = await contest_service.list_channels(session, include_inactive=True)
            return [
                {
                    "id": ch.id,
                    "chat_id": ch.chat_id,
                    "title": ch.title,
                    "username": ch.username,
                    "is_active": ch.is_active,
                    "bot_is_admin": ch.bot_is_admin,
                }
                for ch in rows
            ]

    @app.get("/api/admin/users/{telegram_id}")
    async def admin_user(telegram_id: int, user: User = Depends(require_admin)):
        async with session_scope() as session:
            target = await admin.find_user(session, telegram_id)
            if target is None:
                raise HTTPException(status_code=404, detail="not_found")
            history = await admin.user_history(session, target)
            wins = await admin.user_wins(session, target)
            return {
                "telegram_id": target.telegram_id,
                "first_name": target.first_name,
                "username": target.username,
                "referral_count": target.referral_count,
                "is_blocked": target.is_blocked,
                "entries_total": len(history),
                "wins_total": len(wins),
                "history": [
                    {"contest": c.title, "status": c.status} for _, c in history
                ],
                "wins": [{"contest": c.title} for _, c in wins],
            }


    @app.post("/api/admin/users/{telegram_id}/block")
    async def admin_block(telegram_id: int, payload: BlockIn, user: User = Depends(require_admin)):
        async with session_scope() as session:
            target = await admin.find_user(session, telegram_id)
            if target is None:
                raise HTTPException(status_code=404, detail="not_found")
            await admin.set_blocked(session, target, payload.blocked, payload.reason)
            await admin.audit(
                session,
                actor_id=user.id,
                action="user_block" if payload.blocked else "user_unblock",
                target_type="user",
                target_id=telegram_id,
            )
            return {"ok": True}

    @app.get("/api/admin/settings")
    async def admin_settings(user: User = Depends(require_admin)):
        async with session_scope() as session:
            values = await admin.all_settings(session)
            is_owner = await admin.is_owner(session, user.telegram_id)
            return {"settings": values, "is_owner": is_owner, "owner_only": sorted(admin.OWNER_ONLY_SETTINGS)}


    @app.post("/api/admin/settings")
    async def admin_update_settings(payload: SettingsIn, user: User = Depends(require_admin)):
        async with session_scope() as session:
            is_owner = await admin.is_owner(session, user.telegram_id)
            for key, value in payload.values.items():
                if key in admin.OWNER_ONLY_SETTINGS and not is_owner:
                    raise HTTPException(status_code=403, detail=f"owner_only:{key}")
                await admin.set_setting(session, key, value)
            await admin.audit(
                session,
                actor_id=user.id,
                action="settings_update",
                target_type="settings",
                payload={"keys": sorted(payload.values.keys())},
            )
            return {"ok": True}

    @app.get("/api/admin/audit")
    async def admin_audit(user: User = Depends(require_admin)):
        async with session_scope() as session:
            logs = await admin.recent_audit(session)
            return [
                {
                    "id": log.id,
                    "actor_id": log.actor_id,
                    "action": log.action,
                    "target": f"{log.target_type}:{log.target_id}" if log.target_type else None,
                    "created_at": log.created_at.isoformat() if log.created_at else None,
                }
                for log in logs
            ]

    return app


def _channel_link(channel: Channel) -> str:
    if channel.invite_link:
        return channel.invite_link
    if channel.username:
        return f"https://t.me/{channel.username}"
    return ""
