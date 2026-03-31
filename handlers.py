import re
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import database as db

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


# ─── Utilities ──────────────────────────────────────────────────────────────────

def parse_priority(text: str):
    """Return (priority, cleaned_text)."""
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
    return text if len(text) <= n else text[: n - 1] + "…"


def xp_bar(xp: int) -> str:
    filled = int((xp % 200) / 200 * 10)
    return "█" * filled + "░" * (10 - filled)


# ─── Board Renderer ─────────────────────────────────────────────────────────────

def render_board(chat_id: int, tag_filter: str = None):
    player  = db.get_or_create_player(chat_id)
    level   = player["level"]
    title   = db.get_title(level)
    xp      = player["total_xp"]
    streak  = player["streak_days"]
    to_next = 200 - (xp % 200)

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
            lbl = PRIORITY_LABELS[q["priority"]]
            lines.append(f"⬜ [{lbl}] {trunc(q['text'])}  <i>{q['tag']}</i>")
            keyboard.append([InlineKeyboardButton(
                f"⬜ [{lbl}] {trunc(q['text'], 30)}",
                callback_data=f"quest:{q['id']}"
            )])
    else:
        lines.append("  🎤 Stage is clear~ Add a new quest to begin the show!")

    # IN PROGRESS
    lines.append(f"\n⚡ <b>IN PROGRESS</b> ({len(in_progress)})")
    lines.append("──────────────────────────────")
    if in_progress:
        for q in in_progress:
            lbl = PRIORITY_LABELS[q["priority"]]
            lines.append(f"🔷 [{lbl}] {trunc(q['text'])}  <i>{q['tag']}</i>")
            keyboard.append([InlineKeyboardButton(
                f"🔷 [{lbl}] {trunc(q['text'], 30)}",
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

    # Controls
    keyboard.append([
        InlineKeyboardButton("🔄 Refresh",      callback_data="board:refresh"),
        InlineKeyboardButton("➕ New Quest",     callback_data="board:new"),
        InlineKeyboardButton("📊 Concert Stats", callback_data="board:stats"),
    ])

    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


# ─── Quest Card ─────────────────────────────────────────────────────────────────

def quest_card_markup(quest: dict) -> InlineKeyboardMarkup:
    qid  = quest["id"]
    xpv  = quest["xp_value"]
    rows = []
    if quest["status"] != "in_progress":
        rows.append([
            InlineKeyboardButton("▶ Performing now",          callback_data=f"start:{qid}"),
            InlineKeyboardButton(f"✅ Nailed it! +{xpv}xp",  callback_data=f"done:{qid}"),
        ])
    else:
        rows.append([InlineKeyboardButton(f"✅ Nailed it! +{xpv}xp", callback_data=f"done:{qid}")])

    rows.append([
        InlineKeyboardButton("🔴 Critical", callback_data=f"prio:critical:{qid}"),
        InlineKeyboardButton("🟠 High",     callback_data=f"prio:high:{qid}"),
        InlineKeyboardButton("🗑 Cut from setlist", callback_data=f"drop_confirm:{qid}"),
    ])
    rows.append([InlineKeyboardButton("← Back to Stage", callback_data="board:refresh")])
    return InlineKeyboardMarkup(rows)


def quest_card_text(quest: dict) -> str:
    lbl = PRIORITY_LABELS[quest["priority"]]
    ico = PRIORITY_ICONS[quest["priority"]]
    sti = STATUS_ICONS.get(quest["status"], "❓")
    return (
        f"⚔️ <b>Quest #{quest['id']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{sti} {quest['text']}\n\n"
        f"📌 Priority: <b>{quest['priority'].upper()}</b> {ico}   🏷 {quest['tag']}\n"
        f"💎 XP: <b>+{quest['xp_value']}</b> on clear\n"
        f"📅 Added: {quest['created_at'][:10]}"
    )


# ─── Capture helper ─────────────────────────────────────────────────────────────

async def _create_quest(update: Update, chat_id: int, text: str, source: str = "typed"):
    priority, clean = parse_priority(text)
    tag   = infer_tag(clean)
    quest = db.add_quest(chat_id, clean, priority=priority, tag=tag, source=source)

    ico = PRIORITY_ICONS[priority]
    src_note = "📩 caught that note~" if source == "forwarded" else ""

    msg = (
        f"🎤 <b>Quest Added to Setlist~</b>  {src_note}\n"
        f"━━━━━━━━━━━━━━\n"
        f"{clean}\n\n"
        f"📌 Priority: <b>{priority.upper()}</b> {ico}   🏷 {tag}\n"
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
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb)


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
    if level_up:
        title = db.get_title(player["level"])
        msg += f"\n\n🎉 <b>LEVEL UP — NEW STAGE UNLOCKED!</b>\nWelcome to Lv.{player['level']} — {title}\nThe crowd goes wild~ ✨"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📋 Board", callback_data="board:refresh_new")
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
        "<code>/q &lt;text&gt;</code>       — Add a quest to the setlist\n"
        "<code>/q !h Fix bug</code>   — With priority (!c !h !m !l)\n"
        "<code>/board</code>           — Your stage overview\n"
        "<code>/done &lt;id&gt;</code>      — Clear a quest 🎶\n"
        "<code>/begin &lt;id&gt;</code>     — Start performing\n"
        "<code>/drop &lt;id&gt;</code>      — Cut from setlist\n"
        "<code>/today</code>           — Today's active quests\n"
        "<code>/tag #accurova</code>  — Filter by tag\n"
        "<code>/stats</code>           — Your concert stats\n"
        "<code>/clear</code>           — Archive cleared quests\n\n"
        "<i>Any message you type or forward is auto-captured~ ✨</i>",
        parse_mode=ParseMode.HTML
    )


async def quest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Usage: /q <task>  (prefix with !h !m !l !c for priority)")
        return
    text = " ".join(context.args)
    await _create_quest(update, chat_id, text, source="command")


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-capture any typed or forwarded message as a quest."""
    msg     = update.message
    chat_id = update.effective_chat.id
    text    = (msg.text or "").strip()

    if not text or len(text) < 4:
        return
    if text.lower() in SKIP_WORDS:
        return

    source = (
        "forwarded"
        if (msg.forward_date or msg.forward_from or msg.forward_from_chat or msg.forward_sender_name)
        else "typed"
    )
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
            quest_id = int(context.args[0])
            await _complete(chat_id, quest_id, update=update)
            return
        except ValueError:
            pass

    # Show quick-pick list
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
    await update.message.reply_text("Tap to mark done:", reply_markup=kb)


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
        else:
            await update.message.reply_text("Quest not found.")
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
            await update.message.reply_text(f"🗑 Cut from the setlist. The show must go on~ {quest['text']}")
        else:
            await update.message.reply_text("Quest not found.")
    except ValueError:
        await update.message.reply_text("Usage: /drop <quest_id>")


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
    for q in active[:15]:
        icon = STATUS_ICONS[q["status"]]
        lbl  = PRIORITY_LABELS[q["priority"]]
        lines.append(f"{icon} <code>#{q['id']}</code> [{lbl}] {q['text']}  <i>{q['tag']}</i>")
        kb.append([InlineKeyboardButton(
            f"✅ #{q['id']}: {trunc(q['text'], 28)}",
            callback_data=f"done:{q['id']}"
        )])

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def tag_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Usage: /tag #accurova")
        return
    tag  = context.args[0] if context.args[0].startswith("#") else f"#{context.args[0]}"
    text, kb = render_board(chat_id, tag_filter=tag)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    count   = db.archive_done_quests(chat_id)
    await update.message.reply_text(f"🗂 Archived {count} cleared quest(s). Clean stage~ ✨")


# ─── Callback Handler ─────────────────────────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    chat_id = update.effective_chat.id
    data    = query.data
    await query.answer()

    # ── Quest detail card ────────────────────────────────────────────────────────
    if data.startswith("quest:"):
        quest_id = int(data.split(":")[1])
        quest = db.get_quest(quest_id)
        if not quest:
            await query.edit_message_text("Quest not found.")
            return
        await query.edit_message_text(
            quest_card_text(quest),
            parse_mode=ParseMode.HTML,
            reply_markup=quest_card_markup(quest)
        )

    # ── Mark done ────────────────────────────────────────────────────────────────
    elif data.startswith("done:"):
        quest_id = int(data.split(":")[1])
        await _complete(chat_id, quest_id, query=query)

    # ── Start quest ──────────────────────────────────────────────────────────────
    elif data.startswith("start:"):
        quest_id = int(data.split(":")[1])
        quest    = db.update_quest_status(quest_id, "in_progress")
        if quest:
            await query.edit_message_text(
                quest_card_text(quest),
                parse_mode=ParseMode.HTML,
                reply_markup=quest_card_markup(quest)
            )

    # ── Drop confirm ─────────────────────────────────────────────────────────────
    elif data.startswith("drop_confirm:"):
        quest_id = int(data.split(":")[1])
        quest    = db.get_quest(quest_id)
        if quest:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑 Cut it", callback_data=f"drop:{quest_id}"),
                InlineKeyboardButton("Keep it~", callback_data=f"quest:{quest_id}"),
            ]])
            await query.edit_message_text(
                f"Cut this from the setlist?\n\n<b>{quest['text']}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb
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
                f"🗑 Cut from the setlist. The show must go on~ {quest['text']}", reply_markup=kb
            )

    # ── Set priority ─────────────────────────────────────────────────────────────
    elif data.startswith("prio:"):
        _, priority, quest_id_str = data.split(":")
        quest = db.update_quest_priority(int(quest_id_str), priority)
        if quest:
            await query.edit_message_text(
                quest_card_text(quest),
                parse_mode=ParseMode.HTML,
                reply_markup=quest_card_markup(quest)
            )

    # ── Board: refresh (edit in-place) ───────────────────────────────────────────
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
            "Prefix with <code>!h</code> <code>!m</code> <code>!l</code> <code>!c</code> for priority~\n"
            "Example: <code>!h Fix Accurova booking form</code>",
            parse_mode=ParseMode.HTML
        )

    # ── Board: stats inline ───────────────────────────────────────────────────────
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
