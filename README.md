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
# Edit .env — add BOT_TOKEN
python bot.py
```

### 3. Deploy to Zeabur
1. Push to GitHub
2. New project → Deploy from GitHub → select `miguquest-bot`
3. Add environment variable: `BOT_TOKEN=<your token>`
4. Add persistent volume mounted at `/data`, set `DB_PATH=/data/miguquest.db`
5. Deploy — done

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

Built with `python-telegram-bot` v20 · SQLite · APScheduler · Zeabur
