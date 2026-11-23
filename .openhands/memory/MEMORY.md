# Project memory — Fable v2 Telegram bot

Index of durable, non-obvious facts learned while working in this repo.

## Restored runnable state (2026-11-23, HEAD 509a1d6)
- At this HEAD the repo was **broken**: `database.py` was the legacy JSON-file module
  while `bot.py/api.py/services.py/contests.py/draw.py/admin.py/notifications.py/
  referrals.py/tasks.py` all import SQLAlchemy models from it; `main.py` still started
  the legacy JSON handlers (`handlers/`, `config.py`, `captcha.py`, `keyboards.py`).
  `python main.py` could not work.
- Rewrote `database.py`: 12 models (users, channels, contests, contest_channels,
  entries, referrals, draw_logs, winners, notifications, admins, settings, audit_logs),
  `ContestStatus`/`DrawStatus`, async engine from `settings.DATABASE_URL`,
  `SessionFactory` (`expire_on_commit=False`), `session_scope()`, `init_models()`,
  `ping()`. SQLite `:memory:` uses `StaticPool` + `check_same_thread=False` so tests
  and API share one connection.
- Rewrote `main.py`: `run_bot` (delete_webhook + `start_polling(handle_signals=False)`,
  retry `POLLING_RETRY_SECONDS=15`, watcher calls `dp.stop_polling()` when
  `stop_event` is set — required because with `handle_signals=False` aiogram never
  stops by itself), `run_api` (uvicorn, `capture_signals=nullcontext`,
  `should_exit` watcher, retry), `tasks.worker`, own SIGINT/SIGTERM → `stop_event`,
  `build_dispatcher`/`setup_bot_menu` from `bot.py`, auto-detect `BOT_USERNAME` via getMe,
  `python main.py --check` (fatal only token/DB). Verified: Mini App serves with a fake
  token, `/api/health` 200, `/api/me` 401, SIGTERM logs clean stop.
- Deleted legacy `config.py`, `captcha.py`, `keyboards.py`, `handlers/`,
  `fable_randomizer_project.zip`. Runtime deps now `requirements.txt`; tests in
  `requirements-dev.txt`; added `README.md`, `Dockerfile`, `docker-compose.yml`,
  `Procfile`, `.dockerignore`.
- `settings.WEBAPP_URL` falls back to `MINI_APP_URL/PUBLIC_URL/RENDER_EXTERNAL_URL`
  (adds `https://`, strips trailing `/`); `PORT` falls back to `API_PORT`.
- `services.publish_contest` now sends **one** message per channel with an inline
  «🎉 Участвовать» URL button (previously a second plain-link message).
- Mini App: admin create form now supports dates + required-channel checkboxes
  (`channel_ids`), 403 `not_registered` hint, clipboard fallback, audit markup fix.
- Tests: **91 passed**; `python -m pyflakes *.py tests/*.py` clean.


## ⚠️ THIS repo = flat-layout "Fable Randomizer" (contest bot + Mini App)
The notes below about a package layout (`app/…`) belong to a DIFFERENT repo
(`Fable-v2-tg-bot-`) and are stale here. In THIS repo:
- Flat top-level modules: `main.py, bot.py, api.py, api_runtime.py, admin.py,
  services.py, database.py, contests.py, referrals.py, draw.py, notifications.py,
  tasks.py, settings.py, index.html, tests/`. Entry point `python main.py`.
- **Storage is already SQLite**: `sqlite+aiosqlite:///fable_randomizer.db`, async
  SQLAlchemy 2, 12 tables, `init_models()` → `create_all`. NOT JSON-based, nothing
  to migrate to SQLite.
- Fixed (2026-11): `api.py` read `mini_app.html` at import but the file was renamed
  to `index.html` (byte-identical) and deleted → `import api` raised FileNotFoundError
  → bot + Mini App both dead ("half the functions gone"). Now `_load_mini_app_html()`
  tries `index.html` then `mini_app.html`.
- Fixed (2026-11): `main.py` ran `asyncio.gather(run_bot, run_api, run_background)`
  with no isolation → any Telegram error killed the whole process (Mini App too).
  Now: `run_bot` retries every 15s (`handle_signals=False`), `run_api` sets
  `server.capture_signals = contextlib.nullcontext` + stop-watcher on `should_exit`,
  own SIGINT/SIGTERM handler → `stop_event`. Verified Mini App survives fake token;
  SIGTERM shuts down cleanly.
