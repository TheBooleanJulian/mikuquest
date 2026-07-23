import os
import random
import secrets
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool

DATABASE_URL = os.environ.get("DATABASE_URL")
DEFAULT_TITLE = "Unpaid Intern"

BACKLOG_AUTOPULL_N  = 5
POMO_DEFAULT_MINUTES = 25
POMO_MIN_MINUTES    = 10
POMO_MAX_MINUTES    = 90
POMO_HELIUM_BONUS   = 15

# Helium-3 is the sole progression currency: it funds the shop (spendable
# player.helium3) and, cumulatively, drives levels (player.total_xp — kept as
# the lifetime-earned counter/column name to avoid a schema migration).
HELIUM_MAP       = {"critical": 40, "high": 30, "medium": 20, "low": 10}
LOOT_DROP_CHANCE = 0.3
RARITY_WEIGHTS   = {"common": 70, "rare": 25, "epic": 5}
LOOT_POOL = [
    # common (25)
    {"key": "regolith_chunk",   "name": "Loose Regolith Chunk",      "rarity": "common"},
    {"key": "o2_filter",        "name": "Cracked O2 Filter",         "rarity": "common"},
    {"key": "spare_bolt",       "name": "Spare Bolt",                "rarity": "common"},
    {"key": "frayed_cable",     "name": "Frayed Cable",              "rarity": "common"},
    {"key": "ration_pack",      "name": "Empty Ration Pack",         "rarity": "common"},
    {"key": "solar_shard",      "name": "Dusty Solar Panel Shard",   "rarity": "common"},
    {"key": "bent_antenna",     "name": "Bent Antenna",              "rarity": "common"},
    {"key": "glove_liner",      "name": "Worn Glove Liner",          "rarity": "common"},
    {"key": "coolant_canister", "name": "Empty Coolant Canister",    "rarity": "common"},
    {"key": "visor_scuff",      "name": "Scuffed Helmet Visor",      "rarity": "common"},
    {"key": "loose_washer",     "name": "Loose Washer",              "rarity": "common"},
    {"key": "rusted_hinge",     "name": "Rusted Hinge",              "rarity": "common"},
    {"key": "zipper_pull",      "name": "Broken Zipper Pull",        "rarity": "common"},
    {"key": "visor_seal",       "name": "Cracked Visor Seal",        "rarity": "common"},
    {"key": "mission_patch",    "name": "Faded Mission Patch",       "rarity": "common"},
    {"key": "spare_fuse",       "name": "Spare Fuse",                "rarity": "common"},
    {"key": "water_pouch",      "name": "Empty Water Pouch",         "rarity": "common"},
    {"key": "insulation_wrap",  "name": "Torn Insulation Wrap",      "rarity": "common"},
    {"key": "ceramic_tile",     "name": "Chipped Ceramic Tile",      "rarity": "common"},
    {"key": "boot_sole",        "name": "Worn Boot Sole",            "rarity": "common"},
    {"key": "wire_bundle",      "name": "Tangled Wire Bundle",       "rarity": "common"},
    {"key": "ration_tin",       "name": "Dented Ration Tin",         "rarity": "common"},
    {"key": "star_chart_frag",  "name": "Old Star Chart Fragment",   "rarity": "common"},
    {"key": "broken_flashlight","name": "Broken Flashlight",         "rarity": "common"},
    {"key": "spare_oring",      "name": "Spare O-Ring",              "rarity": "common"},
    # rare (17)
    {"key": "drill_bit",        "name": "Salvaged Drill Bit",        "rarity": "rare"},
    {"key": "titanium_panel",   "name": "Titanium Panel",            "rarity": "rare"},
    {"key": "battery_cell",     "name": "Backup Battery Cell",       "rarity": "rare"},
    {"key": "data_chip",        "name": "Encrypted Data Chip",       "rarity": "rare"},
    {"key": "gyroscope",        "name": "Precision Gyroscope",       "rarity": "rare"},
    {"key": "solar_cell",       "name": "Refurbished Solar Cell",    "rarity": "rare"},
    {"key": "oxygen_tank",      "name": "Sealed Oxygen Tank",        "rarity": "rare"},
    {"key": "tool_kit",         "name": "Modular Tool Kit",          "rarity": "rare"},
    {"key": "signal_booster",   "name": "Signal Booster Array",      "rarity": "rare"},
    {"key": "airlock_seal",     "name": "Reinforced Airlock Seal",   "rarity": "rare"},
    {"key": "nav_beacon",       "name": "Navigation Beacon",         "rarity": "rare"},
    {"key": "thermal_regulator","name": "Thermal Regulator Unit",    "rarity": "rare"},
    {"key": "rover_wheel",      "name": "Spare Rover Wheel",         "rarity": "rare"},
    {"key": "pressure_gauge",   "name": "Calibrated Pressure Gauge", "rarity": "rare"},
    {"key": "circuit_board",    "name": "Hardened Circuit Board",    "rarity": "rare"},
    {"key": "mag_clamp",        "name": "Mag-Lock Clamp",            "rarity": "rare"},
    {"key": "cargo_case",       "name": "Insulated Cargo Case",      "rarity": "rare"},
    # epic (8)
    {"key": "he3_core_sample",  "name": "Helium-3 Core Sample",      "rarity": "epic"},
    {"key": "rover_module",     "name": "Derelict Rover Module",     "rarity": "epic"},
    {"key": "lunar_relic",      "name": "Ancient Lunar Relic",       "rarity": "epic"},
    {"key": "ice_core",         "name": "Pristine Ice Core",         "rarity": "epic"},
    {"key": "reactor_fragment", "name": "Fusion Reactor Fragment",   "rarity": "epic"},
    {"key": "meteorite_shard",  "name": "Meteorite Shard",           "rarity": "epic"},
    {"key": "ai_core",          "name": "Prototype AI Core",         "rarity": "epic"},
    {"key": "colony_blueprint", "name": "Forgotten Colony Blueprint","rarity": "epic"},
]
RARITY_ICONS = {"common": "🪨", "rare": "⚙️", "epic": "💎"}

