# Fable Randomizer

Телеграм-бот для проведения розыгрышей и конкурсов в **любом количестве каналов**
плюс Telegram Mini App (веб-приложение) с тем же функционалом.

Один бот обслуживает сразу несколько каналов: каналы добавляются в панель,
у конкурса выбираются обязательные каналы, а участие возможно только после
подписки на них. Розыгрыш проходит по прозрачному взвешенному алгоритму
(commit-reveal), результат можно проверить.

## Возможности

**Для участников**

- `/start` — регистрация, приветствие и меню;
- `/contests` — активные конкурсы с кнопкой «Участвовать»;
- участие по кнопке, по команде `/join ID` и по прямой ссылке `?start=contest_ID`;
- проверка подписки на обязательные каналы (через `getChatMember`);
- реферальная ссылка `?start=ref_ID`: друг приносит пригласившему небольшой бонус
  к шансам (внутренний параметр, нигде не показывается в процентах);
- `/profile`, `/history`, `/winners`, `/myref`, `/help`.

**Для администраторов**

- `/admin` — панель со статистикой, конкурсами, каналами, пользователями,
  розыгрышами, настройками и журналом действий;
- `/addchannel @channel` или `/addchannel -1001234567890` — подключить канал
  (бот должен быть администратором канала);
- `/newcontest` — пошаговый мастер: название, описание, приз, картинка,
  число победителей, даты, обязательные каналы;
- `/editcontest ID поле значение` — правка условий;
- `/publish ID` — публикация анонса в выбранных каналах;
- `/finish ID` — завершить конкурс и провести розыгрыш;
- `/draw ID [кол-во] [причина]` — повторный розыгрыш (история попыток сохраняется);
- `/participants ID`, `/export ID`, `/draws`, `/mycontests`, `/stats`;
- `/finduser ID`, `/block ID причина`, `/unblock ID`;
- `/settings`, `/set ключ значение`, `/notify тип 0|1`, `/audit`.

**Mini App**

- открывается кнопкой «Открыть Mini App» (нужен публичный HTTPS-адрес);
- список конкурсов, участие, профиль, история, победители;
- админ-раздел: создание/редактирование конкурсов, публикация, завершение,
  архив, участники, отчёт, настройки, журнал, блокировка пользователей.

**Надёжность**

- бот и веб-сервер работают независимо: падение Telegram-поллинга не роняет
  Mini App и наоборот;
- автоперезапуск поллинга и веб-сервера при ошибках;
- уведомления дедуплицируются в БД (пользователь не получит одно и то же дважды);
- корректное завершение по `SIGINT`/`SIGTERM`;
- SQLite «из коробки», PostgreSQL — по желанию.

## Быстрый старт

Нужен Python 3.10+ (проверено на 3.13).

```bash
git clone https://github.com/derfg929-alt/Fable-random-bot-.git
cd Fable-random-bot-

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # заполните BOT_TOKEN и OWNER_IDS
python main.py --check           # проверка конфигурации и создание таблиц
python main.py                   # запуск бота, Mini App и фонового воркера
```

Минимально достаточно указать в `.env`:

```env
BOT_TOKEN=123456:AA...
OWNER_IDS=ваш_telegram_id
```

`BOT_USERNAME` можно не указывать — бот определит его сам при старте.
Для кнопки Mini App нужен `WEBAPP_URL=https://...` (см. ниже).

### Подготовка бота в @BotFather

1. `/newbot` → получите токен → `BOT_TOKEN`.
2. Команды и кнопку меню бот публикует сам при старте.
3. Mini App: разместите приложение на HTTPS-домене и укажите этот адрес в
   `WEBAPP_URL`. Для локальной разработки удобен туннель:
   ```bash
   # пример с cloudflared; подойдёт и ngrok
   cloudflared tunnel --url http://localhost:8000
   ```
   Полученный `https://...` адрес пропишите в `WEBAPP_URL` и перезапустите бота.

### Первый конкурс

1. Добавьте бота администратором в канал (право «Публиковать сообщения»).
2. В личке с ботом: `/addchannel @my_channel`.
3. `/newcontest` → заполните шаги → выберите каналы → «✅ Создать».
4. `/publish ID` — анонс уйдёт в каналы, конкурс станет активным.
5. `/finish ID` — бот выберет победителей и опубликует итоги.

Участвовать можно из каналов (ссылка в анонсе), из бота и из Mini App.

## Конфигурация

