import re
import os
import logging
from datetime import date, timedelta
from typing import Optional

import dateparser
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import database as db
import ai_parser
import gcal

logger = logging.getLogger(__name__)

# ─── Constants ──────────────────────────────────────────────────────────────────

PRIORITY_ICONS  = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
PRIORITY_LABELS = {"critical": "!C", "high": "!H", "medium": "!M", "low": "!L"}
STATUS_ICONS    = {"todo": "⬜", "in_progress": "🔷", "done": "✅", "dropped": "🗑", "archived": "📦"}

TAG_KEYWORDS = {
    "#accurova":  ["accurova", "shoot", "photobooth", "client", "booking", "invoice",
                   "photo", "camera", "edit", "retouch", "selphy", "studio", "portrait"],
    "#dev":       ["bot", "code", "deploy", "zeabur", "github", "bug", "fix", "react",
                   "python", "script", "build", "app", "api", "db", "sql", "commit", "push"],
    "#tutoring":  ["tutor", "student", "angela", "denzel", "jessica", "pakorn",
                   "poon", "pun pun", "rin", "theethus", "lesson", "worksheet",
                   "math", "educare", "class", "homework"],
    "#personal":  ["cosplay", "miku", "figure", "ezlink", "grocery", "food",
                   "buy", "shop", "errands", "dentist", "doctor"],
    "#busking":   ["fattkew", "fattk", "busking", "nac", "busk", "oneboyband"],
}

SKIP_WORDS = {"ok", "okay", "yes", "no", "k", "thanks", "ty", "noted", "sure",
              "yep", "nope", "got it", "ack", "roger"}

RECURRING_MAP = {"daily": "daily", "weekly": "weekly", "monthly": "monthly",
                 "day": "daily", "week": "weekly", "month": "monthly"}


# ─── Utilities ──────────────────────────────────────────────────────────────────

def parse_priority(text: str):
    patterns = {
        "critical": r"^(!c|!critical)\s+",
        "high":     r"^(!h|!high)\s+",
        "medium":   r"^(!m|!medium)\s+",
        "low":      r"^(!l|!low)\s+",
    }
    for priority, pattern in patterns.items():
        m = re.match(pattern, text.strip(), re.IGNORECASE)
        if m:
            return priority, text[m.end():].strip()
    return "medium", text.strip()


def parse_due_date(text: str):
    """Extract due:X from text. Returns (cleaned_text, due_iso_or_None)."""
    m = re.search(r'\bdue:(\S+)', text, re.IGNORECASE)
    if not m:
        return text, None
    raw     = m.group(1)
    cleaned = (text[:m.start()] + text[m.end():]).strip()
    parsed  = dateparser.parse(
        raw,
        settings={
            "PREFER_DATES_FROM": "future",
            "TIMEZONE":          "Asia/Singapore",
            "RETURN_AS_TIMEZONE_AWARE": False,
        },
    )
    due_iso = parsed.date().isoformat() if parsed else None
    return cleaned, due_iso


def parse_recurring(text: str):
    """Extract repeat:X from text. Returns (cleaned_text, recurring_or_None)."""
    m = re.search(r'\brepeat:(\S+)', text, re.IGNORECASE)
    if not m:
        return text, None
    raw       = m.group(1).lower()
    cleaned   = (text[:m.start()] + text[m.end():]).strip()
    recurring = RECURRING_MAP.get(raw)
    return cleaned, recurring


def infer_tag(text: str) -> str:
    low = text.lower()
    for tag, keywords in TAG_KEYWORDS.items():
        if any(kw in low for kw in keywords):
            return tag
    m = re.search(r"#\w+", text)
    if m:
        return m.group(0).lower()
    return "#general"


def trunc(text: str, n: int = 35) -> str:
    return text if len(text) <= n else text[:n - 1] + "…"


def xp_bar(xp: int) -> str:
    filled = int((xp % 200) / 200 * 10)
    return "█" * filled + "░" * (10 - filled)


def fmt_due(due_iso: str) -> str:
    if not due_iso:
        return ""
    try:
        d     = date.fromisoformat(due_iso[:10])
        today = date.today()
        diff  = (d - today).days
        if diff < 0:
            return f" ⚠️ overdue"
        if diff == 0:
            return f" 📅 today"
        if diff == 1:
            return f" 📅 tmr"
        return f" 📅 {d.strftime('%-d %b')}"
    except Exception:
        return ""


