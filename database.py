import asyncio
import json
import os

from config import DB_PATH

_lock = asyncio.Lock()

DEFAULT_DATA = {"users": {}, "contests": {}, "admin_state": {}}


def _load_unlocked():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if not os.path.exists(DB_PATH):
        with open(DB_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_DATA, f, ensure_ascii=False, indent=2)
        return {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULT_DATA.items()}
    with open(DB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_unlocked(data):
    tmp_path = DB_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, DB_PATH)


async def load_data():
    async with _lock:
        return _load_unlocked()


async def save_data(data):
    async with _lock:
        _save_unlocked(data)


async def get_user(user_id):
    async with _lock:
        data = _load_unlocked()
        return data["users"].get(str(user_id))


async def update_user(user_id, **kwargs):
    async with _lock:
        data = _load_unlocked()
        uid = str(user_id)
        user = data["users"].setdefault(uid, {})
        user.update(kwargs)
        _save_unlocked(data)
        return user


async def get_contest(contest_id):
    async with _lock:
        data = _load_unlocked()
        return data["contests"].get(str(contest_id))


async def update_contest(contest_id, **kwargs):
    async with _lock:
        data = _load_unlocked()
        cid = str(contest_id)
        contest = data["contests"].setdefault(cid, {})
        contest.update(kwargs)
        _save_unlocked(data)
        return contest


async def add_participant(contest_id, user_id):
    async with _lock:
        data = _load_unlocked()
        cid = str(contest_id)
        uid = str(user_id)
        contest = data["contests"].get(cid)
        if contest is None:
            return False
        participants = contest.setdefault("participants", [])
        if uid in participants:
            return False
        participants.append(uid)
        _save_unlocked(data)
        return True
