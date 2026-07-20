import os
import secrets
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool

DATABASE_URL = os.environ.get("DATABASE_URL")
XP_MAP = {"critical": 40, "high": 30, "medium": 20, "low": 10}

BACKLOG_AUTOPULL_N  = 5
POMO_DEFAULT_MINUTES = 25
POMO_MIN_MINUTES    = 10
POMO_MAX_MINUTES    = 90
POMO_XP_BONUS       = 15

_pool: Optional[psycopg2.pool.SimpleConnectionPool] = None


def _get_pool() -> psycopg2.pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL environment variable is not set.")
        _pool = psycopg2.pool.SimpleConnectionPool(1, 10, dsn=DATABASE_URL)
    return _pool


@contextmanager
def get_db():
    pool = _get_pool()
    conn = pool.getconn()
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def init_db():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS quests (
                id              SERIAL PRIMARY KEY,
                chat_id         BIGINT  NOT NULL,
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
                chat_id                 BIGINT PRIMARY KEY,
                total_xp               INTEGER DEFAULT 0,
                level                  INTEGER DEFAULT 1,
                streak_days            INTEGER DEFAULT 0,
                last_active_date       TEXT,
                quests_completed_total INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS quest_messages (
                chat_id     BIGINT  NOT NULL,
                message_id  BIGINT  NOT NULL,
                quest_id    INTEGER NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS daily_goals (
                chat_id  BIGINT  NOT NULL,
                quest_id INTEGER NOT NULL,
                date     TEXT    NOT NULL,
                PRIMARY KEY (chat_id, quest_id, date)
            );

            CREATE TABLE IF NOT EXISTS web_login_tokens (
                token       TEXT PRIMARY KEY,
                chat_id     BIGINT NOT NULL,
                created_at  TEXT   NOT NULL,
                expires_at  TEXT   NOT NULL,
                used_at     TEXT
            );

            CREATE TABLE IF NOT EXISTS shares (
                token       TEXT PRIMARY KEY,
                chat_id     BIGINT  NOT NULL,
                kind        TEXT    NOT NULL,
                quest_id    INTEGER,
                created_at  TEXT    NOT NULL,
                expires_at  TEXT,
                revoked     INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS pomodoro_sessions (
                id                SERIAL PRIMARY KEY,
                chat_id           BIGINT  NOT NULL,
                quest_id          INTEGER,
                duration_minutes  INTEGER NOT NULL DEFAULT 25,
                started_at        TEXT    NOT NULL,
                ends_at            TEXT    NOT NULL,
                status            TEXT    NOT NULL DEFAULT 'running',
                xp_awarded        INTEGER DEFAULT 0,
                completed_at      TEXT
            );
        """)
        # Additive migrations for columns added after the initial Postgres rollout.
        cur.execute("ALTER TABLE quests ADD COLUMN IF NOT EXISTS backlogged_at TEXT")
        cur.execute("ALTER TABLE player ADD COLUMN IF NOT EXISTS last_rollover_date TEXT")


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
        cur = conn.cursor()
        cur.execute("SELECT * FROM player WHERE chat_id = %s", (chat_id,))
        row = cur.fetchone()
        if not row:
            cur.execute(
                "INSERT INTO player (chat_id, last_active_date) VALUES (%s, %s)",
                (chat_id, date.today().isoformat())
            )
            cur.execute("SELECT * FROM player WHERE chat_id = %s", (chat_id,))
            row = cur.fetchone()
        return dict(row)


def update_player(chat_id: int, **kwargs):
    if not kwargs:
        return
    sets = ", ".join(f"{k} = %s" for k in kwargs)
    vals = list(kwargs.values()) + [chat_id]
    with get_db() as conn:
        conn.cursor().execute(f"UPDATE player SET {sets} WHERE chat_id = %s", vals)


def get_all_chat_ids() -> List[int]:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT chat_id FROM player")
        return [r["chat_id"] for r in cur.fetchall()]


# ─── Quests ─────────────────────────────────────────────────────────────────────

def add_quest(chat_id: int, text: str, priority: str = "medium",
              tag: str = "#general", source: str = "typed",
              due_date: str = None, recurring: str = None,
              gcal_event_id: str = None) -> Dict:
    xp  = XP_MAP.get(priority, 20)
    now = datetime.now().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO quests (chat_id, text, source, status, priority, tag, xp_value, "
            "due_date, recurring, gcal_event_id, created_at) "
            "VALUES (%s, %s, %s, 'todo', %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (chat_id, text[:300], source, priority, tag, xp,
             due_date, recurring, gcal_event_id, now)
        )
        quest_id = cur.fetchone()["id"]
        cur.execute("SELECT * FROM quests WHERE id = %s", (quest_id,))
        return dict(cur.fetchone())


def get_quest(quest_id: int) -> Optional[Dict]:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM quests WHERE id = %s", (quest_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_quests(chat_id: int, status: Optional[str] = None,
               tag: Optional[str] = None) -> List[Dict]:
    query  = "SELECT * FROM quests WHERE chat_id = %s"
    params: list = [chat_id]
    if status:
        query += " AND status = %s"
        params.append(status)
    else:
        query += " AND status NOT IN ('dropped', 'archived')"
    if tag:
        query += " AND tag = %s"
        params.append(tag)
    query += (
        " ORDER BY pinned DESC, "
        "CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
        "WHEN 'medium' THEN 2 WHEN 'low' THEN 3 END, "
        "CASE WHEN due_date IS NOT NULL THEN 0 ELSE 1 END, "
        "due_date ASC, created_at ASC"
    )
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]


def update_quest_status(quest_id: int, status: str) -> Optional[Dict]:
    completed_at = datetime.now().isoformat() if status == "done" else None
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE quests SET status = %s, completed_at = %s WHERE id = %s",
            (status, completed_at, quest_id)
        )
        cur.execute("SELECT * FROM quests WHERE id = %s", (quest_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def update_quest_priority(quest_id: int, priority: str) -> Optional[Dict]:
    xp = XP_MAP.get(priority, 20)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE quests SET priority = %s, xp_value = %s WHERE id = %s",
            (priority, xp, quest_id)
        )
        cur.execute("SELECT * FROM quests WHERE id = %s", (quest_id,))
        row = cur.fetchone()
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
        cur = conn.cursor()
        cur.execute("SELECT notes FROM quests WHERE id = %s", (quest_id,))
        row = cur.fetchone()
        if not row:
            return None
        existing = row["notes"] or ""
        ts       = datetime.now().strftime("%d %b %H:%M")
        sep      = "\n" if existing else ""
        new_notes = existing + f"{sep}[{ts}] {note}"
        cur.execute("UPDATE quests SET notes = %s WHERE id = %s", (new_notes, quest_id))
        cur.execute("SELECT * FROM quests WHERE id = %s", (quest_id,))
        return dict(cur.fetchone())


# ─── Quest message tracking ───────────────────────────────────────────────────

def save_quest_message(chat_id: int, message_id: int, quest_id: int):
    with get_db() as conn:
        conn.cursor().execute(
            "INSERT INTO quest_messages (chat_id, message_id, quest_id) VALUES (%s,%s,%s) "
            "ON CONFLICT (chat_id, message_id) DO UPDATE SET quest_id = EXCLUDED.quest_id",
            (chat_id, message_id, quest_id)
        )


def get_quest_by_message(chat_id: int, message_id: int) -> Optional[int]:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT quest_id FROM quest_messages WHERE chat_id = %s AND message_id = %s",
            (chat_id, message_id)
        )
        row = cur.fetchone()
        return row["quest_id"] if row else None


# ─── Daily Goals ─────────────────────────────────────────────────────────────

def set_daily_goal(chat_id: int, quest_id: int):
    today = date.today().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO daily_goals (chat_id, quest_id, date) VALUES (%s,%s,%s) "
            "ON CONFLICT (chat_id, quest_id, date) DO NOTHING",
            (chat_id, quest_id, today)
        )
        cur.execute(
            "UPDATE quests SET pinned = 1 WHERE id = %s AND chat_id = %s",
            (quest_id, chat_id)
        )


def unset_daily_goal(chat_id: int, quest_id: int):
    today = date.today().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM daily_goals WHERE chat_id = %s AND quest_id = %s AND date = %s",
            (chat_id, quest_id, today)
        )
        cur.execute(
            "UPDATE quests SET pinned = 0 WHERE id = %s AND chat_id = %s",
            (quest_id, chat_id)
        )


def get_daily_goals(chat_id: int) -> List[int]:
    today = date.today().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT quest_id FROM daily_goals WHERE chat_id = %s AND date = %s",
            (chat_id, today)
        )
        return [r["quest_id"] for r in cur.fetchall()]


def clear_old_pins(chat_id: int):
    """Unpin quests whose goal date has passed."""
    today = date.today().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT quest_id FROM daily_goals WHERE chat_id = %s AND date < %s",
            (chat_id, today)
        )
        old = cur.fetchall()
        for row in old:
            cur.execute(
                "UPDATE quests SET pinned = 0 WHERE id = %s", (row["quest_id"],)
            )
        cur.execute(
            "DELETE FROM daily_goals WHERE chat_id = %s AND date < %s",
            (chat_id, today)
        )


# ─── Backlog & Daily Rollover ─────────────────────────────────────────────────

def ensure_daily_rollover(chat_id: int) -> List[Dict]:
    """Sweep yesterday's unfinished quests to backlog and auto-pull the top N back.

    Idempotent per calendar day via player.last_rollover_date — safe to call from
    every board-render entry point (bot + web) as well as the 6AM cron job.
    """
    today  = date.today().isoformat()
    player = get_or_create_player(chat_id)
    if player.get("last_rollover_date") == today:
        return []

    now = datetime.now().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE quests SET status = 'backlog', backlogged_at = %s "
            "WHERE chat_id = %s AND status IN ('todo', 'in_progress')",
            (now, chat_id)
        )
        cur.execute(
            "SELECT * FROM quests WHERE chat_id = %s AND status = 'backlog' "
            "ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
            "WHEN 'medium' THEN 2 WHEN 'low' THEN 3 END, backlogged_at ASC "
            "LIMIT %s",
            (chat_id, BACKLOG_AUTOPULL_N)
        )
        to_pull = [dict(r) for r in cur.fetchall()]
        for quest in to_pull:
            cur.execute(
                "UPDATE quests SET status = 'todo', backlogged_at = NULL WHERE id = %s",
                (quest["id"],)
            )
        cur.execute(
            "UPDATE player SET last_rollover_date = %s WHERE chat_id = %s",
            (today, chat_id)
        )
    return to_pull


def pull_from_backlog(chat_id: int, quest_id: int) -> Optional[Dict]:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE quests SET status = 'todo', backlogged_at = NULL "
            "WHERE id = %s AND chat_id = %s AND status = 'backlog'",
            (quest_id, chat_id)
        )
        cur.execute("SELECT * FROM quests WHERE id = %s", (quest_id,))
        row = cur.fetchone()
        return dict(row) if row else None


# ─── Due date / reminders ────────────────────────────────────────────────────

def get_due_reminders(within_minutes: int = 60) -> List[Dict]:
    """Quests due within N minutes that haven't been reminded yet."""
    now     = datetime.now()
    cutoff  = (now + timedelta(minutes=within_minutes)).isoformat()
    now_iso = now.isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM quests WHERE due_date IS NOT NULL "
            "AND due_date <= %s AND due_date >= %s "
            "AND reminder_sent = 0 AND status NOT IN ('done','dropped','archived')",
            (cutoff, now_iso[:10])
        )
        return [dict(r) for r in cur.fetchall()]


def mark_reminder_sent(quest_id: int):
    with get_db() as conn:
        conn.cursor().execute(
            "UPDATE quests SET reminder_sent = 1 WHERE id = %s", (quest_id,)
        )


# ─── Completions ────────────────────────────────────────────────────────────

def get_completed_today(chat_id: int) -> List[Dict]:
    today = date.today().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM quests WHERE chat_id = %s AND status = 'done' AND completed_at LIKE %s",
            (chat_id, f"{today}%")
        )
        return [dict(r) for r in cur.fetchall()]


def get_completed_on(chat_id: int, day_iso: str) -> List[Dict]:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM quests WHERE chat_id = %s AND status = 'done' AND completed_at LIKE %s",
            (chat_id, f"{day_iso}%")
        )
        return [dict(r) for r in cur.fetchall()]


