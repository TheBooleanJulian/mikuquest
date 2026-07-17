# ⚔️ MiguQuest Bot

> Your external RAM. A Telegram-native gamified task manager built for solopreneurs.

Every message or forward becomes a quest. Complete quests, earn XP, level up. Daily 6AM SGT debrief keeps you oriented.

---

## Features

- **Auto-capture** — any typed or forwarded message becomes a quest instantly
- **Kanban board** — `/board` shows TODO / IN PROGRESS / DONE TODAY with inline buttons
- **One-tap clearing** — inline buttons on every quest card, no friction
- **Priority system** — `!c` `!h` `!m` `!l` prefixes, plus tap-to-change
- **Auto-tagging** — keyword-based context tags: `#accurova` `#dev` `#tutoring` `#personal` `#busking`
- **XP + Levels** — Critical=40xp, High=30, Medium=20, Low=10. Level up every 200 XP
- **Streaks** — consecutive days with ≥1 quest cleared
- **Daily 6AM SGT Debrief** — yesterday's completions + today's active quests
- **Web app** — `/web` DMs a one-time login link to a browser dashboard that mirrors the same data as the bot
- **Share links** — `/share board|today|week|stats`, or the 🔗 buttons on the board/quest cards, generate public read-only links

---

## Commands

| Command | Description |
|---|---|
| `/q <text>` | Log a quest |
| `/q !h Fix the bug` | Log with priority (`!c` `!h` `!m` `!l`) |
| `/board` | Kanban board |
| `/done <id>` | Mark quest done (or tap — no args shows pick list) |
| `/begin <id>` | Move to In Progress |
| `/drop <id>` | Drop a quest |
| `/today` | Active quests with quick-clear buttons |
| `/tag #accurova` | Board filtered by tag |
| `/stats` | XP, level, streak, totals |
| `/clear` | Archive all done quests |
| `/web` | One-time login link to the web dashboard |
| `/share board` | Public read-only share link (`board`\|`today`\|`week`\|`stats`) |

---

## Setup

### 1. Create your bot
Talk to [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` → copy the token.

### 2. Local run
```bash
git clone https://github.com/TheBooleanJulian/miguquest-bot
cd miguquest-bot
pip install -r requirements.txt
cp .env.example .env
# Edit .env — add BOT_TOKEN, DATABASE_URL (a local/dev Postgres), WEB_BASE_URL, SESSION_SECRET
python bot.py
```

To also run the web app locally:
```bash
uvicorn web.app:app --reload --port 8000
# then set WEB_BASE_URL=http://localhost:8000 in .env
```

If you have existing data in the old `data/miguquest.db` SQLite file, copy it into
Postgres once with:
```bash
DATABASE_URL=... SQLITE_PATH=data/miguquest.db python scripts/migrate_sqlite_to_pg.py
```

### 3. Deploy to Zeabur
The bot and the web app share one Postgres database and run as **two services**
in the same Zeabur project:

1. Push to GitHub
2. New project → add a **Postgres** plugin (Zeabur managed database)
3. **Bot service** — Deploy from GitHub → select `miguquest-bot`
   - `start_command: python bot.py` (default `zbpack.json`)
   - Env vars: `BOT_TOKEN`, `DATABASE_URL` (reference the Postgres plugin), `WEB_BASE_URL`
4. **Web service** — add a second service from the same GitHub repo
   - Override the start command to `uvicorn web.app:app --host 0.0.0.0 --port $PORT`
   - Env vars: `DATABASE_URL` (same Postgres plugin), `SESSION_SECRET`, `WEB_BASE_URL` (this service's own public URL)
5. Deploy both — done

---

## XP & Levels

| Priority | XP |
|---|---|
| 🔴 Critical | +40 |
| 🟠 High | +30 |
| 🟡 Medium | +20 |
| 🟢 Low | +10 |

| Level Range | Title |
|---|---|
| 1–4 | Scout |
| 5–9 | Apprentice |
| 10–14 | Journeyman |
| 15–19 | Specialist |
| 20–24 | Architect |
| 25–29 | Commander |
| 30+ | Overlord |

---

## Auto-Tags

Keywords in your quest text are matched to tags automatically:

| Tag | Keywords |
|---|---|
| `#accurova` | accurova, shoot, photobooth, client, booking, invoice, camera… |
| `#dev` | bot, code, deploy, zeabur, github, bug, fix, react, python… |
| `#tutoring` | angela, denzel, jessica, pakorn, poon, rin, theethus, lesson, math… |
| `#personal` | cosplay, miku, figure, ezlink, grocery… |
| `#busking` | fattkew, nac, busk, oneboyband… |

---

Built with `python-telegram-bot` v20 · FastAPI · Postgres · APScheduler · Zeabur