SHOP_ITEMS = {
    "custom_title":  {"name": "🎫 Custom Title Unlock", "cost": 100,
                       "desc": "Unlocks /settitle to set your own vanity title."},
}

DAILY_QUEST_HELIUM = 35
DAILY_QUEST_POOL = [
    # self-improvement (17)
    {"text": "Do a 10-minute stretch or workout",                          "category": "self-improvement"},
    {"text": "Write down 3 things you're grateful for",                    "category": "self-improvement"},
    {"text": "Read 10 pages of a book you've been meaning to start",       "category": "self-improvement"},
    {"text": "Meditate or sit quietly for 5 minutes",                      "category": "self-improvement"},
    {"text": "Learn one new word in a language you're studying",           "category": "self-improvement"},
    {"text": "Go for a 15-minute walk outside",                           "category": "self-improvement"},
    {"text": "Drink a full glass of water first thing",                    "category": "self-improvement"},
    {"text": "Write a short journal entry about today",                    "category": "self-improvement"},
    {"text": "Try a 5-minute breathing exercise",                         "category": "self-improvement"},
    {"text": "Stretch your neck and shoulders for 5 minutes",              "category": "self-improvement"},
    {"text": "Listen to a podcast episode on something new",               "category": "self-improvement"},
    {"text": "Practice a skill you're learning for 10 minutes",           "category": "self-improvement"},
    {"text": "Get to bed 30 minutes earlier tonight",                     "category": "self-improvement"},
    {"text": "Do 20 push-ups or squats",                                  "category": "self-improvement"},
    {"text": "Write down one goal for next month",                        "category": "self-improvement"},
    {"text": "Declutter your phone's home screen",                        "category": "self-improvement"},
    {"text": "Watch a short educational video",                           "category": "self-improvement"},
    # productive (17)
    {"text": "Clear your inbox to zero",                                  "category": "productive"},
    {"text": "Tidy your desk or workspace for 5 minutes",                 "category": "productive"},
    {"text": "Back up an important file",                                 "category": "productive"},
    {"text": "Batch-schedule tomorrow's top 3 priorities",                "category": "productive"},
    {"text": "Unsubscribe from 3 emails you never read",                  "category": "productive"},
    {"text": "Update your calendar for the week ahead",                    "category": "productive"},
    {"text": "File or organize one folder of documents",                  "category": "productive"},
    {"text": "Review and update your task list",                         "category": "productive"},
    {"text": "Set up a template for an email you keep rewriting",         "category": "productive"},
    {"text": "Delete 10 files you no longer need",                        "category": "productive"},
    {"text": "Update a password you've been putting off",                 "category": "productive"},
    {"text": "Write tomorrow's to-do list before bed",                    "category": "productive"},
    {"text": "Consolidate duplicate notes or bookmarks",                  "category": "productive"},
    {"text": "Review your budget for 5 minutes",                          "category": "productive"},
    {"text": "Archive a completed project",                               "category": "productive"},
    {"text": "Test a backup restore to make sure it works",               "category": "productive"},
    {"text": "Organize your downloads folder",                            "category": "productive"},
    # altruist (16)
    {"text": "Send a thank-you message to someone who helped you recently", "category": "altruist"},
    {"text": "Give a coworker or friend a genuine compliment",            "category": "altruist"},
    {"text": "Check in on someone who's been quiet lately",               "category": "altruist"},
    {"text": "Leave a kind review for a small business you like",         "category": "altruist"},
    {"text": "Donate spare change or a small amount to a cause you care about", "category": "altruist"},
    {"text": "Offer to help a friend with something small",               "category": "altruist"},
    {"text": "Share a useful resource with someone who needs it",         "category": "altruist"},
    {"text": "Let someone go ahead of you today, kindly",                 "category": "altruist"},
    {"text": "Leave a positive comment or shoutout for someone's work",    "category": "altruist"},
    {"text": "Call a family member just to check in",                     "category": "altruist"},
    {"text": "Compliment a stranger's work or effort",                    "category": "altruist"},
    {"text": "Recommend someone for a job or opportunity",                "category": "altruist"},
    {"text": "Pick up litter you see on your walk",                       "category": "altruist"},
    {"text": "Introduce two people who could help each other",            "category": "altruist"},
    {"text": "Send an encouraging message to someone having a hard week", "category": "altruist"},
    {"text": "Leave a generous tip if you can today",                     "category": "altruist"},
]