# ─── Board Renderer ─────────────────────────────────────────────────────────────

def render_board(chat_id: int, tag_filter: str = None):
    db.clear_old_pins(chat_id)
    player  = db.get_or_create_player(chat_id)
    level   = player["level"]
    title   = db.get_title(level)
    xp      = player["total_xp"]
    streak  = player["streak_days"]
    to_next = 200 - (xp % 200)

    today_goal_ids = db.get_daily_goals(chat_id)

    todo        = db.get_quests(chat_id, status="todo",        tag=tag_filter)
    in_progress = db.get_quests(chat_id, status="in_progress", tag=tag_filter)
    done_today  = db.get_completed_today(chat_id)

    tag_note = f"  <i>filter: {tag_filter}</i>" if tag_filter else ""

    lines = [
        f"╔══════════════════════════╗",
        f"║  🎤 MIGUQUEST  •  Lv.{level} {title}",
        f"║  🔥 {streak}-day streak  •  {xp} XP",
        f"║  [{xp_bar(xp)}] {to_next} XP to next stage",
        f"╚══════════════════════════╝{tag_note}",
    ]

    keyboard = []

    # TODO
    lines.append(f"\n📥 <b>TODO</b> ({len(todo)})")
    lines.append("──────────────────────────────")
    if todo:
        for q in todo:
            lbl  = PRIORITY_LABELS[q["priority"]]
            star = "⭐ " if q["id"] in today_goal_ids else ""
            due  = fmt_due(q.get("due_date"))
            lines.append(f"⬜ {star}[{lbl}] {trunc(q['text'])}{due}  <i>{q['tag']}</i>")
            keyboard.append([InlineKeyboardButton(
                f"⬜ {star}[{lbl}] {trunc(q['text'], 28)}",
                callback_data=f"quest:{q['id']}"
            )])
    else:
        lines.append("  🎤 Stage is clear~ Add a new quest to begin the show!")

    # IN PROGRESS
    lines.append(f"\n⚡ <b>IN PROGRESS</b> ({len(in_progress)})")
    lines.append("──────────────────────────────")
    if in_progress:
        for q in in_progress:
            lbl  = PRIORITY_LABELS[q["priority"]]
            star = "⭐ " if q["id"] in today_goal_ids else ""
            due  = fmt_due(q.get("due_date"))
            lines.append(f"🔷 {star}[{lbl}] {trunc(q['text'])}{due}  <i>{q['tag']}</i>")
            keyboard.append([InlineKeyboardButton(
                f"🔷 {star}[{lbl}] {trunc(q['text'], 28)}",
                callback_data=f"quest:{q['id']}"
            )])
    else:
        lines.append("  — Nothing performing right now —")

    # DONE TODAY
    lines.append(f"\n✅ <b>DONE TODAY</b> ({len(done_today)})")
    lines.append("──────────────────────────────")
    if done_today:
        today_xp = sum(q["xp_value"] for q in done_today)
        for q in done_today:
            lines.append(f"✔️  {trunc(q['text'])}  +{q['xp_value']}xp")
        lines.append(f"<i>+{today_xp} XP earned today  🎵</i>")
    else:
        lines.append("  — No completions yet~ —")

    keyboard.append([
        InlineKeyboardButton("🔄 Refresh",        callback_data="board:refresh"),
        InlineKeyboardButton("➕ New Quest",       callback_data="board:new"),
        InlineKeyboardButton("⭐ Set Focus",       callback_data="goals:pick"),
    ])
    keyboard.append([
        InlineKeyboardButton("📊 Concert Stats",  callback_data="board:stats"),
        InlineKeyboardButton("📅 Week",           callback_data="board:week"),
    ])

    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


# ─── Quest Card ─────────────────────────────────────────────────────────────────

def quest_card_text(quest: dict) -> str:
    lbl = PRIORITY_LABELS[quest["priority"]]
    ico = PRIORITY_ICONS[quest["priority"]]
    sti = STATUS_ICONS.get(quest["status"], "❓")
    due = f"\n📅 Due: <b>{quest['due_date']}</b>" if quest.get("due_date") else ""
    rec = f"\n🔁 Recurring: <b>{quest['recurring']}</b>" if quest.get("recurring") else ""
    notes_block = ""
    if quest.get("notes"):
        lines = quest["notes"].splitlines()[-3:]  # last 3 notes
        notes_block = "\n\n📝 <i>" + "\n".join(lines) + "</i>"
    return (
        f"⚔️ <b>Quest #{quest['id']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{sti} {quest['text']}\n\n"
        f"📌 Priority: <b>{quest['priority'].upper()}</b> {ico}   🏷 {quest['tag']}"
        f"{due}{rec}\n"
        f"💎 XP: <b>+{quest['xp_value']}</b> on clear"
        f"{notes_block}"
    )


