import sqlite3
import os
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "data/miguquest.db")
XP_MAP  = {"critical": 40, "high": 30, "medium": 20, "low": 10}


@contextmanager
def get_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS quests (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id         INTEGER NOT NULL,
                text            TEXT    NOT NULL,
                source          TEXT    DEFAULT 'typed',
                status          TEXT    DEFAULT 'todo',
                priority        TEXT    DEFAULT 'medium',
                tag             TEXT    DEFAULT '#general',
                xp_value        INTEGER DEFAULT 20,
                notes           TEXT    DEFAULT '',
                due_date        TEXT,
                reminder_sent   INTEGER DEFAULT 0,
                recurring       TEXT    DEFAULT NULL,
                pinned          INTEGER DEFAULT 0,
                gcal_event_id   TEXT    DEFAULT NULL,
                created_at      TEXT    NOT NULL,
                completed_at    TEXT
            );

            CREATE TABLE IF NOT EXISTS player (
                chat_id                 INTEGER PRIMARY KEY,
                total_xp               INTEGER DEFAULT 0,
                level                  INTEGER DEFAULT 1,
                streak_days            INTEGER DEFAULT 0,
                last_active_date       TEXT,
                quests_completed_total INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS quest_messages (
                chat_id     INTEGER NOT NULL,
                message_id  INTEGER NOT NULL,
                quest_id    INTEGER NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS daily_goals (
                chat_id  INTEGER NOT NULL,
                quest_id INTEGER NOT NULL,
                date     TEXT    NOT NULL,
                PRIMARY KEY (chat_id, quest_id, date)
            );
        """)
    _migrate_db()


def _migrate_db():
    """Safely add new columns to existing deployments."""
    new_columns = [
        ("quests", "notes",         "TEXT    DEFAULT ''"),
        ("quests", "reminder_sent", "INTEGER DEFAULT 0"),
        ("quests", "recurring",     "TEXT    DEFAULT NULL"),
        ("quests", "pinned",        "INTEGER DEFAULT 0"),
        ("quests", "gcal_event_id", "TEXT    DEFAULT NULL"),
    ]
    with get_db() as conn:
        for table, col, coldef in new_columns:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coldef}")
            except Exception:
                pass  # column already exists

        # quest_messages table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS quest_messages (
                chat_id    INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                quest_id   INTEGER NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            )
        """)
        # daily_goals table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_goals (
                chat_id  INTEGER NOT NULL,
                quest_id INTEGER NOT NULL,
                date     TEXT    NOT NULL,
                PRIMARY KEY (chat_id, quest_id, date)
            )
        """)


# ─── Player ────────────────────────────────────────────────────────────────────

def compute_level(xp: int) -> int:
    return max(1, 1 + xp // 200)


def get_title(level: int) -> str:
    if level < 5:  return "Roadie"
    if level < 10: return "Opening Act"
    if level < 15: return "Supporting Act"
    if level < 20: return "Feature Artist"
    if level < 25: return "Headliner"
    if level < 30: return "World Tour"
    return "✨ Diva Mode ✨"


def get_or_create_player(chat_id: int) -> Dict:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM player WHERE chat_id = ?", (chat_id,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO player (chat_id, last_active_date) VALUES (?, ?)",
                (chat_id, date.today().isoformat())
            )
            row = conn.execute("SELECT * FROM player WHERE chat_id = ?", (chat_id,)).fetchone()
        return dict(row)


def update_player(chat_id: int, **kwargs):
    if not kwargs:
        return
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [chat_id]
    with get_db() as conn:
        conn.execute(f"UPDATE player SET {sets} WHERE chat_id = ?", vals)


def get_all_chat_ids() -> List[int]:
    with get_db() as conn:
        rows = conn.execute("SELECT DISTINCT chat_id FROM player").fetchall()
        return [r["chat_id"] for r in rows]


# ─── Quests ─────────────────────────────────────────────────────────────────────

def add_quest(chat_id: int, text: str, priority: str = "medium",
              tag: str = "#general", source: str = "typed",
              due_date: str = None, recurring: str = None,
              gcal_event_id: str = None) -> Dict:
    xp  = XP_MAP.get(priority, 20)
    now = datetime.now().isoformat()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO quests (chat_id, text, source, status, priority, tag, xp_value, "
            "due_date, recurring, gcal_event_id, created_at) "
            "VALUES (?, ?, ?, 'todo', ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, text[:300], source, priority, tag, xp,
             due_date, recurring, gcal_event_id, now)
        )
        row = conn.execute("SELECT * FROM quests WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def get_quest(quest_id: int) -> Optional[Dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM quests WHERE id = ?", (quest_id,)).fetchone()
        return dict(row) if row else None


def get_quests(chat_id: int, status: Optional[str] = None,
               tag: Optional[str] = None) -> List[Dict]:
    query  = "SELECT * FROM quests WHERE chat_id = ?"
    params: list = [chat_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    else:
        query += " AND status NOT IN ('dropped', 'archived')"
    if tag:
        query += " AND tag = ?"
        params.append(tag)
    query += (
        " ORDER BY pinned DESC, "
        "CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
        "WHEN 'medium' THEN 2 WHEN 'low' THEN 3 END, "
        "CASE WHEN due_date IS NOT NULL THEN 0 ELSE 1 END, "
        "due_date ASC, created_at ASC"
    )
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def update_quest_status(quest_id: int, status: str) -> Optional[Dict]:
    completed_at = datetime.now().isoformat() if status == "done" else None
    with get_db() as conn:
        conn.execute(
            "UPDATE quests SET status = ?, completed_at = ? WHERE id = ?",
            (status, completed_at, quest_id)
        )
        row = conn.execute("SELECT * FROM quests WHERE id = ?", (quest_id,)).fetchone()
        return dict(row) if row else None


def update_quest_priority(quest_id: int, priority: str) -> Optional[Dict]:
    xp = XP_MAP.get(priority, 20)
    with get_db() as conn:
        conn.execute(
            "UPDATE quests SET priority = ?, xp_value = ? WHERE id = ?",
            (priority, xp, quest_id)
        )
        row = conn.execute("SELECT * FROM quests WHERE id = ?", (quest_id,)).fetchone()
        return dict(row) if row else None


def complete_quest(chat_id: int, quest_id: int) -> Optional[Dict]:
    quest = update_quest_status(quest_id, "done")
    if not quest:
        return None

    # Auto-spawn recurring quest
    if quest.get("recurring"):
        _spawn_recurring(chat_id, quest)

    player    = get_or_create_player(chat_id)
    today     = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    last      = player.get("last_active_date", "")
    streak    = player.get("streak_days", 0)
    if last == yesterday:
        streak += 1
    elif last != today:
        streak = 1

    new_xp    = player["total_xp"] + quest["xp_value"]
    new_level = compute_level(new_xp)

    update_player(
        chat_id,
        total_xp=new_xp,
        level=new_level,
        streak_days=streak,
        last_active_date=today,
        quests_completed_total=player["quests_completed_total"] + 1,
    )
    return quest


def _spawn_recurring(chat_id: int, quest: Dict):
    """Create the next occurrence of a recurring quest."""
    interval = quest.get("recurring")
    today    = date.today()
    if interval == "daily":
        next_due = (today + timedelta(days=1)).isoformat()
    elif interval == "weekly":
        next_due = (today + timedelta(weeks=1)).isoformat()
    elif interval == "monthly":
        m = today.month % 12 + 1
        y = today.year + (1 if today.month == 12 else 0)
        next_due = today.replace(year=y, month=m).isoformat()
    else:
        return
    add_quest(
        chat_id, quest["text"],
        priority=quest["priority"],
        tag=quest["tag"],
        source="recurring",
        due_date=next_due,
        recurring=interval,
    )


# ─── Notes ──────────────────────────────────────────────────────────────────────

def append_note(quest_id: int, note: str) -> Optional[Dict]:
    with get_db() as conn:
        row = conn.execute("SELECT notes FROM quests WHERE id = ?", (quest_id,)).fetchone()
        if not row:
            return None
        existing = row["notes"] or ""
        ts       = datetime.now().strftime("%d %b %H:%M")
        sep      = "\n" if existing else ""
        new_notes = existing + f"{sep}[{ts}] {note}"
        conn.execute("UPDATE quests SET notes = ? WHERE id = ?", (new_notes, quest_id))
        row = conn.execute("SELECT * FROM quests WHERE id = ?", (quest_id,)).fetchone()
        return dict(row)


# ─── Quest message tracking ───────────────────────────────────────────────────

def save_quest_message(chat_id: int, message_id: int, quest_id: int):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO quest_messages (chat_id, message_id, quest_id) VALUES (?,?,?)",
            (chat_id, message_id, quest_id)
        )


def get_quest_by_message(chat_id: int, message_id: int) -> Optional[int]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT quest_id FROM quest_messages WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id)
        ).fetchone()
        return row["quest_id"] if row else None


# ─── Daily Goals ─────────────────────────────────────────────────────────────

def set_daily_goal(chat_id: int, quest_id: int):
    today = date.today().isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO daily_goals (chat_id, quest_id, date) VALUES (?,?,?)",
            (chat_id, quest_id, today)
        )
        conn.execute(
            "UPDATE quests SET pinned = 1 WHERE id = ? AND chat_id = ?",
            (quest_id, chat_id)
        )


def unset_daily_goal(chat_id: int, quest_id: int):
    today = date.today().isoformat()
    with get_db() as conn:
        conn.execute(
            "DELETE FROM daily_goals WHERE chat_id = ? AND quest_id = ? AND date = ?",
            (chat_id, quest_id, today)
        )
        conn.execute(
            "UPDATE quests SET pinned = 0 WHERE id = ? AND chat_id = ?",
            (quest_id, chat_id)
        )


def get_daily_goals(chat_id: int) -> List[int]:
    today = date.today().isoformat()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT quest_id FROM daily_goals WHERE chat_id = ? AND date = ?",
            (chat_id, today)
        ).fetchall()
        return [r["quest_id"] for r in rows]


def clear_old_pins(chat_id: int):
    """Unpin quests whose goal date has passed."""
    today = date.today().isoformat()
    with get_db() as conn:
        old = conn.execute(
            "SELECT quest_id FROM daily_goals WHERE chat_id = ? AND date < ?",
            (chat_id, today)
        ).fetchall()
        for row in old:
            conn.execute(
                "UPDATE quests SET pinned = 0 WHERE id = ?", (row["quest_id"],)
            )
        conn.execute(
            "DELETE FROM daily_goals WHERE chat_id = ? AND date < ?",
            (chat_id, today)
        )


# ─── Due date / reminders ────────────────────────────────────────────────────

def get_due_reminders(within_minutes: int = 60) -> List[Dict]:
    """Quests due within N minutes that haven't been reminded yet."""
    now     = datetime.now()
    cutoff  = (now + timedelta(minutes=within_minutes)).isoformat()
    now_iso = now.isoformat()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM quests WHERE due_date IS NOT NULL "
            "AND due_date <= ? AND due_date >= ? "
            "AND reminder_sent = 0 AND status NOT IN ('done','dropped','archived')",
            (cutoff, now_iso[:10])
        ).fetchall()
        return [dict(r) for r in rows]


