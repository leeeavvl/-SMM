from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

# DATA_DIR позволяет вынести базу на постоянный диск/volume (например,
# Railway Volume) — без переменной поведение как раньше: файл рядом с кодом.
_data_dir = os.environ.get("DATA_DIR")
DB_PATH = Path(_data_dir) / "data.db" if _data_dir else Path(__file__).resolve().parent.parent / "data.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS platforms (
    id TEXT PRIMARY KEY,
    connected INTEGER NOT NULL DEFAULT 0,
    credentials TEXT NOT NULL DEFAULT '{}',
    brief TEXT NOT NULL DEFAULT '',
    channel_url TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS content (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    media_url TEXT,
    tags TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS content_platforms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id INTEGER NOT NULL REFERENCES content(id) ON DELETE CASCADE,
    platform_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    scheduled_at TEXT,
    published_at TEXT,
    url TEXT,
    error TEXT,
    UNIQUE(content_id, platform_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform_id TEXT NOT NULL,
    date TEXT NOT NULL DEFAULT (date('now')),
    orders INTEGER NOT NULL DEFAULT 0,
    revenue REAL NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS content_plan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date TEXT NOT NULL,
    platform_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    format_hint TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL DEFAULT '',
    content_type TEXT NOT NULL DEFAULT '',
    format TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'idea',
    content_id INTEGER REFERENCES content(id) ON DELETE SET NULL,
    synced_to_sheet INTEGER NOT NULL DEFAULT 0,
    sheet_tab TEXT,
    sheet_row INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS post_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_platform_id INTEGER NOT NULL UNIQUE REFERENCES content_platforms(id) ON DELETE CASCADE,
    views INTEGER,
    likes INTEGER,
    comments INTEGER,
    source TEXT NOT NULL DEFAULT 'manual',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS content_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id INTEGER NOT NULL UNIQUE REFERENCES content(id) ON DELETE CASCADE,
    score INTEGER NOT NULL DEFAULT 0,
    feedback TEXT NOT NULL DEFAULT '',
    suggestions TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS knowledge_base (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'upload',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_cursor():
    conn = get_connection()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    finally:
        conn.close()


def _ensure_column(cur: sqlite3.Cursor, table: str, column: str, ddl: str) -> None:
    cur.execute(f"PRAGMA table_info({table})")
    existing = {row["name"] for row in cur.fetchall()}
    if column not in existing:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


DEFAULT_BRAND_DESCRIPTION = (
    "кадровое агентство, которое помогает студентам-юристам и молодым специалистам расти "
    "профессионально, находить работу и строить карьеру в юриспруденции через сообщество, "
    "мероприятия (конференции, муткорты, олимпиады) и консультации."
)
DEFAULT_BRAND_NAME = "Карьерный юрист"


def init_db() -> None:
    from app.platforms import PLATFORMS

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db_cursor() as cur:
        cur.executescript(SCHEMA)
        _ensure_column(cur, "platforms", "brief", "brief TEXT NOT NULL DEFAULT ''")
        _ensure_column(cur, "platforms", "channel_url", "channel_url TEXT NOT NULL DEFAULT ''")
        _ensure_column(cur, "content_plan", "direction", "direction TEXT NOT NULL DEFAULT ''")
        _ensure_column(cur, "content_plan", "content_type", "content_type TEXT NOT NULL DEFAULT ''")
        _ensure_column(cur, "content_plan", "format", "format TEXT NOT NULL DEFAULT ''")
        _ensure_column(cur, "content_plan", "synced_to_sheet", "synced_to_sheet INTEGER NOT NULL DEFAULT 0")
        _ensure_column(cur, "content_plan", "sheet_tab", "sheet_tab TEXT")
        _ensure_column(cur, "content_plan", "sheet_row", "sheet_row INTEGER")
        for platform in PLATFORMS:
            cur.execute(
                "INSERT OR IGNORE INTO platforms (id, connected, credentials) VALUES (?, 0, '{}')",
                (platform.id,),
            )
        cur.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('brand_name', ?)",
            (DEFAULT_BRAND_NAME,),
        )
        cur.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('brand_description', ?)",
            (DEFAULT_BRAND_DESCRIPTION,),
        )


def row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def load_json(value: str | None) -> dict:
    if not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


def get_setting(key: str) -> str | None:
    with db_cursor() as cur:
        cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
