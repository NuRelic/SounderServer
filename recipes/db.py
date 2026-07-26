"""Schema, seed data, and store-order query for the recipes + grocery list feature.

Exposes `connect()`, `init_schema()`, and `seed_sections()` as plain functions
rather than a module-level connection: callers (tests today, a later
runtime-owning module going forward) pass in the path and drive the lifecycle
themselves. `check_same_thread=False` and WAL are set here regardless, since
whatever ends up holding the long-lived connection will be shared across
threads the same way the soundboard's catalog DB is (server.py:229).
"""

import sqlite3
import threading

from .seed import SECTIONS

LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS section (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,
    position INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS subsection (
    id         INTEGER PRIMARY KEY,
    section_id INTEGER NOT NULL REFERENCES section(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    position   INTEGER NOT NULL,
    UNIQUE(section_id, name)
);

CREATE TABLE IF NOT EXISTS pantry_item (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL UNIQUE,
    subsection_id  INTEGER REFERENCES subsection(id) ON DELETE SET NULL,
    is_staple      INTEGER NOT NULL DEFAULT 0,
    buy_unit       TEXT,
    shaws_url      TEXT,
    shaws_sku      TEXT,
    notes          TEXT
);

CREATE TABLE IF NOT EXISTS pantry_alias (
    pantry_item_id INTEGER NOT NULL REFERENCES pantry_item(id) ON DELETE CASCADE,
    alias          TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS recipe (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    source_name  TEXT,
    source_url   TEXT,
    servings     INTEGER,
    time_minutes INTEGER,
    instructions TEXT,
    notes        TEXT,
    photo_url    TEXT,
    created_by   TEXT,
    created_at   INTEGER NOT NULL,
    archived     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS recipe_ingredient (
    id             INTEGER PRIMARY KEY,
    recipe_id      INTEGER NOT NULL REFERENCES recipe(id) ON DELETE CASCADE,
    position       INTEGER NOT NULL,
    raw_text       TEXT NOT NULL,
    qty            REAL,
    unit           TEXT,
    pantry_item_id INTEGER REFERENCES pantry_item(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS list_line (
    id             INTEGER PRIMARY KEY,
    pantry_item_id INTEGER REFERENCES pantry_item(id) ON DELETE SET NULL,
    free_text      TEXT,
    checked        INTEGER NOT NULL DEFAULT 0,
    checked_by     TEXT,
    checked_at     INTEGER,
    created_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS list_contribution (
    id           INTEGER PRIMARY KEY,
    list_line_id INTEGER NOT NULL REFERENCES list_line(id) ON DELETE CASCADE,
    recipe_id    INTEGER REFERENCES recipe(id) ON DELETE CASCADE,
    added_by     TEXT,
    qty          REAL,
    unit         TEXT,
    raw_text     TEXT
);

CREATE TABLE IF NOT EXISTS meal_plan (
    recipe_id INTEGER PRIMARY KEY REFERENCES recipe(id) ON DELETE CASCADE,
    added_by  TEXT,
    added_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ing_recipe   ON recipe_ingredient(recipe_id);
CREATE INDEX IF NOT EXISTS idx_ing_pantry   ON recipe_ingredient(pantry_item_id);
CREATE INDEX IF NOT EXISTS idx_contrib_line ON list_contribution(list_line_id);
CREATE INDEX IF NOT EXISTS idx_contrib_rcp  ON list_contribution(recipe_id);
CREATE INDEX IF NOT EXISTS idx_line_pantry  ON list_line(pantry_item_id);
"""


def connect(path):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn):
    with LOCK:
        conn.executescript(SCHEMA)
        conn.commit()


def seed_sections(conn):
    """Insert the default store layout. Idempotent — safe on every boot."""
    with LOCK:
        for s_pos, (section_name, subs) in enumerate(SECTIONS):
            conn.execute(
                "INSERT OR IGNORE INTO section(name, position) VALUES(?,?)",
                (section_name, s_pos),
            )
            sid = conn.execute(
                "SELECT id FROM section WHERE name=?", (section_name,)
            ).fetchone()["id"]
            for sub_pos, sub_name in enumerate(subs):
                conn.execute(
                    "INSERT OR IGNORE INTO subsection(section_id, name, position)"
                    " VALUES(?,?,?)",
                    (sid, sub_name, sub_pos),
                )
        conn.commit()


# The store walk, as one ordering: section position, then the invisible
# sub-category position, then alphabetical. Unfiled items (no subsection) sort
# to the very end via the COALESCE sentinel.
STORE_ORDER_SQL = """
SELECT p.*, s.name AS section_name, s.id AS section_id, sub.name AS subsection_name
FROM pantry_item p
LEFT JOIN subsection sub ON sub.id = p.subsection_id
LEFT JOIN section    s   ON s.id  = sub.section_id
ORDER BY COALESCE(s.position, 9999),
         COALESCE(sub.position, 9999),
         p.name COLLATE NOCASE
"""


def bump_version(conn):
    """Bump the list version. Every mutating request must call this."""
    with LOCK:
        conn.execute("""
            INSERT INTO meta(key, value) VALUES('list_version', '1')
            ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1
        """)
        conn.commit()
    return get_version(conn)


def get_version(conn):
    row = conn.execute(
        "SELECT value FROM meta WHERE key='list_version'"
    ).fetchone()
    return int(row["value"]) if row else 0