def get_completed_this_week(chat_id: int) -> List[Dict]:
    today    = date.today()
    monday   = today - timedelta(days=today.weekday())
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM quests WHERE chat_id = %s AND status = 'done' "
            "AND completed_at >= %s ORDER BY completed_at DESC",
            (chat_id, monday.isoformat())
        )
        return [dict(r) for r in cur.fetchall()]


def archive_done_quests(chat_id: int) -> int:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE quests SET status = 'archived' WHERE chat_id = %s AND status = 'done'",
            (chat_id,)
        )
        return cur.rowcount


# ─── GCal dedup ─────────────────────────────────────────────────────────────

def gcal_event_exists(chat_id: int, gcal_event_id: str) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM quests WHERE chat_id = %s AND gcal_event_id = %s "
            "AND status NOT IN ('dropped','archived')",
            (chat_id, gcal_event_id)
        )
        return cur.fetchone() is not None


# ─── Web login tokens (magic link) ───────────────────────────────────────────

def create_login_token(chat_id: int, ttl_minutes: int = 10) -> str:
    token      = secrets.token_urlsafe(24)
    now        = datetime.now()
    expires_at = (now + timedelta(minutes=ttl_minutes)).isoformat()
    with get_db() as conn:
        conn.cursor().execute(
            "INSERT INTO web_login_tokens (token, chat_id, created_at, expires_at) "
            "VALUES (%s, %s, %s, %s)",
            (token, chat_id, now.isoformat(), expires_at)
        )
    return token


