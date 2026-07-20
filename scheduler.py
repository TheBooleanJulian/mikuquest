import logging
from datetime import date, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
import database as db
from handlers import (
    PRIORITY_LABELS, STATUS_ICONS, trunc, _sync_gcal, _send_week_summary
)

logger = logging.getLogger(__name__)
SGT = pytz.timezone("Asia/Singapore")


# ─── GCal Sync (5:50 AM SGT) ─────────────────────────────────────────────────

async def gcal_sync_job(app: Application):
    chat_ids = db.get_all_chat_ids()
    for chat_id in chat_ids:
        try:
            count = await _sync_gcal(chat_id)
            if count and count > 0:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=f"🗓 <b>GCal Sync~</b> {count} calendar event(s) added to your setlist!",
                    parse_mode="HTML",
                )
            logger.info(f"[GCal Sync] {chat_id}: {count} events synced")
        except Exception as e:
            logger.error(f"[GCal Sync] {chat_id}: {e}")


# ─── Daily Debrief (6:00 AM SGT) ─────────────────────────────────────────────

async def send_daily_summary(app: Application):
    chat_ids  = db.get_all_chat_ids()
    today_str = date.today().strftime("%a %-d %b")
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    for chat_id in chat_ids:
        try:
            db.clear_old_pins(chat_id)
            pulled      = db.ensure_daily_rollover(chat_id)
            player      = db.get_or_create_player(chat_id)
            done_yday   = db.get_completed_on(chat_id, yesterday)
            yday_xp     = sum(q["xp_value"] for q in done_yday)
            in_progress = db.get_quests(chat_id, status="in_progress")
            todo        = db.get_quests(chat_id, status="todo")
            active      = in_progress + todo

            lines = [
                f"🌅 <b>MORNING SETLIST  •  {today_str}</b>",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "",
                "<b>YESTERDAY'S PERFORMANCE</b>",
            ]

            if done_yday:
                lines.append(f"✔️  {len(done_yday)} quest(s) cleared  •  +{yday_xp} XP")
                for q in done_yday[:5]:
                    lines.append(f"  ✔️ {trunc(q['text'], 42)}")
                if len(done_yday) > 5:
                    lines.append(f"  … and {len(done_yday) - 5} more")
                lines.append("おつかれさま！You killed it~ 🎵")
            else:
                lines.append("  — No completions yesterday —")

            lines += [
                f"🔥 Streak: <b>{player['streak_days']} days</b>",
                "",
            ]
            if pulled:
                lines.append(f"📥 <b>{len(pulled)} pulled from backlog</b> to kick off today~")
            lines.append(f"<b>TODAY'S SETLIST ({len(active)})</b>")

            if active:
                crit_n = sum(1 for q in active if q["priority"] == "critical")
                high_n = sum(1 for q in active if q["priority"] == "high")
                for q in active[:6]:
                    icon = STATUS_ICONS[q["status"]]
                    lbl  = PRIORITY_LABELS[q["priority"]]
                    lines.append(f"{icon} [{lbl}] {trunc(q['text'], 38)}  <i>{q['tag']}</i>")
                if len(active) > 6:
                    lines.append(f"  … and {len(active) - 6} more on the stage")
                if crit_n:
                    lines.append(f"\n💡 {crit_n} critical quest(s). Clear it first, then take a bow~")
                elif high_n:
                    lines.append(f"\n💡 {high_n} high-priority quest(s). You've got this~ 🎤")
            else:
                lines.append("  — Stage is empty~ Add some quests! —")

            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("📋 Open Stage",    callback_data="board:refresh_new"),
                InlineKeyboardButton("⭐ Set Focus",      callback_data="goals:pick"),
            ]])

            await app.bot.send_message(
                chat_id=chat_id,
                text="\n".join(lines),
                parse_mode="HTML",
                reply_markup=kb,
            )
            logger.info(f"[Debrief] Sent to {chat_id}")
        except Exception as e:
            logger.error(f"[Debrief] {chat_id}: {e}")


# ─── Due Date Reminders (every 30 min) ────────────────────────────────────────

