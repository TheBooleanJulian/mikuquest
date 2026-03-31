import sqlite3
import os
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "data/miguquest.db")

XP_MAP = {"critical": 40, "high": 30, "medium": 20, "low": 10}


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
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id     INTEGER NOT NULL,
                text        TEXT    NOT NULL,
                source      TEXT    DEFAULT 'typed',
                status      TEXT    DEFAULT 'todo',
                priority    TEXT    DEFAULT 'medium',
                tag         TEXT    DEFAULT '#general',
                xp_value    INTEGER DEFAULT 20,
                created_at  TEXT    NOT NULL,
                due_date    TEXT,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS player (
                chat_id                 INTEGER PRIMARY KEY,
                total_xp               INTEGER DEFAULT 0,
                level                  INTEGER DEFAULT 1,
                streak_days            INTEGER DEFAULT 0,
                last_active_date       TEXT,
                quests_completed_total INTEGER DEFAULT 0
            );
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
              tag: str = "#general", source: str = "typed") -> Dict:
    xp = XP_MAP.get(priority, 20)
    now = datetime.now().isoformat()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO quests (chat_id, text, source, status, priority, tag, xp_value, created_at) "
            "VALUES (?, ?, ?, 'todo', ?, ?, ?, ?)",
            (chat_id, text[:300], source, priority, tag, xp, now)
        )
        row = conn.execute("SELECT * FROM quests WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def get_quest(quest_id: int) -> Optional[Dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM quests WHERE id = ?", (quest_id,)).fetchone()
        return dict(row) if row else None


def get_quests(chat_id: int, status: Optional[str] = None,
               tag: Optional[str] = None) -> List[Dict]:
    query = "SELECT * FROM quests WHERE chat_id = ?"
    params: list = [chat_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    if tag:
        query += " AND tag = ?"
        params.append(tag)
    query += (
        " AND status NOT IN ('dropped', 'archived')"
        if not status else ""
    )
    query += (
        " ORDER BY "
        "CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
        "WHEN 'medium' THEN 2 WHEN 'low' THEN 3 END, created_at ASC"
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

    player = get_or_create_player(chat_id)
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    last = player.get("last_active_date", "")
    streak = player.get("streak_days", 0)
    if last == yesterday:
        streak += 1
    elif last != today:
        streak = 1
    # if last == today: keep streak unchanged

    new_xp = player["total_xp"] + quest["xp_value"]
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


def archive_done_quests(chat_id: int) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE quests SET status = 'archived' WHERE chat_id = ? AND status = 'done'",
            (chat_id,)
        )
        return cur.rowcount
