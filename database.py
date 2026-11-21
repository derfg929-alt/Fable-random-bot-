import asyncio
import json
import os

DB_PATH = "/app/data/database.json"

CONTEST_TYPES = {"simple", "raffle", "draw", "skill", "referral"}

_users_lock = asyncio.Lock()
_contests_lock = asyncio.Lock()


async def load_data():
    """Load data from JSON database file."""
    if not os.path.exists(DB_PATH):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        with open(DB_PATH, "w", encoding="utf-8") as f:
            json.dump({"users": {}, "contests": {}, "admin_state": {}}, f, ensure_ascii=False, indent=2)
    with open(DB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


async def save_data(data):
    """Atomically save data to JSON database file using .tmp and os.replace."""
    tmp_path = DB_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, DB_PATH)


async def get_user(user_id: int):
    """Get user data, ensuring captcha_passed and captcha_answer fields exist."""
    async with _users_lock:
        data = await load_data()
        users = data.setdefault("users", {})
        user_id_str = str(user_id)
        if user_id_str not in users:
            users[user_id_str] = {"captcha_passed": False, "captcha_answer": None}
        data["users"] = users
        await save_data(data)
        return users[user_id_str]


async def update_user(user_id: int, **kwargs):
    """Update user data fields."""
    async with _users_lock:
        data = await load_data()
        users = data.setdefault("users", {})
        user_id_str = str(user_id)
        if user_id_str in users:
            users[user_id_str].update(kwargs)
            data["users"] = users
            await save_data(data)


async def get_contest(contest_id: int):
    """Get contest data by ID."""
    async with _contests_lock:
        data = await load_data()
        contests = data.setdefault("contests", {})
        contest_id_str = str(contest_id)
        if contest_id_str in contests:
            return contests[contest_id_str]
        return None


async def update_contest(contest_id: int, **kwargs):
    """Update contest data fields."""
    async with _contests_lock:
        data = await load_data()
        contests = data.setdefault("contests", {})
        contest_id_str = str(contest_id)
        if contest_id_str in contests:
            if "contest_type" in kwargs:
                if kwargs["contest_type"] not in CONTEST_TYPES:
                    raise ValueError(f"Invalid contest_type: {kwargs['contest_type']}")
                contests[contest_id_str]["contest_type"] = kwargs.pop("contest_type")
            contests[contest_id_str].update(kwargs)
            data["contests"] = contests
            await save_data(data)


async def add_participant(contest_id: int, user_id: int):
    """Add a participant to a contest."""
    async with _contests_lock:
        data = await load_data()
        contests = data.setdefault("contests", {})
        contest_id_str = str(contest_id)
        if contest_id_str not in contests:
            return False
        if user_id_str := str(user_id) not in contests[contest_id_str].get("participants", []):
            contests[contest_id_str].setdefault("participants", []).append(user_id_str)
            data["contests"] = contests
            await save_data(data)
            return True
        return False