- Added (2026-11): bot command menu was missing entirely → `bot.USER_COMMANDS` +
  `bot.setup_bot_menu(bot)` (`set_my_commands` + `MenuButtonWebApp`), called from
  `main.main()`, best-effort.
- Tests: `python -m pytest -q` → **88 passed**; `pyflakes` clean. Startup tests in
  `tests/test_startup.py`.
- NOTE: this repo has **no JSON database** (checked 2026-11) — storage is SQLite only.
  The JSON DB (`data/bot_database.json`, `app/db/json_db.py`) lives in the *other*
  repo `derfg929-alt/Fable-v2-tg-bot-` and was removed there (see below).

## Fable-v2-tg-bot- : JSON DB removed (2026-11)
- That repo mirrored every table to `data/bot_database.json` on each commit
  (`session_scope`) and restored it at startup (`init_database`), duplicating SQLite.
  Removed the whole layer: deleted `app/db/json_db.py`, dropped restore/dump/clear from
  `app/db/session.py`, removed `Settings.json_database_path` (`JSON_DATABASE_PATH`) from
  `app/config.py` and legacy `Config.py`, updated `.env.example` + `README.md`.
- Tests: JSON round-trip tests became SQLite persistence checks;
  `tests/test_json_db_and_slot_balance.py` → `tests/test_slots_and_balance.py`;
  `conftest.py` no longer sets `JSON_DATABASE_PATH`. Suite still **272 passed**.
- Work committed on branch `remove-json-database` in the clone `/tmp/fablev2` and **pushed**
  to `derfg929-alt/Fable-v2-tg-bot-` (no PR opened — user explicitly said not to).
- IMPORTANT: `/tmp` is wiped between sessions (an env reset deleted `/tmp/fablev2` once).
  Re-clone with `git clone --depth 1 https://${GITHUB_TOKEN}@github.com/derfg929-alt/Fable-v2-tg-bot-.git fablev2`
  and re-apply; the apply script pattern is easy to re-run.

## Pushed branches (2026-11-17)
- `Fable-random-bot-`: branch `fix-mini-app-loading-and-startup-resilience` (bot fixes:
  api.py HTML loader, main.py resilient startup, bot.py menu, tests/test_startup.py). 88 passed.
- `Fable-v2-tg-bot-`: branch `remove-json-database` (JSON layer removed). 272 passed.
- Both branches now have PRs (user asked "делай PR"):
  - `Fable-random-bot-` PR **#2** (fix-mini-app-loading-and-startup-resilience → main), 4 files, +215/-13.
  - `Fable-v2-tg-bot-` PR **#45** (remove-json-database → main), 10 files, +39/-254.
  - Both opened as **drafts** (create_pr default `draft`); `mergeable_state: clean`. User had NOT
    explicitly asked to mark ready, so left as draft. Live smoke test passed before opening:
    `/api/health`→ok, `/`→HTML, `/api/contests`→401.
  - `create_pr` tool defaults to draft=True — pass `draft: false` if a ready-for-review PR is wanted.

## ⚠️ Repo layout (verify with `ls` — memory has been stale before)
- The tree is a **package layout** (`app/…`, aiogram 3, SQLAlchemy 2). Entry point
  `python -m app.main`; routers in `app/handlers/`, services in `app/services/`, models in
  `app/db/models.py`.
- **Storage is SQLite-only** (since 2026-11, after JSON removal): `data/bot_database.db`,
  default `DATABASE_URL=sqlite+aiosqlite:///data/bot_database.db`. PostgreSQL is optional
  (set `DATABASE_URL`). The old JSON mirror (`app/db/json_db.py`, `JSON_DATABASE_PATH`) was
  deleted — do not reintroduce it. For PostgreSQL portability, avoid PostgreSQL-only SQL
  (`insert().on_conflict_do_nothing`) — use portable read-then-write or savepoints.
- A previous session recorded a **flat layout** (`main.py`, `bot.py`, `admin.py`,
  `draw.py`, … at repo root, 12 tables, 81 tests). **Those files do not exist in the
  current tree** — do not rely on that note; re-check with `ls` and `git log`.
- Root-level legacy files (`Config.py`, `Models.py`, `Bank`, `Economy`, `Activity`,
  `Reputation`, `Transfer`, `Acievement`, `History`, `Player_servise.py`, `Sesiun.py`)
  are dead code — nothing imports them.

