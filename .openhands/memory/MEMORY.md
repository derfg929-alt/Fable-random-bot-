# Project memory — Fable v2 Telegram bot

Index of durable, non-obvious facts learned while working in this repo.

## ⚠️ Two different layouts share this repo
- There is a **flat-layout** "Fable Randomizer" (contest bot + Mini App) at the repo
  root: `main.py, bot.py, admin.py, api.py, api_runtime.py, services.py, database.py,
  contests.py, referrals.py, draw.py, notifications.py, tasks.py, settings.py,
  mini_app.html, tests/`. Everything imports as top-level modules (no package).
  This is the one the TZ describes; it is what the tests under `tests/` cover.
- A **package-layout** variant (`app/…`, PostgreSQL, casino/AI/RP features) is
  described elsewhere in this file. Do not confuse the two: run `ls` first.

## Flat-layout project (contest bot + Mini App)
- 12 tables in `database.py`: users, channels, contests, contest_channels, entries,
  referrals, draw_logs, winners, notifications, admins, settings, audit_logs.
- DB init function is **`init_models()`** (NOT `init_db`). Tests set
  `DATABASE_URL=sqlite+aiosqlite:///:memory:` and rely on `Base.metadata.create_all`
  (migrations don't run on SQLite).
- Draw algorithm `fr-weighted-v1` in `draw.py`: Efraimidis–Spirakis weighted
  sample-without-replacement, `random()**(1/weight)`, seeded from the revealed
  seed. Commit-reveal: `commitment=sha256(seed)` written before selection, plus a
  participant snapshot + integrity hash so the draw is independently reproducible.
  `verify_draw(log, winner_ids=None)` re-checks commitment + integrity + re-derives
  winners. Redraw marks the previous COMPLETED log as `redrawn` (never deletes).
- Referral bonus is internal: `REFERRAL_RATE=0.0001` (0.01%/invite),
  `MAX_REFERRAL_BONUS=0.10` (10% cap). Never shown to users.
- API (`api.py`): module-level Pydantic models (`ContestIn`, `ContestPatchIn`,
  `ActionIn`, `BlockIn`, `SettingsIn`) — with `from __future__ import annotations`
  FastAPI cannot resolve models defined inside `create_app()`. Auth = Telegram
  `initData` HMAC verified server-side (`verify_init_data`), never trust client id.
  `_bot_client()` → `api_runtime.get_bot()`; tests must call `api_runtime.set_bot(...)`.
- Admin commands: `/admin /addchannel /channels /newcontest /editcontest /publish
  /finish /draw /participants /mycontests /stats /draws /finduser /block /unblock
  /notify /export /settings /set`.
- Run tests: `python -m pytest -q` (81 passing). Lint: `python -m pyflakes *.py tests/*.py`.

## Testing
- Tests run against in-memory SQLite (`tests/conftest.py` sets `DATABASE_URL=sqlite+aiosqlite:///:memory:`); the app itself targets PostgreSQL. `app/db/session.py` builds the async URL from whatever scheme is configured.
- Full suite: `python -m pytest -q` (~120 tests, ~12s warm). First run after a fresh env can be much slower (cold imports); re-run before blaming a change.
- New test files must `import pytest` and use `@pytest.mark.asyncio`; `pytest.ini` sets `asyncio_mode=auto`.

## Casino / games (`app/services/games_service.py`)
- All gambling games are tuned to **~90% RTP** (house edge ~10%). Payout multipliers (2.0x, x5, x15, 2.5 …) are kept stable on purpose so UI/tests don't break; only probabilities / payout coefficients change.
- To change a game's edge, adjust: slots `random.choices` weights, dice bot-reroll bias, coinflip win prob, mystery-box weights, crash win prob, shooting target probs, roulette payout multiplier. Verify with a pure-python Monte-Carlo replica — running the real async DB path for millions of rounds is far too slow.
- `play_slots(dice_value=...)` decodes Telegram dice (1..64) and must keep returning fixed results (x15 jackpot at 64, x5 triples) — asserted in `tests/test_slots_and_admin_fixes.py`.

## AI (`app/services/ai_service.py`, `ai_knowledge.py`)
- Provider priority: xAI Grok (`GROK_API_KEY`) then Groq (`GROQ_API_KEY`); no key → offline engine `build_offline_response`. Network/timeout/provider failures should fall back to the offline engine, not raw error strings.
- In handlers use `bot.id` (parsed locally from the token) instead of `await bot.get_me()` to avoid a network round-trip per message.

## Gotchas
- `select` must be imported at module level in handlers (`from sqlalchemy import func, select`). `handle_balance` in `economy_handlers.py` previously crashed with `NameError` because it was only imported inside a different function.
- Root-level legacy files (`Config.py`, `Models.py`, `Bank`, `Economy`, `Activity`, `Reputation`, `Transfer`, `Acievement`, `History`, `Player_servise.py`) are dead code — nothing imports them; real code lives under `app/`. `Player_servise.py` is not even valid Python.

## AI memory & engagement (2026-11)
- AI dialogue memory lives in `app/services/ai_memory_service.py` (model `AIConversation`, table `ai_conversations`, unique on `(user_id, chat_id)`). History is per-user **and per-chat**; handler passes `message.chat.id`, so in DMs that equals the user id. `ai_handlers.handle_ai_query` loads history, calls `ask_grok(..., chat_history=...)`, then `remember_exchange` in a separate `session_scope`. "забудь" clears it.
- `build_offline_response(prompt, user_name, context)` in `ai_knowledge.py` accepts a `context` string ("Игрок: …\nFable: …") so follow-ups ("а подробнее?") stay on topic via `_is_followup` / `_last_user_topic`.
- Background engagement worker (`app/tasks/background.py`) and owner broadcast are both rate-limited to **once per 24h**. Owner broadcast state is stored in `GlobalSetting` key `owner_broadcast_state` (not a dedicated table).

## Owner panel / broadcast
- Owner admin panel command: "понель"/"панель" (typo is intentional and is the primary trigger). DM-only (`ChatType.PRIVATE` from `aiogram.enums`); in groups it returns so it doesn't shadow group admin. Routers are registered owner-first in `app/main.py`.
- Owner broadcast command is `рассылка [текст]` (also a panel button `owner_menu_broadcast`); enforced via `get_broadcast_cooldown` / `mark_broadcast_sent` in `owner_service.py`.

## Money exploits fixed
- `launch_rocket_mission` previously granted coins with **no cost/cooldown** (infinite money). Now has `LAUNCH_COOLDOWN_SECONDS=1800`, recorded as a `GameSession(game_type="cosmodrome_launch")`.
- `bait:claim` callback credited 25 coins with no idempotency → now uses `idempotency_key=f"bait:{chat_id}:{message_id}"` so one drop = one claim.
- `market_maker.run_market_maker_cycle` cancelled its own orders each cycle without returning reserves → now refunds `coin_reserve`/`asset_reserve` before re-quoting (else liquidity drained to zero).

## Global ban
- `Player.is_globally_banned` is enforced in `MessageActivityAndSecurityMiddleware` (blocks text/caption commands; private-chat notice). Does not affect callbacks or chat-level admin.

## Per-chat (local) ban
- Chat bans are **per-chat**, stored as `ModerationCase(action="ban")` keyed by `(chat_id, target_id)`; there is no per-chat boolean on `Player`. Enforced in **both** middlewares: `MessageActivityAndSecurityMiddleware` deletes the banned user's messages in that chat, and `CallbackAnswerMiddleware` blocks their inline-button presses there (`_blocked_by_local_ban`). Admins (level ≥2) are exempt.
- Helpers live in `moderation_service.py`: `has_active_chat_ban(session, chat_id, target_id)` and `revoke_ban(...)` (level-3 gate). The `разбан` handler calls `revoke_ban`; before that fix the local ban case was never deactivated (only Telegram unban was called).
- Local bans must never touch `Player.is_globally_banned`, never delete player data (wallet/businesses/mining), and never propagate to other chats. Verified by `tests/test_chat_local_ban.py`.
- Test seeding gotcha: to make a moderator level-3 in a test chat, create the `Chat` row first (`get_or_create_chat`), then insert `ChatAdmin(chat_id, user_id, level=3, is_active=True)`; FK to `chats.telegram_id` otherwise fails silently on SQLite flush.

## Gotchas (more)
- `aiogram.types` has **no** `ChatType`; import from `aiogram.enums`. This caused real crashes in `owner_handlers` and `games_handlers`.
- `get_or_create_player` requires keyword-only `first_name` — `owner_handlers.handle_give_coins` crashed without it.
- `EconomyResult` has `transaction/actor_balance/recipient_balance/duplicate` — **no** `current_balance`.
- Bank interest in code is 1%/0.5%/0.2%/0.1% (docs/README/ai_knowledge previously claimed 5%/3%/1.5%/0.5% — now corrected).
- Tests that pass a `MagicMock` as the middleware `event` must use `spec=types.Message`, otherwise `isinstance(event, types.Message)` is False and the middleware is skipped.

## RP / fun / utilities (2026-11)
- New routers `rp_router` and `utility_router` (`app/handlers/rp_handlers.py`, `utility_handlers.py`) are registered in `main.py` **between** `games_router` and `social_moderation_router`. The AI router is last, so `KNOWN_COMMANDS` in `ai_handlers.py` was extended with the new keywords to stop the AI from swallowing them.
- RP actions live in `rp_service.RP_ACTIONS` (обнять/поцеловать/укусить/погладить/ударить) + stats table `player_rp_stats` (unique `(user_id, action)`, columns `counter`=received, `outgoing`=given). Handlers use `RpActionFilter` (an `aiogram.filters.BaseFilter` **class** — `BaseFilter` is NOT exported from `aiogram` top-level) so plain words in conversation don't trigger a reply; a target (reply / text_mention / @user / ≥6-digit ID) is required.
- `fun_service.py` is pure/no-DB (dice `NdM`, choose, truth-or-dare, fact/joke/quote, horoscope, stable hash-based compatibility). `reminder_service.py` + `background_reminder_worker` (30s, DMs the user) use table `reminders`; `note_service.py` uses `chat_notes` (unique `(chat_id, name)`, moderated at level ≥2).
- Migration `a1b2c3d4e5f6` adds these 3 tables (down_revision `f7c22c27a6f9`). Pre-existing migrations (`f7c22c27a6f9` drops a check constraint) do **not** run on SQLite — tests rely on `Base.metadata.create_all` instead.
- Test gotcha: `F.text.func(fn)` calls `fn` with the **text string**, not the message; use `F.text.func(lambda t: ...)` for text predicates and a custom `BaseFilter` when you need the whole `Message`.
