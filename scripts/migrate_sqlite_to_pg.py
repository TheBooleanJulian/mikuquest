"""
One-off migration: copy existing data/miguquest.db (SQLite) into Postgres.

Usage:
    SQLITE_PATH=data/miguquest.db DATABASE_URL=postgres://... python scripts/migrate_sqlite_to_pg.py

Run this once after deploying the Postgres-backed database.py, before retiring
the old SQLite file.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db

SQLITE_PATH = os.environ.get("SQLITE_PATH", "data/miguquest.db")

TABLES = {
    "player": ["chat_id", "total_xp", "level", "streak_days",
               "last_active_date", "quests_completed_total"],
    "quests": ["id", "chat_id", "text", "source", "status", "priority", "tag",
               "xp_value", "notes", "due_date", "reminder_sent", "recurring",
               "pinned", "gcal_event_id", "created_at", "completed_at"],
    "quest_messages": ["chat_id", "message_id", "quest_id"],
    "daily_goals": ["chat_id", "quest_id", "date"],
}


def main():
    if not os.path.exists(SQLITE_PATH):
        raise SystemExit(f"SQLite file not found: {SQLITE_PATH}")

    db.init_db()

    src = sqlite3.connect(SQLITE_PATH)
    src.row_factory = sqlite3.Row

    with db.get_db() as conn:
        cur = conn.cursor()
        for table, columns in TABLES.items():
            rows = src.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
            col_list = ", ".join(columns)
            placeholders = ", ".join(["%s"] * len(columns))
            for row in rows:
                cur.execute(
                    f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
                    f"ON CONFLICT DO NOTHING",
                    [row[c] for c in columns],
                )
            print(f"Migrated {len(rows)} rows into {table}")

        # Keep the quests id sequence in sync with the highest migrated id.
        cur.execute("SELECT setval(pg_get_serial_sequence('quests', 'id'), "
                    "COALESCE((SELECT MAX(id) FROM quests), 1))")

    src.close()
    print("Migration complete.")


if __name__ == "__main__":
    main()