async def check_reminders(app: Application):
    chat_ids = db.get_all_chat_ids()
    for chat_id in chat_ids:
        try:
            due_quests = db.get_due_reminders(within_minutes=60)
            due_for_chat = [q for q in due_quests if q["chat_id"] == chat_id]
            for quest in due_for_chat:
                db.mark_reminder_sent(quest["id"])
                lbl = PRIORITY_LABELS[quest["priority"]]
                kb  = InlineKeyboardMarkup([[
                    InlineKeyboardButton(f"✅ Nailed it! +{quest['xp_value']}xp",
                                         callback_data=f"done:{quest['id']}"),
                    InlineKeyboardButton("▶ Start now",
                                         callback_data=f"start:{quest['id']}"),
                ]])
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"⏰ <b>Due soon~</b>\n"
                        f"[{lbl}] {quest['text']}\n"
                        f"📅 Due: <b>{quest['due_date']}</b>"
                    ),
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            if due_for_chat:
                logger.info(f"[Reminders] {chat_id}: {len(due_for_chat)} reminder(s) sent")
        except Exception as e:
            logger.error(f"[Reminders] {chat_id}: {e}")


# ─── Weekly Summary (Sunday 8 PM SGT) ─────────────────────────────────────────

async def send_weekly_summary(app: Application):
    chat_ids = db.get_all_chat_ids()
    for chat_id in chat_ids:
        try:
            await _send_week_summary(
                chat_id,
                send_fn=lambda *a, **kw: app.bot.send_message(chat_id=chat_id, *a, **kw)
            )
            logger.info(f"[Weekly] Sent to {chat_id}")
        except Exception as e:
            logger.error(f"[Weekly] {chat_id}: {e}")


# ─── Pomodoro Sweep (every minute) ────────────────────────────────────────────

async def pomo_sweep_job(app: Application):
    due = db.get_due_pomodoros()
    for session in due:
        try:
            completed = db.complete_pomodoro(session["chat_id"], session["id"])
            if not completed:
                continue
            quest_note = ""
            if session.get("quest_id"):
                quest = db.get_quest(session["quest_id"])
                if quest:
                    quest_note = f"\nQuest: <b>{trunc(quest['text'], 60)}</b>"
            await app.bot.send_message(
                chat_id=session["chat_id"],
                text=(
                    f"🍅 <b>Pomodoro complete!</b>  +{completed['xp_awarded']} XP\n"
                    f"{session['duration_minutes']} minutes of focus, nailed it~ 🎵"
                    f"{quest_note}"
                ),
                parse_mode="HTML",
            )
            logger.info(f"[Pomodoro] {session['chat_id']}: session {session['id']} completed")
        except Exception as e:
            logger.error(f"[Pomodoro] session {session['id']}: {e}")


# ─── Setup ────────────────────────────────────────────────────────────────────

def setup_scheduler(app: Application):
    scheduler = AsyncIOScheduler(timezone=SGT)

    # GCal sync — 5:50 AM SGT
    scheduler.add_job(
        gcal_sync_job, CronTrigger(hour=5, minute=50, timezone=SGT),
        args=[app], id="gcal_sync", replace_existing=True,
    )
    # Morning debrief — 6:00 AM SGT
    scheduler.add_job(
        send_daily_summary, CronTrigger(hour=6, minute=0, timezone=SGT),
        args=[app], id="daily_summary", replace_existing=True,
    )
    # Due date reminders — every 30 min
    scheduler.add_job(
        check_reminders, CronTrigger(minute="0,30", timezone=SGT),
        args=[app], id="reminders", replace_existing=True,
    )
    # Weekly summary — Sunday 8 PM SGT
    scheduler.add_job(
        send_weekly_summary, CronTrigger(day_of_week="sun", hour=20, minute=0, timezone=SGT),
        args=[app], id="weekly_summary", replace_existing=True,
    )
    # Pomodoro sweep — every minute
    scheduler.add_job(
        pomo_sweep_job, CronTrigger(minute="*", timezone=SGT),
        args=[app], id="pomo_sweep", replace_existing=True,
    )

    scheduler.start()
    logger.info("[Scheduler] Jobs: GCal sync 05:50 | Debrief 06:00 | Reminders :00/:30 | Weekly Sun 20:00 | Pomodoro sweep :every min — all SGT")
