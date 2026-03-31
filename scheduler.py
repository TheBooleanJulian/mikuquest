import logging
from datetime import date, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
import database as db
from handlers import PRIORITY_LABELS, STATUS_ICONS, trunc

logger = logging.getLogger(__name__)
SGT = pytz.timezone("Asia/Singapore")


async def send_daily_summary(app: Application):
    chat_ids = db.get_all_chat_ids()
    today_str  = date.today().strftime("%a %-d %b")
    yesterday  = (date.today() - timedelta(days=1)).isoformat()

    for chat_id in chat_ids:
        try:
            player       = db.get_or_create_player(chat_id)
            done_yday    = db.get_completed_on(chat_id, yesterday)
            yesterday_xp = sum(q["xp_value"] for q in done_yday)

            in_progress  = db.get_quests(chat_id, status="in_progress")
            todo         = db.get_quests(chat_id, status="todo")
            active       = in_progress + todo

            lines = [
                f"🌅 <b>MORNING SETLIST  •  {today_str}</b>",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "",
                "<b>YESTERDAY'S PERFORMANCE</b>",
            ]

            if done_yday:
                lines.append(
                    f"✔️  {len(done_yday)} quest(s) cleared  •  +{yesterday_xp} XP"
                )
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
                f"<b>TODAY'S SETLIST ({len(active)})</b>",
            ]

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
                    lines.append(f"\n💡 {high_n} high-priority quest(s) outstanding. You've got this~ 🎤")
            else:
                lines.append("  — Stage is empty~ Add some quests! —")

            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("📋 Open Stage", callback_data="board:refresh_new")
            ]])

            await app.bot.send_message(
                chat_id=chat_id,
                text="\n".join(lines),
                parse_mode="HTML",
                reply_markup=kb,
            )
            logger.info(f"[Scheduler] Sent daily summary to {chat_id}")

        except Exception as e:
            logger.error(f"[Scheduler] Error sending to {chat_id}: {e}")


def setup_scheduler(app: Application):
    scheduler = AsyncIOScheduler(timezone=SGT)
    scheduler.add_job(
        send_daily_summary,
        CronTrigger(hour=6, minute=0, timezone=SGT),
        args=[app],
        id="daily_summary",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("[Scheduler] Daily summary scheduled — 06:00 SGT")