def quest_card_markup(quest: dict, is_goal: bool = False) -> InlineKeyboardMarkup:
    qid = quest["id"]
    xpv = quest["xp_value"]
    rows = []
    if quest["status"] != "in_progress":
        rows.append([
            InlineKeyboardButton("▶ Performing now",         callback_data=f"start:{qid}"),
            InlineKeyboardButton(f"✅ Nailed it! +{xpv}xp", callback_data=f"done:{qid}"),
        ])
    else:
        rows.append([InlineKeyboardButton(f"✅ Nailed it! +{xpv}xp", callback_data=f"done:{qid}")])

    rows.append([
        InlineKeyboardButton("🔴 Critical", callback_data=f"prio:critical:{qid}"),
        InlineKeyboardButton("🟠 High",     callback_data=f"prio:high:{qid}"),
        InlineKeyboardButton("🗑 Cut from setlist", callback_data=f"drop_confirm:{qid}"),
    ])

    goal_btn = (
        InlineKeyboardButton("⭐ Unset Focus", callback_data=f"goal:unset:{qid}")
        if is_goal else
        InlineKeyboardButton("⭐ Set as Focus", callback_data=f"goal:set:{qid}")
    )
    rows.append([goal_btn])
    rows.append([InlineKeyboardButton("← Back to Stage", callback_data="board:refresh")])
    return InlineKeyboardMarkup(rows)


# ─── Capture helper ─────────────────────────────────────────────────────────────

async def _create_quest(update: Update, chat_id: int, text: str,
                        source: str = "typed", priority_override: str = None,
                        tag_override: str = None, due_override: str = None,
                        recurring_override: str = None) -> Optional[int]:
    # Extract priority
    priority, clean = parse_priority(text)
    if priority_override:
        priority = priority_override
        clean    = text

    # Extract due date
    clean, due_date = parse_due_date(clean)
    if due_override:
        due_date = due_override

    # Extract recurring
    clean, recurring = parse_recurring(clean)
    if recurring_override:
        recurring = recurring_override

    # Remove explicit hashtags from text
    clean = re.sub(r'#\w+', '', clean).strip()

    tag = tag_override or infer_tag(text)
    quest = db.add_quest(
        chat_id, clean, priority=priority, tag=tag,
        source=source, due_date=due_date, recurring=recurring
    )

    ico      = PRIORITY_ICONS[priority]
    src_note = "📩 caught that note~" if source == "forwarded" else (
               "🔁 recurring" if source == "recurring" else "")
    due_line = f"\n📅 Due: <b>{due_date}</b>" if due_date else ""
    rec_line = f"\n🔁 Recurring: <b>{recurring}</b>" if recurring else ""

    msg = (
        f"🎤 <b>Quest Added to Setlist~</b>  {src_note}\n"
        f"━━━━━━━━━━━━━━\n"
        f"{clean}\n\n"
        f"📌 Priority: <b>{priority.upper()}</b> {ico}   🏷 {tag}"
        f"{due_line}{rec_line}\n"
        f"💎 <b>+{quest['xp_value']} XP</b> when you nail this one!"
    )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Already crushed it",  callback_data=f"done:{quest['id']}"),
            InlineKeyboardButton("▶ Performing now",       callback_data=f"start:{quest['id']}"),
        ],
        [
            InlineKeyboardButton("🔴 Critical",            callback_data=f"prio:critical:{quest['id']}"),
            InlineKeyboardButton("🟠 High",                callback_data=f"prio:high:{quest['id']}"),
            InlineKeyboardButton("🗑 Cut from setlist",    callback_data=f"drop_confirm:{quest['id']}"),
        ],
    ])

    sent = await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb)
    db.save_quest_message(chat_id, sent.message_id, quest["id"])
    return quest["id"]


# ─── Complete helper ─────────────────────────────────────────────────────────────