COSMETIC_SEED = [
    # rare (35)
    ("stardust_wanderer",     "✨ Stardust Wanderer",       "rare"),
    ("moonlit_courier",       "🌙 Moonlit Courier",         "rare"),
    ("regolith_ranger",       "🪐 Regolith Ranger",         "rare"),
    ("vacuum_virtuoso",       "🛰️ Vacuum Virtuoso",         "rare"),
    ("crater_champion",       "🕳️ Crater Champion",         "rare"),
    ("airlock_ace",           "🚪 Airlock Ace",             "rare"),
    ("solar_sailor",          "⛵ Solar Sailor",            "rare"),
    ("orbit_operator",        "🛸 Orbit Operator",          "rare"),
    ("zero_g_gardener",       "🌱 Zero-G Gardener",         "rare"),
    ("comet_chaser",          "☄️ Comet Chaser",            "rare"),
    ("dust_devil_dodger",     "🌪️ Dust Devil Dodger",       "rare"),
    ("crater_cartographer",   "🗺️ Crater Cartographer",     "rare"),
    ("satellite_scout",       "📡 Satellite Scout",         "rare"),
    ("tether_technician",     "🔧 Tether Technician",       "rare"),
    ("capsule_captain",       "🚀 Capsule Captain",         "rare"),
    ("frost_line_forager",    "❄️ Frost Line Forager",      "rare"),
    ("star_chart_scholar",    "📖 Star Chart Scholar",      "rare"),
    ("gravity_gambler",       "⚖️ Gravity Gambler",         "rare"),
    ("airlock_artisan",       "🎨 Airlock Artisan",         "rare"),
    ("telescope_tinkerer",    "🔭 Telescope Tinkerer",      "rare"),
    ("moondust_merchant",     "💰 Moondust Merchant",       "rare"),
    ("capsule_custodian",     "🧹 Capsule Custodian",       "rare"),
    ("solar_flare_survivor",  "🔥 Solar Flare Survivor",    "rare"),
    ("nebula_navigator",      "🌫️ Nebula Navigator",        "rare"),
    ("lunar_prospector",      "⛏️ Lunar Prospector",        "rare"),
    ("orbital_engineer",      "⚙️ Orbital Engineer",        "rare"),
    ("cryo_specialist",       "🧊 Cryo Specialist",         "rare"),
    ("rover_wrangler",        "🚙 Rover Wrangler",          "rare"),
    ("signal_seeker",         "📶 Signal Seeker",           "rare"),
    ("airborne_astronomer",   "🌟 Airborne Astronomer",     "rare"),
    ("crater_cadet",          "🎓 Crater Cadet",            "rare"),
    ("dust_bowl_diver",       "🏜️ Dust Bowl Diver",         "rare"),
    ("tranquility_trekker",   "🥾 Tranquility Trekker",     "rare"),
    ("habitat_handyman",      "🛠️ Habitat Handyman",        "rare"),
    ("starlight_scavenger",   "💫 Starlight Scavenger",     "rare"),
    # epic (15)
    ("lunar_legend",          "🌕 Lunar Legend",            "epic"),
    ("helium_hoarder",        "💠 Helium Hoarder",          "epic"),
    ("outpost_overseer",      "🏮 Outpost Overseer",        "epic"),
    ("void_wanderer",         "🌌 Void Wanderer",           "epic"),
    ("colony_icon",           "👑 Colony Icon",             "epic"),
    ("supernova_sovereign",   "💥 Supernova Sovereign",     "epic"),
    ("galactic_governor",     "🌠 Galactic Governor",       "epic"),
    ("eclipse_emperor",       "🌑 Eclipse Emperor",         "epic"),
    ("orbit_overlord",        "🪐 Orbit Overlord",          "epic"),
    ("starforge_savant",      "⚒️ Starforge Savant",        "epic"),
    ("cosmic_custodian",      "🌀 Cosmic Custodian",        "epic"),
    ("helium_baron",          "🏆 Helium Baron",            "epic"),
    ("lunar_luminary",        "🏵️ Lunar Luminary",          "epic"),
    ("astral_architect",      "🏛️ Astral Architect",        "epic"),
    ("singularity_seeker",    "⚫ Singularity Seeker",       "epic"),
]

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
                tag             TEXT    DEFAULT '#misc',
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

            CREATE TABLE IF NOT EXISTS inventory (
                id          SERIAL PRIMARY KEY,
                chat_id     BIGINT NOT NULL,
                item_key    TEXT   NOT NULL,
                item_name   TEXT   NOT NULL,
                rarity      TEXT   NOT NULL,
                obtained_at TEXT   NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_quests (
                date     TEXT PRIMARY KEY,
                text     TEXT NOT NULL,
                category TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cosmetics (
                id     SERIAL PRIMARY KEY,
                key    TEXT UNIQUE NOT NULL,
                text   TEXT NOT NULL,
                rarity TEXT NOT NULL DEFAULT 'rare'
            );
        """)
        # Additive migrations for columns added after the initial Postgres rollout.
        cur.execute("ALTER TABLE quests ADD COLUMN IF NOT EXISTS backlogged_at TEXT")
        cur.execute("ALTER TABLE quests ADD COLUMN IF NOT EXISTS daily_quest_date TEXT")
        cur.execute("ALTER TABLE player ADD COLUMN IF NOT EXISTS last_rollover_date TEXT")
        cur.execute("ALTER TABLE player ADD COLUMN IF NOT EXISTS helium3 INTEGER DEFAULT 0")
        cur.execute("ALTER TABLE player ADD COLUMN IF NOT EXISTS custom_title TEXT")
        cur.execute("ALTER TABLE player ADD COLUMN IF NOT EXISTS custom_title_unlocked INTEGER DEFAULT 0")
        cur.execute("ALTER TABLE player DROP COLUMN IF EXISTS streak_freezes")
        cur.execute("ALTER TABLE inventory ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'material'")

        # One-time relabel of quests tagged under the old context-tag names.
        cur.execute("UPDATE quests SET tag = '#thebooleanjulian' WHERE tag = '#dev'")
        cur.execute("UPDATE quests SET tag = '#upteach' WHERE tag = '#tutoring'")
        cur.execute("UPDATE quests SET tag = '#xymiku' WHERE tag = '#personal'")
        cur.execute("UPDATE quests SET tag = '#misc' WHERE tag IN ('#busking', '#general')")

        for key, text, rarity in COSMETIC_SEED:
            cur.execute(
                "INSERT INTO cosmetics (key, text, rarity) VALUES (%s, %s, %s) "
                "ON CONFLICT (key) DO NOTHING",
                (key, text, rarity)
            )


# ─── Player ────────────────────────────────────────────────────────────────────

def compute_level(xp: int) -> int:
    return max(1, 1 + xp // 200)


def get_display_title(player: Dict) -> str:
    return player.get("custom_title") or DEFAULT_TITLE


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
              tag: str = "#misc", source: str = "typed",
              due_date: str = None, recurring: str = None,
              gcal_event_id: str = None) -> Dict:
    xp  = HELIUM_MAP.get(priority, 20)
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
    xp = HELIUM_MAP.get(priority, 20)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE quests SET priority = %s, xp_value = %s WHERE id = %s",
            (priority, xp, quest_id)
        )
        cur.execute("SELECT * FROM quests WHERE id = %s", (quest_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_cosmetics() -> List[Dict]:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM cosmetics")
        return [dict(r) for r in cur.fetchall()]


def roll_loot() -> Optional[Dict]:
    if random.random() > LOOT_DROP_CHANCE:
        return None
    rarity = random.choices(list(RARITY_WEIGHTS.keys()), weights=list(RARITY_WEIGHTS.values()))[0]
    candidates = [
        {"key": it["key"], "name": it["name"], "rarity": it["rarity"], "kind": "material"}
        for it in LOOT_POOL if it["rarity"] == rarity
    ] + [
        {"key": c["key"], "name": c["text"], "rarity": c["rarity"], "kind": "cosmetic"}
        for c in get_cosmetics() if c["rarity"] == rarity
    ]
    return random.choice(candidates) if candidates else None


def add_loot_drop(chat_id: int, item: Dict):
    with get_db() as conn:
        conn.cursor().execute(
            "INSERT INTO inventory (chat_id, item_key, item_name, rarity, obtained_at, kind) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (chat_id, item["key"], item["name"], item["rarity"],
             datetime.now().isoformat(), item.get("kind", "material"))
        )


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

    helium3     = quest["xp_value"]
    new_lifetime = player["total_xp"] + helium3
    new_level   = compute_level(new_lifetime)
    new_helium3 = (player.get("helium3", 0) or 0) + helium3

    update_player(
        chat_id,
        total_xp=new_lifetime,
        level=new_level,
        streak_days=streak,
        last_active_date=today,
        quests_completed_total=player["quests_completed_total"] + 1,
        helium3=new_helium3,
    )

    loot = roll_loot()
    if loot:
        add_loot_drop(chat_id, loot)

    quest["helium3_awarded"] = helium3
    quest["loot"]            = loot
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
            "WHERE chat_id = %s AND status IN ('todo', 'in_progress') "
            "AND source != 'daily_miku'",
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


# ─── Miku's Quest of the Day ──────────────────────────────────────────────────

def next_daily_reset() -> datetime:
    """Midnight tonight (server-local) — when Miku's Quest of the Day rotates."""
    tomorrow = date.today() + timedelta(days=1)
    return datetime.combine(tomorrow, datetime.min.time())


def get_or_create_daily_quest() -> Dict:
    """The single quest-of-the-day, shared globally across every account."""
    today = date.today().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM daily_quests WHERE date = %s", (today,))
        row = cur.fetchone()
        if row:
            return dict(row)
        pick = random.choice(DAILY_QUEST_POOL)
        cur.execute(
            "INSERT INTO daily_quests (date, text, category) VALUES (%s, %s, %s) "
            "ON CONFLICT (date) DO NOTHING",
            (today, pick["text"], pick["category"])
        )
        cur.execute("SELECT * FROM daily_quests WHERE date = %s", (today,))
        return dict(cur.fetchone())


def ensure_daily_quest_for_chat(chat_id: int) -> Optional[Dict]:
    """Materialize today's global Miku quest as a per-chat quest row.

    Any of the chat's previous Miku quests still open are expired (dropped, not
    backlogged) — this is the "doesn't carry over" mechanic, distinct from the
    regular backlog rollover.
    """
    global_quest = get_or_create_daily_quest()
    today = date.today().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM quests WHERE chat_id = %s AND daily_quest_date = %s "
            "AND status NOT IN ('dropped', 'archived')",
            (chat_id, today)
        )
        row = cur.fetchone()
        if row:
            quest = dict(row)
            quest["category"] = global_quest["category"]
            return quest

        cur.execute(
            "UPDATE quests SET status = 'dropped' WHERE chat_id = %s AND source = 'daily_miku' "
            "AND (daily_quest_date IS NULL OR daily_quest_date != %s) "
            "AND status IN ('todo', 'in_progress')",
            (chat_id, today)
        )
        now = datetime.now().isoformat()
        cur.execute(
            "INSERT INTO quests (chat_id, text, source, status, priority, tag, xp_value, "
            "created_at, daily_quest_date) "
            "VALUES (%s, %s, 'daily_miku', 'todo', 'high', '#daily', %s, %s, %s) RETURNING id",
            (chat_id, global_quest["text"], DAILY_QUEST_HELIUM, now, today)
        )
        qid = cur.fetchone()["id"]
        cur.execute("SELECT * FROM quests WHERE id = %s", (qid,))
        quest = dict(cur.fetchone())
        quest["category"] = global_quest["category"]
        return quest


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
            (now, POMO_HELIUM_BONUS, session_id)
        )
        awarded = cur.rowcount > 0
        cur.execute("SELECT * FROM pomodoro_sessions WHERE id = %s", (session_id,))
        session = cur.fetchone()
        if not session:
            return None
        session = dict(session)

    session["helium3_awarded"] = POMO_HELIUM_BONUS if awarded else 0
    if awarded:
        player       = get_or_create_player(chat_id)
        new_lifetime = player["total_xp"] + POMO_HELIUM_BONUS
        new_level    = compute_level(new_lifetime)
        new_helium3  = (player.get("helium3", 0) or 0) + POMO_HELIUM_BONUS
        update_player(chat_id, total_xp=new_lifetime, level=new_level, helium3=new_helium3)
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


