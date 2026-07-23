import re
import os
import logging
from datetime import date, datetime, timedelta
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

SHARE_KINDS = {"board", "today", "week", "stats"}


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
    db.ensure_daily_rollover(chat_id)
    daily_quest = db.ensure_daily_quest_for_chat(chat_id)
    player  = db.get_or_create_player(chat_id)
    level   = player["level"]
    title   = db.get_display_title(player)
    xp      = player["total_xp"]
    streak  = player["streak_days"]
    to_next = 200 - (xp % 200)
    helium3 = player.get("helium3", 0) or 0

    today_goal_ids = db.get_daily_goals(chat_id)

    todo        = [q for q in db.get_quests(chat_id, status="todo", tag=tag_filter)
                   if q["source"] != "daily_miku"]
    in_progress = db.get_quests(chat_id, status="in_progress", tag=tag_filter)
    done_today  = db.get_completed_today(chat_id)
    backlog_n   = len(db.get_quests(chat_id, status="backlog"))
    active_pomo = db.get_active_pomodoro(chat_id)

    tag_note = f"  <i>filter: {tag_filter}</i>" if tag_filter else ""

    lines = [
        f"╔══════════════════════════╗",
        f"║  🎤 MIGUQUEST  •  Lv.{level} {title}",
        f"║  🔥 {streak}-day streak  •  🛰️ {helium3} He-3",
        f"║  [{xp_bar(xp)}] {to_next} He-3 to next level",
        f"╚══════════════════════════╝{tag_note}",
    ]

    if active_pomo:
        mins_left = max(0, int((datetime.fromisoformat(active_pomo["ends_at"]) - datetime.now()).total_seconds() // 60))
        lines.append(f"\n🍅 <i>Focus session running — {mins_left}m left</i>")

    keyboard = []

    if daily_quest and daily_quest["status"] in ("todo", "in_progress"):
        reset_in = db.next_daily_reset() - datetime.now()
        hrs, rem = divmod(max(0, int(reset_in.total_seconds())), 3600)
        mins = rem // 60
        lines.append(f"\n🌟 <b>MIKU'S QUEST OF THE DAY</b>  <i>({daily_quest['category']})</i>")
        lines.append("──────────────────────────────")
        lines.append(f"{daily_quest['text']}  🛰️ +{daily_quest['xp_value']} He-3")
        lines.append(f"⏳ <i>Resets in {hrs}h {mins}m</i>")
        keyboard.append([InlineKeyboardButton(
            f"✅ {trunc(daily_quest['text'], 30)}",
            callback_data=f"done:{daily_quest['id']}"
        )])

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
            lines.append(f"✔️  {trunc(q['text'])}  +{q['xp_value']} He-3")
        lines.append(f"<i>+{today_xp} He-3 earned today  🎵</i>")
    else:
        lines.append("  — No completions yet~ —")

    lines.append(f"\n📦 <i>Backlog: {backlog_n} quest(s) waiting</i>")

    keyboard.append([
        InlineKeyboardButton("🔄 Refresh",        callback_data="board:refresh"),
        InlineKeyboardButton("➕ New Quest",       callback_data="board:new"),
        InlineKeyboardButton("⭐ Set Focus",       callback_data="goals:pick"),
    ])
    keyboard.append([
        InlineKeyboardButton("📊 Concert Stats",  callback_data="board:stats"),
        InlineKeyboardButton("📅 Week",           callback_data="board:week"),
    ])
    keyboard.append([
        InlineKeyboardButton(f"📦 Backlog ({backlog_n})", callback_data="backlog:list"),
        InlineKeyboardButton("🔗 Share Stage",    callback_data="share:board"),
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
        f"🛰️ He-3: <b>+{quest['xp_value']}</b> on clear"
        f"{notes_block}"
    )


def quest_card_markup(quest: dict, is_goal: bool = False) -> InlineKeyboardMarkup:
    qid = quest["id"]
    xpv = quest["xp_value"]
    rows = []
    if quest["status"] != "in_progress":
        rows.append([
            InlineKeyboardButton("▶ Performing now",         callback_data=f"start:{qid}"),
            InlineKeyboardButton(f"✅ Nailed it! +{xpv} He-3", callback_data=f"done:{qid}"),
        ])
    else:
        rows.append([InlineKeyboardButton(f"✅ Nailed it! +{xpv} He-3", callback_data=f"done:{qid}")])

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
    rows.append([
        InlineKeyboardButton("🍅 Focus 25m", callback_data=f"pomo:start:{qid}"),
        InlineKeyboardButton("🔗 Share Quest", callback_data=f"share:quest:{qid}"),
    ])
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
        f"🛰️ <b>+{quest['xp_value']} He-3</b> when you nail this one!"
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
        f"🛰️ +{quest.get('helium3_awarded', 0)} He-3  •  Lv.{player['level']}\n"
        f"🔥 {player['streak_days']}-day streak  •  {done_cnt} quests cleared today"
    )
    loot = quest.get("loot")
    if loot:
        icon = db.RARITY_ICONS.get(loot["rarity"], "📦")
        if loot.get("kind") == "cosmetic":
            msg += (f"\n🎫 <b>Cosmetic found!</b> {icon} {loot['name']} <i>({loot['rarity']})</i>"
                     f"\n<i>/equip {loot['key']} to wear it~</i>")
        else:
            msg += f"\n📦 <b>Cargo find!</b> {icon} {loot['name']} <i>({loot['rarity']})</i>"
    if quest.get("recurring"):
        msg += f"\n🔁 Next <b>{quest['recurring']}</b> quest auto-added to setlist~"
    if level_up:
        msg += f"\n\n🎉 <b>LEVEL UP!</b>\nWelcome to Lv.{player['level']}~\nThe crowd goes wild~ ✨"

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
        "<b>Backlog</b>\n"
        "<code>/backlog</code>               — View unfinished quests swept from previous days\n"
        "<i>Each morning, today's board starts fresh — unfinished quests move to\n"
        "the backlog, and the top 5 get auto-pulled back in~</i>\n\n"
        "<b>🌟 Miku's Quest of the Day</b>\n"
        "<i>A fresh self-improvement/productive/kindness quest, the same for everyone,\n"
        "shown at the top of /board — doesn't carry over if you skip it~</i>\n\n"
        "<b>Pomodoro</b>\n"
        "<code>/pomo</code>                  — Start a 25-min freeform focus session\n"
        "<code>/pomo &lt;id&gt;</code>             — Focus session tied to a quest\n"
        "<code>/pomo &lt;id&gt; 45</code>          — Custom duration (10–90 min)\n"
        "<i>+15 He-3 bonus per completed session~</i>\n\n"
        "<b>Helium-3 &amp; Salvage</b>\n"
        "<code>/inventory</code>             — View collected salvage, cosmetics &amp; Helium-3\n"
        "<code>/shop</code>                  — Spend Helium-3 (custom title unlock)\n"
        "<code>/settitle &lt;text&gt;</code>       — Set your custom title (after unlocking)\n"
        "<code>/equip &lt;key&gt;</code>           — Wear an owned cosmetic as your title, free\n"
        "<i>Clearing quests has a chance to drop salvage or cosmetics~ 🛰️</i>\n\n"
        "<b>Notes</b>\n"
        "<code>/note &lt;id&gt; &lt;text&gt;</code>   — Add context to a quest\n"
        "Reply to a quest card             — Also adds a note~\n\n"
        "<b>Google Calendar</b>\n"
        "<code>/gcalauth</code>              — Connect Google Calendar\n"
        "<code>/gcalsync</code>              — Sync today's events now\n\n"
        "<b>Stats &amp; Housekeeping</b>\n"
        "<code>/stats</code>                 — Concert stats\n"
        "<code>/clear</code>                 — Archive cleared quests\n\n"
        "<b>Web</b>\n"
        "<code>/web</code>                   — Get a login link for the web app\n"
        "<code>/share board</code>           — Get a public read-only share link\n\n"
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
    title      = db.get_display_title(player)
    to_next    = 200 - (player["total_xp"] % 200)
    pomo       = db.get_today_pomodoro_stats(chat_id)
    await update.message.reply_text(
        f"🎵 <b>Concert Stats</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🎤 Level: <b>{player['level']} — {title}</b>\n"
        f"🛰️ Helium-3: <b>{player.get('helium3', 0) or 0}</b>\n"
        f"[{xp_bar(player['total_xp'])}] {to_next} He-3 to next level  <i>(lifetime: {player['total_xp']})</i>\n\n"
        f"🔥 Streak: <b>{player['streak_days']} days</b>\n"
        f"📋 Total cleared: <b>{player['quests_completed_total']}</b>\n"
        f"☀️ Today: <b>{len(done_today)} cleared  •  +{today_xp} He-3</b>\n"
        f"🍅 Pomodoros today: <b>{pomo['count']}</b>  •  {pomo['minutes']}m focused  •  +{pomo['xp']} He-3",
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


# ─── Backlog ──────────────────────────────────────────────────────────────────

def _backlog_view(chat_id: int):
    db.ensure_daily_rollover(chat_id)
    quests = db.get_quests(chat_id, status="backlog")[:15]
    lines  = ["📦 <b>Backlog</b>", "<i>Unfinished quests swept from previous days~</i>", ""]
    kb     = []
    if quests:
        for q in quests:
            lbl = PRIORITY_LABELS[q["priority"]]
            lines.append(f"[{lbl}] {trunc(q['text'], 40)}  <i>{q['tag']}</i>")
            kb.append([InlineKeyboardButton(
                f"⬆ Pull #{q['id']}: {trunc(q['text'], 26)}",
                callback_data=f"backlog:pull:{q['id']}"
            )])
    else:
        lines.append("  — Backlog is empty~ Nothing waiting! —")
    kb.append([InlineKeyboardButton("← Back to Stage", callback_data="board:refresh_new")])
    return "\n".join(lines), InlineKeyboardMarkup(kb)


async def backlog_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id  = update.effective_chat.id
    text, kb = _backlog_view(chat_id)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


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
        f"✔️  <b>{len(quests)} quests cleared</b>  •  +{week_xp} He-3",
        f"🔥 Streak: <b>{player['streak_days']} days</b>",
        f"🎤 Level: <b>{player['level']} — {db.get_display_title(player)}</b>",
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


# ─── Pomodoro ─────────────────────────────────────────────────────────────────

async def _start_pomodoro(chat_id: int, quest_id: Optional[int],
                          duration: int, send_fn):
    session = db.start_pomodoro(chat_id, quest_id=quest_id, duration_minutes=duration)
    quest_note = ""
    if quest_id:
        quest = db.get_quest(quest_id)
        if quest:
            quest_note = f"\nQuest: <b>{trunc(quest['text'], 60)}</b>"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⏹ Cancel", callback_data=f"pomo:cancel:{session['id']}")
    ]])
    await send_fn(
        f"🍅 <b>Focus session started~</b>\n"
        f"{session['duration_minutes']}:00 on the clock. I'll ping you when it's done!"
        f"{quest_note}",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def pomo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args    = context.args or []
    quest_id: Optional[int] = None
    duration = db.POMO_DEFAULT_MINUTES

    if len(args) >= 1:
        try:
            first = int(args[0])
            quest = db.get_quest(first)
            if quest and quest["chat_id"] == chat_id:
                quest_id = first
                if len(args) >= 2:
                    duration = int(args[1])
            else:
                duration = first
        except ValueError:
            await update.message.reply_text("Usage: /pomo [quest_id] [minutes]")
            return

    await _start_pomodoro(chat_id, quest_id, duration, send_fn=update.message.reply_text)


# ─── Inventory & Shop ─────────────────────────────────────────────────────────

async def inventory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id   = update.effective_chat.id
    player    = db.get_or_create_player(chat_id)
    materials = db.get_inventory(chat_id, kind="material")
    cosmetics = db.get_inventory(chat_id, kind="cosmetic")

    lines = [
        "🛰️ <b>Cargo Hold</b>",
        f"Helium-3: <b>{player.get('helium3', 0) or 0}</b>",
        "",
        "🎒 <b>Salvage</b>",
    ]
    if materials:
        for it in materials:
            icon = db.RARITY_ICONS.get(it["rarity"], "📦")
            lines.append(f"{icon} {it['item_name']}  <i>×{it['qty']}</i>")
    else:
        lines.append("— No salvage collected yet~ Clear some quests for a chance at a drop! —")

    lines.append("\n✨ <b>Cosmetics</b>")
    if cosmetics:
        for it in cosmetics:
            icon = db.RARITY_ICONS.get(it["rarity"], "📦")
            lines.append(f"{icon} {it['item_name']}  <code>{it['item_key']}</code>")
        lines.append("\n<i>/equip &lt;key&gt; to wear one as your title~</i>")
    else:
        lines.append("— No cosmetics found yet~ —")

    lines.append("\n<i>/shop to spend your Helium-3~</i>")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def equip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Usage: /equip <key>  (see /inventory for your keys)")
        return
    key   = context.args[0]
    title = db.equip_cosmetic(chat_id, key)
    if title:
        await update.message.reply_text(f"✨ Equipped: <b>{title}</b>", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("You don't own that cosmetic~ Check /inventory for your keys.")


async def shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    player  = db.get_or_create_player(chat_id)

    lines = [
        "🏪 <b>Lunar Outpost Shop</b>",
        f"Helium-3: <b>{player.get('helium3', 0) or 0}</b>",
        "",
    ]
    kb = []
    for item_id, item in db.SHOP_ITEMS.items():
        lines.append(f"{item['name']}  —  {item['cost']} He-3\n<i>{item['desc']}</i>")
        kb.append([InlineKeyboardButton(
            f"Buy {item['name']} ({item['cost']} He-3)",
            callback_data=f"shop:buy:{item_id}"
        )])
    await update.message.reply_text(
        "\n\n".join(lines), parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def settitle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    player  = db.get_or_create_player(chat_id)
    if not player.get("custom_title_unlocked"):
        await update.message.reply_text(
            "🔒 Custom titles aren't unlocked yet~ Buy one in /shop first!"
        )
        return
    if not context.args:
        await update.message.reply_text("Usage: /settitle <text>")
        return
    text = " ".join(context.args)
    db.set_custom_title(chat_id, text)
    await update.message.reply_text(f"🎫 Title set to: <b>{text[:40]}</b>", parse_mode=ParseMode.HTML)


# ─── Web (magic login + share links) ─────────────────────────────────────────

def web_base_url() -> Optional[str]:
    url = os.environ.get("WEB_BASE_URL")
    return url.rstrip("/") if url else None


async def web_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    base    = web_base_url()
    if not base:
        await update.message.reply_text("⚠️ WEB_BASE_URL is not configured yet.")
        return
    token = db.create_login_token(chat_id)
    await update.message.reply_text(
        f"🌐 <b>Your MiguQuest login link~</b>\n\n"
        f"{base}/auth/{token}\n\n"
        f"<i>Valid for 10 minutes, one-time use. Don't share this one!</i>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def share_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    base    = web_base_url()
    if not base:
        await update.message.reply_text("⚠️ WEB_BASE_URL is not configured yet.")
        return
    kind = context.args[0].lower() if context.args else "board"
    if kind not in SHARE_KINDS:
        await update.message.reply_text(f"Usage: /share [{'|'.join(sorted(SHARE_KINDS))}]")
        return
    token = db.create_share(chat_id, kind)
    await update.message.reply_text(
        f"🔗 <b>Public share link ({kind})~</b>\n\n"
        f"{base}/s/{token}\n\n"
        f"<i>Anyone with this link can view a read-only snapshot. "
        f"Send /share again to make a new one, or ask to have it revoked.</i>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def _share_reply(chat_id: int, kind: str, quest_id: Optional[int], send_fn):
    base = web_base_url()
    if not base:
        await send_fn("⚠️ WEB_BASE_URL is not configured yet.")
        return
    token = db.create_share(chat_id, kind, quest_id=quest_id)
    label = f"quest #{quest_id}" if quest_id else kind
    await send_fn(
        f"🔗 <b>Public share link ({label})~</b>\n\n{base}/s/{token}",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


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
        title      = db.get_display_title(player)
        to_next    = 200 - (player["total_xp"] % 200)
        pomo       = db.get_today_pomodoro_stats(chat_id)
        text = (
            f"🎵 <b>Concert Stats</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🎤 Level: <b>{player['level']} — {title}</b>\n"
            f"🛰️ Helium-3: <b>{player.get('helium3', 0) or 0}</b>\n"
            f"[{xp_bar(player['total_xp'])}] {to_next} He-3 to next level  <i>(lifetime: {player['total_xp']})</i>\n\n"
            f"🔥 Streak: <b>{player['streak_days']} days</b>\n"
            f"📋 Total cleared: <b>{player['quests_completed_total']}</b>\n"
            f"☀️ Today: <b>{len(done_today)} cleared  •  +{today_xp} He-3</b>\n"
            f"🍅 Pomodoros today: <b>{pomo['count']}</b>  •  {pomo['minutes']}m focused  •  +{pomo['xp']} He-3"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("← Back to Stage", callback_data="board:refresh")]])
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    # ── Board: week inline ────────────────────────────────────────────────────────
    elif data == "board:week":
        await _send_week_summary(chat_id, send_fn=query.message.reply_text)

    # ── Share links ──────────────────────────────────────────────────────────────
    elif data.startswith("share:quest:"):
        quest_id = int(data.split(":")[2])
        await _share_reply(chat_id, "quest", quest_id, send_fn=query.message.reply_text)

    elif data.startswith("share:"):
        kind = data.split(":")[1]
        if kind in SHARE_KINDS:
            await _share_reply(chat_id, kind, None, send_fn=query.message.reply_text)

    # ── Backlog ──────────────────────────────────────────────────────────────────
    elif data == "backlog:list":
        text, kb = _backlog_view(chat_id)
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    elif data.startswith("backlog:pull:"):
        quest_id = int(data.split(":")[2])
        db.pull_from_backlog(chat_id, quest_id)
        text, kb = _backlog_view(chat_id)
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    # ── Pomodoro ─────────────────────────────────────────────────────────────────
    elif data.startswith("pomo:start:"):
        quest_id = int(data.split(":")[2])
        await _start_pomodoro(chat_id, quest_id, db.POMO_DEFAULT_MINUTES,
                               send_fn=query.message.reply_text)

    elif data.startswith("pomo:cancel:"):
        session_id = int(data.split(":")[2])
        session    = db.cancel_pomodoro(chat_id, session_id)
        if session and session["status"] == "cancelled":
            await query.edit_message_text("⏹ Focus session cancelled~ No worries, try again anytime.")

    # ── Shop ─────────────────────────────────────────────────────────────────────
    elif data.startswith("shop:buy:"):
        item_id = data.split(":")[2]
        item    = db.SHOP_ITEMS.get(item_id)
        if not item:
            return
        if item_id == "custom_title":
            ok = db.buy_custom_title(chat_id)
        else:
            ok = False
        if ok:
            await query.message.reply_text(
                f"✅ Purchased <b>{item['name']}</b>!", parse_mode=ParseMode.HTML
            )
        else:
            await query.message.reply_text(
                f"⚠️ Not enough Helium-3~ Need {item['cost']}."
            )


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