Все параметры читаются из переменных окружения или `.env` (см. `.env.example`).

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `BOT_TOKEN` | — | токен от @BotFather (обязательно) |
| `BOT_USERNAME` | автодетект | username без `@`, для реферальных ссылок |
| `OWNER_IDS` | — | Telegram ID владельцев через запятую (нельзя снять через панель) |
| `WEBAPP_URL` | — | публичный HTTPS-адрес Mini App |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | адрес веб-сервера (`PORT` подхватывается у PaaS) |
| `DATABASE_URL` | `sqlite+aiosqlite:///fable_randomizer.db` | БД; для PostgreSQL `postgresql+asyncpg://...` |
| `DB_ECHO` | `false` | логировать SQL |
| `TIMEZONE` | `Europe/Moscow` | часовой пояс дат конкурсов |
| `INIT_DATA_MAX_AGE` | `86400` | срок жизни подписи Mini App, сек |
| `REFERRAL_RATE` | `0.0001` | бонус к весу за друга (0.01%) |
| `MAX_REFERRAL_BONUS` | `0.10` | максимальный бонус (10%) |
| `NOTIFICATION_MIN_GAP_SECONDS` | `0` | антифлуд уведомлений |

Публичные URL также подхватываются из `MINI_APP_URL`, `PUBLIC_URL`,
`RENDER_EXTERNAL_URL`, если `WEBAPP_URL` не задан.

## Запуск в Docker

```bash
cp .env.example .env      # заполните BOT_TOKEN и OWNER_IDS
docker compose up -d --build
```

SQLite-файл хранится в volume `bot-data` (`/data/fable_randomizer.db`).
Порт `8000` проброшен наружу; для Mini App поставьте перед контейнером
HTTPS-прокси (Caddy, nginx, Cloudflare Tunnel) и укажите адрес в `WEBAPP_URL`.

## Деплой на PaaS

Подходит любой хостинг, умеющий запускать Python-процесс:

- **Render / Railway / Amvera / Heroku** — `Procfile` уже есть (`web: python main.py`),
  переменные окружения задаются в панели, `PORT` подхватывается автоматически;
- **systemd** — пример unit-файла:

```ini
[Unit]
Description=Fable Randomizer
After=network.target

[Service]
WorkingDirectory=/opt/fable-random-bot
EnvironmentFile=/opt/fable-random-bot/.env
ExecStart=/opt/fable-random-bot/.venv/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Для нескольких тысяч участников и нескольких инстансов используйте PostgreSQL
(`pip install asyncpg` и `DATABASE_URL=postgresql+asyncpg://...`).

## Как устроен розыгрыш

- У каждого участника базовый вес `1.0`; за приглашённых друзей добавляется
  `REFERRAL_RATE` за друга, но не больше `MAX_REFERRAL_BONUS`.
- Победители выбираются без повторов взвешенным алгоритмом
  Efraimidis–Spirakis (`u ** (1/weight)`, наибольшие ключи побеждают).
- Перед выбором сохраняется commitment `sha256(seed)`, после — раскрывается seed.
  В `draw_logs` пишутся снимок участников, seed, победители и общий `integrity_hash`.
- `verify_draw()` пересчитывает результат из сохранённого seed и снимка, поэтому
  подмену участников или победителей видно.
- Повторный розыгрыш не удаляет прошлый: старая запись получает статус `redrawn`.

## HTTP API (используется Mini App)

Все запросы требуют заголовок `X-Telegram-Init-Data` с подписанным Telegram
`initData` (проверяется HMAC по токену бота).

- `GET /` — Mini App, `GET /api/health`;
- `GET /api/me`, `/api/contests`, `/api/contests/{id}`, `/api/history`, `/api/winners`;
- `POST /api/contests/{id}/join`;
- админские: `/api/admin/stats`, `/api/admin/contests` (GET/POST/PATCH),
  `/{id}/publish|finish|cancel|archive|participants|report`,
  `/api/admin/channels`, `/api/admin/users/{tg}`, `.../block`,
  `/api/admin/settings`, `/api/admin/audit`, `/api/admin/draws`.

## Структура проекта

```
main.py            запуск бота + API + фонового воркера, graceful shutdown
bot.py             все хендлеры aiogram (пользователи и админ-панель)
api.py             FastAPI: Mini App и HTTP API, проверка initData
database.py        SQLAlchemy 2.0 async-модели, engine, session_scope
settings.py        конфигурация из окружения/.env
admin.py           права, настройки, статистика, аудит, блокировки
contests.py        каналы, конкурсы, проверка подписок, участие
draw.py            взвешенный розыгрыш и проверка честности
services.py        общие сценарии для бота и API
notifications.py   типизированные уведомления с дедупликацией
referrals.py       регистрация и реферальная система
tasks.py           фоновый воркер (старт/напоминание/автозавершение)
index.html         Mini App (одностраничное приложение)
tests/             pytest-набор (91 тест)
```

## Тесты

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Тесты используют in-memory SQLite и фейковый Telegram-клиент, реальные
сервисы и хендлеры выполняются как в продакшене.

## Безопасность

- подпись `initData` проверяется на сервере, id пользователя из клиента не доверяется;
- админские API-методы дополнительно проверяют права в БД;
- `OWNER_IDS` из окружения всегда владельцы и не могут быть сняты из панели;
- не коммитьте `.env` — он уже в `.gitignore`;
- блокировка пользователя не удаляет его историю и не влияет на другие чаты.