# ─── Inventory & Shop ─────────────────────────────────────────────────────────

def get_inventory(chat_id: int, kind: Optional[str] = None) -> List[Dict]:
    query  = ("SELECT item_key, item_name, rarity, kind, COUNT(*) AS qty FROM inventory "
              "WHERE chat_id = %s")
    params: list = [chat_id]
    if kind:
        query += " AND kind = %s"
        params.append(kind)
    query += (" GROUP BY item_key, item_name, rarity, kind "
              "ORDER BY CASE rarity WHEN 'epic' THEN 0 WHEN 'rare' THEN 1 ELSE 2 END, item_name")
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]


def owns_item(chat_id: int, item_key: str, kind: str) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM inventory WHERE chat_id = %s AND item_key = %s AND kind = %s LIMIT 1",
            (chat_id, item_key, kind)
        )
        return cur.fetchone() is not None


def spend_helium3(chat_id: int, amount: int) -> bool:
    """Atomic check-and-deduct so concurrent spends can't overdraw the balance."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE player SET helium3 = helium3 - %s WHERE chat_id = %s AND helium3 >= %s",
            (amount, chat_id, amount)
        )
        return cur.rowcount > 0


def buy_custom_title(chat_id: int) -> bool:
    if not spend_helium3(chat_id, SHOP_ITEMS["custom_title"]["cost"]):
        return False
    with get_db() as conn:
        conn.cursor().execute(
            "UPDATE player SET custom_title_unlocked = 1 WHERE chat_id = %s",
            (chat_id,)
        )
    return True


def set_custom_title(chat_id: int, text: str) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE player SET custom_title = %s WHERE chat_id = %s AND custom_title_unlocked = 1",
            (text[:40], chat_id)
        )
        return cur.rowcount > 0


def equip_cosmetic(chat_id: int, item_key: str) -> Optional[str]:
    """Equip an owned cosmetic as the display title. Returns the title text, or None if not owned."""
    if not owns_item(chat_id, item_key, "cosmetic"):
        return None
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT item_name FROM inventory WHERE chat_id = %s AND item_key = %s AND kind = 'cosmetic' LIMIT 1",
            (chat_id, item_key)
        )
        row = cur.fetchone()
        if not row:
            return None
        text = row["item_name"]
        cur.execute("UPDATE player SET custom_title = %s WHERE chat_id = %s", (text, chat_id))
        return text