def mark_reminder_sent(quest_id: int):
    with get_db() as conn:
        conn.execute(
            "UPDATE quests SET reminder_sent = 1 WHERE id = ?", (quest_id,)
        )


# ─── Completions ────────────────────────────────────────────────────────────

def get_completed_today(chat_id: int) -> List[Dict]:
    today = date.today().isoformat()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM quests WHERE chat_id = ? AND status = 'done' AND completed_at LIKE ?",
            (chat_id, f"{today}%")
        ).fetchall()
        return [dict(r) for r in rows]


def get_completed_on(chat_id: int, day_iso: str) -> List[Dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM quests WHERE chat_id = ? AND status = 'done' AND completed_at LIKE ?",
            (chat_id, f"{day_iso}%")
        ).fetchall()
        return [dict(r) for r in rows]


def get_completed_this_week(chat_id: int) -> List[Dict]:
    today    = date.today()
    monday   = today - timedelta(days=today.weekday())
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM quests WHERE chat_id = ? AND status = 'done' "
            "AND completed_at >= ? ORDER BY completed_at DESC",
            (chat_id, monday.isoformat())
        ).fetchall()
        return [dict(r) for r in rows]


def archive_done_quests(chat_id: int) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE quests SET status = 'archived' WHERE chat_id = ? AND status = 'done'",
            (chat_id,)
        )
        return cur.rowcount


# ─── GCal dedup ─────────────────────────────────────────────────────────────

def gcal_event_exists(chat_id: int, gcal_event_id: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM quests WHERE chat_id = ? AND gcal_event_id = ? "
            "AND status NOT IN ('dropped','archived')",
            (chat_id, gcal_event_id)
        ).fetchone()
        return row is not None