## Testing
- Tests run against in-memory SQLite (`tests/conftest.py` sets `DATABASE_URL=sqlite+aiosqlite:///:memory:`). `app/db/session.py` builds the async URL from whatever scheme is configured.
- Full suite: `python -m pytest -q` — currently **272 tests** (~45s) in Fable-v2-tg-bot- (`/tmp/fablev2`); **88** in the flat-layout Fable-random-bot-. Tests rely on `Base.metadata.create_all`, not Alembic migrations (SQLite can't run some migrations).
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

## Fable Randomizer (2026-11)
- New subsystem: contests/draws/referrals/notifications + Mini App + FastAPI. Files: `app/services/{randomizer_settings,audit_service,referral_service,contest_service,draw_service,notifications_service}.py`, `app/handlers/{randomizer_handlers,randomizer_admin_handlers}.py`, `app/api.py`, `mini_app.html`, migration `c3d4e5f6a7b8` (down_revision `f7c22c27a6f9`).
- Routers registered **first** in `create_dispatcher()` (`randomizer_router`, then `randomizer_admin_router`) before all existing routers; AI router last. `KNOWN_COMMANDS` in `ai_handlers.py` extended so the AI doesn't swallow `/contests`, `/join`, `/myref`, `/profile`, `/history`, `/winners`, `/help`.
- `/start` greeting changed to the Randomizer text (old greeting assertion in `tests/test_commands_and_greeting.py` was updated). Registration is idempotent per Telegram ID; referral deep link payload is `ref_<inviter_id>`.
- Bot username for referral links: stored in `settings` table key `randomizer` (field `bot_username`); auto-detected at startup in `main._ensure_bot_username` via `getMe`, falls back to `BOT_USERNAME` env. Only admins see the technical "username not configured" error; players get a generic message.
- Referral bonus = 1 unit per invite = 0.01%, cap `max_referral_bonus_units` = 1000 (10%). Self-referral/duplicate/second-inviter blocked by unique constraint on `referrals.invited_id`.
- Draw algorithm in `draw_service.run_draw` (v1.1): filters (registered/participating/not banned/no dupes), weighted random without replacement, non-zero base weight, bonus only boosts odds. Every draw persisted to `draw_logs` (participants, weights, seed, `algorithm_version`, round, reason). Re-draw keeps prior rounds (`winners.draw_round` unique `(contest_id,user_id,draw_round)`). `verify_draw(log)` re-runs from the stored seed (fairness check); `run_draw(exclude_previous_winners=True)` redraws among non-winners. Admin buttons: `adm_verify_*`, `adm_redraw_*` (menu) → `adm_redrawall_*` / `adm_redrawnew_*`. Helper `_render_admin_contest(callback, contest_id)` renders the admin contest card (do **not** call `on_view_contest_admin` directly — it parses `callback.data`).
- Notification idempotency: `notifications` unique `(user_id, event_key)`; each type toggled via `randomizer_settings.set_notification_enabled`. Types list in `randomizer_settings.NOTIFICATION_TYPES`. `background_randomizer_worker` (300s) sends start/ending/finish events.
- Mini App/API security: `app/api.py` validates Telegram `initData` with HMAC-SHA256 using secret `HMAC_SHA256("WebAppData", bot_token)`; TTL via `INITDATA_TTL_SECONDS`. Telegram ID is taken **only** from validated data (header `X-Init-Data`). API runs alongside the bot on `API_HOST:API_PORT`; `/` serves `mini_app.html`.
- Gotcha: the admin contest wizard (`ContestWizard` FSM) needs `_WIZ_FIELDS` tuples `(state_name, data_key, label, next_state, next_prompt)` — state names and data keys differ (e.g. `image` → `image_url`). The final entry's `next_state` must be `None` so it shows the preview instead of `answer(None)`.
- Gotcha: in admin callbacks, `callback.message.from_user` is the **bot**, not the admin — pass `actor_id=callback.from_user.id` explicitly (e.g. `_persist_contest`).
- Tests: `tests/test_randomizer.py`, `tests/test_randomizer_api.py`, `tests/test_randomizer_routing.py` (dispatcher-level: menu buttons, admin gating, wizard E2E, join flow, admin draw/verify/redraw callbacks). API endpoints can be called directly as coroutines. Full suite: 212 passed.

## Fable Randomizer — user-owned channel contests (2026-11)
- Channel owners/admins can create contests for their own channel without the Randomizer admin. New router `user_contest_router` (`app/handlers/user_contest_handlers.py`) registered in `create_dispatcher()` **right after** `randomizer_admin_router`, before `owner_router`. New service `app/services/channel_service.py`.
- Ownership is verified server-side via `bot.getChatMember` (`channel_service.is_channel_admin`: status must be `creator`/`administrator`) — cannot be faked. `channel_service.ensure_channel` upserts the `Channel` row and stamps `owner_id`.
- New columns: `contests.owner_id`, `contests.source_channel_id` (FK `channels.id` SET NULL), `channels.owner_id` (migration `d4e5f6a7b8c9`, down_revision `c3d4e5f6a7b8`). SQLite schema-sync for existing DBs lives in `app/db/session.py::_migrate_legacy_schema` (PRAGMA table_info + ALTER TABLE).
- **Owner cannot win**: `draw_service._eligible_participants` adds `contest.owner_id` to the exclude set (algorithm bumped to `1.2`); `contest_service.join_contest` raises `OwnerCannotJoinError` if the owner tries to join. Handled in both the bot join flow and `app/api.py`.
- Owner statistics: `contest_service.contest_statistics` / `contest_join_timeline` compute in **Python** (SQLite-safe) — per-hour (UTC), per-day, per-weekday, peak hour; plus active/left totals, referred participants, conversion, duration. `weekday_labels()` helper. Surfaced in bot (`uc_stats_*`) and API `/api/contests/{id}/stats` (owner or admin only, else 403).
- New bot callbacks: `uc_my_contests`, `uc_create`, `uc_view_*`, `uc_stats_*`, `uc_parts_*`, `uc_finish_*`, `uc_cancel_*`, `uc_draw_*`, wizard `uc_wiz_*`. New commands `/mycontests`, `/createcontest`. **Removed** the admin router's `/mycontests` command (it blocked the user flow); the admin `adm_mycontests` button still works. New menu buttons: `📣 Мои конкурсы`, `➕ Создать конкурс`; `KNOWN_COMMANDS` extended (incl. bare `📣`/`➕` first-word guards).
- `/start` now builds the greeting with stats + the user's referral link automatically and sends an inline "quick actions" menu. Contest deep link payload `c_<id>` (plus existing `ref_<id>`).
- Gotcha: in dispatcher-level tests with `AsyncMock` bots, define `get_chat_member` with keyword params (`async def _gcm(chat_id=None, user_id=None, **kwargs)`) — aiogram calls it with `chat_id=`/`user_id=`.
- Mini App: new nav "Мои" (`renderMyContests` → `/api/my-contests`, `openStats` → `/api/contests/{id}/stats`).
- Tests: `tests/test_user_contests.py` (13 tests). Full suite: **225 passed**.

## Randomizer growth mechanics (2026-11)
- `contests.settings_json` is **SQLAlchemy `JSON`** (not `JSONB`) — user requires literal JSON. Schema/defaults/normalization live in `app/services/contest_settings.py` (`normalize_settings`, `read_settings`, `toggle`, `TOGGLE_ORDER`, `TOGGLE_LABELS`). Never add separate columns for toggles.
- Ticket ledger: `app/services/ticket_service.py` — idempotent via unique `ticket_logs(contest_id,user_id,reason_key)`; `entries.tickets` = materialized sum (min 1). Reasons: base/referral/code_word/sponsor_task/manual.
- Referral = action: `contest_referral_service.credit_referral_for_user` only credits after invited entry has `conditions_met` (set in `join_contest` when no code-words/tasks, or via `mark_conditions_met`). Unique `contest_referrals(contest_id, referred_id)`.
- Leaderboard materialized: `app/services/leaderboard_service.py` (`increment_invited` on credit, `get_top`, `refresh_leaderboard`).
- Draw v1.3 (`draw_service.run_draw`): weight = `BASE_WEIGHT(10000) × tickets` + capped referral bonus; numeric seed `str(secrets.randbits(64))` + `salt`; `compute_participants_hash(participants,weights,salt)` SHA-256; `verify_draw` re-runs from seed. Reserves via `picked[target:]`; `is_reserve`/`status`/`notified_at` on winners. `get_latest_winners` excludes `status='transferred'`.
- Reserve timeout flow: `background_randomizer_worker` calls `crefs.process_contest_referrals` for active contests and `_process_reserve_timeouts` (uses `settings.reserve_timeout_hours`, default 24) → `transfer_to_reserve` + `notify_prize_transferred`.
- Bot callbacks: participant `rz_my_tickets`, `rz_codeword_*`, `rz_task_*`, `rz_share_*`, `rz_parts_*`, `rz_top_*`, `rz_confirm_*`; admin `adm_set_*` (settings step in wizard) + sources/export/verify. Owner (`user_contest_handlers`): `uc_set_*` settings step, `uc_sources_*`, `uc_export_*` (uses `randomizer_admin_handlers._build_participants_csv`), `uc_verify_*`.
- API (`app/api.py`): `/api/contests/create`, `/code-word`, `/sponsor-task`, `/{id}/participants|leaderboard|sources|export|verify`, `/verifier`; `/` and `/verifier` serve `mini_app.html` / `verifier.html` from repo root. `verifier.html` embeds MT19937 matching CPython `random.Random(int)`.
- Migration `e5f6a7b8c9d0` (down_revision `d4e5f6a7b8c9`); SQLite legacy sync in `app/db/session.py::_migrate_legacy_schema`.
- Tests: `tests/test_randomizer_growth.py` (tickets, code words, sponsor tasks, referral=action, leaderboard/sources, draw transparency/reserves, timeout transfer); API tests appended to `tests/test_randomizer_api.py`; routing tests appended to `tests/test_randomizer_routing.py`. Full suite: **244 passed**.
- Gotcha: `starlette.testclient` needs `httpx` (not installed here) — call FastAPI handlers directly as coroutines in tests instead.

## Конкурсы может создавать любой (2026-11)
- PR #52 сделал создание конкурсов открытым, но остались гейты: корневой `Keyboards.py` скрывал «👑 Создать конкурс» от не-админов, а корневой `handlers/admin.py` отказывал не-админам (`_is_admin`) и требовал канал, где бот админ.
- Исправлено: `Keyboards.main_menu` всегда показывает BTN_CREATE; из `handlers/admin.py` удалён `_is_admin`, шаг канала принимает «-»/«нет»/«без канала»/«пропустить»/«skip»/«—» → `channel=None`, публикация вызывается только если канал указан (иначе текст «🌍 Конкурс без канала»).
- JSON-бот: `keyboards.channel_keyboard()` с кнопкой «🌍 Без канала (для всех)» (callback `skip_channel`, добавлен в `ADMIN_CALLBACKS`), в `_publish_contest` предупреждение только при наличии `channel_id`; итоги (`handlers/draw.py::_publish_results`) теперь уходят организатору (`owner_id`) в ЛС, а не «свяжитесь с администратором».
- Тексты: `app/api.py` help, README-заголовок «Конкурсы: создать может любой», fable README, приветствие JSON-бота упоминают создание конкурса любым пользователем.
- Тесты: +5 в `tests/test_random_bot_json.py` (skip_channel callback/игнор вне шага, корневой мастер без админа и без канала, BTN_CREATE всем, уведомление организатору). Всего **319 passed**.
- PR #53 (ветка `fix/contest-creation-for-everyone`) — доделывает PR #52 для корневого бота.
- Важно: гейт по правам остаётся **только на публикацию в канал** (`_validate_channel`, `chs.is_channel_admin`) — это по ТЗ.
- Незакрытый пробел: в JSON-боте нет диплинка `?start=c_<id>` (только `ref_`), поэтому конкурс без канала нельзя расшарить прямой ссылкой — только через «🎁 Активные розыгрыши».

## GitHub access (2026-11)
- `GITHUB_TOKEN` (`ghu_…`, identity `derfg929-alt`) initially behaved as **read-only** for
  `derfg929-alt/Fable-v2-tg-bot-` (all writes 403: git push, `POST /git/refs`,
  `PUT /contents/…`, `POST /pulls`, `gh pr create`). It later **began working for writes** —
  a `git push` and `create_pr` both succeeded (PR #37). So the token is refreshed/rotated by
  the platform over time: if writes 403, wait/retry with the current token rather than
  concluding it is permanently read-only.
- The credential helper `~/.openhands/.openhands/git-credential-helper` needs the argv word
  `get` (git passes it; a manual `sh helper github` does not) and returns the same token.
- `create_pr` tool works and returns the PR URL. No PR template exists in this repo.

## Startup / hosting & Mini App management (2026-11)
- **SIGTERM/exit-1 root cause**: two bugs. (1) `_maybe_start_api` bound `API_PORT` (8080), ignoring the host `$PORT` (Heroku/Replit/Amvera/Railway), so the platform killed the container. (2) uvicorn's `Server.serve` unconditionally captures SIGTERM/SIGINT via `capture_signals()` and re-raises after shutdown, fighting aiogram's handler → "Получен сигнал SIGTERM" + process death; also if Telegram polling raised, the whole process exited and took the Mini App ("что-то пошло не так") with it.
- Fixes: `Settings.effective_api_port` (`$PORT` wins over `API_PORT`), `effective_mini_app_url` (`MINI_APP_URL` → `PUBLIC_URL` → `RENDER_EXTERNAL_URL`, auto-prepends `https://`). `app/main.py`: API starts **before** polling; `server.capture_signals = contextlib.nullcontext`; own `loop.add_signal_handler(SIGINT/SIGTERM)` driving a `stop_event` + `dp.stop_polling()`; resilient polling retry loop (15s, keeps Mini App up). aiogram installs its signal handlers on each `start_polling`, so pass `handle_signals=False` or they clobber yours (and no-op while polling is stopped).
- **Gotcha**: aiogram's `Dispatcher._signal_stop_polling` returns early when `_running_lock` isn't held — signals are effectively ignored during retry gaps. `stop_polling()` also raises if polling isn't running, so guard with `dp._running_lock.locked()`.
- Bot menu already has "🚀 Открыть Mini App"; `_mini_app_url` (randomizer_handlers) now uses `effective_mini_app_url`. `_setup_bot_menu` sets `MenuButtonWebApp`.
- Mini App owner management added: API `PATCH /api/contests/{id}`, `PUT /{id}/settings`, `POST /{id}/finish|cancel|draw|delete|archive`, `GET /{id}/results`; all gated by `_require_contest_owner` (owner or Randomizer admin, else 403). UI: `openManage`, `toggleSetting`, `editContest`, `runDraw`, `openResults`, `finish/cancel/archive/deleteContest`, `openVerify`; home + "Мои" got create/manage buttons.
- Tests: `tests/test_startup_and_miniapp.py` (port/URL resolution, uvicorn signal capture disabled, menu button, Mini App markup); owner-endpoint tests appended to `tests/test_randomizer_api.py`. Full suite: **262 passed**.
- Test gotcha: `Settings` reads a repo-root `.env` (model_config env_file); in unit tests pass `Settings(_env_file=None, ...)` to isolate. `conftest.py` sets `DATABASE_URL=sqlite+aiosqlite:///:memory:` — so for a real browser E2E use a file DB + a `/test` route injecting a valid `Telegram.WebApp` stub.
- `initData` E2E: HMAC is correct; generate + validate in the **same** process (a stale `/tmp/init.txt` from an earlier run gives "Неверная подпись initData").

## Mini App audit + rename (2026-11)
- **`mini_app.html` renamed to `index.html`** (git mv). Served via `app/api.py::_serve_static_html(*names)` — `/` tries `index.html` then `mini_app.html` (fallback), `/verifier` serves `verifier.html`. Any deploy config must now point at `index.html`.
- Fixed Mini App bugs: native `alert/confirm/prompt` **are not supported in Telegram WebView** → replaced with own `_modal`/`uiAlert`/`uiConfirm`/`uiPrompt` (CSS `.modal*` added). `api()` now wraps `fetch` (network error message), returns `{ok:false,message}` action bodies, throws on real errors; a global `unhandledrejection` listener toasts errors so onclick actions don't die silently. `copyLink` falls back to a hidden textarea + `execCommand`. All render `.map/.length` uses wrapped in `arr()`. Empty `initData` → "открой в Telegram" hint instead of a 401. Create-form validates date format.
- **Participants search bug (real)**: API filtered `q` *after* pagination → only found page 1. Now `cs.list_participants_public(..., q=)` + `cs.participants_count(..., q=)` filter in SQL before paging (outerjoin Player); Mini App got pager + search box. Test: `test_api_participants_search_finds_any_page`.
- **`verifier.html` MT19937 matches CPython `random.Random(int)` exactly** (verified over seeds incl. >2^64) — no bug there.
- E2E: ran uvicorn on a file DB, curled `/`, `/verifier`, `/api/health`, `/api/me`, create→join→participants(q)→draw→verify. Node+jsdom harness (`/tmp/harness2-4.mjs`) drives all views incl. empty/500 responses and the modal callbacks. Full suite: **274 passed**.