async def _complete(chat_id: int, quest_id: int, query=None, update=None):
    quest = db.complete_quest(chat_id, quest_id)
    if not quest:
        txt = "Quest not found."
        if query:
            await query.edit_message_text(txt)
        else:
            await update.message.reply_text(txt)
        return

    player   = db.get_or_create_player(chat_id)
    old_xp   = player["total_xp"] - quest["xp_value"]
    old_lvl  = db.compute_level(old_xp)
    level_up = player["level"] > old_lvl
    done_cnt = len(db.get_completed_today(chat_id))

    msg = (
        f"🎶 <b>QUEST CLEARED — ENCORE!</b>\n"
        f"{quest['text']}\n\n"
        f"⚡ +{quest['xp_value']} XP  •  {player['total_xp']} total  •  Lv.{player['level']}\n"
        f"🔥 {player['streak_days']}-day streak  •  {done_cnt} quests cleared today"
    )
    if quest.get("recurring"):
        msg += f"\n🔁 Next <b>{quest['recurring']}</b> quest auto-added to setlist~"
    if level_up:
        title = db.get_title(player["level"])
        msg  += f"\n\n🎉 <b>LEVEL UP — NEW STAGE UNLOCKED!</b>\nWelcome to Lv.{player['level']} — {title}\nThe crowd goes wild~ ✨"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📋 Stage", callback_data="board:refresh_new")
    ]])

    if query:
        await query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb)


# ─── Command Handlers ─────────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    db.get_or_create_player(chat_id)
    await update.message.reply_text(
        "🎤 <b>Miku is online! Let's compose today's setlist~</b>\n\n"
        "Drop any task here and I'll turn it into a quest.\n"
        "Forward messages, type freely — I'll catch everything.\n\n"
        "/board to see your stage  •  /help for all commands\n"
        "がんばって！✨",
        parse_mode=ParseMode.HTML
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎵 <b>MiguQuest Commands</b>\n\n"
        "<b>Core</b>\n"
        "<code>/q &lt;text&gt;</code>             — Add a quest\n"
        "<code>/q !h Fix bug due:tomorrow</code>  — Priority + due date\n"
        "<code>/q Invoice client repeat:weekly</code> — Recurring quest\n"
        "<code>/board</code>                  — Stage overview\n"
        "<code>/done &lt;id&gt;</code>            — Clear a quest 🎶\n"
        "<code>/begin &lt;id&gt;</code>           — Start performing\n"
        "<code>/drop &lt;id&gt;</code>            — Cut from setlist\n"
        "<code>/today</code>                 — Active quests\n\n"
        "<b>Goals &amp; Planning</b>\n"
        "<code>/goals</code>                 — Set today's focus (⭐ up to 3)\n"
        "<code>/week</code>                  — Weekly performance summary\n"
        "<code>/tag #accurova</code>         — Filter board by tag\n\n"
        "<b>Notes</b>\n"
        "<code>/note &lt;id&gt; &lt;text&gt;</code>   — Add context to a quest\n"
        "Reply to a quest card             — Also adds a note~\n\n"
        "<b>Google Calendar</b>\n"
        "<code>/gcalauth</code>              — Connect Google Calendar\n"
        "<code>/gcalsync</code>              — Sync today's events now\n\n"
        "<b>Stats &amp; Housekeeping</b>\n"
        "<code>/stats</code>                 — Concert stats\n"
        "<code>/clear</code>                 — Archive cleared quests\n\n"
        "<i>Any message you type or forward is auto-captured~ ✨\n"
        "Syntax: !c/!h/!m/!l  due:tomorrow  repeat:weekly</i>",
        parse_mode=ParseMode.HTML
    )


async def quest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text(
            "Usage: /q <task>\n"
            "Prefix: !c !h !m !l  •  Suffix: due:tomorrow repeat:weekly"
        )
        return
    await _create_quest(update, chat_id, " ".join(context.args), source="command")


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg     = update.message
    chat_id = update.effective_chat.id
    text    = (msg.text or "").strip()

    if not text or len(text) < 4:
        return
    if text.lower() in SKIP_WORDS:
        return

    # ── Reply to a quest card → append note ──────────────────────────────────
    if msg.reply_to_message:
        replied_mid = msg.reply_to_message.message_id
        quest_id    = db.get_quest_by_message(chat_id, replied_mid)
        if quest_id:
            quest = db.append_note(quest_id, text)
            if quest:
                await msg.reply_text(
                    f"📝 <b>Note added to Quest #{quest_id}~</b>\n<i>{text[:100]}</i>",
                    parse_mode=ParseMode.HTML
                )
            return

    is_forwarded = bool(
        msg.forward_date or msg.forward_from or
        msg.forward_from_chat or msg.forward_sender_name
    )

    # ── Forwarded → AI smart parse ────────────────────────────────────────────
    if is_forwarded and os.environ.get("ANTHROPIC_API_KEY"):
        parsed = await ai_parser.parse_forwarded_message(text)
        await _create_quest(
            update, chat_id,
            text=parsed["task"],
            source="forwarded",
            priority_override=parsed.get("priority"),
            tag_override=parsed.get("tag"),
            due_override=parsed.get("due"),
        )
        return

    # ── Standard typed capture ────────────────────────────────────────────────
    source = "forwarded" if is_forwarded else "typed"
    await _create_quest(update, chat_id, text, source=source)