def consume_login_token(token: str) -> Optional[int]:
    now_iso = datetime.now().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT chat_id FROM web_login_tokens WHERE token = %s "
            "AND used_at IS NULL AND expires_at > %s",
            (token, now_iso)
        )
        row = cur.fetchone()
        if not row:
            return None
        cur.execute(
            "UPDATE web_login_tokens SET used_at = %s WHERE token = %s",
            (now_iso, token)
        )
        return row["chat_id"]


# ─── Shares ──────────────────────────────────────────────────────────────────

def create_share(chat_id: int, kind: str, quest_id: int = None,
                  expires_in_days: int = None) -> str:
    token      = secrets.token_urlsafe(16)
    now        = datetime.now()
    expires_at = (now + timedelta(days=expires_in_days)).isoformat() if expires_in_days else None
    with get_db() as conn:
        conn.cursor().execute(
            "INSERT INTO shares (token, chat_id, kind, quest_id, created_at, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (token, chat_id, kind, quest_id, now.isoformat(), expires_at)
        )
    return token


def get_share(token: str) -> Optional[Dict]:
    now_iso = datetime.now().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM shares WHERE token = %s AND revoked = 0 "
            "AND (expires_at IS NULL OR expires_at > %s)",
            (token, now_iso)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def list_shares(chat_id: int) -> List[Dict]:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM shares WHERE chat_id = %s AND revoked = 0 ORDER BY created_at DESC",
            (chat_id,)
        )
        return [dict(r) for r in cur.fetchall()]


