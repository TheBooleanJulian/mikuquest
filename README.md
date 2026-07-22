<div align="center">

# MikuQuest

**Telegram-native gamified task manager with Helium-3 rewards, streaks & daily debrief — built for solopreneurs juggling too many things at once.**

![Python](https://img.shields.io/badge/-Python-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/-FastAPI-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/-PostgreSQL-336791?logo=postgresql&logoColor=white)
![Telegram](https://img.shields.io/badge/-Telegram-26A5E4?logo=telegram&logoColor=white)
![Zeabur](https://img.shields.io/badge/-Zeabur-6C5CE7)
![License](https://img.shields.io/badge/license-MIT-00D4C8.svg)

</div>

---

## What it does

MikuQuest turns every Telegram message or forward into a tracked quest — no forms, no context switching, no friction. Solopreneurs log tasks the way they already think: by typing. The bot auto-tags quests by context, lets you move them through a Kanban board via inline buttons, and rewards completions with Helium-3 and level-ups. A daily 6AM SGT debrief keeps yesterday's wins and today's priorities front of mind, while a shareable web dashboard gives you a browser view of the same data without leaving your workflow.

## Features

- **Auto-capture** — any typed or forwarded message instantly becomes a quest
- **Kanban board** — `/board` shows TODO / IN PROGRESS / DONE TODAY with inline action buttons
- **Priority system** — `!c` `!h` `!m` `!l` prefixes, plus tap-to-change on any quest card
- **Auto-tagging** — keyword-based context tags: `#accurova` `#dev` `#tutoring` `#personal` `#busking`
- **Helium-3 + Levels** — Critical=40, High=30, Medium=20, Low=10 He-3 per quest; level up every 200 lifetime He-3 earned
- **Cosmetic titles** — a chance to drop a collectible title on quest clear; `/equip` it for free, or buy a freeform `/settitle` in the shop
- **Streaks** — consecutive days with at least one quest cleared
- **Daily 6AM SGT Debrief** — yesterday's completions + today's active quests delivered automatically
- **Web dashboard** — `/web` DMs a one-time login link to a browser view mirroring all bot data
- **Share links** — `/share board|today|week|stats` generates public read-only snapshot links
- **Google Calendar integration** — via `gcal.py` for scheduling context
- **AI-powered parsing** — Claude API parses natural-language quest text via `ai_parser.py`

## Tech Stack

| Layer | Choice |
|---|---|
| Backend | FastAPI + PostgreSQL |
| Frontend | HTML/CSS web dashboard (`web/`) |
| Bot | python-telegram-bot 20.7 (polling) |
| Scheduler | APScheduler (daily 6AM debrief) |
| AI | Claude API (Anthropic) |
| Calendar | Google Calendar API |
| Hosting | Zeabur (two services, shared Postgres — bot + web) |

## Quick Start

```bash
git clone https://github.com/TheBooleanJulian/mikuquest
cd mikuquest
pip install -r requirements.txt
cp .env.example .env
# Edit .env — add BOT_TOKEN, DATABASE_URL, WEB_BASE_URL, SESSION_SECRET
python bot.py
```

To also run the web app locally:

```bash
uvicorn web.app:app --reload --port 8000
# Set WEB_BASE_URL=http://localhost:8000 in .env
```

If migrating from an old SQLite database:

```bash
DATABASE_URL=... SQLITE_PATH=data/miguquest.db python scripts/migrate_sqlite_to_pg.py
```

## Configuration

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | ✅ | Telegram bot token from @BotFather |
| `DATABASE_URL` | ✅ | PostgreSQL connection string |
| `WEB_BASE_URL` | ✅ | Public base URL of the web service (for login & share links) |
| `SESSION_SECRET` | ✅ | Secret key for web session signing |
| `ANTHROPIC_API_KEY` | ⚠️ | Claude API key for AI quest parsing |
| `GOOGLE_*` | ⚠️ | Google Calendar OAuth credentials (see `gcal.py`) |

## Project Structure

```
mikuquest/
├── bot.py               # Bot entry point (polling)
├── handlers.py          # Command & message handlers
├── database.py          # PostgreSQL queries
├── ai_parser.py         # Claude-powered quest text parsing
├── scheduler.py         # APScheduler — daily 6AM debrief
├── gcal.py              # Google Calendar integration
├── web/                 # FastAPI web dashboard app
├── scripts/             # Migration & utility scripts
├── requirements.txt
├── zbpack.json          # Zeabur build config
└── .env.example
```

## Commands

| Command | Description |
|---|---|
| `/q <text>` | Log a quest (optionally with `!c` `!h` `!m` `!l` priority prefix) |
| `/board` | Kanban board — TODO / IN PROGRESS / DONE TODAY |
| `/today` | Active quests with quick-clear buttons |
| `/done <id>` | Mark quest done (no args shows pick list) |
| `/begin <id>` | Move quest to In Progress |
| `/drop <id>` | Drop a quest |
| `/tag #<tag>` | Board filtered by tag |
| `/stats` | Helium-3, level, streak, totals |
| `/clear` | Archive all done quests |
| `/web` | One-time login link to the web dashboard |
| `/share board\|today\|week\|stats` | Public read-only share link |

## Deployment

Deployed on Zeabur as **two services** sharing one managed Postgres database:

1. Push to GitHub
2. New Zeabur project → add a **Postgres** plugin
3. **Bot service** — deploy from GitHub, `start_command: python bot.py` (default in `zbpack.json`); set `BOT_TOKEN`, `DATABASE_URL`, `WEB_BASE_URL`
4. **Web service** — second service from same repo, override start command to `uvicorn web.app:app --host 0.0.0.0 --port $PORT`; set `DATABASE_URL`, `SESSION_SECRET`, `WEB_BASE_URL`
5. Deploy both — done

## Status / Roadmap

- [x] Core quest capture, board, priorities, Helium-3 & levels
- [x] Streaks and daily 6AM debrief
- [x] Hatsune Miku flavour across all bot messages
- [x] Web dashboard with one-time login links
- [x] Public share links for board / today / week / stats
- [x] AI-powered quest parsing via Claude
- [x] Google Calendar integration
- [ ] Recurring quests
- [ ] Multi-user support beyond single Telegram user

## Changelog

- **Jul 2026** — XP deprecated: Helium-3 is now the sole progression currency (rescaled to the old XP amounts) and drives levels; titles are purely cosmetic-drop based (`/equip`), defaulting to "Unpaid Intern"
- **Jul 2026** — Web dashboard launched: `/web` one-time login links, public share links (`/share board|today|week|stats`), FastAPI + Jinja2 web service sharing the same Postgres database as the bot
- **Mar 2026** — Major feature update: AI parsing via Claude, Google Calendar integration, PostgreSQL migration from SQLite, APScheduler daily debrief, streaks, XP system
- **Mar 2026** — Hatsune Miku personality applied across all bot messages
- **Mar 2026** — Initial MikuQuest bot shipped: quest capture, Kanban board, priorities, inline buttons, auto-tagging, basic XP

## License

MIT

---

<div align="center">
<sub>Built by <a href="https://github.com/TheBooleanJulian">@TheBooleanJulian</a></sub>
</div>