async def board_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id    = update.effective_chat.id
    tag_filter = context.args[0] if context.args else None
    text, kb   = render_board(chat_id, tag_filter)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if context.args:
        try:
            await _complete(chat_id, int(context.args[0]), update=update)
            return
        except ValueError:
            pass
    quests = db.get_quests(chat_id, status="todo") + db.get_quests(chat_id, status="in_progress")
    if not quests:
        await update.message.reply_text("🎶 No active quests~ Stage is clear! ✨")
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"✅ [{PRIORITY_LABELS[q['priority']]}] {trunc(q['text'], 30)}",
            callback_data=f"done:{q['id']}"
        )]
        for q in quests[:12]
    ])
    await update.message.reply_text("Tap to clear~", reply_markup=kb)


async def begin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Usage: /begin <quest_id>")
        return
    try:
        quest = db.update_quest_status(int(context.args[0]), "in_progress")
        if quest:
            await update.message.reply_text(
                f"🎵 <b>Performing now~</b> {quest['text']}", parse_mode=ParseMode.HTML
            )
    except ValueError:
        await update.message.reply_text("Usage: /begin <quest_id>")


async def drop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Usage: /drop <quest_id>")
        return
    try:
        quest = db.update_quest_status(int(context.args[0]), "dropped")
        if quest:
            await update.message.reply_text(
                f"🗑 Cut from the setlist. The show must go on~ {quest['text']}"
            )
    except ValueError:
        await update.message.reply_text("Usage: /drop <quest_id>")