def revoke_share(token: str):
    with get_db() as conn:
        conn.cursor().execute(
            "UPDATE shares SET revoked = 1 WHERE token = %s", (token,)
        )


# ─── Pomodoro ────────────────────────────────────────────────────────────────

def start_pomodoro(chat_id: int, quest_id: int = None,
                    duration_minutes: int = POMO_DEFAULT_MINUTES) -> Dict:
    duration_minutes = max(POMO_MIN_MINUTES, min(POMO_MAX_MINUTES, duration_minutes))
    now     = datetime.now()
    ends_at = now + timedelta(minutes=duration_minutes)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO pomodoro_sessions (chat_id, quest_id, duration_minutes, "
            "started_at, ends_at) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (chat_id, quest_id, duration_minutes, now.isoformat(), ends_at.isoformat())
        )
        session_id = cur.fetchone()["id"]
        cur.execute("SELECT * FROM pomodoro_sessions WHERE id = %s", (session_id,))
        return dict(cur.fetchone())


def get_pomodoro(session_id: int) -> Optional[Dict]:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM pomodoro_sessions WHERE id = %s", (session_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def cancel_pomodoro(chat_id: int, session_id: int) -> Optional[Dict]:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE pomodoro_sessions SET status = 'cancelled' "
            "WHERE id = %s AND chat_id = %s AND status = 'running'",
            (session_id, chat_id)
        )
        cur.execute("SELECT * FROM pomodoro_sessions WHERE id = %s", (session_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_active_pomodoro(chat_id: int) -> Optional[Dict]:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM pomodoro_sessions WHERE chat_id = %s AND status = 'running' "
            "ORDER BY started_at DESC LIMIT 1",
            (chat_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_due_pomodoros() -> List[Dict]:
    now_iso = datetime.now().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM pomodoro_sessions WHERE status = 'running' AND ends_at <= %s",
            (now_iso,)
        )
        return [dict(r) for r in cur.fetchall()]


def complete_pomodoro(chat_id: int, session_id: int) -> Optional[Dict]:
    now = datetime.now().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE pomodoro_sessions SET status = 'completed', completed_at = %s, "
            "xp_awarded = %s WHERE id = %s AND status = 'running'",
            (now, POMO_XP_BONUS, session_id)
        )
        awarded = cur.rowcount > 0
        cur.execute("SELECT * FROM pomodoro_sessions WHERE id = %s", (session_id,))
        session = cur.fetchone()
        if not session:
            return None
        session = dict(session)

    if awarded:
        player    = get_or_create_player(chat_id)
        new_xp    = player["total_xp"] + POMO_XP_BONUS
        new_level = compute_level(new_xp)
        update_player(chat_id, total_xp=new_xp, level=new_level)
    return session


def get_today_pomodoro_stats(chat_id: int) -> Dict:
    today = date.today().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS count, COALESCE(SUM(duration_minutes), 0) AS minutes, "
            "COALESCE(SUM(xp_awarded), 0) AS xp FROM pomodoro_sessions "
            "WHERE chat_id = %s AND status = 'completed' AND completed_at LIKE %s",
            (chat_id, f"{today}%")
        )
        return dict(cur.fetchone())