async def note_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /note <quest_id> <text>")
        return
    try:
        quest_id = int(context.args[0])
        note     = " ".join(context.args[1:])
        quest    = db.append_note(quest_id, note)
        if quest:
            await update.message.reply_text(
                f"📝 <b>Note added to Quest #{quest_id}~</b>\n<i>{note[:100]}</i>",
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text("Quest not found.")
    except ValueError:
        await update.message.reply_text("Usage: /note <quest_id> <text>")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id    = update.effective_chat.id
    player     = db.get_or_create_player(chat_id)
    done_today = db.get_completed_today(chat_id)
    today_xp   = sum(q["xp_value"] for q in done_today)
    title      = db.get_title(player["level"])
    to_next    = 200 - (player["total_xp"] % 200)
    await update.message.reply_text(
        f"🎵 <b>Concert Stats</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🎤 Level: <b>{player['level']} — {title}</b>\n"
        f"💎 Total XP: <b>{player['total_xp']}</b>\n"
        f"[{xp_bar(player['total_xp'])}] {to_next} XP to next stage\n\n"
        f"🔥 Streak: <b>{player['streak_days']} days</b>\n"
        f"📋 Total cleared: <b>{player['quests_completed_total']}</b>\n"
        f"☀️ Today: <b>{len(done_today)} cleared  •  +{today_xp} XP</b>",
        parse_mode=ParseMode.HTML
    )


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id     = update.effective_chat.id
    in_progress = db.get_quests(chat_id, status="in_progress")
    todo        = db.get_quests(chat_id, status="todo")
    active      = in_progress + todo
    if not active:
        await update.message.reply_text("🎶 Stage is clear! All quests cleared~ 今日もお疲れ様！✨")
        return
    lines = ["🎤 <b>Active Quests</b>\n"]
    kb    = []
    goal_ids = db.get_daily_goals(chat_id)
    for q in active[:15]:
        icon = STATUS_ICONS[q["status"]]
        lbl  = PRIORITY_LABELS[q["priority"]]
        star = "⭐" if q["id"] in goal_ids else ""
        due  = fmt_due(q.get("due_date"))
        lines.append(f"{icon} <code>#{q['id']}</code> [{lbl}] {star}{q['text']}{due}  <i>{q['tag']}</i>")
        kb.append([InlineKeyboardButton(
            f"✅ #{q['id']}: {trunc(q['text'], 28)}",
            callback_data=f"done:{q['id']}"
        )])
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def tag_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Usage: /tag #accurova")
        return
    tag      = context.args[0] if context.args[0].startswith("#") else f"#{context.args[0]}"
    text, kb = render_board(chat_id, tag_filter=tag)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    count   = db.archive_done_quests(chat_id)
    await update.message.reply_text(f"🗂 Archived {count} cleared quest(s). Clean stage~ ✨")


async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await _send_week_summary(chat_id, send_fn=update.message.reply_text)


async def _send_week_summary(chat_id: int, send_fn):
    player   = db.get_or_create_player(chat_id)
    quests   = db.get_completed_this_week(chat_id)
    week_xp  = sum(q["xp_value"] for q in quests)

    today  = date.today()
    monday = today - timedelta(days=today.weekday())
    week_str = f"{monday.strftime('%-d %b')} – {today.strftime('%-d %b')}"

    # Stats by tag
    tag_counts: dict = {}
    for q in quests:
        tag_counts[q["tag"]] = tag_counts.get(q["tag"], 0) + 1

    # Stats by priority
    crit = sum(1 for q in quests if q["priority"] == "critical")
    high = sum(1 for q in quests if q["priority"] == "high")

    lines = [
        f"📊 <b>WEEKLY REPORT  •  {week_str}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"✔️  <b>{len(quests)} quests cleared</b>  •  +{week_xp} XP",
        f"🔥 Streak: <b>{player['streak_days']} days</b>",
        f"🎤 Level: <b>{player['level']} — {db.get_title(player['level'])}</b>",
        "",
        "<b>BY TAG</b>",
    ]

    for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1]):
        bar = "▓" * count + "░" * max(0, 10 - count)
        lines.append(f"  {tag:<14} [{bar}] {count}")

    if crit or high:
        lines += ["", "<b>PRIORITY BREAKDOWN</b>",
                  f"  🔴 Critical: {crit}  •  🟠 High: {high}"]

    if not quests:
        lines.append("\n<i>— No quests cleared this week~ —</i>")
    else:
        lines.append(f"\n<i>おつかれさま！Great week~ 🎵</i>")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📋 Open Stage", callback_data="board:refresh_new")
    ]])
    await send_fn(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb
    )


async def goals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await _send_goals_picker(chat_id, send_fn=update.message.reply_text)


async def _send_goals_picker(chat_id: int, send_fn, edit_fn=None):
    in_progress = db.get_quests(chat_id, status="in_progress")
    todo        = db.get_quests(chat_id, status="todo")
    active      = in_progress + todo
    goal_ids    = db.get_daily_goals(chat_id)

    if not active:
        txt = "🎤 No active quests to focus on~ Add some first!"
        await (edit_fn(txt) if edit_fn else send_fn(txt))
        return

    lines = [
        "⭐ <b>Set Today's Focus</b>",
        "Pick up to 3 quests to pin as your main stage for today~",
        "",
    ]
    kb = []
    for q in active[:12]:
        lbl    = PRIORITY_LABELS[q["priority"]]
        is_set = q["id"] in goal_ids
        star   = "⭐ " if is_set else "   "
        action = f"goal:unset:{q['id']}" if is_set else f"goal:set:{q['id']}"
        label  = f"{'✓' if is_set else '○'} [{lbl}] {trunc(q['text'], 28)}"
        lines.append(f"{'⭐' if is_set else '○'} [{lbl}] {trunc(q['text'], 35)}")
        kb.append([InlineKeyboardButton(label, callback_data=action)])

    kb.append([InlineKeyboardButton("✅ Done setting focus", callback_data="board:refresh_new")])

    txt = "\n".join(lines)
    if edit_fn:
        await edit_fn(txt, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await send_fn(txt, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


# ─── Google Calendar Commands ─────────────────────────────────────────────────

async def gcalauth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if gcal.is_authenticated():
        await update.message.reply_text("🗓 Google Calendar already connected~ ✅\nUse /gcalsync to pull today's events.")
        return
    url = gcal.get_auth_url()
    if not url:
        await update.message.reply_text(
            "⚠️ <b>GOOGLE_CREDENTIALS_JSON not set.</b>\n\n"
            "To connect Google Calendar:\n"
            "1. Create a Google Cloud project\n"
            "2. Enable Calendar API\n"
            "3. Create OAuth2 credentials (Desktop app)\n"
            "4. Set env var <code>GOOGLE_CREDENTIALS_JSON</code> = contents of credentials.json\n"
            "5. Run /gcalauth again",
            parse_mode=ParseMode.HTML
        )
        return
    await update.message.reply_text(
        f"🗓 <b>Authorise Google Calendar</b>\n\n"
        f"1. Visit this URL:\n{url}\n\n"
        f"2. Authorise access\n"
        f"3. Copy the code and send: <code>/gcalcode YOUR_CODE</code>",
        parse_mode=ParseMode.HTML
    )


async def gcalcode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /gcalcode <code>")
        return
    code = " ".join(context.args)
    ok   = gcal.exchange_code(code)
    if ok:
        await update.message.reply_text(
            "✅ <b>Google Calendar connected~</b>\n"
            "Events will sync daily at 5:50 AM SGT.\n"
            "Use /gcalsync to pull today's events now~ 🗓",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text("❌ Auth failed. Try /gcalauth again.")


async def gcalsync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    count   = await _sync_gcal(chat_id)
    if count is None:
        await update.message.reply_text(
            "🗓 Google Calendar not connected. Use /gcalauth to set it up~"
        )
    elif count == 0:
        await update.message.reply_text("🗓 No new events to sync today~ Calendar is quiet ✨")
    else:
        await update.message.reply_text(
            f"🗓 <b>Synced {count} calendar event(s) to your setlist~</b>\n"
            f"Use /board to see them!",
            parse_mode=ParseMode.HTML
        )


async def _sync_gcal(chat_id: int) -> Optional[int]:
    if not gcal.is_authenticated():
        return None
    events = gcal.fetch_todays_events(days_ahead=2)
    count  = 0
    for ev in events:
        if db.gcal_event_exists(chat_id, ev["id"]):
            continue
        tag = gcal.infer_tag_from_event(ev["summary"])
        db.add_quest(
            chat_id,
            text=ev["summary"],
            priority="medium",
            tag=tag,
            source="gcal",
            due_date=ev["due"],
            gcal_event_id=ev["id"],
        )
        count += 1
    return count


# ─── Callback Handler ─────────────────────────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    chat_id = update.effective_chat.id
    data    = query.data
    await query.answer()

    # ── Quest detail card ────────────────────────────────────────────────────────
    if data.startswith("quest:"):
        quest_id = int(data.split(":")[1])
        quest    = db.get_quest(quest_id)
        if not quest:
            await query.edit_message_text("Quest not found.")
            return
        goal_ids = db.get_daily_goals(chat_id)
        await query.edit_message_text(
            quest_card_text(quest),
            parse_mode=ParseMode.HTML,
            reply_markup=quest_card_markup(quest, is_goal=quest_id in goal_ids)
        )

    # ── Mark done ────────────────────────────────────────────────────────────────
    elif data.startswith("done:"):
        await _complete(chat_id, int(data.split(":")[1]), query=query)

    # ── Start quest ──────────────────────────────────────────────────────────────
    elif data.startswith("start:"):
        quest_id = int(data.split(":")[1])
        quest    = db.update_quest_status(quest_id, "in_progress")
        if quest:
            goal_ids = db.get_daily_goals(chat_id)
            await query.edit_message_text(
                quest_card_text(quest),
                parse_mode=ParseMode.HTML,
                reply_markup=quest_card_markup(quest, is_goal=quest_id in goal_ids)
            )

    # ── Drop confirm ─────────────────────────────────────────────────────────────
    elif data.startswith("drop_confirm:"):
        quest_id = int(data.split(":")[1])
        quest    = db.get_quest(quest_id)
        if quest:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑 Cut it",   callback_data=f"drop:{quest_id}"),
                InlineKeyboardButton("Keep it~",    callback_data=f"quest:{quest_id}"),
            ]])
            await query.edit_message_text(
                f"Cut this from the setlist?\n\n<b>{quest['text']}</b>",
                parse_mode=ParseMode.HTML, reply_markup=kb
            )

    # ── Drop confirmed ───────────────────────────────────────────────────────────
    elif data.startswith("drop:"):
        quest_id = int(data.split(":")[1])
        quest    = db.update_quest_status(quest_id, "dropped")
        if quest:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("← Back to Stage", callback_data="board:refresh_new")
            ]])
            await query.edit_message_text(
                f"🗑 Cut from the setlist. The show must go on~ {quest['text']}",
                reply_markup=kb
            )

    # ── Set priority ─────────────────────────────────────────────────────────────
    elif data.startswith("prio:"):
        _, priority, quest_id_str = data.split(":")
        quest = db.update_quest_priority(int(quest_id_str), priority)
        if quest:
            goal_ids = db.get_daily_goals(chat_id)
            await query.edit_message_text(
                quest_card_text(quest),
                parse_mode=ParseMode.HTML,
                reply_markup=quest_card_markup(quest, is_goal=int(quest_id_str) in goal_ids)
            )

    # ── Goal: set / unset ────────────────────────────────────────────────────────
    elif data.startswith("goal:set:"):
        quest_id = int(data.split(":")[2])
        goal_ids = db.get_daily_goals(chat_id)
        if len(goal_ids) >= 3:
            await query.answer("⭐ Max 3 focus quests~ Unset one first!", show_alert=True)
            return
        db.set_daily_goal(chat_id, quest_id)
        await _refresh_goals_picker(query, chat_id)

    elif data.startswith("goal:unset:"):
        quest_id = int(data.split(":")[2])
        db.unset_daily_goal(chat_id, quest_id)
        await _refresh_goals_picker(query, chat_id)

    elif data == "goals:pick":
        await _send_goals_picker(
            chat_id,
            send_fn=query.message.reply_text,
            edit_fn=None
        )

    # ── Board: refresh in-place ───────────────────────────────────────────────────
    elif data == "board:refresh":
        text, kb = render_board(chat_id)
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    # ── Board: send new message ───────────────────────────────────────────────────
    elif data == "board:refresh_new":
        text, kb = render_board(chat_id)
        await query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    # ── Board: new quest prompt ───────────────────────────────────────────────────
    elif data == "board:new":
        await query.message.reply_text(
            "🎤 What's next on the setlist?\n"
            "Tip: <code>!h Fix bug due:tomorrow repeat:weekly</code>",
            parse_mode=ParseMode.HTML
        )

    # ── Board: stats ─────────────────────────────────────────────────────────────
    elif data == "board:stats":
        player     = db.get_or_create_player(chat_id)
        done_today = db.get_completed_today(chat_id)
        today_xp   = sum(q["xp_value"] for q in done_today)
        title      = db.get_title(player["level"])
        to_next    = 200 - (player["total_xp"] % 200)
        text = (
            f"🎵 <b>Concert Stats</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🎤 Level: <b>{player['level']} — {title}</b>\n"
            f"💎 Total XP: <b>{player['total_xp']}</b>\n"
            f"[{xp_bar(player['total_xp'])}] {to_next} XP to next stage\n\n"
            f"🔥 Streak: <b>{player['streak_days']} days</b>\n"
            f"📋 Total cleared: <b>{player['quests_completed_total']}</b>\n"
            f"☀️ Today: <b>{len(done_today)} cleared  •  +{today_xp} XP</b>"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("← Back to Stage", callback_data="board:refresh")]])
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    # ── Board: week inline ────────────────────────────────────────────────────────
    elif data == "board:week":
        await _send_week_summary(chat_id, send_fn=query.message.reply_text)


async def _refresh_goals_picker(query, chat_id: int):
    in_progress = db.get_quests(chat_id, status="in_progress")
    todo        = db.get_quests(chat_id, status="todo")
    active      = in_progress + todo
    goal_ids    = db.get_daily_goals(chat_id)

    lines = [
        "⭐ <b>Set Today's Focus</b>",
        f"<i>{len(goal_ids)}/3 focus quests set~</i>",
        "",
    ]
    kb = []
    for q in active[:12]:
        lbl    = PRIORITY_LABELS[q["priority"]]
        is_set = q["id"] in goal_ids
        action = f"goal:unset:{q['id']}" if is_set else f"goal:set:{q['id']}"
        label  = f"{'✓' if is_set else '○'} [{lbl}] {trunc(q['text'], 28)}"
        lines.append(f"{'⭐' if is_set else '○'} [{lbl}] {trunc(q['text'], 35)}")
        kb.append([InlineKeyboardButton(label, callback_data=action)])

    kb.append([InlineKeyboardButton("✅ Done setting focus", callback_data="board:refresh_new")])
    await query.edit_message_text(
        "\n".join(lines), parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb)
    )
